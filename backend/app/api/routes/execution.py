"""Authenticated worker polling and transfer of managed uploaded sources."""

from uuid import UUID
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core import remote_execution as execution
from app.core.database import get_task_store
from app.core.events import TaskEvent, get_event_bus

router = APIRouter(prefix="/execution", tags=["execution"])


class Poll(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    state: dict | None = None
    capacity: bool = False


@router.post("/poll")
async def poll(request: Poll):
    try:
        result = execution.poll(request.worker_id, request.state, request.capacity)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if request.state:
        kind = result.get("command", "step")
        await get_event_bus().publish(TaskEvent(request.state["id"],
            kind if kind in {"failed", "cancelled", "paused", "completed"} else "step", request.state))
    return result


@router.get("/tasks/{task_id}/source")
async def source(task_id: UUID, worker_id: str, attempt: str):
    task = get_task_store().get(task_id)
    execution.require_owner(task, worker_id, attempt)
    path = execution.source_file(task)
    if path is None:
        raise HTTPException(404, "Managed source file is unavailable")
    return FileResponse(path, filename=path.name)
