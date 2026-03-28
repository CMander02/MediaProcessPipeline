"""Runtime settings API routes - thin wrapper over core.settings."""

import re
from typing import Any

from fastapi import APIRouter

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
    incoming = new_settings.model_dump()
    for field in _SECRET_FIELDS:
        if isinstance(incoming.get(field), str) and _is_masked(incoming[field]):
            incoming[field] = getattr(current, field)
    return update_runtime_settings(RuntimeSettings(**incoming))


@router.patch("")
async def patch_settings(updates: dict[str, Any]):
    """Partially update runtime settings and persist to file."""
    cleaned = _restore_secrets(updates)
    return _mask_settings(patch_runtime_settings(cleaned))
