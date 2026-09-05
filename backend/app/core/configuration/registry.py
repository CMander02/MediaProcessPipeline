"""Provider and service model registry normalization."""

from typing import Any

from app.core.configuration.constants import (
    _MASKED_SECRET_PATTERN,
    _MODEL_FIELD_SPECS,
    _MODEL_TYPE_CAPABILITIES,
    _MODEL_TYPE_ENDPOINT_PATHS,
    _PROVIDER_CONNECTION_ALIASES,
    _PROVIDER_FLAT_KEYS,
    _PROVIDER_MODEL_TYPE_CAPABILITIES,
    _SILICONFLOW_ASR_DEFAULT_PARAMS,
    _SILICONFLOW_RERANK_DEFAULT_PARAMS,
)
from app.core.configuration.profiles import _coerce_custom_profiles, _positive_int, _str_value
from pydantic import BaseModel


def _looks_masked_secret(value: Any) -> bool:
    return isinstance(value, str) and bool(_MASKED_SECRET_PATTERN.match(value))


def _service_connection_record(
    *,
    connection_id: str,
    name: str,
    service_scope: str,
    provider: str,
    endpoint_type: str,
    api_base: Any,
    api_key: Any,
) -> dict[str, Any]:
    return {
        "id": connection_id,
        "name": name,
        "service_scope": service_scope,
        "provider": provider,
        "endpoint_type": endpoint_type,
        "api_base": _str_value(api_base),
        "api_key": _str_value(api_key),
        "headers": {},
        "enabled": True,
        "timeout_sec": 120.0,
        "max_concurrency": 4,
        "status": "unknown",
        "last_checked_at": "",
    }


def _default_service_connections(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _service_connection_record(
            connection_id="anthropic",
            name="Anthropic",
            service_scope="api",
            provider="anthropic",
            endpoint_type="anthropic",
            api_base=data.get("anthropic_api_base"),
            api_key=data.get("anthropic_api_key"),
        ),
        _service_connection_record(
            connection_id="openai",
            name="OpenAI",
            service_scope="api",
            provider="openai",
            endpoint_type="openai_compatible",
            api_base=data.get("openai_api_base"),
            api_key=data.get("openai_api_key"),
        ),
        _service_connection_record(
            connection_id="deepseek",
            name="DeepSeek",
            service_scope="api",
            provider="deepseek",
            endpoint_type="deepseek_native",
            api_base=data.get("deepseek_api_base"),
            api_key=data.get("deepseek_api_key"),
        ),
        _service_connection_record(
            connection_id="siliconflow-asr",
            name="SiliconFlow ASR",
            service_scope="api",
            provider="siliconflow",
            endpoint_type="audio_transcription",
            api_base=data.get("siliconflow_api_base"),
            api_key=data.get("siliconflow_api_key"),
        ),
        _service_connection_record(
            connection_id="vision-default",
            name="Vision API",
            service_scope="api",
            provider="custom_openai",
            endpoint_type="openai_compatible",
            api_base=data.get("vlm_api_base"),
            api_key=data.get("vlm_api_key"),
        ),
        _service_connection_record(
            connection_id="embedding-default",
            name="Knowledge Base Embedding",
            service_scope="api",
            provider="custom_openai",
            endpoint_type="openai_compatible",
            api_base=data.get("kb_embedding_api_base"),
            api_key=data.get("kb_embedding_api_key"),
        ),
    ]


def _default_service_connection_by_id(
    data: dict[str, Any],
    connection_id: str,
) -> dict[str, Any] | None:
    return next(
        (
            connection
            for connection in _default_service_connections(data)
            if connection["id"] == connection_id
        ),
        None,
    )


def _generic_service_connection(connection_id: str) -> dict[str, Any]:
    return _service_connection_record(
        connection_id=connection_id,
        name=connection_id,
        service_scope="api",
        provider=connection_id,
        endpoint_type="openai_compatible",
        api_base="",
        api_key="",
    )


