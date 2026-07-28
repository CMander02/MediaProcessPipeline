"""Read-only system inspection endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.services.system_diagnostics import get_system_diagnostics

router = APIRouter(prefix="/system", tags=["system"])

_NO_STORE_HEADERS = {"Cache-Control": "no-store"}


@router.get("/diagnostics")
async def system_diagnostics() -> JSONResponse:
    try:
        payload = await asyncio.to_thread(get_system_diagnostics)
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Runtime diagnostics are unavailable",
            headers=_NO_STORE_HEADERS,
        ) from None
    return JSONResponse(payload, headers=_NO_STORE_HEADERS)


__all__ = ["router"]
