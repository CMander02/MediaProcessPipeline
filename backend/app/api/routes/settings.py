"""Runtime settings API routes - thin wrapper over core.settings."""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from app.core.network import httpx_client_kwargs, urllib_urlopen
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
    "jina_reader_api_key",
}

_MASK_PATTERN = re.compile(r"^\*{3,}\.{3}.{0,4}$")  # matches "***...xxxx"


def _normalize_siliconflow_api_base(api_base: str) -> str:
    base = str(api_base or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def _siliconflow_models_url(api_base: str) -> str:
    base = _normalize_siliconflow_api_base(api_base)
    return f"{base}/models" if base else ""


def _infer_siliconflow_model_type(model_id: str) -> str:
    name = str(model_id or "").strip().lower()
    if "reranker" in name or "rerank" in name:
        return "rerank"
    if any(keyword in name for keyword in ("embedding", "embed", "bge-m3")):
        return "embedding"
    if any(keyword in name for keyword in ("sensevoice", "telespeech", "speechasr", "asr", "audio", "whisper")):
        return "asr"
    if (
        "vision" in name
        or "visual" in name
        or re.search(r"(^|[/_.:-])vl($|[/_.:-])", name)
    ):
        return "vlm"
    return "llm"


def _provider_models_from_payload(payload: Any) -> list[dict[str, str]]:
    raw_models = payload.get("data", []) if isinstance(payload, dict) else payload
    if not isinstance(raw_models, list):
        return []

    models: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_models:
        model_type = ""
        if isinstance(item, str):
            model_id = item.strip()
            display_name = model_id
        elif isinstance(item, dict):
            model_id = str(item.get("id") or item.get("model") or item.get("name") or "").strip()
            display_name = str(item.get("display_name") or item.get("name") or model_id).strip()
            model_type = str(item.get("model_type") or "").strip().lower()
        else:
            continue

        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        models.append(
            {
                "id": model_id,
                "display_name": display_name or model_id,
                "model_type": model_type if model_type in {"llm", "vlm", "embedding", "rerank", "asr"} else _infer_siliconflow_model_type(model_id),
            }
        )
    return models


def _siliconflow_models_from_payload(payload: Any) -> list[dict[str, str]]:
    return _provider_models_from_payload(payload)


async def _fetch_siliconflow_models_payload(api_base: str, api_key: str) -> Any:
    url = _siliconflow_models_url(api_base)
    if not url:
        raise HTTPException(status_code=400, detail="siliconflow_api_base is empty.")
    if not api_key:
        raise HTTPException(status_code=400, detail="siliconflow_api_key is empty.")

    import httpx

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=20.0, **httpx_client_kwargs(url)) as client:
            response = await client.get(url, headers=headers)
    except httpx.TimeoutException as e:
        return await _fetch_json_with_urllib(url, headers, timeout=30.0, label="SiliconFlow models")
    except httpx.RequestError as e:
        return await _fetch_json_with_urllib(url, headers, timeout=30.0, label="SiliconFlow models")

    if response.status_code >= 400:
        detail = response.text[:500] if response.text else response.reason_phrase
        raise HTTPException(
            status_code=502,
            detail=f"SiliconFlow models request failed ({response.status_code}): {detail}",
        )

    try:
        return response.json()
    except ValueError as e:
        raise HTTPException(status_code=502, detail="SiliconFlow models response is not JSON.") from e


async def _fetch_json_with_urllib(
    url: str,
    headers: dict[str, str],
    *,
    timeout: float,
    label: str,
) -> Any:
    """Fallback JSON fetcher for endpoints that stall in httpx on Windows/proxy setups."""

    def _fetch() -> Any:
        import socket
        import urllib.error
        import urllib.request

        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib_urlopen(request, timeout=timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500] if e.fp else str(e)
            raise HTTPException(
                status_code=502,
                detail=f"{label} request failed ({e.code}): {detail}",
            ) from e
        except TimeoutError as e:
            raise HTTPException(status_code=504, detail=f"{label} request timed out.") from e
        except socket.timeout as e:
            raise HTTPException(status_code=504, detail=f"{label} request timed out.") from e
        except OSError as e:
            raise HTTPException(status_code=502, detail=f"{label} request failed: {e}") from e

        try:
            return json.loads(body.decode("utf-8"))
        except ValueError as e:
            raise HTTPException(status_code=502, detail=f"{label} response is not JSON.") from e

    return await asyncio.to_thread(_fetch)


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
    providers = data.get("providers")
    if isinstance(providers, list):
        masked_providers: list[dict[str, Any]] = []
        for provider in providers:
            if not isinstance(provider, dict):
                continue
            item = dict(provider)
            api_key = item.get("api_key")
            if isinstance(api_key, str) and api_key:
                item["api_key"] = _mask_value(api_key)
            masked_providers.append(item)
        data["providers"] = masked_providers
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


