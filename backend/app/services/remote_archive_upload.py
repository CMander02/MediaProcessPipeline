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
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlparse, urlunparse
from uuid import uuid4

import httpx

from app.core.workspace_lifecycle import run_in_thread, uses_workspace

from app.core.archive_sync import build_archive_zip
from app.core.paths import get_workspace_paths
from app.core.database import TaskStore, _db_lock, _get_conn, get_task_store, task_write_transaction
from app.core.library import catalog, collection_metadata, revision
from app.core.library_sync import (
    folder_mappings, library_sync_lock, remember_folder, remote_origin, replica_folder_id,
)
from app.core.logging_setup import log_event
from app.core.network import httpx_client_kwargs
from app.core.settings import RuntimeSettings, get_runtime_settings
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
        local_to_remote = {local: remote for remote, local in folder_mappings(conn, server, workspace).items()}
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
        folders = [dict(row) for row in conn.execute("SELECT * FROM library_folders")
                   if row["id"] not in replicas or row["id"] in referenced]
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
        return current_revision, {
            "folders": folders, "members": members, "covers": covers, "collections": collections,
        }, submitted_ids


def _acknowledge_library(server: str, workspace: str, pushed_revision: int, folder_ids: dict[str, str]) -> None:
    with task_write_transaction() as conn:
        for local_id, remote_id in folder_ids.items():
            if conn.execute("SELECT 1 FROM library_folders WHERE id=?", (local_id,)).fetchone():
                remember_folder(conn, server, workspace, remote_id, local_id)
        conn.execute(
            "INSERT INTO remote_sync_cursors(server_url,workspace_id,pushed_library_revision) VALUES(?,?,?) "
            "ON CONFLICT(server_url,workspace_id) DO UPDATE SET "
            "pushed_library_revision=MAX(pushed_library_revision,excluded.pushed_library_revision)",
            (server, workspace, pushed_revision),
        )


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
        from app.services.remote_archive_pull import RemoteStatus, is_self_workspace

        settings = settings or self._settings_getter()
        if not (settings.remote_sync_enabled and settings.remote_sync_upload_results and settings.remote_server_url):
            return False
        server_url = _normalized_server_url(settings.remote_server_url)
        data_root = Path(settings.data_root).resolve()
        store = self._store_getter()
        pending = None
        for task in store.list(status=TaskStatus.COMPLETED, limit=10_000):
            if task.options.get("_remote_origin") or task.options.get("_remote_pull"):
                continue
            output_dir = self._archive_dir(task, data_root)
            if output_dir is not None and not self._already_uploaded(task, server_url):
                pending = (task, output_dir)
                break
        if pending:
            await self._check_server_health(server_url)
        elif await run_in_thread(_library_revision) == 0:
            return False

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
                    return False
                workspace = status.workspace_id
            if pending:
                from app.core.media_retention import uploading_media

                task, output_dir = pending
                with uploading_media(output_dir):
                    await self._upload(task, output_dir, server_url, settings, store, workspace=workspace)
                return True
            if workspace is not None:
                client.headers["X-MPP-Workspace-ID"] = quote(workspace, safe="")
                async with library_sync_lock:
                    return await self._push_library(client, server_url, workspace)
        return False

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
            result = dict(task.result or {})
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