def _model_record_id(connection_id: str, model_id: str) -> str:
    slug = model_id.strip().lower().replace("/", "-").replace(":", "-")
    return f"{connection_id}:{slug}"


def _normalize_model_type(model_type: Any, capabilities: list[str] | None = None) -> str:
    normalized = _str_value(model_type).strip().lower()
    if normalized in _MODEL_TYPE_CAPABILITIES:
        return normalized

    capability_set = {capability.strip().lower() for capability in capabilities or []}
    if "asr" in capability_set:
        return "asr"
    if "rerank" in capability_set:
        return "rerank"
    if "embedding" in capability_set:
        return "embedding"
    if "vision" in capability_set:
        return "vlm"
    return "llm"


def _normalize_model_capabilities(model_type: str, capabilities: list[str] | None) -> list[str]:
    cleaned = [
        capability.strip().lower()
        for capability in capabilities or []
        if capability and capability.strip()
    ]
    if cleaned:
        return list(dict.fromkeys(cleaned))
    return list(_MODEL_TYPE_CAPABILITIES[model_type])


def _model_endpoint_path(model_type: str) -> str:
    return _MODEL_TYPE_ENDPOINT_PATHS.get(model_type, "/chat/completions")


def _model_default_params(
    provider_id: str, model_type: str, default_params: dict[str, Any] | None
) -> dict[str, Any]:
    params = default_params if isinstance(default_params, dict) else {}
    if provider_id == "siliconflow" and model_type == "asr":
        return {**_SILICONFLOW_ASR_DEFAULT_PARAMS, **params}
    if provider_id == "siliconflow" and model_type == "rerank":
        return {**_SILICONFLOW_RERANK_DEFAULT_PARAMS, **params}
    return params


def _provider_model_capabilities(model_type: str, capabilities: list[str] | None) -> list[str]:
    cleaned = [
        capability.strip().lower()
        for capability in capabilities or []
        if capability and capability.strip()
    ]
    defaults = _PROVIDER_MODEL_TYPE_CAPABILITIES.get(model_type, ["llm", "chat", "json"])
    return list(dict.fromkeys([*defaults, *cleaned]))


def _provider_model_record(
    provider_id: str,
    model_id: Any,
    *,
    model_type: str = "llm",
    capabilities: list[str] | None = None,
    display_name: Any = "",
    enabled: Any = True,
    endpoint_path: Any = "",
    default_params: dict[str, Any] | None = None,
    cli_model_name: Any = "",
) -> dict[str, Any] | None:
    model = _str_value(model_id).strip()
    if not model:
        return None
    normalized_type = _normalize_model_type(model_type, capabilities)
    record = {
        "id": f"{provider_id}:{model}",
        "model_id": model,
        "display_name": _str_value(display_name).strip() or model,
        "enabled": bool(enabled),
        "model_type": normalized_type,
        "capabilities": _provider_model_capabilities(normalized_type, capabilities),
        "endpoint_path": _str_value(endpoint_path).strip() or _model_endpoint_path(normalized_type),
        "default_params": _model_default_params(provider_id, normalized_type, default_params),
    }
    normalized_cli_model_name = _str_value(cli_model_name).strip()
    if normalized_cli_model_name:
        record["cli_model_name"] = normalized_cli_model_name
    return record


def _provider_record(
    *,
    provider_id: str,
    name: str,
    provider_type: str,
    api_base: Any = "",
    api_key: Any = "",
    enabled: Any = True,
    api_mode: str = "chat_completions",
    cli_path: Any = "",
    timeout_sec: Any = 600,
    headers: dict[str, Any] | None = None,
    extra_body: dict[str, Any] | None = None,
    balance: dict[str, Any] | None = None,
    models: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": provider_id,
        "name": name,
        "provider_type": provider_type,
        "enabled": bool(enabled),
        "api_base": _str_value(api_base),
        "api_key": _str_value(api_key),
        "api_mode": api_mode,
        "cli_path": _str_value(cli_path),
        "timeout_sec": _positive_int(timeout_sec, 600),
        "headers": headers if isinstance(headers, dict) else {},
        "extra_body": extra_body if isinstance(extra_body, dict) else {},
        "balance": balance
        if isinstance(balance, dict)
        else {"enabled": False, "endpoint_path": "", "method": "GET"},
        "models": models or [],
    }


