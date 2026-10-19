"""Desktop-initiated polling; the server never needs an inbound desktop port."""

import asyncio
import logging
from contextlib import suppress
from pathlib import Path
from uuid import UUID

import httpx

from app.core import remote_execution as execution
from app.core.database import get_task_store
from app.core.network import httpx_client_kwargs
from app.core.paths import get_workspace_paths
from app.core.queue import get_task_queue
from app.core.settings import get_runtime_settings
from app.core.workspace_lifecycle import uses_workspace
from app.models import Task, TaskStatus
from app.services.remote_archive_upload import RemoteArchiveUploadService, _normalized_server_url

logger = logging.getLogger(__name__)


class RemoteTaskWorker:
    def __init__(self):
        self.runner = None
        self.active = None

    async def start(self):
        if self.runner is None:
            execution.server_started()
            self.runner = asyncio.create_task(self.run(), name="remote-task-worker")

    async def stop(self):
        if self.runner is not None:
            self.runner.cancel()
            with suppress(asyncio.CancelledError):
                await self.runner
            self.runner = None

    async def run(self):
        while True:
            try:
                for task_id in execution.fallbacks():
                    await get_task_queue().submit(task_id)
                settings = get_runtime_settings()
                if settings.remote_execution_worker_enabled:
                    await self.tick(settings)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Remote task polling failed: %s", exc)
            await asyncio.sleep(10)

    @uses_workspace
    async def tick(self, settings):
        server = _normalized_server_url(settings.remote_server_url)
        store = get_task_store()
        queue = get_task_queue()
        if self.active is None:
            for candidate in store.list(limit=10000):
                origin = candidate.options.get(execution.ORIGIN, {})
                if (origin.get("server_url") == server and not origin.get("settled")
                        and not RemoteArchiveUploadService._already_uploaded(candidate, server)):
                    self.active = candidate.id
                    break
        headers = {"Authorization": f"Bearer {settings.remote_api_token}",
                   "User-Agent": "MPP-Task-Worker/1", "X-Requested-With": "MPP-Remote-Sync"}
        async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(60, connect=15),
                                     follow_redirects=False, **httpx_client_kwargs(server)) as client:
            task = store.get(self.active) if self.active else None
            state = None
            if task:
                origin = task.options[execution.ORIGIN]
                state = {key: value for key, value in task.model_dump(mode="json").items()
                         if key in {"id", "status", "progress", "current_step", "completed_steps", "message", "error"}}
                state["attempt"] = origin["attempt"]
            response = await client.post(server + "/api/execution/poll", json={
                "worker_id": settings.remote_worker_id, "state": state,
                "capacity": task is None and not queue.active_task_ids and not queue.pending_count})
            response.raise_for_status()
            payload = response.json()
            if task:
                command = payload.get("command")
                if command != "processing":
                    if task.status in {TaskStatus.QUEUED, TaskStatus.PROCESSING}:
                        if command == "paused":
                            await queue.pause(task.id)
                        else:
                            await queue.cancel(task.id)
                    task = store.get(task.id)
                    task.options[execution.ORIGIN]["settled"] = True
                    store.save(task)
                    self.active = None
                    return
                if (task.status in {TaskStatus.QUEUED, TaskStatus.PROCESSING}
                        and task.id not in queue.active_task_ids
                        and task.id not in queue.get_queue_snapshot()):
                    await queue.submit(task.id)
                if task.status == TaskStatus.COMPLETED:
                    uploader = RemoteArchiveUploadService()
                    if not uploader._already_uploaded(task, server):
                        output = uploader._archive_dir(task, Path(settings.data_root).resolve())
                        if output is None:
                            raise ValueError("Completed remote task has no archive")
                        # Cloud-origin media must remain playable on the cloud endpoint.
                        await self.upload_result(uploader, task, output, server, settings, store, client, state)
                    self.active = None
                return
            if payload.get("task"):
                assigned = Task.model_validate(payload["task"])
                await self.accept(assigned, server, client, settings)

    async def upload_result(self, uploader, task, output, server, settings, store, client, state):
        upload = asyncio.create_task(uploader._upload(task, output, server,
            settings.model_copy(update={"remote_sync_include_media": True}), store))
        try:
            while not upload.done():
                await asyncio.wait({upload}, timeout=10)
                if upload.done():
                    break
                response = await client.post(server + "/api/execution/poll", json={
                    "worker_id": settings.remote_worker_id, "capacity": False,
                    "state": {**state, "message": "正在回传归档"}})
                response.raise_for_status()
                if response.json().get("command") != "processing":
                    return
            await upload
        finally:
            if not upload.done():
                upload.cancel()
                with suppress(asyncio.CancelledError):
                    await upload

    async def accept(self, assigned, server, client, settings):
        store = get_task_store()
        marker = assigned.options[execution.KEY]
        existing = store.get(assigned.id)
        origin = {"server_url": server, "attempt": marker["attempt"]}
        if existing:
            previous = existing.options.get(execution.ORIGIN, {})
            if previous.get("server_url") != server:
                raise ValueError("Remote task conflicts with an existing local task")
            if previous.get("attempt") == marker["attempt"]:
                self.active = existing.id
                return
            if existing.status in {TaskStatus.QUEUED, TaskStatus.PROCESSING}:
                await get_task_queue().cancel(existing.id)
        source = assigned.source
        if not source.lower().startswith(("http://", "https://")):
            directory = get_workspace_paths(settings.data_root).temporary("remote_execution") / str(assigned.id)
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / ("source" + Path(source).suffix)
            part = target.with_suffix(target.suffix + ".part")
            try:
                async with client.stream("GET", server + f"/api/execution/tasks/{assigned.id}/source",
                    params={"worker_id": settings.remote_worker_id, "attempt": marker["attempt"]},
                    timeout=httpx.Timeout(3600, connect=15)) as response:
                    response.raise_for_status()
                    with part.open("wb") as output:
                        async for chunk in response.aiter_bytes():
                            output.write(chunk)
                part.replace(target)
            finally:
                part.unlink(missing_ok=True)
            source = str(target)
        options = {key: value for key, value in assigned.options.items()
                   if key not in {execution.KEY, execution.ORIGIN, "output_dir", "audio_path", "upload_dir"}}
        options[execution.ORIGIN] = origin
        local = Task(id=assigned.id, task_type=assigned.task_type, source=source,
            options=options, status=TaskStatus.QUEUED, created_at=assigned.created_at,
            steps=assigned.steps, platform=assigned.platform, content_subtype=assigned.content_subtype)
        from app.api.routes.tasks import _initialize_task
        _initialize_task(local, metadata={"title": Path(source).stem if "://" not in source else str(local.id),
            "archive_id": str(local.id), "content_key": options.get("content_key"),
            "source_url": assigned.source, "status": "queued", "platform": assigned.platform})
        store.save(local)
        self.active = local.id
        await get_task_queue().submit(local.id)


_service = None


def get_remote_task_worker():
    global _service
    if _service is None:
        _service = RemoteTaskWorker()
    return _service
