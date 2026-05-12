"""Runtime settings API routes - thin wrapper over core.settings."""

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from app.core.settings import (
    RuntimeSettings,
    get_runtime_settings,
    update_runtime_settings,
    patch_runtime_settings,
    SETTINGS_FILE,
)

router = APIRouter(prefix="/settings", tags=["settings"])

# Re-export for backwards compatibility with existing imports
__all__ = ["RuntimeSettings", "get_runtime_settings", "SETTINGS_FILE", "router"]

# Fields that contain secrets — values are masked in GET responses
_SECRET_FIELDS = {
    "api_token",
    "anthropic_api_key",
    "openai_api_key",
    "custom_api_key",
    "hf_token",
    "bilibili_sessdata",
    "bilibili_bili_jct",
    "bilibili_dede_user_id",
}

_MASK_PATTERN = re.compile(r"^\*{3,}\.{3}.{0,4}$")  # matches "***...xxxx"


def _mask_value(value: str) -> str:
    """Mask a secret value, keeping the last 4 chars visible."""
    if not value:
        return ""
    if len(value) <= 4:
        return "***"
    return f"***...{value[-4:]}"


def _is_masked(value: str) -> bool:
    """Check if a value looks like a masked secret."""
    return bool(_MASK_PATTERN.match(value))


def _mask_settings(settings: RuntimeSettings) -> dict[str, Any]:
    """Return settings dict with secret fields masked."""
    data = settings.model_dump()
    for field in _SECRET_FIELDS:
        if field in data and data[field]:
            data[field] = _mask_value(data[field])
    return data


def _restore_secrets(updates: dict[str, Any]) -> dict[str, Any]:
    """If a secret field still has masked value, drop it so the old value is kept."""
    cleaned = {}
    for key, value in updates.items():
        if key in _SECRET_FIELDS and isinstance(value, str) and _is_masked(value):
            continue  # skip — frontend sent back the masked placeholder
        cleaned[key] = value
    return cleaned


def _prepare_data_root_change(new_data_root: str | None) -> bool:
    """Reject workspace switches while queued or processing tasks exist."""
    if not new_data_root:
        return False
    current_root = Path(get_runtime_settings().data_root).resolve()
    next_root = Path(new_data_root).resolve()
    if next_root == current_root:
        return False

    from app.core.database import get_task_store
    from app.core.queue import get_task_queue
    from app.models import TaskStatus

    queue = get_task_queue()
    active = queue.active_task_ids or get_task_store().list_by_statuses([
        TaskStatus.QUEUED,
        TaskStatus.PROCESSING,
    ])
    if active:
        raise HTTPException(
            status_code=409,
            detail="Cannot change data_root while queued or processing tasks exist.",
        )
    return True


def _reopen_task_db(data_root: str) -> None:
    from app.core.database import reset_db_path
    reset_db_path(Path(data_root))


@router.get("")
async def get_settings():
    """Get current runtime settings (secrets masked)."""
    return _mask_settings(get_runtime_settings())


@router.put("", response_model=RuntimeSettings)
async def update_settings(new_settings: RuntimeSettings):
    """Update runtime settings and persist to file.

    Secret fields that still contain the masked placeholder are
    preserved from the current settings (not overwritten with the mask).
    """
    current = get_runtime_settings()
    reopen_db = _prepare_data_root_change(new_settings.data_root)
    incoming = new_settings.model_dump()
    for field in _SECRET_FIELDS:
        if isinstance(incoming.get(field), str) and _is_masked(incoming[field]):
            incoming[field] = getattr(current, field)
    updated = update_runtime_settings(RuntimeSettings(**incoming))
    if reopen_db:
        _reopen_task_db(updated.data_root)
    return updated


@router.patch("")
async def patch_settings(updates: dict[str, Any]):
    """Partially update runtime settings and persist to file."""
    cleaned = _restore_secrets(updates)
    reopen_db = _prepare_data_root_change(cleaned.get("data_root"))
    updated = patch_runtime_settings(cleaned)
    if reopen_db:
        _reopen_task_db(updated.data_root)
    return _mask_settings(updated)
