"""Output, validation, and confirmation helpers for CLI commands."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, NoReturn

import typer

from app.cli.context import get_cli_context

SECRET_MARKERS = (
    "api_key",
    "api-key",
    "token",
    "password",
    "secret",
    "sessdata",
    "bili_jct",
    "cookie",
    "authorization",
    "credential",
    "proxy",
)


def is_secret_key(key: str) -> bool:
    normalized = key.lower()
    return any(marker in normalized for marker in SECRET_MARKERS)


def mask_secret(value: Any) -> Any:
    if value in (None, ""):
        return value
    text = str(value)
    return f"{text[:4]}..." if len(text) > 4 else "***"


def redact(value: Any, key: str = "") -> Any:
    """Recursively mask secret-like values in data from online and offline paths."""
    if key and is_secret_key(key):
        return mask_secret(value)
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def envelope(data: Any = None, *, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = get_cli_context()
    return {
        "ok": True,
        "data": data,
        "meta": {"server": ctx.server_url, **(meta or {})},
    }


def emit(data: Any, *, text: str | None = None, redact_secrets: bool = False) -> None:
    ctx = get_cli_context()
    safe = redact(data) if redact_secrets else data
    if ctx.output_mode == "json":
        typer.echo(json.dumps(envelope(safe), ensure_ascii=False, default=str))
    elif ctx.output_mode == "jsonl":
        items = safe if isinstance(safe, list) else [safe]
        for item in items:
            typer.echo(json.dumps(item, ensure_ascii=False, default=str))
    elif text is not None:
        typer.echo(text)
    elif isinstance(safe, str):
        typer.echo(safe)
    else:
        typer.echo(json.dumps(safe, ensure_ascii=False, indent=2, default=str))


def emit_event(event: dict[str, Any]) -> None:
    ctx = get_cli_context()
    if ctx.output_mode == "jsonl":
        typer.echo(json.dumps(event, ensure_ascii=False, default=str))
    elif ctx.output_mode == "json":
        return


def emit_error(
    code: str,
    message: str,
    *,
    detail: Any = None,
    retryable: bool = False,
    exit_code: int = 1,
) -> NoReturn:
    ctx = get_cli_context()
    payload = {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "detail": detail,
            "retryable": retryable,
        },
    }
    if ctx.output_mode in {"json", "jsonl"}:
        typer.echo(json.dumps(payload, ensure_ascii=False, default=str))
    else:
        typer.echo(f"Error [{code}]: {message}", err=True)
        if detail not in (None, "", {}, []):
            rendered = (
                detail
                if isinstance(detail, str)
                else json.dumps(detail, ensure_ascii=False, default=str)
            )
            typer.echo(str(rendered), err=True)
    raise typer.Exit(exit_code)


def confirm_action(prompt: str, *, explicit_yes: bool = False) -> None:
    ctx = get_cli_context()
    if explicit_yes or ctx.assume_yes:
        return
    if not ctx.interactive:
        emit_error("confirmation_required", f"{prompt} Use --yes to confirm.", exit_code=2)
    if not typer.confirm(prompt, default=False):
        emit_error("cancelled", "Operation cancelled.", exit_code=2)


def parse_value(raw: str) -> Any:
    """Parse a CLI scalar or JSON value while keeping ordinary strings intact."""
    value = raw.strip()
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    if value.startswith(("{", "[", '"')):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            emit_error("invalid_json", str(exc), exit_code=2)
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return raw


def parse_assignments(values: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in values:
        key, separator, raw = item.partition("=")
        key = key.strip()
        if not separator or not key:
            emit_error("invalid_assignment", f"Expected KEY=VALUE, got: {item}", exit_code=2)
        result[key] = parse_value(raw)
    return result


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        emit_error("invalid_json_file", str(exc), exit_code=2)
    if not isinstance(value, dict):
        emit_error("invalid_json_file", "The JSON root must be an object.", exit_code=2)
    return value


def print_debug_exception(exc: BaseException) -> None:
    if get_cli_context().debug:
        import traceback

        traceback.print_exception(exc, file=sys.stderr)
