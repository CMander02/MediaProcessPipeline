"""Shared CLI execution context."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, replace
from urllib.parse import urlparse

DEFAULT_SERVER_URL = "http://localhost:18000"


def normalize_server_url(value: str | None) -> str:
    """Return a normalized HTTP(S) daemon URL."""
    raw = (value or DEFAULT_SERVER_URL).strip()
    if not raw:
        raw = DEFAULT_SERVER_URL
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Invalid server URL: {value!r}")
    return raw.rstrip("/")


def is_local_server_url(value: str) -> bool:
    parsed = urlparse(value)
    return (parsed.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}


@dataclass(slots=True)
class CliContext:
    server_url: str = DEFAULT_SERVER_URL
    api_token: str = ""
    explicit_server: bool = False
    explicit_token: bool = False
    remote_preference: bool | None = None
    timeout: float = 30.0
    output_mode: str = "text"
    plain: bool = False
    quiet: bool = False
    no_input: bool = False
    assume_yes: bool = False
    debug: bool = False

    @property
    def interactive(self) -> bool:
        return not self.no_input and bool(getattr(sys.stdin, "isatty", lambda: False)())

    @property
    def is_local(self) -> bool:
        return is_local_server_url(self.server_url)


_context = CliContext(
    server_url=normalize_server_url(os.environ.get("MPP_SERVER_URL")),
    api_token=os.environ.get("MPP_API_TOKEN", ""),
    explicit_server=bool(os.environ.get("MPP_SERVER_URL")),
    explicit_token=bool(os.environ.get("MPP_API_TOKEN")),
    timeout=float(os.environ.get("MPP_TIMEOUT", "30") or 30),
    no_input=os.environ.get("MPP_NO_INPUT", "").strip().lower() in {"1", "true", "yes"},
)


def get_cli_context() -> CliContext:
    return _context


def configure_cli_context(**updates: object) -> CliContext:
    global _context
    _context = replace(_context, **updates)
    return _context


def route_to_configured_server(
    *, remote: bool | None = None, target: str = "", settings=None,
) -> None:
    from app.cli.output import emit_error
    from app.core.settings import get_runtime_settings

    ctx = get_cli_context()
    if ctx.explicit_server:
        return
    effective_remote = remote if remote is not None else ctx.remote_preference
    settings = settings if settings is not None else get_runtime_settings()
    if effective_remote is False:
        configure_cli_context(
            server_url=DEFAULT_SERVER_URL,
            api_token=ctx.api_token if ctx.explicit_token else settings.api_token,
        )
        return
    url = settings.remote_server_url.strip()
    if effective_remote and not url:
        emit_error("remote_target_missing", "remote_server_url is not configured.", exit_code=2)
    if not url or not (
        effective_remote or settings.cli_use_remote_server
        or (target and target != "local" and not settings.remote_execution_enabled)
    ):
        return
    try:
        server_url = normalize_server_url(url)
    except ValueError as exc:
        emit_error("invalid_server_url", str(exc), exit_code=2)
    configure_cli_context(
        server_url=server_url,
        api_token=ctx.api_token if ctx.explicit_token else settings.remote_api_token,
    )
