"""Request authentication and local/remote capability helpers."""

from __future__ import annotations

import ipaddress
import secrets
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

if TYPE_CHECKING:
    from app.core.settings import RuntimeSettings


SESSION_COOKIE_NAME = "mpp_session"


def is_loopback_host(host: str | None) -> bool:
    """Return whether a bind address or client address is loopback-only."""
    normalized = (host or "").strip().lower().strip("[]")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def is_local_request(request: Request) -> bool:
    client_host = request.client.host if request.client else ""
    return is_loopback_host(client_host)


def constant_time_token_matches(candidate: str, expected: str) -> bool:
    if not candidate or not expected:
        return False
    return secrets.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def request_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, bearer = authorization.partition(" ")
    if scheme.lower() == "bearer" and bearer:
        return bearer.strip()
    return request.cookies.get(SESSION_COOKIE_NAME, "")


def request_is_authenticated(request: Request, expected_token: str) -> bool:
    if not expected_token:
        return True
    return constant_time_token_matches(request_token(request), expected_token)


def filesystem_access_allowed(request: Request, settings: RuntimeSettings) -> bool:
    return is_local_request(request) or settings.allow_remote_filesystem


def require_filesystem_access(request: Request, settings: RuntimeSettings) -> None:
    if not filesystem_access_allowed(request, settings):
        raise HTTPException(
            status_code=403,
            detail="当前远程实例未开放服务器文件系统访问。",
        )

