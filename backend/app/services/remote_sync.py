"""Desktop EXE client for coordinator-backed task and archive synchronization."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import socket
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlparse
from uuid import UUID, uuid4

import httpx

from app.core.archive_sync import (
    MAX_PORTABLE_RESULT_JSON_BYTES,
    MAX_UPLOAD_BYTES,
    archive_file_manifest,
    build_archive_zip,
    publish_archive,
    rewrite_archive_image_paths,
    rewrite_result_image_paths,
    safe_extract_zip,
    sanitize_portable_result,
)
from app.core.database import TaskStore, get_task_store
from app.core.logging_setup import log_event
from app.core.network import httpx_client_kwargs
from app.core.queue import TaskQueue, get_task_queue
from app.core.settings import (
    RuntimeSettings,
    get_runtime_settings,
    patch_runtime_settings,
)
from app.models import Task, TaskCreate, TaskStatus, TaskType
from app.models.task import (
    PREFERRED_WORKER_OPTION,
    REMOTE_ARCHIVE_REVISION_OPTION,
    REMOTE_MIRROR_OPTION,
)

logger = logging.getLogger(__name__)

_LEASE_SECONDS = 900
_MIRROR_LIMIT = 200
_TERMINAL_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
}


class RemoteSyncConfigurationError(ValueError):
    """Raised when desktop coordinator settings are incomplete or unsafe."""


class RemoteSyncUnavailableError(RuntimeError):
    """Raised when a foreground task cannot reach the configured coordinator."""


class _LeaseLostError(RuntimeError):
    pass


@dataclass(frozen=True)
class _RemoteConfig:
    api_base: str
    api_token: str = field(repr=False)
    worker_id: str
    worker_name: str
    interval_sec: float
    upload_results: bool
    download_results: bool
    include_media: bool

    @classmethod
    def from_settings(cls, settings: RuntimeSettings) -> _RemoteConfig:
        raw_url = str(settings.remote_server_url or "").strip()
        parsed = urlparse(raw_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RemoteSyncConfigurationError(
                "远程服务器地址必须是完整的 HTTP 或 HTTPS 地址"
            )
        if parsed.username or parsed.password:
            raise RemoteSyncConfigurationError("远程服务器地址不能包含用户名或密码")

        base = raw_url.rstrip("/")
        api_base = base if base.endswith("/api") else f"{base}/api"
        return cls(
            api_base=f"{api_base}/",
            api_token=str(settings.remote_api_token or ""),
            worker_id=str(settings.remote_worker_id or "").strip(),
            worker_name=(
                str(settings.remote_worker_name or "").strip()
                or socket.gethostname().strip()
                or "MPP EXE"
            ),
            interval_sec=max(5.0, float(settings.remote_sync_interval_sec)),
            upload_results=bool(settings.remote_sync_upload_results),
            download_results=bool(settings.remote_sync_download_results),
            include_media=bool(settings.remote_sync_include_media),
        )

    @property
    def registration_key(self) -> tuple[str, str, str]:
        return self.api_base, self.worker_id, self.worker_name


def _is_http_source(source: str) -> bool:
    return urlparse(str(source or "").strip()).scheme.lower() in {"http", "https"}


def _parse_lease_expiry(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _safe_remote_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
        return payload["detail"][:500]
    return f"HTTP {response.status_code}"


def _safe_remote_input_name(raw_name: str, task_id: UUID) -> str:
    name = Path(str(raw_name or "")).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:200] or f"{task_id}.media"


class RemoteSyncService:
    """Coordinates one desktop daemon with the canonical server."""

    def __init__(
        self,
        *,
        settings_getter: Callable[[], RuntimeSettings] = get_runtime_settings,
        store_getter: Callable[[], TaskStore] = get_task_store,
        queue_getter: Callable[[], TaskQueue] = get_task_queue,
    ) -> None:
        self._settings_getter = settings_getter
        self._store_getter = store_getter
        self._queue_getter = queue_getter
        self._runner: asyncio.Task[None] | None = None
        self._registered_key: tuple[str, str, str] | None = None
        self._registered_worker_id = ""
        self._active_task_id: UUID | None = None

    @property
    def active_task_id(self) -> UUID | None:
        return self._active_task_id

    async def start(self) -> None:
        if self._runner and not self._runner.done():
            return
        self._runner = asyncio.create_task(self._run(), name="remote-sync")

    async def stop(self) -> None:
        runner = self._runner
        self._runner = None
        if not runner:
            return
        runner.cancel()
        with suppress(asyncio.CancelledError):
            await runner

    def _make_client(self, config: _RemoteConfig) -> httpx.AsyncClient:
        headers = {
            "User-Agent": "MPP-EXE-Remote-Sync/1",
            "X-Requested-With": "MPP-Remote-Sync",
        }
        if config.api_token:
            headers["Authorization"] = f"Bearer {config.api_token}"
        return httpx.AsyncClient(
            base_url=config.api_base,
            headers=headers,
            timeout=httpx.Timeout(60.0, connect=15.0),
            follow_redirects=False,
            **httpx_client_kwargs(config.api_base),
        )

    async def _wait_or_stop(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def _run(self) -> None:
        while True:
            settings = self._settings_getter()
            if not settings.remote_sync_enabled:
                self._registered_key = None
                self._registered_worker_id = ""
                await self._wait_or_stop(2.0)
                continue

            try:
                config = _RemoteConfig.from_settings(settings)
                async with self._make_client(config) as client:
                    worker_id = await self._ensure_registered(client, config)
                    await self._heartbeat(client, worker_id)
                    if config.upload_results:
                        claim = await self._claim(client, worker_id)
                        if claim:
                            await self._process_claim(client, config, worker_id, claim)
                    await self._mirror_remote_tasks(client, config)
            except asyncio.CancelledError:
                raise
            except RemoteSyncConfigurationError as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "remote_sync.configuration_invalid",
                    error=exc,
                )
            except httpx.HTTPStatusError as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "remote_sync.request_failed",
                    status_code=exc.response.status_code,
                )
                if exc.response.status_code == 404:
                    self._registered_key = None
                    self._registered_worker_id = ""
            except httpx.RequestError as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "remote_sync.connection_failed",
                    error_type=type(exc).__name__,
                )
            except Exception as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    "remote_sync.cycle_failed",
                    error_type=type(exc).__name__,
                    exc_info=True,
                )

            interval = max(
                5.0,
                float(self._settings_getter().remote_sync_interval_sec),
            )
            await self._wait_or_stop(interval)

    async def _ensure_registered(
        self,
        client: httpx.AsyncClient,
        config: _RemoteConfig,
    ) -> str:
        if self._registered_key == config.registration_key and self._registered_worker_id:
            return self._registered_worker_id

        response = await client.post(
            "workers/register",
            json={
                "worker_id": config.worker_id or None,
                "name": config.worker_name,
                "capabilities": {
                    "executor": "exe",
                    "task_types": ["pipeline"],
                    "portable_archives": True,
                    "upload_results": config.upload_results,
                    "download_results": config.download_results,
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
        worker = payload.get("worker") if isinstance(payload, dict) else None
        worker_id = str(worker.get("id") if isinstance(worker, dict) else "").strip()
        if not worker_id:
            raise RemoteSyncUnavailableError("协调服务器返回了无效的 worker_id")

        if not config.worker_id:
            patch_runtime_settings({"remote_worker_id": worker_id})
        self._registered_worker_id = worker_id
        self._registered_key = (
            config.api_base,
            worker_id,
            config.worker_name,
        )
        log_event(logger, logging.INFO, "remote_sync.worker_registered", worker_id=worker_id)
        return worker_id

    async def _heartbeat(self, client: httpx.AsyncClient, worker_id: str) -> None:
        response = await client.post(
            f"workers/{quote(worker_id, safe='')}/heartbeat",
            json={"capabilities": None},
        )
        response.raise_for_status()

    async def _claim(
        self,
        client: httpx.AsyncClient,
        worker_id: str,
    ) -> dict[str, Any] | None:
        response = await client.post(
            f"workers/{quote(worker_id, safe='')}/claim",
            json={"lease_seconds": _LEASE_SECONDS},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("task") is None:
            return None
        return payload

    async def _process_claim(
        self,
        client: httpx.AsyncClient,
        config: _RemoteConfig,
        worker_id: str,
        claim: dict[str, Any],
    ) -> None:
        remote_task = Task.model_validate(claim.get("task"))
        lease_token = str(claim.get("lease_token") or "")
        if len(lease_token) < 16:
            raise RemoteSyncUnavailableError("协调服务器返回了无效的任务租约")
        if remote_task.task_type != TaskType.PIPELINE:
            await self._report_failure(
                client,
                config,
                worker_id,
                remote_task.id,
                lease_token,
                "EXE 远程处理仅支持 pipeline 任务",
            )
            return

        queue = self._queue_getter()
        self._active_task_id = remote_task.id
        lease_lost = asyncio.Event()
        lease_guard = asyncio.create_task(
            self._maintain_lease(
                client,
                config,
                worker_id,
                remote_task.id,
                lease_token,
                claim.get("lease_expires_at"),
                lease_lost,
            ),
            name=f"remote-lease-{remote_task.id}",
        )
        mirror_guard = asyncio.create_task(
            self._maintain_remote_mirror(client, config, lease_lost),
            name=f"remote-mirror-{remote_task.id}",
        )
        local_task: Task | None = None

        try:
            local_task = await self._materialize_claimed_source(
                client,
                config,
                worker_id,
                remote_task,
                claim.get("input"),
                lease_token,
                lease_lost,
            )
            store = self._store_getter()
            store.save_remote_mirror(local_task)
            await queue.submit_remote_claim(local_task.id, worker_id)
            completed = await self._wait_for_local_terminal(local_task.id, lease_lost)
            if completed is None:
                await queue.cancel(local_task.id)
                raise _LeaseLostError("Remote task lease expired")

            if completed.status == TaskStatus.COMPLETED:
                payload = await self._upload_completed_task(
                    client,
                    config,
                    worker_id,
                    completed,
                    lease_token,
                    lease_lost,
                )
                self._apply_remote_revision(completed, payload)
                await asyncio.to_thread(self._cleanup_staged_source, completed)
            else:
                error = completed.error or (
                    "EXE 本地任务已取消"
                    if completed.status == TaskStatus.CANCELLED
                    else "EXE 本地任务处理失败"
                )
                await self._report_failure(
                    client,
                    config,
                    worker_id,
                    completed.id,
                    lease_token,
                    error,
                    lease_lost=lease_lost,
                )
        except (OSError, ValueError, RemoteSyncUnavailableError) as exc:
            await self._report_failure(
                client,
                config,
                worker_id,
                remote_task.id,
                lease_token,
                f"EXE 无法获取远程任务输入: {exc}",
                lease_lost=lease_lost,
            )
        except _LeaseLostError:
            log_event(
                logger,
                logging.WARNING,
                "remote_sync.lease_lost",
                task_id=remote_task.id,
                worker_id=worker_id,
            )
        finally:
            for guard in (mirror_guard, lease_guard):
                guard.cancel()
            for guard in (mirror_guard, lease_guard):
                with suppress(asyncio.CancelledError):
                    await guard
            queue.release_remote_claim(remote_task.id, worker_id)
            self._active_task_id = None

    async def _materialize_claimed_source(
        self,
        client: httpx.AsyncClient,
        config: _RemoteConfig,
        worker_id: str,
        task: Task,
        input_info: Any,
        lease_token: str,
        lease_lost: asyncio.Event,
    ) -> Task:
        if _is_http_source(task.source):
            return self._claimed_task_for_local(task, worker_id)
        if isinstance(input_info, dict):
            local_source = await self._download_claimed_input(
                client,
                config,
                worker_id,
                task,
                input_info,
                lease_token,
                lease_lost,
            )
            materialized = task.model_copy(deep=True)
            materialized.source = str(local_source)
            return self._claimed_task_for_local(materialized, worker_id)
        if Path(task.source).is_file():
            return self._claimed_task_for_local(task, worker_id)
        raise RemoteSyncUnavailableError(
            "任务既没有可读取的 URL、本机文件，也没有服务器暂存输入"
        )

    async def _download_claimed_input(
        self,
        client: httpx.AsyncClient,
        config: _RemoteConfig,
        worker_id: str,
        task: Task,
        input_info: dict[str, Any],
        lease_token: str,
        lease_lost: asyncio.Event,
    ) -> Path:
        filename = _safe_remote_input_name(
            str(input_info.get("filename") or task.source),
            task.id,
        )
        try:
            expected_size = int(input_info.get("size"))
        except (TypeError, ValueError) as exc:
            raise RemoteSyncUnavailableError("协调服务器返回了无效的输入大小") from exc
        if expected_size < 0 or expected_size > MAX_UPLOAD_BYTES:
            raise RemoteSyncUnavailableError("远程任务输入超过 10 GB 下载上限")

        data_root = Path(self._settings_getter().data_root).resolve()
        staging_dir = (
            data_root / "_staging" / f"remote-{task.id}-{uuid4().hex}"
        )
        destination = staging_dir / filename
        partial = staging_dir / f".{filename}.{uuid4().hex}.part"
        staging_dir.mkdir(parents=True, exist_ok=False)
        try:
            while not lease_lost.is_set():
                try:
                    await self._stream_claimed_input_download(
                        client,
                        worker_id,
                        task.id,
                        lease_token,
                        partial,
                        expected_size,
                        lease_lost,
                    )
                    await asyncio.to_thread(os.replace, partial, destination)
                    return destination
                except httpx.RequestError:
                    partial.unlink(missing_ok=True)
                    try:
                        await asyncio.wait_for(
                            lease_lost.wait(),
                            timeout=config.interval_sec,
                        )
                    except asyncio.TimeoutError:
                        continue
            raise _LeaseLostError("Lease expired before task input download completed")
        except Exception:
            await asyncio.to_thread(shutil.rmtree, staging_dir, True)
            raise

    async def _stream_claimed_input_download(
        self,
        client: httpx.AsyncClient,
        worker_id: str,
        task_id: UUID,
        lease_token: str,
        destination: Path,
        expected_size: int,
        lease_lost: asyncio.Event,
    ) -> None:
        async with client.stream(
            "GET",
            f"workers/{quote(worker_id, safe='')}/tasks/{task_id}/input",
            headers={"X-MPP-Lease-Token": lease_token},
            timeout=httpx.Timeout(3600.0, connect=30.0),
        ) as response:
            if response.status_code == 409:
                lease_lost.set()
                raise _LeaseLostError("Lease rejected during task input download")
            if response.status_code >= 400:
                await response.aread()
                raise RemoteSyncUnavailableError(
                    f"协调服务器拒绝输入下载：{_safe_remote_error(response)}"
                )
            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    announced_size = int(content_length)
                except ValueError as exc:
                    raise RemoteSyncUnavailableError("远程输入长度无效") from exc
                if announced_size > MAX_UPLOAD_BYTES:
                    raise RemoteSyncUnavailableError("远程任务输入超过 10 GB 下载上限")
                if announced_size != expected_size:
                    raise RemoteSyncUnavailableError("远程任务输入大小已发生变化")

            output = await asyncio.to_thread(destination.open, "xb")
            written = 0
            try:
                async for chunk in response.aiter_bytes(8 * 1024 * 1024):
                    if lease_lost.is_set():
                        raise _LeaseLostError("Lease expired during task input download")
                    written += len(chunk)
                    if written > MAX_UPLOAD_BYTES or written > expected_size:
                        raise RemoteSyncUnavailableError(
                            "远程任务输入超过声明的下载上限"
                        )
                    await asyncio.to_thread(output.write, chunk)
            finally:
                await asyncio.to_thread(output.close)
            if written != expected_size:
                destination.unlink(missing_ok=True)
                raise RemoteSyncUnavailableError("远程任务输入下载不完整")

    def _claimed_task_for_local(self, task: Task, worker_id: str) -> Task:
        local = task.model_copy(deep=True)
        local.options = {
            **local.options,
            REMOTE_MIRROR_OPTION: True,
        }
        local.options.pop(PREFERRED_WORKER_OPTION, None)
        local.assigned_executor = worker_id
        local.result = None
        local.error = None
        local.completed_at = None
        local.progress = 0.0
        local.status = TaskStatus.QUEUED
        local.message = "已从协调服务器领取，等待本机处理..."
        local.completed_steps = []
        return local

    async def _maintain_lease(
        self,
        client: httpx.AsyncClient,
        config: _RemoteConfig,
        worker_id: str,
        task_id: UUID,
        lease_token: str,
        initial_expiry: Any,
        lease_lost: asyncio.Event,
    ) -> None:
        expiry = _parse_lease_expiry(initial_expiry)
        renew_every = min(300.0, max(30.0, _LEASE_SECONDS / 3))
        heartbeat_every = min(60.0, max(10.0, config.interval_sec))
        next_renew = time.monotonic() + renew_every

        while not lease_lost.is_set():
            try:
                await self._heartbeat(client, worker_id)
                if time.monotonic() >= next_renew:
                    response = await client.post(
                        (
                            f"workers/{quote(worker_id, safe='')}/tasks/"
                            f"{task_id}/lease/renew"
                        ),
                        json={
                            "lease_token": lease_token,
                            "lease_seconds": _LEASE_SECONDS,
                        },
                    )
                    if response.status_code == 409:
                        lease_lost.set()
                        return
                    response.raise_for_status()
                    payload = response.json()
                    expiry = _parse_lease_expiry(payload.get("lease_expires_at"))
                    next_renew = time.monotonic() + renew_every
            except httpx.RequestError:
                if datetime.now(timezone.utc) >= expiry:
                    lease_lost.set()
                    return
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {404, 409}:
                    lease_lost.set()
                    return
                if datetime.now(timezone.utc) >= expiry:
                    lease_lost.set()
                    return
            try:
                await asyncio.wait_for(
                    lease_lost.wait(),
                    timeout=heartbeat_every,
                )
            except asyncio.TimeoutError:
                pass

    async def _maintain_remote_mirror(
        self,
        client: httpx.AsyncClient,
        config: _RemoteConfig,
        lease_lost: asyncio.Event,
    ) -> None:
        while not lease_lost.is_set():
            try:
                await self._mirror_remote_tasks(client, config)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "remote_sync.active_task_mirror_failed",
                    error_type=type(exc).__name__,
                )
            try:
                await asyncio.wait_for(
                    lease_lost.wait(),
                    timeout=config.interval_sec,
                )
            except asyncio.TimeoutError:
                pass

    async def _wait_for_local_terminal(
        self,
        task_id: UUID,
        lease_lost: asyncio.Event,
    ) -> Task | None:
        while not lease_lost.is_set():
            task = self._store_getter().get(task_id)
            if task and task.status in _TERMINAL_STATUSES:
                return task
            await asyncio.sleep(1.0)
        return None

    async def _upload_completed_task(
        self,
        client: httpx.AsyncClient,
        config: _RemoteConfig,
        worker_id: str,
        task: Task,
        lease_token: str,
        lease_lost: asyncio.Event,
    ) -> dict[str, Any]:
        output_dir = self._local_archive_dir(task)
        if output_dir is None:
            await self._report_failure(
                client,
                config,
                worker_id,
                task.id,
                lease_token,
                "EXE 本地任务完成后没有生成可上传的归档目录",
                lease_lost=lease_lost,
            )
            return {}

        data_root = Path(self._settings_getter().data_root).resolve()
        staging_dir = data_root / "_remote_sync_client" / f"upload-{task.id}-{uuid4().hex}"
        zip_path = staging_dir / "archive.zip"
        staging_dir.mkdir(parents=True, exist_ok=False)
        try:
            portable_result = rewrite_result_image_paths(
                sanitize_portable_result(task.result),
                output_dir,
                relative=True,
            )
            result_json = json.dumps(
                portable_result,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if len(result_json.encode("utf-8")) > MAX_PORTABLE_RESULT_JSON_BYTES:
                raise ValueError("portable result metadata exceeds the 4 MB limit")
            await asyncio.to_thread(
                build_archive_zip,
                output_dir,
                zip_path,
                include_media=config.include_media,
            )
            while not lease_lost.is_set():
                try:
                    with zip_path.open("rb") as archive:
                        response = await client.post(
                            (
                                f"workers/{quote(worker_id, safe='')}/tasks/"
                                f"{task.id}/archive"
                            ),
                            data={
                                "lease_token": lease_token,
                                "result_json": result_json,
                            },
                            files={"archive": ("archive.zip", archive, "application/zip")},
                            timeout=httpx.Timeout(3600.0, connect=30.0),
                        )
                    if response.status_code == 409:
                        lease_lost.set()
                        raise _LeaseLostError("Lease rejected during result upload")
                    response.raise_for_status()
                    payload = response.json()
                    log_event(
                        logger,
                        logging.INFO,
                        "remote_sync.archive_uploaded",
                        task_id=task.id,
                        worker_id=worker_id,
                    )
                    return payload if isinstance(payload, dict) else {}
                except httpx.RequestError:
                    await asyncio.sleep(config.interval_sec)
            raise _LeaseLostError("Lease expired before result upload completed")
        except (OSError, ValueError) as exc:
            await self._report_failure(
                client,
                config,
                worker_id,
                task.id,
                lease_token,
                f"EXE 归档打包失败: {exc}",
                lease_lost=lease_lost,
            )
            return {}
        finally:
            await asyncio.to_thread(shutil.rmtree, staging_dir, True)

    async def _report_failure(
        self,
        client: httpx.AsyncClient,
        config: _RemoteConfig,
        worker_id: str,
        task_id: UUID,
        lease_token: str,
        error: str,
        *,
        lease_lost: asyncio.Event | None = None,
    ) -> None:
        message = str(error or "EXE 处理失败")[:20_000]
        while lease_lost is None or not lease_lost.is_set():
            try:
                response = await client.post(
                    f"workers/{quote(worker_id, safe='')}/tasks/{task_id}/fail",
                    json={"lease_token": lease_token, "error": message},
                )
                if response.status_code == 409:
                    if lease_lost is not None:
                        lease_lost.set()
                    raise _LeaseLostError("Lease rejected while reporting failure")
                response.raise_for_status()
                return
            except httpx.RequestError:
                if lease_lost is None:
                    raise
                await asyncio.sleep(config.interval_sec)
        raise _LeaseLostError("Lease expired before failure report completed")

    def _apply_remote_revision(
        self,
        local_task: Task,
        payload: dict[str, Any],
    ) -> None:
        remote_payload = payload.get("task") if isinstance(payload, dict) else None
        if not isinstance(remote_payload, dict):
            return
        try:
            revision = int(remote_payload.get("sync_revision") or 0)
        except (TypeError, ValueError):
            return
        store = self._store_getter()
        synced = store.get(local_task.id) or local_task.model_copy(deep=True)
        synced.status = TaskStatus.COMPLETED
        synced.message = "处理完成，已同步到服务器"
        synced.sync_revision = max(0, revision)
        synced.options = {
            **synced.options,
            REMOTE_MIRROR_OPTION: True,
            REMOTE_ARCHIVE_REVISION_OPTION: max(0, revision),
        }
        store.save_remote_mirror(synced)

    def _local_archive_dir(self, task: Task) -> Path | None:
        result = task.result if isinstance(task.result, dict) else {}
        raw = result.get("output_dir")
        if not raw:
            return None
        data_root = Path(self._settings_getter().data_root).resolve()
        try:
            output_dir = Path(str(raw)).resolve()
            output_dir.relative_to(data_root)
        except (OSError, ValueError):
            return None
        if not output_dir.is_dir() or not (output_dir / "metadata.json").is_file():
            return None
        return output_dir

    def _cleanup_staged_source(self, task: Task) -> None:
        if _is_http_source(task.source):
            return
        data_root = Path(self._settings_getter().data_root).resolve()
        staging_root = (data_root / "_staging").resolve()
        try:
            source = Path(task.source).resolve()
            source.relative_to(staging_root)
        except (OSError, ValueError):
            return
        try:
            source.unlink(missing_ok=True)
            source.parent.rmdir()
        except OSError:
            pass

    async def _mirror_remote_tasks(
        self,
        client: httpx.AsyncClient,
        config: _RemoteConfig,
    ) -> None:
        offset = 0
        while True:
            response = await client.get(
                "tasks",
                params={"limit": _MIRROR_LIMIT, "offset": offset},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise RemoteSyncUnavailableError("协调服务器返回了无效的任务列表")

            for item in reversed(payload):
                try:
                    remote_task = Task.model_validate(item)
                except Exception:
                    continue
                if remote_task.id == self._active_task_id:
                    continue
                try:
                    await self._mirror_remote_task(client, config, remote_task)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log_event(
                        logger,
                        logging.WARNING,
                        "remote_sync.task_mirror_failed",
                        task_id=remote_task.id,
                        error_type=type(exc).__name__,
                    )

            if len(payload) < _MIRROR_LIMIT:
                break
            offset += len(payload)

    async def _mirror_remote_task(
        self,
        client: httpx.AsyncClient,
        config: _RemoteConfig,
        remote_task: Task,
    ) -> None:
        store = self._store_getter()
        local_task = store.get(remote_task.id)
        if local_task and not local_task.options.get(REMOTE_MIRROR_OPTION):
            return

        local_archive = self._local_archive_dir(local_task) if local_task else None
        archive_revision = self._local_archive_revision(local_task)
        archive_is_current = (
            local_archive is not None
            and archive_revision >= remote_task.sync_revision
        )
        archive_refresh_required = (
            remote_task.status == TaskStatus.COMPLETED
            and config.download_results
            and not archive_is_current
        )
        if (
            local_task is not None
            and local_task.status == remote_task.status
            and local_task.sync_revision >= remote_task.sync_revision
            and not archive_refresh_required
        ):
            return

        files: dict[str, str] | None = None
        if archive_refresh_required:
            local_archive, files = await self._download_remote_archive(
                client,
                config,
                remote_task,
            )
            archive_revision = remote_task.sync_revision
        mirrored = self._task_for_local_mirror(
            remote_task,
            local_archive=local_archive,
            files=files,
            archive_revision=(
                archive_revision
                if local_archive is not None and archive_revision >= 0
                else None
            ),
            server_url=config.api_base.removesuffix("/api/"),
        )
        store.save_remote_mirror(mirrored)

    @staticmethod
    def _local_archive_revision(task: Task | None) -> int:
        if task is None:
            return -1
        raw = task.options.get(REMOTE_ARCHIVE_REVISION_OPTION)
        try:
            return max(-1, int(raw))
        except (TypeError, ValueError):
            return -1

    async def _download_remote_archive(
        self,
        client: httpx.AsyncClient,
        config: _RemoteConfig,
        task: Task,
    ) -> tuple[Path, dict[str, str]]:
        data_root = Path(self._settings_getter().data_root).resolve()
        staging_dir = data_root / "_remote_sync_client" / f"download-{task.id}-{uuid4().hex}"
        zip_path = staging_dir / "archive.zip"
        extraction_dir = staging_dir / "extracted"
        destination = data_root / f"remote-{task.id}"
        staging_dir.mkdir(parents=True, exist_ok=False)
        try:
            await self._stream_archive_download(
                client,
                task.id,
                zip_path,
                include_media=config.include_media,
            )
            extracted_root = await asyncio.to_thread(
                safe_extract_zip,
                zip_path,
                extraction_dir,
            )
            await asyncio.to_thread(
                publish_archive,
                extracted_root,
                destination,
                data_root,
            )
            await asyncio.to_thread(rewrite_archive_image_paths, destination)
            files = await asyncio.to_thread(archive_file_manifest, destination)
            log_event(
                logger,
                logging.INFO,
                "remote_sync.archive_downloaded",
                task_id=task.id,
            )
            return destination, files
        finally:
            await asyncio.to_thread(shutil.rmtree, staging_dir, True)

    async def _stream_archive_download(
        self,
        client: httpx.AsyncClient,
        task_id: UUID,
        destination: Path,
        *,
        include_media: bool,
    ) -> None:
        async with client.stream(
            "GET",
            f"sync/tasks/{task_id}/archive",
            params={"include_media": str(include_media).lower()},
            timeout=httpx.Timeout(3600.0, connect=30.0),
        ) as response:
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > MAX_UPLOAD_BYTES:
                        raise RemoteSyncUnavailableError("远程归档超过 10 GB 下载上限")
                except ValueError:
                    pass

            output = await asyncio.to_thread(destination.open, "wb")
            written = 0
            try:
                async for chunk in response.aiter_bytes(8 * 1024 * 1024):
                    written += len(chunk)
                    if written > MAX_UPLOAD_BYTES:
                        raise RemoteSyncUnavailableError("远程归档超过 10 GB 下载上限")
                    await asyncio.to_thread(output.write, chunk)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
            finally:
                await asyncio.to_thread(output.close)

    def _task_for_local_mirror(
        self,
        task: Task,
        *,
        local_archive: Path | None,
        files: dict[str, str] | None,
        archive_revision: int | None = None,
        server_url: str,
    ) -> Task:
        mirrored = task.model_copy(deep=True)
        mirrored.options = {
            **mirrored.options,
            REMOTE_MIRROR_OPTION: True,
        }
        if archive_revision is not None:
            mirrored.options[REMOTE_ARCHIVE_REVISION_OPTION] = archive_revision
        remote_result = dict(mirrored.result or {})
        remote_output_dir = remote_result.pop("output_dir", None)
        archive = remote_result.get("archive")
        if isinstance(archive, dict):
            archive = dict(archive)
            archive.pop("output_dir", None)
            archive.pop("files", None)
            if archive:
                remote_result["archive"] = archive
            else:
                remote_result.pop("archive", None)
        sync_info = (
            dict(remote_result.get("remote_sync") or {})
            if isinstance(remote_result.get("remote_sync"), dict)
            else {}
        )
        sync_info.update(
            {
                "mirror": True,
                "server_url": server_url,
                "remote_output_dir": remote_output_dir,
                "sync_revision": mirrored.sync_revision,
            }
        )
        remote_result["remote_sync"] = sync_info
        if local_archive is not None:
            local_files = files or archive_file_manifest(local_archive)
            remote_result["output_dir"] = str(local_archive)
            remote_result["archive"] = {
                "output_dir": str(local_archive),
                "files": local_files,
            }
            remote_result = rewrite_result_image_paths(
                remote_result,
                local_archive,
            )
        mirrored.result = remote_result
        return mirrored

    async def forward_task(self, task_create: TaskCreate) -> Task:
        """Create one URL task on the coordinator and retain a safe local mirror."""
        settings = self._settings_getter()
        if not settings.remote_sync_enabled:
            raise RemoteSyncConfigurationError("远程协调尚未启用")
        config = _RemoteConfig.from_settings(settings)
        requested_executor = (
            task_create.requested_executor
            if "requested_executor" in task_create.model_fields_set
            else settings.default_task_executor
        )
        if requested_executor == "exe" and not config.upload_results:
            raise RemoteSyncConfigurationError(
                "EXE 结果上传已关闭，无法创建由 EXE 处理的远程任务"
            )
        is_http_source = _is_http_source(task_create.source)
        if not is_http_source:
            if requested_executor != "exe":
                raise RemoteSyncConfigurationError(
                    "本地文件只能指派给 EXE；服务器无法访问 EXE 本地路径"
                )
            if task_create.source.startswith("upload://"):
                raise RemoteSyncConfigurationError(
                    "浏览器暂存文件缺少稳定路径，请重新选择本地文件"
                )
            source_path = Path(task_create.source)
            if not source_path.is_file():
                raise RemoteSyncConfigurationError("本地任务来源必须是当前 EXE 可读取的文件")

        payload = task_create.model_dump(mode="json")
        payload.update(
            {
                "origin_client": "exe",
                "requested_executor": requested_executor,
            }
        )

        try:
            async with self._make_client(config) as client:
                worker_id = await self._ensure_registered(client, config)
                if not is_http_source:
                    payload["options"] = {
                        **payload.get("options", {}),
                        PREFERRED_WORKER_OPTION: worker_id,
                    }
                response = await client.post("tasks", json=payload)
                if response.status_code >= 400:
                    raise RemoteSyncUnavailableError(
                        f"协调服务器拒绝了任务：{_safe_remote_error(response)}"
                    )
                remote_task = Task.model_validate(response.json())
        except httpx.RequestError as exc:
            raise RemoteSyncUnavailableError("无法连接远程协调服务器") from exc

        mirrored = self._task_for_local_mirror(
            remote_task,
            local_archive=None,
            files=None,
            archive_revision=None,
            server_url=config.api_base.removesuffix("/api/"),
        )
        self._store_getter().save_remote_mirror(mirrored)
        return mirrored


_remote_sync_service: RemoteSyncService | None = None


def get_remote_sync_service() -> RemoteSyncService:
    global _remote_sync_service
    if _remote_sync_service is None:
        _remote_sync_service = RemoteSyncService()
    return _remote_sync_service


def reset_remote_sync_service() -> None:
    global _remote_sync_service
    _remote_sync_service = None
