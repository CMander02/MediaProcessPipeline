"""Async task queue with single worker.

GPU is the bottleneck — only one ASR model fits in VRAM at a time,
so tasks execute sequentially via a single worker consuming an asyncio.Queue.
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
    """Single-worker async task queue backed by asyncio.Queue."""

    def __init__(self):
        self._queue: asyncio.Queue[UUID] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._pipeline_fn: Callable[[UUID], Coroutine[Any, Any, None]] | None = None
        self._current_task_id: UUID | None = None
        self._running = False

    @property
    def current_task_id(self) -> UUID | None:
        """The task currently being processed, or None."""
        return self._current_task_id

    @property
    def pending_count(self) -> int:
        """Number of tasks waiting in the queue."""
        return self._queue.qsize()

    def set_pipeline(self, fn: Callable[[UUID], Coroutine[Any, Any, None]]) -> None:
        """Register the pipeline function that processes a single task."""
        self._pipeline_fn = fn

    async def submit(self, task_id: UUID) -> None:
        """Add a task to the queue."""
        store = get_task_store()
        bus = get_event_bus()

        store.update_status(task_id, TaskStatus.QUEUED)
        await bus.publish(TaskEvent(task_id, "queued"))
        await self._queue.put(task_id)
        logger.info(f"Task {task_id} queued (queue depth: {self._queue.qsize()})")

    async def cancel(self, task_id: UUID) -> bool:
        """Cancel a queued (not yet running) task. Returns True if found and cancelled."""
        store = get_task_store()
        task = store.get(task_id)
        if not task:
            return False
        if task.status not in (TaskStatus.PENDING, TaskStatus.QUEUED):
            return False

        store.update_status(
            task_id,
            TaskStatus.CANCELLED,
            completed_at=datetime.now(),
        )
        bus = get_event_bus()
        await bus.publish(TaskEvent(task_id, "cancelled"))
        logger.info(f"Task {task_id} cancelled")
        return True

    async def start(self) -> None:
        """Start the worker and restore queued tasks from DB."""
        if self._running:
            return
        self._running = True

        # Restore tasks that were QUEUED when server last shut down
        store = get_task_store()
        stale = store.list_by_statuses([TaskStatus.QUEUED, TaskStatus.PROCESSING])
        for task in stale:
            if task.status == TaskStatus.PROCESSING:
                # Was mid-flight when server died — re-queue it
                store.update_status(task.id, TaskStatus.QUEUED, message="已重新排队")
            await self._queue.put(task.id)
            logger.info(f"Restored task {task.id} to queue")

        self._worker_task = asyncio.create_task(self._worker(), name="task-queue-worker")
        logger.info("Task queue worker started")

    async def stop(self) -> None:
        """Gracefully stop the worker."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        logger.info("Task queue worker stopped")

    async def _worker(self) -> None:
        """Worker loop — pull tasks and execute one at a time."""
        while self._running:
            try:
                task_id = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            store = get_task_store()
            task = store.get(task_id)

            # Skip cancelled tasks
            if not task or task.status == TaskStatus.CANCELLED:
                self._queue.task_done()
                continue

            self._current_task_id = task_id
            logger.info(f"Worker processing task {task_id}")

            try:
                if self._pipeline_fn:
                    await self._pipeline_fn(task_id)
            except Exception:
                logger.exception(f"Unhandled error in pipeline for task {task_id}")
            finally:
                self._current_task_id = None
                self._queue.task_done()

    def get_queue_snapshot(self) -> list[UUID]:
        """Return a snapshot of queued task IDs (for display)."""
        # asyncio.Queue doesn't expose items, but we can snapshot via internal _queue
        return list(self._queue._queue)  # type: ignore[attr-defined]


# Singleton
_task_queue: TaskQueue | None = None


def get_task_queue() -> TaskQueue:
    """Get the global TaskQueue singleton."""
    global _task_queue
    if _task_queue is None:
        _task_queue = TaskQueue()
    return _task_queue
