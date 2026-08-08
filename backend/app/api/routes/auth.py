"""Browser session authentication and capability discovery routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.core.security import (
    SESSION_COOKIE_NAME,
    constant_time_token_matches,
    filesystem_access_allowed,
    is_local_request,
    request_is_authenticated,
)
from app.core.settings import get_runtime_settings


router = APIRouter(tags=["access"])


class UnlockRequest(BaseModel):
    token: str


def _access_status(request: Request) -> dict[str, object]:
    settings = get_runtime_settings()
    local = is_local_request(request)
    return {
        "required": bool(settings.api_token),
        "authenticated": request_is_authenticated(request, settings.api_token),
        "mode": "local" if local else "remote",
    }


@router.get("/auth/status")
async def auth_status(request: Request):
    return _access_status(request)


@router.post("/auth/unlock")
async def auth_unlock(payload: UnlockRequest, request: Request, response: Response):
    settings = get_runtime_settings()
    if settings.api_token and not constant_time_token_matches(payload.token.strip(), settings.api_token):
        raise HTTPException(status_code=401, detail="访问令牌无效。")

    if settings.api_token:
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=settings.api_token,
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/",
        )
    return _access_status_with_auth(request, authenticated=True)


def _access_status_with_auth(request: Request, *, authenticated: bool) -> dict[str, object]:
    settings = get_runtime_settings()
    return {
        "required": bool(settings.api_token),
        "authenticated": authenticated,
        "mode": "local" if is_local_request(request) else "remote",
    }


@router.post("/auth/logout")
async def auth_logout(request: Request, response: Response):
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="strict",
    )
    status = _access_status(request)
    status["authenticated"] = not bool(get_runtime_settings().api_token)
    return status


@router.get("/capabilities")
async def capabilities(request: Request):
    settings = get_runtime_settings()
    local = is_local_request(request)
    filesystem = filesystem_access_allowed(request, settings)
    return {
        "mode": "local" if local else "remote",
        "authenticated": request_is_authenticated(request, settings.api_token),
        "url_submission": True,
        "browser_file_upload": True,
        "browser_folder_upload": True,
        "task_control": True,
        "settings": True,
        "filesystem_browse": filesystem,
        "local_path_submission": filesystem,
        "open_local_folder": filesystem,
        "archive_mutation": True,
    }
