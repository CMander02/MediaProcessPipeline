"""Legacy custom endpoint profiles and scalar normalization."""

from typing import Any

from pydantic import BaseModel


def _str_value(value: Any) -> str:
    return "" if value is None else str(value)


def _positive_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _coerce_custom_profiles(raw: Any) -> list[dict[str, str]]:
    """Coerce persisted/custom profile data into stable dicts."""
    profiles: list[dict[str, str]] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        raw = []

    for index, item in enumerate(raw):
        if isinstance(item, BaseModel):
            data = item.model_dump()
        elif isinstance(item, dict):
            data = item
        else:
            data = {}

        profile_id = _str_value(data.get("id") or f"custom-{index + 1}").strip()
        if not profile_id:
            profile_id = f"custom-{index + 1}"
        if profile_id in seen:
            profile_id = f"{profile_id}-{index + 1}"
        seen.add(profile_id)

        profiles.append(
            {
                "id": profile_id,
                "name": _str_value(data.get("name") or data.get("custom_name") or "Custom"),
                "api_base": _str_value(data.get("api_base") or data.get("custom_api_base")),
                "model": _str_value(data.get("model") or data.get("custom_model")),
                "api_key": _str_value(data.get("api_key") or data.get("custom_api_key")),
            }
        )
    return profiles


def _legacy_custom_profile(data: dict[str, Any]) -> dict[str, str]:
    return {
        "id": _str_value(data.get("custom_active_profile_id") or "default"),
        "name": _str_value(data.get("custom_name") or "Custom"),
        "api_base": _str_value(data.get("custom_api_base")),
        "model": _str_value(data.get("custom_model")),
        "api_key": _str_value(data.get("custom_api_key")),
    }


def _normalize_custom_profile_state(data: dict[str, Any], *, prefer_profiles: bool) -> None:
    """Keep multi-profile config and legacy custom_* fields in sync.

    The service still reads the legacy custom_* fields for active calls. The
    profile list is the durable multi-config representation, while the legacy
    fields mirror the active profile for older code paths and CLI commands.
    """
    profiles = _coerce_custom_profiles(data.get("custom_llm_profiles"))
    if not profiles:
        profiles = [_legacy_custom_profile(data)]

    active_id = _str_value(data.get("custom_active_profile_id") or profiles[0]["id"])
    active = next((profile for profile in profiles if profile["id"] == active_id), profiles[0])
    active_id = active["id"]

    if not prefer_profiles:
        active["name"] = _str_value(data.get("custom_name") or active["name"] or "Custom")
        active["api_base"] = _str_value(data.get("custom_api_base") or active["api_base"])
        active["model"] = _str_value(data.get("custom_model") or active["model"])
        active["api_key"] = _str_value(data.get("custom_api_key") or active["api_key"])

    data["custom_llm_profiles"] = profiles
    data["custom_active_profile_id"] = active_id
    data["custom_name"] = active["name"]
    data["custom_api_base"] = active["api_base"]
    data["custom_model"] = active["model"]
    data["custom_api_key"] = active["api_key"]
