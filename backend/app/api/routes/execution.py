"""Authenticated worker polling and transfer of managed uploaded sources."""

from uuid import UUID
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core import remote_execution as execution
from app.core.database import get_task_store
from app.core.events import TaskEvent, get_event_bus
from app.core.security import constant_time_token_matches
from app.core.settings import get_runtime_settings
from app.core.workspace_lifecycle import run_in_thread
from app.models.task import WorkerTarget

router = APIRouter(prefix="/execution", tags=["execution"])


class WorkerState(BaseModel):
    id: UUID
    attempt: str
    status: Literal["queued", "processing", "completed", "failed", "cancelled", "paused"] = "processing"
    progress: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    current_step: str | None = None
    completed_steps: list[str] = Field(default_factory=list)
    message: str | None = None
    error: str | None = None


class Poll(BaseModel):
    worker_id: WorkerTarget
    worker_name: str = Field(default="", max_length=256)
    platform: Literal["", "windows", "macos", "linux", "unknown"] = ""
    compute_capabilities: dict[str, Any] | None = None
    state: WorkerState | None = None
    capacity: bool = False


@router.post("/poll")
async def poll(body: Poll, request: Request):
    settings = get_runtime_settings()
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if settings.api_token or not settings.remote_worker_id:
        if scheme.lower() != "bearer" or not constant_time_token_matches(token.strip(), settings.api_token):
            raise HTTPException(401, "Worker registration requires a valid server Bearer token")
    state = body.state.model_dump(mode="json") if body.state is not None else None
    try:
        result = execution.poll(body.worker_id, state, body.capacity, worker_name=body.worker_name,
                                platform=body.platform, compute_capabilities=body.compute_capabilities)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if state and result.get("command") != "cancel":
        kind = result.get("command", "step")
        await get_event_bus().publish(TaskEvent(state["id"],
            kind if kind in {"failed", "cancelled", "paused", "completed"} else "step", state))
    return result


@router.get("/nodes")
async def nodes():
    await run_in_thread(execution.host_capabilities)
    return execution.list_nodes()


@router.get("/tasks/{task_id}/source")
async def source(task_id: UUID, worker_id: str, attempt: str):
    task = get_task_store().get(task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    execution.require_owner(task, worker_id, attempt)
    path = execution.source_file(task)
    if path is None:
        raise HTTPException(404, "Managed source file is unavailable")
    return FileResponse(path, filename=path.name)