def _restore_provider_secrets(
    value: Any,
    current: RuntimeSettings,
) -> list[dict[str, Any]]:
    """Restore masked nested provider keys from current settings."""
    if not isinstance(value, list):
        return []

    current_providers = current.model_dump().get("providers", [])
    if not isinstance(current_providers, list):
        current_providers = []
    current_by_id = {
        str(provider.get("id")): provider
        for provider in current_providers
        if isinstance(provider, dict)
    }
    restored: list[dict[str, Any]] = []

    for index, provider in enumerate(value):
        if not isinstance(provider, dict):
            continue
        item = dict(provider)
        api_key = item.get("api_key")
        old = current_by_id.get(str(item.get("id")))
        if old is None and index < len(current_providers):
            old_item = current_providers[index]
            old = old_item if isinstance(old_item, dict) else None
        if isinstance(api_key, str) and _is_masked(api_key) and old:
            restored_api_key = old.get("api_key", "")
            if isinstance(restored_api_key, str) and _is_masked(restored_api_key):
                provider_id = str(item.get("id") or old.get("id") or "")
                flat_key = _provider_flat_secret_key(provider_id)
                restored_api_key = getattr(current, flat_key, "") if flat_key else ""
            item["api_key"] = restored_api_key
        restored.append(item)

    return restored


def _provider_flat_secret_key(provider_id: str) -> str:
    if provider_id == "deepseek":
        return "deepseek_api_key"
    if provider_id == "siliconflow":
        return "siliconflow_api_key"
    if provider_id == "openai":
        return "openai_api_key"
    if provider_id == "anthropic":
        return "anthropic_api_key"
    if provider_id == "custom-vision-default":
        return "vlm_api_key"
    if provider_id == "custom-embedding-default":
        return "kb_embedding_api_key"
    return ""


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
        if key == "providers":
            cleaned[key] = _restore_provider_secrets(value, current)
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


@router.get("/providers/siliconflow/models")
async def list_siliconflow_models():
    settings = get_runtime_settings()
    payload = await _fetch_siliconflow_models_payload(
        settings.siliconflow_api_base,
        settings.siliconflow_api_key,
    )
    return {"models": _siliconflow_models_from_payload(payload)}


def _model_type_capabilities(model_type: str) -> list[str]:
    if model_type == "vlm":
        return ["vlm", "chat", "vision", "json"]
    if model_type == "embedding":
        return ["embedding"]
    if model_type == "rerank":
        return ["rerank"]
    if model_type == "asr":
        return ["asr"]
    return ["llm", "chat", "json"]


def _model_endpoint_path(model_type: str) -> str:
    if model_type == "embedding":
        return "/embeddings"
    if model_type == "rerank":
        return "/rerank"
    if model_type == "asr":
        return "/audio/transcriptions"
    return "/chat/completions"


_SILICONFLOW_ASR_DEFAULT_PARAMS: dict[str, Any] = {
    "request_format": "multipart",
    "file_field": "file",
    "model_field": "model",
    "include_language": False,
    "max_file_mb": 50,
    "max_duration_sec": 3600,
}

_SILICONFLOW_RERANK_DEFAULT_PARAMS: dict[str, Any] = {
    "request_format": "json",
    "query_field": "query",
    "documents_field": "documents",
    "return_documents": False,
    "max_chunks_per_doc": 1024,
}


def _model_default_params(provider_id: str, model_type: str, params: Any) -> dict[str, Any]:
    current = params if isinstance(params, dict) else {}
    if provider_id == "siliconflow" and model_type == "asr":
        return {**_SILICONFLOW_ASR_DEFAULT_PARAMS, **current}
    if provider_id == "siliconflow" and model_type == "rerank":
        return {**_SILICONFLOW_RERANK_DEFAULT_PARAMS, **current}
    return current


def _provider_model_record(provider_id: str, model: dict[str, str]) -> dict[str, Any]:
    model_type = _infer_siliconflow_model_type(model["id"])
    if model.get("model_type"):
        model_type = str(model["model_type"]).strip().lower()
    return {
        "id": f"{provider_id}:{model['id']}",
        "model_id": model["id"],
        "display_name": model.get("display_name") or model["id"],
        "enabled": True,
        "model_type": model_type,
        "capabilities": _model_type_capabilities(model_type),
        "endpoint_path": _model_endpoint_path(model_type),
        "default_params": _model_default_params(provider_id, model_type, {}),
    }