def _normalize_provider_id(value: Any) -> str:
    return _str_value(value).strip().lower().replace(" ", "-")


def _custom_provider_id(profile_id: Any) -> str:
    slug = _normalize_provider_id(profile_id) or "default"
    if slug.startswith("custom-"):
        return slug
    return f"custom-{slug}"


def _canonical_provider_id(provider_id: Any) -> str:
    normalized = _normalize_provider_id(provider_id)
    return _PROVIDER_CONNECTION_ALIASES.get(normalized, normalized)


def _normalize_provider_model_array(
    provider_id: str,
    models: list[Any],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in models:
        if isinstance(item, BaseModel):
            data = item.model_dump()
        elif isinstance(item, dict):
            data = dict(item)
        else:
            continue

        model_id = _str_value(
            data.get("model_id") or data.get("id") or data.get("display_name")
        ).strip()
        if not model_id or model_id in seen:
            continue
        raw_capabilities = data.get("capabilities")
        capabilities = (
            [str(value) for value in raw_capabilities] if isinstance(raw_capabilities, list) else []
        )
        model_type = _normalize_model_type(data.get("model_type"), capabilities)
        record = _provider_model_record(
            provider_id,
            model_id,
            model_type=model_type,
            capabilities=capabilities,
            display_name=data.get("display_name"),
            enabled=data.get("enabled", True),
            endpoint_path=data.get("endpoint_path"),
            default_params=data.get("default_params")
            if isinstance(data.get("default_params"), dict)
            else {},
            cli_model_name=data.get("cli_model_name"),
        )
        if record is None:
            continue
        seen.add(model_id)
        normalized.append(record)
    return normalized


def _merge_provider_model(
    models: list[dict[str, Any]],
    record: dict[str, Any] | None,
) -> None:
    if record is None:
        return
    for index, model in enumerate(models):
        if model.get("model_id") == record["model_id"]:
            models[index] = {
                **record,
                **model,
                "capabilities": _provider_model_capabilities(
                    _normalize_model_type(model.get("model_type", record["model_type"])),
                    [str(value) for value in model.get("capabilities", [])]
                    if isinstance(model.get("capabilities"), list)
                    else record["capabilities"],
                ),
            }
            return
    models.append(record)


def _provider_index(providers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(provider.get("id")): provider
        for provider in providers
        if isinstance(provider, dict) and provider.get("id")
    }


def _deleted_provider_ids(data: dict[str, Any]) -> set[str]:
    raw_ids = data.get("deleted_provider_ids")
    if not isinstance(raw_ids, list):
        return set()
    return {
        _canonical_provider_id(provider_id)
        for provider_id in raw_ids
        if _canonical_provider_id(provider_id)
    }


def _upsert_provider(
    providers: list[dict[str, Any]],
    record: dict[str, Any],
) -> dict[str, Any]:
    existing = _provider_index(providers).get(record["id"])
    if existing is None:
        providers.append(record)
        return record

    existing.update(
        {
            "name": existing.get("name") or record["name"],
            "provider_type": existing.get("provider_type") or record["provider_type"],
            "api_base": existing.get("api_base", record["api_base"]),
            "api_key": record["api_key"]
            if _looks_masked_secret(existing.get("api_key"))
            and not _looks_masked_secret(record.get("api_key"))
            else existing.get("api_key", record["api_key"]),
            "api_mode": existing.get("api_mode") or record["api_mode"],
            "cli_path": existing.get("cli_path", record["cli_path"]),
            "timeout_sec": _positive_int(existing.get("timeout_sec", record["timeout_sec"]), 600),
            "enabled": bool(existing.get("enabled", record["enabled"])),
            "headers": existing.get("headers")
            if isinstance(existing.get("headers"), dict)
            else record["headers"],
            "extra_body": existing.get("extra_body")
            if isinstance(existing.get("extra_body"), dict)
            else record["extra_body"],
            "balance": existing.get("balance")
            if isinstance(existing.get("balance"), dict)
            else record["balance"],
        }
    )
    models = existing.setdefault("models", [])
    if not isinstance(models, list):
        models = []
        existing["models"] = models
    for model in record.get("models", []):
        _merge_provider_model(models, model)
    return existing


def _service_model_record(
    connection_id: str,
    model_id: Any,
    capabilities: list[str],
    *,
    model_type: str = "llm",
    default_params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    model = _str_value(model_id).strip()
    if not model:
        return None
    normalized_type = _normalize_model_type(model_type, capabilities)
    normalized_capabilities = _normalize_model_capabilities(normalized_type, capabilities)
    return {
        "id": _model_record_id(connection_id, model),
        "connection_id": connection_id,
        "model_id": model,
        "display_name": model,
        "model_type": normalized_type,
        "capabilities": normalized_capabilities,
        "endpoint_path": _model_endpoint_path(normalized_type),
        "enabled": True,
        "default_params": default_params or {},
    }


def _default_service_models(data: dict[str, Any]) -> list[dict[str, Any]]:
    models: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(field: str, default_params: dict[str, Any] | None = None) -> None:
        connection_id, model_type, capabilities = _MODEL_FIELD_SPECS[field]
        record = _service_model_record(
            connection_id,
            data.get(field),
            capabilities,
            model_type=model_type,
            default_params=default_params,
        )
        if record is None:
            return
        key = (record["connection_id"], record["model_id"])
        if key in seen:
            return
        seen.add(key)
        models.append(record)

    add("anthropic_model")
    add("openai_model")
    add("deepseek_analyze_model")
    add("deepseek_polish_model")
    add("deepseek_summary_model")
    add("deepseek_mindmap_model")
    add("siliconflow_asr_model")
    add("vlm_model")
    add("kb_embedding_model", {"dim": data.get("kb_embedding_dim", 1024)})
    return models


def _default_provider_records(data: dict[str, Any]) -> list[dict[str, Any]]:
    providers: list[dict[str, Any]] = []

    deepseek_models: list[dict[str, Any]] = []
    for field in (
        "deepseek_analyze_model",
        "deepseek_polish_model",
        "deepseek_summary_model",
        "deepseek_mindmap_model",
    ):
        _merge_provider_model(
            deepseek_models,
            _provider_model_record(
                "deepseek",
                data.get(field),
                model_type="llm",
                capabilities=["reasoning", "json"],
            ),
        )
    providers.append(
        _provider_record(
            provider_id="deepseek",
            name="DeepSeek",
            provider_type="deepseek",
            api_base=data.get("deepseek_api_base"),
            api_key=data.get("deepseek_api_key"),
            extra_body={"thinking": {"type": "disabled"}},
            models=deepseek_models,
        )
    )

    siliconflow_models: list[dict[str, Any]] = []
    for model in data.get("service_models", []):
        if not isinstance(model, dict):
            continue
        if _canonical_provider_id(model.get("connection_id")) != "siliconflow":
            continue
        raw_capabilities = model.get("capabilities")
        capabilities = (
            [str(value) for value in raw_capabilities] if isinstance(raw_capabilities, list) else []
        )
        _merge_provider_model(
            siliconflow_models,
            _provider_model_record(
                "siliconflow",
                model.get("model_id"),
                model_type=_normalize_model_type(model.get("model_type"), capabilities),
                capabilities=capabilities,
                display_name=model.get("display_name"),
                enabled=model.get("enabled", True),
                endpoint_path=model.get("endpoint_path"),
                default_params=model.get("default_params")
                if isinstance(model.get("default_params"), dict)
                else {},
            ),
        )
    _merge_provider_model(
        siliconflow_models,
        _provider_model_record(
            "siliconflow",
            data.get("siliconflow_asr_model"),
            model_type="asr",
            capabilities=["asr"],
        ),
    )
    _merge_provider_model(
        siliconflow_models,
        _provider_model_record(
            "siliconflow",
            "TeleAI/TeleSpeechASR",
            model_type="asr",
            capabilities=["asr"],
        ),
    )
    _merge_provider_model(
        siliconflow_models,
        _provider_model_record(
            "siliconflow",
            "BAAI/bge-reranker-v2-m3",
            model_type="rerank",
            capabilities=["rerank"],
        ),
    )
    if (
        _str_value(data.get("vlm_api_base")).strip()
        == _str_value(data.get("siliconflow_api_base")).strip()
    ):
        _merge_provider_model(
            siliconflow_models,
            _provider_model_record(
                "siliconflow",
                data.get("vlm_model"),
                model_type="vlm",
                capabilities=["vision", "json"],
            ),
        )
    if (
        _str_value(data.get("kb_embedding_api_base")).strip()
        == _str_value(data.get("siliconflow_api_base")).strip()
    ):
        _merge_provider_model(
            siliconflow_models,
            _provider_model_record(
                "siliconflow",
                data.get("kb_embedding_model"),
                model_type="embedding",
                capabilities=["embedding"],
                default_params={"dim": data.get("kb_embedding_dim", 1024)},
            ),
        )
    providers.append(
        _provider_record(
            provider_id="siliconflow",
            name="SiliconFlow",
            provider_type="siliconflow",
            api_base=data.get("siliconflow_api_base"),
            api_key=data.get("siliconflow_api_key"),
            balance={"enabled": True, "endpoint_path": "/user/info", "method": "GET"},
            models=siliconflow_models,
        )
    )

    openai_model = _provider_model_record(
        "openai",
        data.get("openai_model"),
        model_type="llm",
        capabilities=["vision", "json"],
    )
    providers.append(
        _provider_record(
            provider_id="openai",
            name="OpenAI",
            provider_type="openai_compatible",
            api_base=data.get("openai_api_base"),
            api_key=data.get("openai_api_key"),
            models=[openai_model] if openai_model else [],
        )
    )

    anthropic_model = _provider_model_record(
        "anthropic",
        data.get("anthropic_model"),
        model_type="llm",
        capabilities=["reasoning", "json"],
    )
    providers.append(
        _provider_record(
            provider_id="anthropic",
            name="Anthropic",
            provider_type="anthropic",
            api_base=data.get("anthropic_api_base"),
            api_key=data.get("anthropic_api_key"),
            models=[anthropic_model] if anthropic_model else [],
        )
    )

    coding_plan_model = _provider_model_record(
        "codex-oauth",
        "default",
        display_name="CLI 当前默认模型",
        model_type="llm",
        capabilities=["reasoning", "json"],
    )
    providers.append(
        _provider_record(
            provider_id="codex-oauth",
            name="Codex OAuth",
            provider_type="codex_oauth",
            api_mode="oauth_cli",
            timeout_sec=600,
            models=[coding_plan_model] if coding_plan_model else [],
        )
    )

    agy_model = _provider_model_record(
        "agy-oauth",
        "default",
        display_name="CLI 当前默认模型",
        model_type="llm",
        capabilities=["reasoning", "json"],
    )
    providers.append(
        _provider_record(
            provider_id="agy-oauth",
            name="Antigravity OAuth",
            provider_type="agy_oauth",
            api_mode="oauth_cli",
            timeout_sec=600,
            models=[agy_model] if agy_model else [],
        )
    )

    for profile in _coerce_custom_profiles(data.get("custom_llm_profiles")):
        provider_id = _custom_provider_id(profile["id"])
        model = _provider_model_record(
            provider_id,
            profile.get("model"),
            model_type="llm",
            capabilities=["json"],
        )
        providers.append(
            _provider_record(
                provider_id=provider_id,
                name=profile.get("name") or "Custom",
                provider_type="openai_compatible",
                api_base=profile.get("api_base"),
                api_key=profile.get("api_key"),
                models=[model] if model else [],
            )
        )

    if data.get("vlm_api_base"):
        model = _provider_model_record(
            "custom-vision-default",
            data.get("vlm_model"),
            model_type="vlm",
            capabilities=["vision", "json"],
        )
        providers.append(
            _provider_record(
                provider_id="custom-vision-default",
                name="Vision API",
                provider_type="openai_compatible",
                api_base=data.get("vlm_api_base"),
                api_key=data.get("vlm_api_key"),
                models=[model] if model else [],
            )
        )

    if data.get("kb_embedding_api_base"):
        model = _provider_model_record(
            "custom-embedding-default",
            data.get("kb_embedding_model"),
            model_type="embedding",
            capabilities=["embedding"],
            default_params={"dim": data.get("kb_embedding_dim", 1024)},
        )
        providers.append(
            _provider_record(
                provider_id="custom-embedding-default",
                name="Knowledge Base Embedding",
                provider_type="openai_compatible",
                api_base=data.get("kb_embedding_api_base"),
                api_key=data.get("kb_embedding_api_key"),
                models=[model] if model else [],
            )
        )

    return providers


def _provider_from_service_connection(
    data: dict[str, Any],
    connection: dict[str, Any],
    service_models: list[dict[str, Any]],
) -> dict[str, Any] | None:
    raw_id = _str_value(connection.get("id")).strip()
    provider_id = _canonical_provider_id(raw_id)
    if not provider_id:
        return None

    provider_type = _str_value(
        connection.get("endpoint_type") or connection.get("provider") or "openai_compatible"
    )
    if provider_id == "deepseek":
        provider_type = "deepseek"
    elif provider_id == "siliconflow":
        provider_type = "siliconflow"

    models: list[dict[str, Any]] = []
    for model in service_models:
        if not isinstance(model, dict):
            continue
        if _canonical_provider_id(model.get("connection_id")) != provider_id:
            continue
        raw_capabilities = model.get("capabilities")
        capabilities = (
            [str(value) for value in raw_capabilities] if isinstance(raw_capabilities, list) else []
        )
        _merge_provider_model(
            models,
            _provider_model_record(
                provider_id,
                model.get("model_id"),
                model_type=_normalize_model_type(model.get("model_type"), capabilities),
                capabilities=capabilities,
                display_name=model.get("display_name"),
                enabled=model.get("enabled", True),
                endpoint_path=model.get("endpoint_path"),
                default_params=model.get("default_params")
                if isinstance(model.get("default_params"), dict)
                else {},
            ),
        )

    return _provider_record(
        provider_id=provider_id,
        name=_str_value(connection.get("name") or provider_id),
        provider_type=provider_type,
        api_base=connection.get("api_base"),
        api_key=connection.get("api_key"),
        enabled=connection.get("enabled", True),
        headers=connection.get("headers") if isinstance(connection.get("headers"), dict) else {},
        models=models,
        balance={"enabled": True, "endpoint_path": "/user/info", "method": "GET"}
        if provider_id == "siliconflow"
        else None,
    )


def _ensure_service_connections(data: dict[str, Any]) -> list[dict[str, Any]]:
    connections = data.get("service_connections")
    if not isinstance(connections, list) or not connections:
        connections = _default_service_connections(data)
        data["service_connections"] = connections
    return connections


def _ensure_service_models(data: dict[str, Any]) -> list[dict[str, Any]]:
    models = data.get("service_models")
    if not isinstance(models, list) or not models:
        models = _default_service_models(data)
        data["service_models"] = models
    return models


def _normalize_service_model_array(models: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in models:
        if isinstance(item, BaseModel):
            data = item.model_dump()
        elif isinstance(item, dict):
            data = dict(item)
        else:
            continue

        connection_id = _str_value(data.get("connection_id")).strip()
        model_id = _str_value(data.get("model_id") or data.get("display_name")).strip()
        if not connection_id or not model_id:
            continue

        raw_capabilities = data.get("capabilities")
        capabilities = raw_capabilities if isinstance(raw_capabilities, list) else []
        model_type = _normalize_model_type(
            data.get("model_type"), [str(item) for item in capabilities]
        )
        data["id"] = _str_value(data.get("id") or _model_record_id(connection_id, model_id))
        data["connection_id"] = connection_id
        data["model_id"] = model_id
        data["display_name"] = _str_value(data.get("display_name") or model_id)
        data["model_type"] = model_type
        data["capabilities"] = _normalize_model_capabilities(
            model_type,
            [str(item) for item in capabilities],
        )
        data["endpoint_path"] = _str_value(
            data.get("endpoint_path") or _model_endpoint_path(model_type)
        )
        data["enabled"] = bool(data.get("enabled", True))
        if not isinstance(data.get("default_params"), dict):
            data["default_params"] = {}
        normalized.append(data)
    return normalized


def _normalize_provider_array(providers: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(providers):
        if isinstance(item, BaseModel):
            data = item.model_dump()
        elif isinstance(item, dict):
            data = dict(item)
        else:
            continue

        provider_id = _canonical_provider_id(data.get("id") or f"provider-{index + 1}")
        if not provider_id:
            continue
        if provider_id in seen:
            provider_id = f"{provider_id}-{index + 1}"
        seen.add(provider_id)

        provider_type = _str_value(
            data.get("provider_type") or data.get("endpoint_type") or "openai_compatible"
        )
        if provider_id == "deepseek":
            provider_type = "deepseek"
        elif provider_id == "siliconflow":
            provider_type = "siliconflow"
        balance = data.get("balance") if isinstance(data.get("balance"), dict) else {}
        if provider_id == "siliconflow":
            balance = {"enabled": True, "endpoint_path": "/user/info", "method": "GET", **balance}

        normalized.append(
            _provider_record(
                provider_id=provider_id,
                name=_str_value(data.get("name") or provider_id),
                provider_type=provider_type,
                enabled=data.get("enabled", True),
                api_base=data.get("api_base"),
                api_key=data.get("api_key"),
                api_mode=_str_value(data.get("api_mode") or "chat_completions"),
                cli_path=data.get("cli_path"),
                timeout_sec=data.get("timeout_sec", 600),
                headers=data.get("headers") if isinstance(data.get("headers"), dict) else {},
                extra_body=data.get("extra_body")
                if isinstance(data.get("extra_body"), dict)
                else {},
                balance=balance,
                models=_normalize_provider_model_array(
                    provider_id,
                    data.get("models") if isinstance(data.get("models"), list) else [],
                ),
            )
        )
    return normalized


def _ensure_providers(data: dict[str, Any]) -> list[dict[str, Any]]:
    deleted = _deleted_provider_ids(data)
    providers = data.get("providers")
    if isinstance(providers, list) and providers:
        normalized = [
            provider
            for provider in _normalize_provider_array(providers)
            if _canonical_provider_id(provider.get("id")) not in deleted
        ]
    else:
        normalized = []

    for record in _default_provider_records(data):
        if _canonical_provider_id(record.get("id")) in deleted:
            continue
        _upsert_provider(normalized, record)

    service_connections = data.get("service_connections")
    service_models = data.get("service_models")
    if isinstance(service_connections, list) and isinstance(service_models, list):
        normalized_service_models = _normalize_service_model_array(service_models)
        for connection in service_connections:
            if not isinstance(connection, dict):
                continue
            record = _provider_from_service_connection(data, connection, normalized_service_models)
            if record is not None:
                if _canonical_provider_id(record.get("id")) in deleted:
                    continue
                _upsert_provider(normalized, record)

    data["providers"] = normalized
    data["deleted_provider_ids"] = sorted(deleted)
    return normalized


def _find_provider(data: dict[str, Any], provider_id: Any) -> dict[str, Any] | None:
    canonical_id = _canonical_provider_id(provider_id)
    for provider in _ensure_providers(data):
        if provider.get("id") == canonical_id:
            return provider
    return None


def _find_provider_model(
    provider: dict[str, Any] | None,
    model_id: Any = "",
    capability: str = "",
) -> dict[str, Any] | None:
    if provider is None:
        return None
    models = provider.get("models")
    if not isinstance(models, list):
        return None
    requested_model = _str_value(model_id).strip()
    requested_capability = _str_value(capability).strip().lower()
    for model in models:
        if not isinstance(model, dict):
            continue
        if requested_model and _str_value(model.get("model_id")) != requested_model:
            continue
        if requested_capability:
            caps = {str(value).strip().lower() for value in model.get("capabilities", [])}
            model_type = _str_value(model.get("model_type")).strip().lower()
            if requested_capability not in caps and requested_capability != model_type:
                continue
        return model
    return None


def _provider_model_to_service_model(
    provider_id: str,
    model: dict[str, Any],
) -> dict[str, Any] | None:
    model_type = _normalize_model_type(
        model.get("model_type"), [str(value) for value in model.get("capabilities", [])]
    )
    service_connection_id = "siliconflow-asr" if provider_id == "siliconflow" else provider_id
    if provider_id == "custom-vision-default":
        service_connection_id = "vision-default"
    elif provider_id == "custom-embedding-default":
        service_connection_id = "embedding-default"
    return _service_model_record(
        service_connection_id,
        model.get("model_id"),
        _normalize_model_capabilities(
            model_type,
            [
                capability
                for capability in [str(value) for value in model.get("capabilities", [])]
                if capability not in {"llm", "vlm"}
            ],
        ),
        model_type=model_type,
        default_params=model.get("default_params")
        if isinstance(model.get("default_params"), dict)
        else {},
    )


def _sync_service_registry_from_providers(data: dict[str, Any]) -> None:
    providers = _ensure_providers(data)
    existing_connections = data.get("service_connections")
    preserve_connections = isinstance(existing_connections, list) and bool(existing_connections)
    if not preserve_connections:
        data["service_connections"] = _default_service_connections(data)

    existing_models = data.get("service_models")
    if not isinstance(existing_models, list) or not existing_models:
        service_models: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for provider in providers:
            if not isinstance(provider, dict):
                continue
            provider_id = str(provider.get("id"))
            for model in provider.get("models", []):
                if not isinstance(model, dict):
                    continue
                record = _provider_model_to_service_model(provider_id, model)
                if record is None:
                    continue
                key = (record["connection_id"], record["model_id"])
                if key in seen:
                    continue
                seen.add(key)
                service_models.append(record)
        data["service_models"] = service_models or _default_service_models(data)


def _sync_provider_flat_fields(data: dict[str, Any]) -> None:
    providers = _ensure_providers(data)
    by_id = _provider_index(providers)
    for provider_id, fields in _PROVIDER_FLAT_KEYS.items():
        provider = by_id.get(provider_id)
        if provider is None:
            continue
        if fields.get("api_base"):
            data[fields["api_base"]] = provider.get("api_base", "")
        if fields.get("api_key"):
            provider_api_key = provider.get("api_key", "")
            if _looks_masked_secret(provider_api_key):
                existing_api_key = data.get(fields["api_key"], "")
                if not _looks_masked_secret(existing_api_key):
                    provider["api_key"] = existing_api_key
            else:
                data[fields["api_key"]] = provider_api_key
        model_field = fields.get("model")
        if model_field:
            capability = "asr" if provider_id == "siliconflow" else ""
            if provider_id == "custom-vision-default":
                capability = "vision"
            elif provider_id == "custom-embedding-default":
                capability = "embedding"
            model = _find_provider_model(provider, capability=capability) or _find_provider_model(
                provider
            )
            if model is not None:
                data[model_field] = model.get("model_id", data.get(model_field, ""))
