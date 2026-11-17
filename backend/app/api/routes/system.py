"""The 系统 view: GPU, loaded models, workers, disk and recent errors."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.security import is_local_request, request_is_authenticated
from app.core.settings import get_runtime_settings
from app.core.system_status import system_status, worker_status
from app.core.workspace_lifecycle import run_in_thread
from app.services.update_checker import UpdateError, get_update_checker

router = APIRouter(prefix="/system", tags=["system"])


@router.get("")
async def get_system_status() -> dict[str, Any]:
    # Workers are read here on the event loop, where the queue changes them.
    return await run_in_thread(system_status, worker_status())


class UpdateDownloadRequest(BaseModel):
    asset_name: str | None = None


@router.get("/update/check")
async def check_update(force: bool = False) -> dict[str, Any]:
    try:
        return await get_update_checker().check(force=force)
    except (UpdateError, OSError) as exc:
        raise HTTPException(
            exc.status_code if isinstance(exc, UpdateError) else 503, str(exc)
        ) from exc


@router.post("/update/download")
async def download_update(request: Request, body: UpdateDownloadRequest) -> dict[str, Any]:
    token = get_runtime_settings().api_token
    if not is_local_request(request) and not (token and request_is_authenticated(request, token)):
        raise HTTPException(403, "下载更新需要本机访问或管理员认证")
    try:
        return await get_update_checker().download(body.asset_name)
    except (UpdateError, OSError) as exc:
        raise HTTPException(
            exc.status_code if isinstance(exc, UpdateError) else 503, str(exc)
        ) from exc