def _configured_provider_model_record(provider_id: str, model: dict[str, Any]) -> dict[str, Any] | None:
    model_id = str(model.get("model_id") or model.get("id") or "").strip()
    if not model_id:
        return None
    model_type = str(model.get("model_type") or "").strip().lower()
    if model_type not in {"llm", "vlm", "embedding", "rerank", "asr"}:
        model_type = _infer_siliconflow_model_type(model_id)
    capabilities = model.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        capabilities = _model_type_capabilities(model_type)
    default_params = _model_default_params(provider_id, model_type, model.get("default_params"))
    return {
        **model,
        "id": str(model.get("id") or f"{provider_id}:{model_id}"),
        "model_id": model_id,
        "display_name": str(model.get("display_name") or model_id),
        "enabled": model.get("enabled", True) is not False,
        "model_type": model_type,
        "capabilities": [str(item) for item in capabilities if str(item).strip()],
        "endpoint_path": str(model.get("endpoint_path") or _model_endpoint_path(model_type)),
        "default_params": default_params,
    }


def _model_matches_capability(model: dict[str, Any], capability: str) -> bool:
    normalized = str(capability or "").strip().lower()
    if not normalized:
        return True
    model_type = str(model.get("model_type") or "").strip().lower()
    capabilities = {
        str(item).strip().lower()
        for item in model.get("capabilities", [])
        if str(item).strip()
    }
    if normalized == "llm":
        return model_type == "llm" or "llm" in capabilities or "chat" in capabilities
    if normalized == "vision":
        return model_type == "vlm" or "vision" in capabilities
    return model_type == normalized or normalized in capabilities


def _provider_configured_models(provider: dict[str, Any], capability: str = "") -> list[dict[str, Any]]:
    raw_models = provider.get("models")
    if not isinstance(raw_models, list):
        return []
    records = [
        record
        for model in raw_models
        if isinstance(model, dict)
        for record in [_configured_provider_model_record(str(provider.get("id") or ""), model)]
        if record is not None
    ]
    return [record for record in records if _model_matches_capability(record, capability)]


def _provider_allowed_models(provider: dict[str, Any], capability: str = "") -> list[dict[str, Any]]:
    if provider.get("enabled", True) is False:
        return []
    return [
        model
        for model in _provider_configured_models(provider, capability)
        if model.get("enabled", True) is not False
    ]


def _settings_provider(data: dict[str, Any], provider_id: str) -> dict[str, Any] | None:
    providers = data.get("providers")
    if not isinstance(providers, list):
        return None
    for provider in providers:
        if isinstance(provider, dict) and str(provider.get("id")) == provider_id:
            return provider
    return None


async def _fetch_provider_models_payload(provider: dict[str, Any]) -> Any:
    provider_id = str(provider.get("id") or "")
    provider_type = str(provider.get("provider_type") or "")
    api_base = str(provider.get("api_base") or "")
    api_key = str(provider.get("api_key") or "")
    if provider_id == "siliconflow" or provider_type == "siliconflow":
        return await _fetch_siliconflow_models_payload(api_base, api_key)

    url = _siliconflow_models_url(api_base)
    if not url:
        raise HTTPException(status_code=400, detail="provider api_base is empty.")
    headers: dict[str, str] = {}
    raw_headers = provider.get("headers")
    if isinstance(raw_headers, dict):
        headers.update({str(key): str(value) for key, value in raw_headers.items() if str(key).strip()})
    if api_key:
        headers.setdefault("Authorization", f"Bearer {api_key}")

    import httpx

    try:
        async with httpx.AsyncClient(timeout=20.0, **httpx_client_kwargs(url)) as client:
            response = await client.get(url, headers=headers)
    except httpx.TimeoutException as e:
        return await _fetch_json_with_urllib(url, headers, timeout=30.0, label="Provider models")
    except httpx.RequestError as e:
        return await _fetch_json_with_urllib(url, headers, timeout=30.0, label="Provider models")

    if response.status_code >= 400:
        detail = response.text[:500] if response.text else response.reason_phrase
        raise HTTPException(
            status_code=502,
            detail=f"Provider models request failed ({response.status_code}): {detail}",
        )
    try:
        return response.json()
    except ValueError as e:
        raise HTTPException(status_code=502, detail="Provider models response is not JSON.") from e


