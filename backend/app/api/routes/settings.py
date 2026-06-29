"""Runtime settings API routes - thin wrapper over core.settings."""

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from app.core.settings import (
    SETTINGS_FILE,
    RuntimeSettings,
    get_runtime_settings,
    patch_runtime_settings,
    update_runtime_settings,
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
    "deepseek_api_key",
    "hf_token",
    "hf_proxy",
    "siliconflow_api_key",
    "bilibili_sessdata",
    "bilibili_bili_jct",
    "bilibili_dede_user_id",
    "xiaohongshu_cookie",
    "vlm_api_key",
    "kb_embedding_api_key",
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
    profiles = data.get("custom_llm_profiles")
    if isinstance(profiles, list):
        masked_profiles: list[dict[str, Any]] = []
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            item = dict(profile)
            api_key = item.get("api_key")
            if isinstance(api_key, str) and api_key:
                item["api_key"] = _mask_value(api_key)
            masked_profiles.append(item)
        data["custom_llm_profiles"] = masked_profiles
    connections = data.get("service_connections")
    if isinstance(connections, list):
        masked_connections: list[dict[str, Any]] = []
        for connection in connections:
            if not isinstance(connection, dict):
                continue
            item = dict(connection)
            api_key = item.get("api_key")
            if isinstance(api_key, str) and api_key:
                item["api_key"] = _mask_value(api_key)
            masked_connections.append(item)
        data["service_connections"] = masked_connections
    return data


def _restore_custom_profile_secrets(value: Any, current: RuntimeSettings) -> list[dict[str, Any]]:
    """Restore masked nested custom profile keys from current settings."""
    if not isinstance(value, list):
        return []

    current_profiles = [p.model_dump() for p in current.custom_llm_profiles]
    current_by_id = {str(p.get("id")): p for p in current_profiles}
    restored: list[dict[str, Any]] = []

    for index, profile in enumerate(value):
        if not isinstance(profile, dict):
            continue
        item = dict(profile)
        api_key = item.get("api_key")
        old = current_by_id.get(str(item.get("id")))
        if old is None and index < len(current_profiles):
            old = current_profiles[index]
        if isinstance(api_key, str) and _is_masked(api_key) and old:
            item["api_key"] = old.get("api_key", "")
        restored.append(item)

    return restored


def _restore_service_connection_secrets(
    value: Any,
    current: RuntimeSettings,
) -> list[dict[str, Any]]:
    """Restore masked nested service connection keys from current settings."""
    if not isinstance(value, list):
        return []

    current_connections = current.model_dump().get("service_connections", [])
    if not isinstance(current_connections, list):
        current_connections = []
    current_by_id = {
        str(connection.get("id")): connection
        for connection in current_connections
        if isinstance(connection, dict)
    }
    restored: list[dict[str, Any]] = []

    for index, connection in enumerate(value):
        if not isinstance(connection, dict):
            continue
        item = dict(connection)
        api_key = item.get("api_key")
        old = current_by_id.get(str(item.get("id")))
        if old is None and index < len(current_connections):
            old_item = current_connections[index]
            old = old_item if isinstance(old_item, dict) else None
        if isinstance(api_key, str) and _is_masked(api_key) and old:
            item["api_key"] = old.get("api_key", "")
        restored.append(item)

    return restored


def _restore_service_connection_dot_secret(
    key: str,
    value: Any,
    current: RuntimeSettings,
) -> Any | None:
    parts = key.split(".", 2)
    if (
        len(parts) != 3
        or parts[0] != "service_connections"
        or parts[2] != "api_key"
        or not isinstance(value, str)
        or not _is_masked(value)
    ):
        return None

    connections = current.model_dump().get("service_connections", [])
    if not isinstance(connections, list):
        return None
    connection_id = parts[1]
    for connection in connections:
        if isinstance(connection, dict) and connection.get("id") == connection_id:
            return connection.get("api_key", "")
    return None


def _restore_secrets(
    updates: dict[str, Any],
    current: RuntimeSettings | None = None,
    *,
    preserve_masked: bool = False,
) -> dict[str, Any]:
    """Restore or drop masked secret placeholders so they are not persisted."""
    if current is None:
        current = get_runtime_settings()
    cleaned = {}
    for key, value in updates.items():
        if key in _SECRET_FIELDS and isinstance(value, str) and _is_masked(value):
            if preserve_masked:
                cleaned[key] = getattr(current, key)
            continue  # skip — frontend sent back the masked placeholder
        dot_secret = _restore_service_connection_dot_secret(key, value, current)
        if dot_secret is not None:
            if preserve_masked:
                cleaned[key] = dot_secret
            continue
        if key == "custom_llm_profiles":
            cleaned[key] = _restore_custom_profile_secrets(value, current)
            continue
        if key == "service_connections":
            cleaned[key] = _restore_service_connection_secrets(value, current)
            continue
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


@router.put("")
async def update_settings(new_settings: RuntimeSettings):
    """Update runtime settings and persist to file.

    Secret fields that still contain the masked placeholder are
    preserved from the current settings (not overwritten with the mask).
    """
    current = get_runtime_settings()
    reopen_db = _prepare_data_root_change(new_settings.data_root)
    incoming = new_settings.model_dump()
    incoming = _restore_secrets(incoming, current, preserve_masked=True)
    updated = update_runtime_settings(RuntimeSettings(**incoming))
    if reopen_db:
        _reopen_task_db(updated.data_root)
    return _mask_settings(updated)


@router.patch("")
async def patch_settings(updates: dict[str, Any]):
    """Partially update runtime settings and persist to file."""
    cleaned = _restore_secrets(updates)
    reopen_db = _prepare_data_root_change(cleaned.get("data_root"))
    updated = patch_runtime_settings(cleaned)
    if reopen_db:
        _reopen_task_db(updated.data_root)
    return _mask_settings(updated)


@router.get("/uvr/local")
async def detect_local_uvr():
    """Detect a locally installed UVR model directory."""
    from app.services.preprocessing.uvr import find_local_uvr_model_dir

    model_dir = find_local_uvr_model_dir()
    if not model_dir:
        return {"found": False, "path": "", "models": []}

    models: list[str] = []
    for subdir in ("MDX_Net_Models", "VR_Models", "Demucs_Models"):
        folder = model_dir / subdir
        if not folder.exists():
            continue
        for item in folder.iterdir():
            if item.is_file() and item.suffix.lower() in {".onnx", ".pth", ".yaml", ".th"}:
                models.append(item.stem)

    return {"found": True, "path": str(model_dir), "models": sorted(set(models))}
