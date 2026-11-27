"""Background upload of completed local archives to a remote MPP daemon."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import re
import shutil
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlparse, urlunparse
from uuid import UUID, uuid4

import httpx

from app.core.archive_index import timestamp
from app.core.archive_sync import build_archive_zip
from app.core.database import TaskStore, _db_lock, _get_conn, get_task_store, task_write_transaction
from app.core.library import catalog, collection_metadata, revision
from app.core.library_sync import (
    folder_mappings,
    folder_sync_records,
    library_sync_lock,
    remember_folder,
    remote_origin,
    replica_folder_id,
)
from app.core.logging_setup import log_event
from app.core.network import httpx_client_kwargs
from app.core.paths import get_workspace_paths
from app.core.settings import RuntimeSettings, get_runtime_settings
from app.core.workspace_lifecycle import run_in_thread, uses_workspace
from app.models import Task, TaskStatus

logger = logging.getLogger(__name__)

_MAX_RETRY_DELAY_SEC = 15 * 60.0

_PORTABLE_RESULT_FIELDS = {
    "metadata",
    "image_descriptions",
    "image_download_diagnostics",
    "analysis",
    "warnings",
    "warning",
    "content_subtype",
    "subtitle_source",
    "transcript_segments",
}


def _normalized_server_url(raw_url: str) -> str:
    value = str(raw_url or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("remote_server_url must be a complete HTTP or HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("remote_server_url cannot contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("remote_server_url cannot contain a query or fragment")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port
    if port and port != {"http": 80, "https": 443}[parsed.scheme]:
        host = f"{host}:{port}"
    path = parsed.path.rstrip("/")
    if path.endswith("/api"):
        path = path[:-4]
    return urlunparse((parsed.scheme.lower(), host, path, "", "", ""))


def _portable_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    return {
        key: copy.deepcopy(result[key])
        for key in _PORTABLE_RESULT_FIELDS
        if key in result
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _retry_delay(interval: float, consecutive_failures: int) -> float:
    """Back off repeated remote failures while still probing for recovery."""
    if consecutive_failures <= 0:
        return interval
    # The minimum interval is 5s, so ten doublings already exceed the cap.
    return min(_MAX_RETRY_DELAY_SEC, interval * (2 ** min(consecutive_failures, 10)))


def _cloudflare_error_code(exc: BaseException) -> str | None:
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    match = re.search(r"error code:\s*(\d+)", exc.response.text, re.IGNORECASE)
    return match.group(1) if match else None


def _library_revision() -> int:
    with _db_lock:
        return revision(_get_conn())


def _library_for_push(server: str, workspace: str) -> tuple[int, dict, dict[str, str]] | None:
    with _db_lock:
        conn = _get_conn()
        current_revision = revision(conn)
        cursor = conn.execute(
            "SELECT pushed_library_revision FROM remote_sync_cursors WHERE server_url=? AND workspace_id=?",
            (server, workspace),
        ).fetchone()
        if cursor and current_revision <= cursor["pushed_library_revision"]:
            return None
        sync_records = folder_sync_records(conn, server, workspace)
        local_to_remote = {local: remote for local, (remote, _) in sync_records.items()}
        remote_updated_at_map = {local: updated_at for local, (_, updated_at) in sync_records.items()}
        members = [dict(row) for row in conn.execute(
            "SELECT * FROM library_folder_members WHERE origin!=?", (remote_origin(server, workspace),),
        )]
        covers = [dict(row) for row in conn.execute(
            "SELECT c.* FROM library_folder_covers c JOIN library_folder_members m "
            "ON c.content_key=m.content_key AND c.folder_id=m.folder_id",
        )]
        referenced = {row["folder_id"] for row in [*members, *covers]}
        replicas = {local for local, remote in local_to_remote.items()
                    if local == replica_folder_id(server, workspace, remote)}
        folders = [
            dict(row) for row in conn.execute("SELECT * FROM library_folders")
            if row["id"] not in replicas
            or row["id"] in referenced
            or row["updated_at"] != remote_updated_at_map.get(row["id"], "")
        ]
        collection_keys = {folder["source_collection_key"] for folder in folders}
        collections = [meta for meta in collection_metadata(conn) if meta["collection_key"] in collection_keys]
        submitted_ids = {}
        for folder in folders:
            local_id = folder["id"]
            folder["id"] = local_to_remote.get(local_id, local_id)
            submitted_ids[folder["id"]] = local_id
        records = [*members, *covers]
        contents = catalog(conn) if records else {}
        archive_ids = {}
        for key in {row["content_key"] for row in records}:
            entry = contents.get(key)
            if entry and entry["archives"]:
                archive_ids[key] = entry["archives"][0]["archive_id"]
                for task in entry["tasks"]:
                    marker = (task.result or {}).get("remote_sync") or {}
                    if marker.get("server_url") == server and marker.get("archive_id"):
                        archive_ids[key] = marker["archive_id"]
                        break
        for row in records:
            if row["content_key"] in archive_ids:
                row["archive_id"] = archive_ids[row["content_key"]]
            row["folder_id"] = local_to_remote.get(row["folder_id"], row["folder_id"])
        live_folder_updated = {folder["id"]: folder["updated_at"] for folder in folders}
        raw_deletions = [dict(row) for row in conn.execute("SELECT folder_id,deleted_at FROM library_folder_deletions")]
        raw_deletion_ids = {row["folder_id"] for row in raw_deletions}
        deleted_folders: dict[str, str] = {}
        for row in raw_deletions:
            fid = row["folder_id"]
            if any(other != fid and replica_folder_id(server, workspace, other) == fid for other in raw_deletion_ids):
                continue
            mapped = local_to_remote.get(fid, fid)
            if mapped in live_folder_updated and timestamp(live_folder_updated[mapped]) > timestamp(row["deleted_at"]):
                continue
            existing_deleted = deleted_folders.get(mapped)
            if existing_deleted is None or timestamp(row["deleted_at"]) > timestamp(existing_deleted):
                deleted_folders[mapped] = row["deleted_at"]
        live_cover_updated = {row["folder_id"]: row["updated_at"] for row in covers}
        deleted_covers: dict[str, str] = {}
        for row in conn.execute("SELECT folder_id,deleted_at FROM library_folder_cover_deletions"):
            mapped = local_to_remote.get(row["folder_id"], row["folder_id"])
            if mapped in live_cover_updated and timestamp(live_cover_updated[mapped]) >= timestamp(row["deleted_at"]):
                continue
            existing_deleted = deleted_covers.get(mapped)
            if existing_deleted is None or timestamp(row["deleted_at"]) > timestamp(existing_deleted):
                deleted_covers[mapped] = row["deleted_at"]
        collection_members = [
            dict(row) for row in conn.execute("SELECT collection_key,content_key FROM library_collection_members")
        ]
        return current_revision, {
            "folders": folders,
            "members": members,
            "covers": covers,
            "collections": collections,
            "deleted_folders": [{"folder_id": fid, "deleted_at": at} for fid, at in sorted(deleted_folders.items())],
            "deleted_covers": [{"folder_id": fid, "deleted_at": at} for fid, at in sorted(deleted_covers.items())],
            "collection_members": collection_members,
        }, submitted_ids


def _acknowledge_library(server: str, workspace: str, pushed_revision: int, folder_ids: dict[str, str]) -> None:
    with task_write_transaction() as conn:
        for local_id, remote_id in folder_ids.items():
            row = conn.execute("SELECT updated_at FROM library_folders WHERE id=?", (local_id,)).fetchone()
            if row is not None:
                remember_folder(conn, server, workspace, remote_id, local_id, remote_updated_at=row["updated_at"])
        conn.execute(
            "INSERT INTO remote_sync_cursors(server_url,workspace_id,pushed_library_revision) VALUES(?,?,?) "
            "ON CONFLICT(server_url,workspace_id) DO UPDATE SET "
            "pushed_library_revision=MAX(pushed_library_revision,excluded.pushed_library_revision)",
            (server, workspace, pushed_revision),
        )


@dataclass(frozen=True)
class UploadResult:
    task_id: UUID | None = None
    library_pushed: bool = False


class RemoteArchiveUploadService:
    """Upload completed archives and their library metadata."""

    def __init__(
        self,
        *,
        settings_getter: Callable[[], RuntimeSettings] = get_runtime_settings,
        store_getter: Callable[[], TaskStore] = get_task_store,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        self._settings_getter = settings_getter
        self._store_getter = store_getter
        self._client_factory = client_factory
        self._runner: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._runner and not self._runner.done():
            return
        self._runner = asyncio.create_task(self._run(), name="remote-archive-upload")

    async def stop(self) -> None:
        runner = self._runner
        self._runner = None
        if runner is None:
            return
        runner.cancel()
        with suppress(asyncio.CancelledError):
            await runner

    async def _run(self) -> None:
        consecutive_failures = 0
        while True:
            settings = self._settings_getter()
            interval = max(5.0, float(settings.remote_sync_interval_sec))
            delay = interval
            if settings.remote_sync_enabled and settings.remote_sync_upload_results:
                try:
                    await self.sync_once(settings)
                    consecutive_failures = 0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    consecutive_failures += 1
                    delay = _retry_delay(interval, consecutive_failures)
                    status_code = (
                        exc.response.status_code
                        if isinstance(exc, httpx.HTTPStatusError)
                        else None
                    )
                    log_event(
                        logger,
                        logging.WARNING,
                        "remote_archive_upload.cycle_failed",
                        error_type=type(exc).__name__,
                        error=exc,
                        status_code=status_code,
                        cloudflare_error_code=_cloudflare_error_code(exc),
                        consecutive_failures=consecutive_failures,
                        retry_in_sec=delay,
                    )
            else:
                consecutive_failures = 0
            await asyncio.sleep(delay)

    @uses_workspace
    async def sync_once(self, settings: RuntimeSettings | None = None) -> bool:
        """Upload one archive or push changed library metadata."""
        settings = settings or self._settings_getter()
        if not (settings.remote_sync_enabled and settings.remote_sync_upload_results and settings.remote_server_url):
            return False
        async with self._lock:
            result = await self._sync_once(settings)
        return result.task_id is not None or result.library_pushed

    @uses_workspace
    async def push(self, task_ids: list[UUID] | None = None) -> dict[str, Any]:
        settings = self._settings_getter()
        summary = {"enabled": settings.remote_sync_enabled, "uploaded": 0, "library_pushed": False}
        if not settings.remote_sync_enabled:
            return summary
        if not settings.remote_server_url.strip():
            raise ValueError("remote_server_url is required")
        uploaded = []
        async with self._lock:
            if task_ids is not None:
                for task_id in dict.fromkeys(task_ids):
                    result = await self._sync_once(settings, task_id=task_id)
                    if result.task_id is not None:
                        uploaded.append(str(result.task_id))
            else:
                seen: set[UUID] = set()
                while True:
                    result = await self._sync_once(settings, exclude=seen)
                    if result.task_id is None and not result.library_pushed:
                        break
                    if result.task_id is not None:
                        seen.add(result.task_id)
                        uploaded.append(str(result.task_id))
                    summary["library_pushed"] |= result.library_pushed
        return {**summary, "uploaded": len(uploaded), "task_ids": uploaded}

    async def _sync_once(
        self, settings: RuntimeSettings, *, task_id: UUID | None = None,
        exclude: set[UUID] | None = None,
    ) -> UploadResult:
        from app.services.remote_archive_pull import (
            RemoteStatus,
            _is_self_server,
            is_self_workspace,
        )

        server_url = _normalized_server_url(settings.remote_server_url)
        data_root = Path(settings.data_root).resolve()
        store = self._store_getter()
        pending = self._pending_archive(store, data_root, server_url, task_id, exclude or set())
        if pending:
            await self._check_server_health(server_url)
        elif task_id is not None or await run_in_thread(_library_revision) == 0:
            return UploadResult()

        async with self._client_factory(
            headers=self._headers(settings),
            timeout=httpx.Timeout(60, connect=15),
            follow_redirects=False,
            **httpx_client_kwargs(server_url),
        ) as client:
            response = await client.get(f"{server_url}/api/sync/status")
            workspace = None
            if response.status_code not in {404, 405}:
                response.raise_for_status()
                status = RemoteStatus.model_validate(response.json())
                if await is_self_workspace(server_url, status, data_root):
                    return UploadResult()
                workspace = status.workspace_id
            elif await run_in_thread(_is_self_server, server_url, str(data_root), data_root):
                return UploadResult()
            if pending:
                from app.core.media_retention import uploading_media

                task, output_dir = pending
                try:
                    with uploading_media(output_dir):
                        await self._upload(task, output_dir, server_url, settings, store, workspace=workspace)
                except Exception:
                    if workspace is not None and task_id is None:
                        with suppress(Exception):
                            client.headers["X-MPP-Workspace-ID"] = quote(workspace, safe="")
                            async with library_sync_lock:
                                await self._push_library(client, server_url, workspace)
                    raise
                return UploadResult(task_id=task.id)
            if workspace is not None:
                client.headers["X-MPP-Workspace-ID"] = quote(workspace, safe="")
                async with library_sync_lock:
                    pushed = await self._push_library(client, server_url, workspace)
                    return UploadResult(library_pushed=pushed)
        return UploadResult()

    def _pending_archive(self, store, root, server, task_id, exclude) -> tuple[Task, Path] | None:
        if task_id is not None:
            task = store.get(task_id)
            if (
                task
                and task.status == TaskStatus.COMPLETED
                and not task.options.get("_remote_pull")
            ):
                if not task.options.get("_remote_origin") and (
                    path := self._archive_dir(task, root)
                ):
                    return task, path
            return None
        page_size = 500
        offset = 0
        while True:
            page = store.list(status=TaskStatus.COMPLETED, limit=page_size, offset=offset)
            for task in page:
                if (
                    task.id in exclude
                    or task.options.get("_remote_origin")
                    or task.options.get("_remote_pull")
                ):
                    continue
                marker = (task.result or {}).get("remote_sync") or {}
                if marker.get("worker_id") and not marker.get("server_url"):
                    continue
                if not self._already_uploaded(task, server) and (
                    path := self._archive_dir(task, root)
                ):
                    return task, path
            if len(page) < page_size:
                return None
            offset += len(page)

    @staticmethod
    def _headers(settings: RuntimeSettings) -> dict[str, str]:
        headers = {
            "User-Agent": "MPP-Remote-Archive-Upload/1",
            "X-Requested-With": "MPP-Remote-Sync",
        }
        if settings.remote_api_token:
            headers["Authorization"] = f"Bearer {settings.remote_api_token}"
        return headers

    @staticmethod
    async def _push_library(client: httpx.AsyncClient, server: str, workspace: str) -> bool:
        snapshot = await run_in_thread(_library_for_push, server, workspace)
        if snapshot is None:
            return False
        pushed_revision, payload, submitted_ids = snapshot
        folder_ids = {}
        if any(payload.values()):
            response = await client.post(f"{server}/api/sync/library", json=payload)
            if response.status_code in {404, 405}:
                logger.info("Remote library upload is unavailable at %s", server)
                return False
            response.raise_for_status()
            outcome = response.json()
            mapping = outcome.get("folder_id_map")
            if (outcome.get("ok") is not True or not isinstance(mapping, dict)
                    or mapping.keys() != submitted_ids.keys()
                    or any(not isinstance(remote_id, str) or not remote_id for remote_id in mapping.values())):
                raise ValueError("remote server returned an invalid library mapping")
            folder_ids = {submitted_ids[source_id]: remote_id for source_id, remote_id in mapping.items()}
        await run_in_thread(_acknowledge_library, server, workspace, pushed_revision, folder_ids)
        return any(payload.values())

    @staticmethod
    async def _check_server_health(server_url: str) -> None:
        """Check the public endpoint before building a potentially large archive."""
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=10.0),
            follow_redirects=False,
            **httpx_client_kwargs(server_url),
        ) as client:
            response = await client.get(f"{server_url}/health")
        response.raise_for_status()

    @staticmethod
    def _already_uploaded(task: Task, server_url: str) -> bool:
        result = task.result if isinstance(task.result, dict) else {}
        marker = result.get("remote_sync")
        return bool(
            isinstance(marker, dict)
            and str(marker.get("server_url") or "").rstrip("/") == server_url
            and marker.get("archive_sha256")
        )

    @staticmethod
    def _archive_dir(task: Task, data_root: Path) -> Path | None:
        result = task.result if isinstance(task.result, dict) else {}
        raw_path = result.get("output_dir")
        if not raw_path and isinstance(result.get("archive"), dict):
            raw_path = result["archive"].get("output_dir")
        if not raw_path:
            return None
        try:
            output_dir = Path(str(raw_path)).resolve()
            output_dir.relative_to(data_root)
        except (OSError, ValueError):
            return None
        if not output_dir.is_dir() or not (output_dir / "metadata.json").is_file():
            return None
        return output_dir

    async def _upload(
        self,
        task: Task,
        output_dir: Path,
        server_url: str,
        settings: RuntimeSettings,
        store: TaskStore,
        *,
        workspace: str | None = None,
    ) -> None:
        staging_dir = (
            get_workspace_paths(settings.data_root).temporary("remote_sync_client")
            / f"upload-{task.id}-{uuid4().hex}"
        )
        zip_path = staging_dir / "archive.zip"
        staging_dir.mkdir(parents=True, exist_ok=False)
        try:
            await run_in_thread(
                build_archive_zip,
                output_dir,
                zip_path,
                include_media=bool(settings.remote_sync_include_media),
            )
            archive_sha256 = await run_in_thread(_sha256, zip_path)
            task_payload = task.model_dump(mode="json")
            task_payload["result"] = _portable_result(task.result)
            from app.core.remote_execution import worker_identity
            worker_id = worker_identity(settings)
            headers = self._headers(settings)
            if workspace is not None:
                headers["X-MPP-Workspace-ID"] = quote(workspace, safe="")
            async with self._client_factory(
                headers=headers,
                timeout=httpx.Timeout(3600.0, connect=30.0),
                follow_redirects=False,
                **httpx_client_kwargs(server_url),
            ) as client:
                with zip_path.open("rb") as archive:
                    response = await client.post(
                        f"{server_url}/api/sync/import",
                        data={
                            "task_json": json.dumps(task_payload, ensure_ascii=False),
                            "archive_name": output_dir.name,
                            "archive_sha256": archive_sha256,
                            "worker_id": worker_id,
                            "execution_id": (task.options.get("_remote_origin") or {}).get("attempt", ""),
                        },
                        files={"archive": ("archive.zip", archive, "application/zip")},
                    )
            response.raise_for_status()
            payload = response.json()
            sync_info = payload.get("sync") if isinstance(payload, dict) else None
            if not isinstance(sync_info, dict):
                raise ValueError("remote server returned an invalid sync response")
            current = store.get(task.id)
            if (
                current is None
                or current.status != TaskStatus.COMPLETED
                or current.updated_at != task.updated_at
            ):
                return
            result = dict(current.result or {})
            result["remote_sync"] = {
                **sync_info,
                "server_url": server_url,
                "worker_id": worker_id,
            }
            store.update_status(
                task.id,
                TaskStatus.COMPLETED,
                result=result,
                completed_at=task.completed_at,
                progress=1.0,
                message="处理完成，已同步到远程服务器",
            )
            log_event(
                logger,
                logging.INFO,
                "remote_archive_upload.completed",
                task_id=task.id,
                server_url=server_url,
            )
        finally:
            await run_in_thread(shutil.rmtree, staging_dir, True)


_service: RemoteArchiveUploadService | None = None


def get_remote_archive_upload_service() -> RemoteArchiveUploadService:
    global _service
    if _service is None:
        _service = RemoteArchiveUploadService()
    return _service