@router.get("/providers/{provider_id}/models/catalog")
async def list_provider_model_catalog(provider_id: str, capability: str = ""):
    data = get_runtime_settings().model_dump()
    provider = _settings_provider(data, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found.")

    configured_models = _provider_configured_models(provider, capability)
    allowed_models = _provider_allowed_models(provider, capability)
    remote_models: list[dict[str, Any]] = []
    source = "configured"
    error: str | None = None

    try:
        payload = await _fetch_provider_models_payload(provider)
        for model in _provider_models_from_payload(payload):
            record = _provider_model_record(provider_id, model)
            if _model_matches_capability(record, capability):
                remote_models.append(record)
    except HTTPException as exc:
        error = str(exc.detail)

    if remote_models:
        source = "remote"

    return {
        "provider_id": provider_id,
        "source": source,
        "models": remote_models or configured_models,
        "configured_models": configured_models,
        "allowed_models": allowed_models,
        "error": error,
    }


@router.post("/providers/{provider_id}/models/sync")
async def sync_provider_models(provider_id: str):
    current = get_runtime_settings()
    data = current.model_dump()
    provider = _settings_provider(data, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found.")

    payload = await _fetch_provider_models_payload(provider)
    fetched = [
        _provider_model_record(provider_id, model)
        for model in _provider_models_from_payload(payload)
    ]
    by_model_id = {
        str(model.get("model_id")): model
        for model in provider.get("models", [])
        if isinstance(model, dict)
    }
    for model in fetched:
        existing = by_model_id.get(model["model_id"])
        existing_type = str((existing or {}).get("model_type") or "").strip().lower()
        fetched_type = str(model.get("model_type") or "").strip().lower()
        model_type_changed = bool(existing_type and fetched_type and existing_type != fetched_type)
        merged_type = fetched_type if model_type_changed or not existing_type else existing_type
        existing_params = (existing or {}).get("default_params") if isinstance(existing, dict) else {}
        by_model_id[model["model_id"]] = {
            **model,
            **(existing or {}),
            "id": model["id"],
            "model_id": model["model_id"],
            "display_name": (existing or {}).get("display_name") or model["display_name"],
            "model_type": merged_type,
            "capabilities": model["capabilities"]
            if model_type_changed
            else (existing or {}).get("capabilities") or model["capabilities"],
            "endpoint_path": model["endpoint_path"]
            if model_type_changed
            else (existing or {}).get("endpoint_path") or model["endpoint_path"],
            "default_params": _model_default_params(
                provider_id,
                merged_type,
                existing_params,
            ),
        }
    provider["models"] = list(by_model_id.values())
    updated = patch_runtime_settings({"providers": data.get("providers", [])})
    updated_provider = _settings_provider(_mask_settings(updated), provider_id)
    return {
        "provider": updated_provider,
        "models": updated_provider.get("models", []) if isinstance(updated_provider, dict) else [],
    }


@router.post("/providers/models/metadata")
async def infer_provider_model_metadata(payload: dict[str, Any]):
    model_id = str(payload.get("model_id") or payload.get("id") or "").strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="model_id is required.")
    requested_type = str(payload.get("model_type") or "").strip().lower()
    model_type = requested_type or _infer_siliconflow_model_type(model_id)
    if model_type not in {"llm", "vlm", "embedding", "rerank", "asr"}:
        model_type = "llm"
    provider_id = str(payload.get("provider_id") or "").strip()
    return {
        "model_id": model_id,
        "display_name": str(payload.get("display_name") or model_id),
        "model_type": model_type,
        "capabilities": _model_type_capabilities(model_type),
        "endpoint_path": _model_endpoint_path(model_type),
        "default_params": _model_default_params(provider_id, model_type, payload.get("default_params")),
    }


@router.post("/providers/{provider_id}/balance")
async def query_provider_balance(provider_id: str):
    data = get_runtime_settings().model_dump()
    provider = _settings_provider(data, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found.")
    balance = provider.get("balance")
    if not isinstance(balance, dict) or not balance.get("enabled"):
        raise HTTPException(status_code=400, detail="Provider does not support balance query.")
    endpoint_path = str(balance.get("endpoint_path") or "").strip()
    if not endpoint_path:
        raise HTTPException(status_code=400, detail="Provider does not support balance query.")

    api_base = _normalize_siliconflow_api_base(str(provider.get("api_base") or ""))
    api_key = str(provider.get("api_key") or "")
    if not api_base or not api_key:
        raise HTTPException(status_code=400, detail="Provider api_base or api_key is empty.")
    if not endpoint_path.startswith("/"):
        endpoint_path = f"/{endpoint_path}"
    url = f"{api_base}{endpoint_path}"
    method = str(balance.get("method") or "GET").upper()

    import httpx

    try:
        async with httpx.AsyncClient(timeout=20.0, **httpx_client_kwargs(url)) as client:
            response = await client.request(method, url, headers={"Authorization": f"Bearer {api_key}"})
    except httpx.TimeoutException as e:
        raise HTTPException(status_code=504, detail="Provider balance request timed out.") from e
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Provider balance request failed: {e}") from e

    if response.status_code >= 400:
        detail = response.text[:500] if response.text else response.reason_phrase
        raise HTTPException(
            status_code=502,
            detail=f"Provider balance request failed ({response.status_code}): {detail}",
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {"text": response.text}
    return {"provider_id": provider_id, "balance": payload}


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
