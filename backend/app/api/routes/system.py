"""The 系统 view: GPU, loaded models, workers, disk and recent errors."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.system_status import system_status, worker_status
from app.core.workspace_lifecycle import run_in_thread

router = APIRouter(prefix="/system", tags=["system"])


@router.get("")
async def get_system_status() -> dict[str, Any]:
    # Workers are read here on the event loop, where the queue changes them.
    return await run_in_thread(system_status, worker_status())
