"""Runtime settings API routes - thin wrapper over core.settings."""

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


@router.get("", response_model=RuntimeSettings)
async def get_settings():
    """Get current runtime settings."""
    return get_runtime_settings()


@router.put("", response_model=RuntimeSettings)
async def update_settings(new_settings: RuntimeSettings):
    """Update runtime settings and persist to file."""
    return update_runtime_settings(new_settings)


@router.patch("")
async def patch_settings(updates: dict[str, Any]):
    """Partially update runtime settings and persist to file."""
    return patch_runtime_settings(updates)
