"""Task management API routes.

Thin HTTP layer — all business logic lives in core.pipeline, core.queue,
core.database, and core.events.
"""

import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.core.database import get_task_store
from app.core.events import get_event_bus
from app.core.pipeline import PIPELINE_STEPS, PipelineStep, _detect_source_type
from app.core.queue import get_task_queue
from app.models import Task, TaskCreate, TaskStatus

router = APIRouter(prefix="/tasks", tags=["tasks"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task CRUD
# ---------------------------------------------------------------------------

@router.post("", response_model=Task)
async def create_task(task_create: TaskCreate):
    """Create a new processing task and submit it to the queue."""
    task = Task(
        task_type=task_create.task_type,
        source=task_create.source,
        options=task_create.options,
        webhook_url=task_create.webhook_url,
        status=TaskStatus.QUEUED,
        current_step=PipelineStep.DOWNLOAD,
        message="等待处理...",
        steps=[s["id"] for s in PIPELINE_STEPS],
        completed_steps=[],
    )

    store = get_task_store()
    store.save(task)

    queue = get_task_queue()
    await queue.submit(task.id)

    return task


@router.get("", response_model=list[Task])
async def list_tasks(status: TaskStatus | None = None, limit: int = 50):
    """List tasks with optional filtering."""
    store = get_task_store()
    return store.list(status=status, limit=limit)


@router.get("/stats")
async def get_stats():
    """Get task count statistics."""
    store = get_task_store()
    return store.stats()


@router.get("/{task_id}", response_model=Task)
async def get_task(task_id: UUID):
    """Get task by ID."""
    store = get_task_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: UUID):
    """Cancel a pending/queued task."""
    queue = get_task_queue()
    if await queue.cancel(task_id):
        return {"message": "Cancelled", "task_id": str(task_id)}

    store = get_task_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    raise HTTPException(400, f"Cannot cancel task in status: {task.status}")


@router.delete("/{task_id}")
async def delete_task(task_id: UUID):
    """Delete a task from the store."""
    store = get_task_store()
    if store.delete(task_id):
        return {"message": "Deleted", "task_id": str(task_id)}
    raise HTTPException(404, "Task not found")


# ---------------------------------------------------------------------------
# SSE endpoints
# ---------------------------------------------------------------------------

@router.get("/events")
async def stream_all_events():
    """SSE stream for ALL task events (global dashboard)."""
    bus = get_event_bus()
    q = bus.subscribe_global()

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield event.to_sse()
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            await bus.unsubscribe_global(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{task_id}/events")
async def stream_task_events(task_id: UUID):
    """SSE stream for a specific task's events."""
    store = get_task_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    bus = get_event_bus()
    q = bus.subscribe_task(task_id)

    async def event_generator():
        try:
            # Send current state as initial event
            yield f"data: {{\"task_id\": \"{task_id}\", \"type\": \"snapshot\", \"data\": {{\"status\": \"{task.status}\", \"progress\": {task.progress}, \"message\": \"{task.message or ''}\"}}}}\n\n"

            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield event.to_sse()
                    # Stop streaming after terminal states
                    if event.event_type in ("completed", "failed", "cancelled"):
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            await bus.unsubscribe_task(task_id, q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# History-compatible endpoints (kept for API backwards compat)
# ---------------------------------------------------------------------------

@router.get("/history/stats")
async def get_history_stats():
    """Get history statistics (tasks in terminal states)."""
    store = get_task_store()
    return store.stats()


@router.get("/history")
async def get_history(status: str | None = None, limit: int = 50, offset: int = 0):
    """Get task history (completed/failed/cancelled)."""
    store = get_task_store()
    # Only return terminal-state tasks for history view
    if status:
        tasks = store.list(status=status, limit=limit, offset=offset)
    else:
        # All terminal states
        completed = store.list(status="completed", limit=limit, offset=offset)
        failed = store.list(status="failed", limit=limit, offset=offset)
        cancelled = store.list(status="cancelled", limit=limit, offset=offset)
        tasks = sorted(
            completed + failed + cancelled,
            key=lambda t: t.created_at,
            reverse=True,
        )[:limit]
    return {
        "stats": store.stats(),
        "tasks": tasks,
    }


@router.delete("/history/{task_id}")
async def delete_history_entry(task_id: str):
    """Delete a history entry."""
    store = get_task_store()
    if store.delete(UUID(task_id)):
        return {"message": "Deleted", "task_id": task_id}
    raise HTTPException(404, "History entry not found")
