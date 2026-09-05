"""Background upload of completed local archives to a remote MPP daemon."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import shutil
import socket
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from app.core.workspace_lifecycle import run_in_thread, uses_workspace

from app.core.archive_sync import build_archive_zip
from app.core.database import TaskStore, get_task_store
from app.core.logging_setup import log_event
from app.core.network import httpx_client_kwargs
from app.core.settings import RuntimeSettings, get_runtime_settings
from app.models import Task, TaskStatus

logger = logging.getLogger(__name__)

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
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("remote_server_url must be a complete HTTP or HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("remote_server_url cannot contain credentials")
    return value[:-4] if value.endswith("/api") else value


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


class RemoteArchiveUploadService:
    """Upload one unsynchronized completed archive per background cycle."""

    def __init__(
        self,
        *,
        settings_getter: Callable[[], RuntimeSettings] = get_runtime_settings,
        store_getter: Callable[[], TaskStore] = get_task_store,
    ) -> None:
        self._settings_getter = settings_getter
        self._store_getter = store_getter
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
        while True:
            settings = self._settings_getter()
            interval = max(5.0, float(settings.remote_sync_interval_sec))
            if settings.remote_sync_enabled and settings.remote_sync_upload_results:
                try:
                    await self.sync_once(settings)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log_event(
                        logger,
                        logging.WARNING,
                        "remote_archive_upload.cycle_failed",
                        error_type=type(exc).__name__,
                        error=exc,
                    )
            await asyncio.sleep(interval)

    @uses_workspace
    async def sync_once(self, settings: RuntimeSettings | None = None) -> bool:
        """Upload the newest completed task that has no marker for this server."""
        settings = settings or self._settings_getter()
        server_url = _normalized_server_url(settings.remote_server_url)
        data_root = Path(settings.data_root).resolve()
        store = self._store_getter()
        for task in store.list(status=TaskStatus.COMPLETED, limit=10_000):
            output_dir = self._archive_dir(task, data_root)
            if output_dir is None or self._already_uploaded(task, server_url):
                continue
            await self._upload(task, output_dir, server_url, settings, store)
            return True
        return False

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
    ) -> None:
        staging_dir = (
            Path(settings.data_root).resolve()
            / "_remote_sync_client"
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
            worker_id = (
                str(settings.remote_worker_id or "").strip()
                or f"desktop-{socket.gethostname().strip() or 'mpp'}"
            )
            headers = {
                "User-Agent": "MPP-Remote-Archive-Upload/1",
                "X-Requested-With": "MPP-Remote-Sync",
            }
            if settings.remote_api_token:
                headers["Authorization"] = f"Bearer {settings.remote_api_token}"
            async with httpx.AsyncClient(
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
