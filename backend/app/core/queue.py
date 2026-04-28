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
from app.core.logging_setup import (
    set_task_context, set_worker_context, reset_context,
    task_id_var, worker_var,
)
from app.models.task import TaskStatus

logger = logging.getLogger(__name__)


def _flush_gpu_models() -> None:
    """Release all GPU models to free VRAM. Runs in a thread (torch calls are blocking)."""
    import gc
    try:
        from app.services.preprocessing.uvr import get_uvr_service
        svc = get_uvr_service()
        if svc._separator is not None:
            logger.info("Releasing UVR model")
            svc._separator = None
            svc._current_model = None
            svc._current_model_dir = None
    except Exception as e:
        logger.warning(f"UVR release failed: {e}")
    try:
        from app.services.recognition.qwen3_asr import get_qwen3_service
        svc = get_qwen3_service()
        if svc._model is not None:
            logger.info("Releasing Qwen3-ASR model")
            svc._model = None
            svc._current_model_path = None
            svc._current_aligner_path = None
        if svc._diarize_pipeline is not None:
            logger.info("Releasing diarization pipeline")
            svc._diarize_pipeline = None
            svc._diarize_model = None
    except Exception as e:
        logger.warning(f"ASR/Diarization release failed: {e}")
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            logger.info("CUDA cache cleared")
    except Exception as e:
        logger.warning(f"CUDA cache clear failed: {e}")
    try:
        gc.collect()
        logger.info("GC collected")
    except Exception:
        pass


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
        t_token = set_task_context(str(task_id))
        try:
            await self._download_queue.put(task_id)
            logger.info(f"queued → download (depth={self._download_queue.qsize()})")
        finally:
            reset_context(t_token, task_id_var)

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
        t_token = set_task_context(str(task_id))
        try:
            logger.info("task cancelled")
        finally:
            reset_context(t_token, task_id_var)
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
                t_token = set_task_context(str(task.id))
                try:
                    if fast_path_done.issubset(completed) and PipelineStep.ARCHIVE not in completed:
                        await self._download_queue.put(task.id)
                        logger.info("restored (fast-path) → download queue for re-download")
                    else:
                        await self._gpu_queue.put(task.id)
                        logger.info("restored → gpu queue (download already done)")
                finally:
                    reset_context(t_token, task_id_var)
            else:
                t_token = set_task_context(str(task.id))
                try:
                    await self._download_queue.put(task.id)
                    logger.info("restored → download queue")
                finally:
                    reset_context(t_token, task_id_var)

        # Start download workers
        for i in range(n_dl):
            t = asyncio.create_task(
                self._download_worker(worker_index=i + 1), name=f"dl-worker-{i + 1}"
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
    # VRAM flush
    # ------------------------------------------------------------------

    async def _maybe_flush_all_models(self) -> None:
        """If queue is fully drained, release all GPU models to free VRAM."""
        if (
            self._download_queue.empty()
            and self._gpu_queue.empty()
            and not self._active_download_ids
            and self._active_gpu_id is None
        ):
            logger.info("Queue drained — releasing all GPU models")
            await asyncio.to_thread(_flush_gpu_models)

    # ------------------------------------------------------------------
    # Workers
    # ------------------------------------------------------------------

    async def _download_worker(self, worker_index: int = 0) -> None:
        """Pull tasks, run DOWNLOAD step, then hand off to GPU queue."""
        worker_name = f"dl-{worker_index}"
        w_token = set_worker_context(worker_name)
        try:
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
                t_token = set_task_context(str(task_id))
                logger.info("Download worker picked up task")

                try:
                    if self._pipeline_fn:
                        await self._pipeline_fn(task_id, True)  # download-worker call
                except Exception:
                    logger.exception("Download step failed")
                finally:
                    reset_context(t_token, task_id_var)
                    self._active_download_ids.discard(task_id)
                    self._download_queue.task_done()
                    await self._maybe_flush_all_models()
        finally:
            reset_context(w_token, worker_var)

    async def _gpu_worker(self) -> None:
        """Pull downloaded tasks and run GPU + LLM steps sequentially."""
        w_token = set_worker_context("gpu-1")
        try:
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

                # Serial mode: wait until all downloads are idle before using GPU
                from app.core.settings import get_runtime_settings
                if not get_runtime_settings().pipeline_overlap:
                    waited = 0
                    while self._active_download_ids and self._running:
                        if waited == 0:
                            logger.info("Serial mode: waiting for downloads to finish before GPU step")
                        await asyncio.sleep(0.5)
                        waited += 1

                self._active_gpu_id = task_id
                t_token = set_task_context(str(task_id))
                logger.info("GPU worker picked up task")

                try:
                    if self._pipeline_fn:
                        await self._pipeline_fn(task_id, False)  # gpu-worker call
                except Exception:
                    logger.exception("GPU/LLM steps failed")
                finally:
                    reset_context(t_token, task_id_var)
                    self._active_gpu_id = None
                    self._gpu_queue.task_done()
                    await self._maybe_flush_all_models()
        finally:
            reset_context(w_token, worker_var)

    # ------------------------------------------------------------------
    # Called by pipeline to hand a task from download → GPU queue
    # ------------------------------------------------------------------

    async def advance_to_gpu(self, task_id: UUID) -> None:
        """Move a task from download stage to the GPU queue."""
        await self._gpu_queue.put(task_id)
        logger.info(f"→ gpu queue (depth={self._gpu_queue.qsize()})")

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
