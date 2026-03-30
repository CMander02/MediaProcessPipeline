"""Async task queue — parallel downloads, serialised GPU.

New tasks enter a download queue consumed by N concurrent download workers
(I/O-bound, configurable via settings.max_download_concurrency, default 2).
After the DOWNLOAD step completes, each task is moved to the GPU queue,
which is consumed by a single GPU worker that runs UVR separation and ASR
transcription serially to avoid VRAM conflicts. LLM steps (analysis, polish,
summary) run freely inside the GPU worker after the GPU is released.

On backend restart, tasks that already completed DOWNLOAD are restored
directly into the GPU queue so they skip re-downloading.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Coroutine
from uuid import UUID

from app.core.database import get_task_store
from app.core.events import TaskEvent, get_event_bus
from app.models.task import TaskStatus

logger = logging.getLogger(__name__)


class TaskQueue:
    """Two-stage queue: parallel download → serialised GPU → free LLM."""

    def __init__(self):
        self._download_queue: asyncio.Queue[UUID] = asyncio.Queue()
        self._gpu_queue: asyncio.Queue[UUID] = asyncio.Queue()

        self._download_worker_tasks: list[asyncio.Task] = []
        self._gpu_worker_task: asyncio.Task | None = None

        # Called by the download worker to run just the DOWNLOAD step.
        # Called by the GPU worker to run SEPARATE → TRANSCRIBE → ANALYZE → POLISH → ARCHIVE.
        self._pipeline_fn: Callable[[UUID, bool], Coroutine[Any, Any, None]] | None = None

        self._active_download_ids: set[UUID] = set()
        self._active_gpu_id: UUID | None = None
        self._running = False

        # Exposed so pipeline.py can acquire it around GPU-heavy steps.
        # Always Semaphore(1) — one task on GPU at a time.
        self.gpu_semaphore = asyncio.Semaphore(1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def current_task_id(self) -> UUID | None:
        """Backwards-compat: return current GPU task (or any download task)."""
        return self._active_gpu_id or next(iter(self._active_download_ids), None)

    @property
    def active_task_ids(self) -> set[UUID]:
        ids = set(self._active_download_ids)
        if self._active_gpu_id:
            ids.add(self._active_gpu_id)
        return ids

    @property
    def pending_count(self) -> int:
        return self._download_queue.qsize() + self._gpu_queue.qsize()

    def set_pipeline(self, fn: Callable[[UUID, bool], Coroutine[Any, Any, None]]) -> None:
        self._pipeline_fn = fn

    async def submit(self, task_id: UUID) -> None:
        """Enqueue a new task — it goes straight to the download queue."""
        store = get_task_store()
        bus = get_event_bus()
        store.update_status(task_id, TaskStatus.QUEUED)
        await bus.publish(TaskEvent(task_id, "queued"))
        await self._download_queue.put(task_id)
        logger.info(f"Task {task_id} → download queue (depth: {self._download_queue.qsize()})")

    async def cancel(self, task_id: UUID) -> bool:
        store = get_task_store()
        task = store.get(task_id)
        if not task:
            return False
        if task.status not in (TaskStatus.PENDING, TaskStatus.QUEUED):
            return False
        store.update_status(task_id, TaskStatus.CANCELLED, completed_at=datetime.now())
        bus = get_event_bus()
        await bus.publish(TaskEvent(task_id, "cancelled"))
        logger.info(f"Task {task_id} cancelled")
        return True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        from app.core.settings import get_runtime_settings
        n_dl = get_runtime_settings().max_download_concurrency

        # Restore stale tasks from DB
        store = get_task_store()
        from app.models.task import TaskStatus as TS
        from app.core.pipeline import PipelineStep
        stale = store.list_by_statuses([TS.QUEUED, TS.PROCESSING])
        for task in stale:
            if task.status == TS.PROCESSING:
                store.update_status(task.id, TS.QUEUED, message="已重新排队")
            # If download was already done, skip straight to GPU queue
            completed = set(task.completed_steps or [])
            if PipelineStep.DOWNLOAD in completed:
                # Check if this was a fast-path task that already finished LLM steps.
                # If TRANSCRIBE+ANALYZE+POLISH are done but ARCHIVE isn't, it was a
                # fast-path task interrupted during video download — send to download
                # queue to re-download the video, not GPU queue.
                fast_path_done = {PipelineStep.TRANSCRIBE, PipelineStep.ANALYZE, PipelineStep.POLISH}
                if fast_path_done.issubset(completed) and PipelineStep.ARCHIVE not in completed:
                    await self._download_queue.put(task.id)
                    logger.info(f"Restored fast-path task {task.id} → download queue (video re-download)")
                else:
                    await self._gpu_queue.put(task.id)
                    logger.info(f"Restored task {task.id} → gpu queue (download already done)")
            else:
                await self._download_queue.put(task.id)
                logger.info(f"Restored task {task.id} → download queue")

        # Start download workers
        for i in range(n_dl):
            t = asyncio.create_task(
                self._download_worker(), name=f"dl-worker-{i}"
            )
            self._download_worker_tasks.append(t)

        # Start single GPU worker
        self._gpu_worker_task = asyncio.create_task(
            self._gpu_worker(), name="gpu-worker"
        )

        logger.info(f"TaskQueue started: {n_dl} download workers + 1 GPU worker")

    async def stop(self) -> None:
        self._running = False
        for t in self._download_worker_tasks:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._download_worker_tasks.clear()

        if self._gpu_worker_task:
            self._gpu_worker_task.cancel()
            try:
                await self._gpu_worker_task
            except asyncio.CancelledError:
                pass
            self._gpu_worker_task = None

        logger.info("TaskQueue stopped")

    # ------------------------------------------------------------------
    # Workers
    # ------------------------------------------------------------------

    async def _download_worker(self) -> None:
        """Pull tasks, run DOWNLOAD step, then hand off to GPU queue."""
        while self._running:
            try:
                task_id = await asyncio.wait_for(self._download_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            store = get_task_store()
            task = store.get(task_id)
            if not task or task.status == TaskStatus.CANCELLED:
                self._download_queue.task_done()
                continue

            self._active_download_ids.add(task_id)
            logger.info(f"Download worker: starting task {task_id}")

            try:
                if self._pipeline_fn:
                    await self._pipeline_fn(task_id, True)  # download-worker call
            except Exception:
                logger.exception(f"Download step failed for task {task_id}")
            finally:
                self._active_download_ids.discard(task_id)
                self._download_queue.task_done()

    async def _gpu_worker(self) -> None:
        """Pull downloaded tasks and run GPU + LLM steps sequentially."""
        while self._running:
            try:
                task_id = await asyncio.wait_for(self._gpu_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            store = get_task_store()
            task = store.get(task_id)
            if not task or task.status == TaskStatus.CANCELLED:
                self._gpu_queue.task_done()
                continue

            self._active_gpu_id = task_id
            logger.info(f"GPU worker: starting task {task_id}")

            try:
                if self._pipeline_fn:
                    await self._pipeline_fn(task_id, False)  # gpu-worker call
            except Exception:
                logger.exception(f"GPU/LLM steps failed for task {task_id}")
            finally:
                self._active_gpu_id = None
                self._gpu_queue.task_done()

    # ------------------------------------------------------------------
    # Called by pipeline to hand a task from download → GPU queue
    # ------------------------------------------------------------------

    async def advance_to_gpu(self, task_id: UUID) -> None:
        """Move a task from download stage to the GPU queue."""
        await self._gpu_queue.put(task_id)
        logger.info(f"Task {task_id} → gpu queue (depth: {self._gpu_queue.qsize()})")

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def get_queue_snapshot(self) -> list[UUID]:
        dl = list(self._download_queue._queue)   # type: ignore[attr-defined]
        gpu = list(self._gpu_queue._queue)        # type: ignore[attr-defined]
        return dl + gpu


# Singleton
_task_queue: TaskQueue | None = None


def get_task_queue() -> TaskQueue:
    global _task_queue
    if _task_queue is None:
        _task_queue = TaskQueue()
    return _task_queue
