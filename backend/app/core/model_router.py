"""Pure model binding helpers for backend routing decisions.

The helpers in this module do not create clients, read global settings, or
start pipeline work. They convert a RuntimeSettings instance plus task options
into explicit binding records that services can inspect or test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.settings import RuntimeSettings

LLM_STAGES = {"analyze", "polish", "summary", "mindmap"}
ASR_PROVIDERS = {"qwen3", "qwen3_gguf", "siliconflow"}

_DEFAULT_QWEN3_ASR_MODEL = "Qwen/Qwen3-ASR-1.7B"
_DEFAULT_QWEN3_GGUF_REPO = "ggml-org/Qwen3-ASR-1.7B-GGUF:Q8_0"
_DEFAULT_QWEN3_GGUF_ALIAS = "Qwen3-ASR-1.7B"
_DEFAULT_DEEPSEEK_API_BASE = "https://api.deepseek.com"
_DEFAULT_SILICONFLOW_VLM_MODEL = "Qwen/Qwen3.5-4B"
_LEGACY_VLM_DEFAULT_MODELS = {"qwen2.5-vl-7b-instruct"}

_MODEL_TYPE_ENDPOINT_PATHS = {
    "llm": "/chat/completions",
    "vlm": "/chat/completions",
    "embedding": "/embeddings",
    "rerank": "/rerank",
    "asr": "/audio/transcriptions",
}

_MODEL_TYPE_API_KINDS = {
    "llm": "chat",
    "vlm": "vision_chat",
    "embedding": "embedding",
    "rerank": "rerank",
    "asr": "audio_transcription",
}


@dataclass(frozen=True)
class LLMBinding:
    """Resolved LLM call target and provider-specific request kwargs."""

    provider: str
    stage: str
    transport: str
    model: str = ""
    api_base: str = ""
    api_key: str = ""
    configured: bool = True
    reason: str = ""
    fallback_from: str = ""
    request_kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ASRBinding:
    """Resolved ASR target for a task's transcription step."""

    provider: str
    source: str
    model: str
    api_base: str = ""
    api_key: str = ""
    language: str | None = None
    diarize: bool = True
    num_speakers: int | None = None
    chunk_strategy: str | None = None
    configured: bool = True
    reason: str = ""
    request_kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EndpointBinding:
    """Resolved OpenAI-compatible endpoint for VLM or embeddings."""

    capability: str
    model: str
    api_base: str
    api_key: str
    configured: bool
    enabled: bool = True
    reason: str = ""
    request_kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceModelBinding:
    """Resolved registry model endpoint for provider-level model lists."""

    connection_id: str
    model_id: str
    model_type: str
    api_base: str
    api_key: str
    endpoint: str
    configured: bool
    enabled: bool = True
    reason: str = ""
    request_kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderModelBinding:
    """Resolved provider inventory model endpoint."""

    provider_id: str
    provider_type: str
    model_id: str
    model_type: str
    api_base: str
    api_key: str
    endpoint: str
    configured: bool
    enabled: bool = True
    reason: str = ""
    request_kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineModelBindings:
    """Resolved model-facing branches for a pipeline task."""

    branch: str
    transcript_source: str
    run_separation: bool
    run_asr: bool
    run_subtitle_processor: bool
    run_polish: bool
    run_analysis: bool
    run_vlm: bool
    run_kb_index: bool
    asr: ASRBinding | None = None
    polish: LLMBinding | None = None
    llm_stages: dict[str, LLMBinding] = field(default_factory=dict)
    vlm: EndpointBinding | None = None
    embedding: EndpointBinding | None = None
    notes: dict[str, Any] = field(default_factory=dict)


def _clean_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _normalize_provider(provider: str | None) -> str:
    return _clean_text(provider).lower()


def _looks_like_gguf_path(value: str) -> bool:
    normalized = value.replace("\\", "/").lower()
    return (
        normalized.endswith(".gguf")
        or normalized.startswith("/")
        or normalized.startswith("~/")
        or ":/" in normalized
    )


def _normalize_stage(stage: str) -> str:
    normalized = _clean_text(stage).lower() or "polish"
    return normalized if normalized in LLM_STAGES else "polish"


def _openai_compatible_api_key(value: str) -> str:
    return value or "not-needed"


def _active_custom_profile(rt: RuntimeSettings) -> dict[str, str]:
    """Return the active custom profile, falling back to legacy custom_* fields."""

    profiles: list[dict[str, str]] = []
    for profile in rt.custom_llm_profiles:
        data = profile.model_dump() if hasattr(profile, "model_dump") else dict(profile)
        profiles.append(
            {
                "id": _clean_text(data.get("id")),
                "name": _clean_text(data.get("name")),
                "api_base": _clean_text(data.get("api_base")),
                "model": _clean_text(data.get("model")),
                "api_key": _clean_text(data.get("api_key")),
            }
        )

    if profiles:
        active_id = _clean_text(rt.custom_active_profile_id)
        active = next((profile for profile in profiles if profile["id"] == active_id), profiles[0])
        return active

    return {
        "id": _clean_text(rt.custom_active_profile_id) or "default",
        "name": _clean_text(rt.custom_name) or "Custom",
        "api_base": _clean_text(rt.custom_api_base),
        "model": _clean_text(rt.custom_model),
        "api_key": _clean_text(rt.custom_api_key),
    }


def _deepseek_stage_values(rt: RuntimeSettings, stage: str) -> tuple[str, str, str]:
    stage = _normalize_stage(stage)
    stage_map = {
        "analyze": (
            rt.deepseek_analyze_model,
            rt.deepseek_analyze_thinking,
            rt.deepseek_analyze_effort,
        ),
        "polish": (
            rt.deepseek_polish_model,
            rt.deepseek_polish_thinking,
            rt.deepseek_polish_effort,
        ),
        "summary": (
            rt.deepseek_summary_model,
            rt.deepseek_summary_thinking,
            rt.deepseek_summary_effort,
        ),
        "mindmap": (
            rt.deepseek_mindmap_model,
            rt.deepseek_mindmap_thinking,
            rt.deepseek_mindmap_effort,
        ),
    }
    return stage_map[stage]


def resolve_deepseek_llm_binding(rt: RuntimeSettings, stage: str = "polish") -> LLMBinding:
    """Resolve DeepSeek native v4 options for a given LLM stage."""

    normalized_stage = _normalize_stage(stage)
    model, thinking, effort = _deepseek_stage_values(rt, normalized_stage)
    stage_binding = _stage_runtime_binding(rt, normalized_stage)
    if _canonical_provider_id(stage_binding.get("provider_id")) == "deepseek" and stage_binding.get("model_id"):
        model = stage_binding["model_id"]
    thinking_type = "enabled" if _normalize_provider(thinking) == "enabled" else "disabled"
    api_base = _clean_text(rt.deepseek_api_base) or _DEFAULT_DEEPSEEK_API_BASE
    request_kwargs: dict[str, Any] = {
        "model": model,
        "api_key": rt.deepseek_api_key,
        "api_base": api_base,
        "extra_body": {"thinking": {"type": thinking_type}},
    }
    if thinking_type == "enabled" and effort:
        request_kwargs["reasoning_effort"] = effort

    configured = bool(rt.deepseek_api_key and model)
    return LLMBinding(
        provider="deepseek",
        stage=normalized_stage,
        transport="openai_sdk",
        model=model,
        api_base=api_base,
        api_key=rt.deepseek_api_key,
        configured=configured,
        reason="" if configured else "deepseek_api_key or stage model is empty",
        request_kwargs=request_kwargs,
    )


def resolve_llm_binding(
    rt: RuntimeSettings,
    provider_override: str = "",
    stage: str = "polish",
) -> LLMBinding:
    """Resolve an LLM provider binding without applying polish-local fallback."""

    normalized_stage = _normalize_stage(stage)
    stage_binding = _stage_runtime_binding(rt, normalized_stage) if not provider_override else {}
    provider = (
        _canonical_provider_id(provider_override)
        or _canonical_provider_id(stage_binding.get("provider_id"))
        or _normalize_provider(rt.llm_provider)
    )
    bound_model = _clean_text(stage_binding.get("model_id")) if stage_binding else ""

    provider_binding = _llm_binding_from_provider(rt, provider, bound_model, normalized_stage)
    if provider_binding is not None:
        return provider_binding

    if provider == "local":
        model_path = _clean_text(rt.local_llm_model_path)
        return LLMBinding(
            provider="local",
            stage=normalized_stage,
            transport="local",
            model=model_path,
            configured=bool(model_path),
            reason="" if model_path else "local_llm_model_path is empty",
            request_kwargs={
                "model_path": model_path,
                "device": rt.local_llm_device,
                "dtype": rt.local_llm_dtype,
                "max_new_tokens": rt.local_llm_max_new_tokens,
            },
        )

    if provider == "deepseek":
        return resolve_deepseek_llm_binding(rt, normalized_stage)

    if provider == "anthropic":
        model_name = bound_model or rt.anthropic_model or "claude-sonnet-4-6"
        request_kwargs: dict[str, Any] = {
            "model": f"anthropic/{model_name}",
            "api_key": rt.anthropic_api_key,
            "num_retries": 3,
        }
        if rt.anthropic_api_base:
            request_kwargs["api_base"] = rt.anthropic_api_base
        configured = bool(rt.anthropic_api_key and model_name)
        return LLMBinding(
            provider="anthropic",
            stage=normalized_stage,
            transport="litellm",
            model=request_kwargs["model"],
            api_base=rt.anthropic_api_base,
            api_key=rt.anthropic_api_key,
            configured=configured,
            reason="" if configured else "anthropic_api_key or anthropic_model is empty",
            request_kwargs=request_kwargs,
        )

    if provider == "openai":
        model_name = bound_model or rt.openai_model or "gpt-4o"
        request_kwargs = {
            "model": f"openai/{model_name}",
            "api_key": rt.openai_api_key,
            "num_retries": 3,
        }
        if rt.openai_api_base:
            request_kwargs["api_base"] = rt.openai_api_base
        configured = bool(rt.openai_api_key and model_name)
        return LLMBinding(
            provider="openai",
            stage=normalized_stage,
            transport="litellm",
            model=request_kwargs["model"],
            api_base=rt.openai_api_base,
            api_key=rt.openai_api_key,
            configured=configured,
            reason="" if configured else "openai_api_key or openai_model is empty",
            request_kwargs=request_kwargs,
        )

    if provider == "custom":
        profile = _active_custom_profile(rt)
        api_base = profile["api_base"]
        model_name = bound_model or profile["model"]
        api_key = _openai_compatible_api_key(profile["api_key"])
        configured = bool(api_base and model_name)
        return LLMBinding(
            provider="custom",
            stage=normalized_stage,
            transport="litellm",
            model=f"openai/{model_name}" if model_name else "",
            api_base=api_base,
            api_key=api_key,
            configured=configured,
            reason="" if configured else "custom api_base or model is empty",
            request_kwargs={
                "model": f"openai/{model_name}" if model_name else "",
                "api_key": api_key,
                "api_base": api_base,
                "custom_llm_provider": "openai",
                "num_retries": 3,
            },
        )

    return LLMBinding(
        provider=provider or "unknown",
        stage=normalized_stage,
        transport="disabled",
        configured=False,
        reason=f"unsupported llm provider: {provider or 'empty'}",
    )


def resolve_polish_llm_binding(rt: RuntimeSettings) -> LLMBinding:
    """Resolve the provider used by polish_text, including local fallback."""

    stage_binding = _stage_runtime_binding(rt, "polish")
    bound_provider = _canonical_provider_id(stage_binding.get("provider_id"))
    if bound_provider and bound_provider != "local":
        return resolve_llm_binding(rt, stage="polish")

    polish_provider = _normalize_provider(rt.polish_provider)
    if polish_provider == "local":
        local = resolve_llm_binding(rt, provider_override="local", stage="polish")
        if local.configured:
            return local
        fallback = resolve_llm_binding(rt, stage="polish")
        if fallback.provider != "local":
            return LLMBinding(
                provider=fallback.provider,
                stage=fallback.stage,
                transport=fallback.transport,
                model=fallback.model,
                api_base=fallback.api_base,
                api_key=fallback.api_key,
                configured=fallback.configured,
                reason=fallback.reason,
                fallback_from="local",
                request_kwargs=fallback.request_kwargs,
            )
        return local

    return resolve_llm_binding(rt, provider_override=polish_provider, stage="polish")


def _normalize_siliconflow_base(api_base: str) -> str:
    base = api_base.rstrip("/")
    if base and not base.endswith("/v1") and "/v" not in base:
        return f"{base}/v1"
    return base


def _base_with_optional_v1(api_base: str) -> str:
    base = _clean_text(api_base).rstrip("/")
    if base and not base.endswith("/v1") and "/v" not in base:
        return f"{base}/v1"
    return base


def _endpoint_for_model_type(api_base: str, model_type: str, endpoint_path: str = "") -> str:
    base = _base_with_optional_v1(api_base)
    path = endpoint_path or _MODEL_TYPE_ENDPOINT_PATHS.get(model_type, "/chat/completions")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}" if base else ""


def _model_type_from_capabilities(model_type: Any, capabilities: Any) -> str:
    normalized = _normalize_provider(str(model_type or ""))
    if normalized in _MODEL_TYPE_ENDPOINT_PATHS:
        return normalized
    caps = {str(capability).strip().lower() for capability in capabilities or []}
    if "asr" in caps:
        return "asr"
    if "rerank" in caps:
        return "rerank"
    if "embedding" in caps:
        return "embedding"
    if "vision" in caps:
        return "vlm"
    return "llm"


def _canonical_provider_id(provider_id: Any) -> str:
    normalized = _normalize_provider(str(provider_id or "")).replace(" ", "-")
    aliases = {
        "siliconflow-asr": "siliconflow",
        "vision-default": "custom-vision-default",
        "embedding-default": "custom-embedding-default",
    }
    return aliases.get(normalized, normalized)


def _runtime_binding(rt: RuntimeSettings, key: str) -> dict[str, str]:
    bindings = rt.model_dump().get("runtime_model_bindings", {})
    if not isinstance(bindings, dict):
        return {}
    item = bindings.get(key)
    if isinstance(item, dict):
        return {
            "provider_id": _clean_text(item.get("provider_id")),
            "model_id": _clean_text(item.get("model_id")),
            "capability": _clean_text(item.get("capability")),
        }
    return {}


def _stage_runtime_binding(rt: RuntimeSettings, stage: str) -> dict[str, str]:
    normalized_stage = _normalize_stage(stage)
    item = _runtime_binding(rt, normalized_stage)
    if item:
        return item
    if normalized_stage == "polish":
        return _runtime_binding(rt, "subtitle_polish")
    return {}


def _provider_models(provider: dict[str, Any]) -> list[dict[str, Any]]:
    models = provider.get("models")
    return [model for model in models if isinstance(model, dict)] if isinstance(models, list) else []


def _find_provider(rt: RuntimeSettings, provider_id: str) -> dict[str, Any] | None:
    requested = _canonical_provider_id(provider_id)
    providers = rt.model_dump().get("providers", [])
    if not isinstance(providers, list):
        return None
    for provider in providers:
        if isinstance(provider, dict) and _canonical_provider_id(provider.get("id")) == requested:
            return provider
    return None


def _find_provider_model(
    provider: dict[str, Any] | None,
    model_id: str = "",
    capability: str = "",
) -> dict[str, Any] | None:
    if provider is None:
        return None
    requested_model = _clean_text(model_id)
    requested_capability = _clean_text(capability).lower()
    for model in _provider_models(provider):
        if requested_model and _clean_text(model.get("model_id")) != requested_model:
            continue
        if requested_capability:
            caps = {str(value).strip().lower() for value in model.get("capabilities", [])}
            model_type = _clean_text(model.get("model_type")).lower()
            if requested_capability not in caps and requested_capability != model_type:
                continue
        return model
    return None


def resolve_provider_model_binding(
    rt: RuntimeSettings,
    provider_id: str,
    model_id: str = "",
    capability: str = "",
) -> ProviderModelBinding:
    """Resolve a provider/model inventory selection into endpoint details."""

    requested_provider = _canonical_provider_id(provider_id)
    provider = _find_provider(rt, requested_provider)
    if provider is None:
        return ProviderModelBinding(
            provider_id=requested_provider,
            provider_type="",
            model_id=_clean_text(model_id),
            model_type="llm",
            api_base="",
            api_key="",
            endpoint="",
            configured=False,
            enabled=False,
            reason="provider not found",
        )

    model = _find_provider_model(provider, model_id, capability)
    if model is None:
        return ProviderModelBinding(
            provider_id=requested_provider,
            provider_type=_clean_text(provider.get("provider_type")),
            model_id=_clean_text(model_id),
            model_type="llm",
            api_base=_base_with_optional_v1(_clean_text(provider.get("api_base"))),
            api_key=_openai_compatible_api_key(_clean_text(provider.get("api_key"))),
            endpoint="",
            configured=False,
            enabled=False,
            reason="provider model not found",
        )

    model_type = _model_type_from_capabilities(model.get("model_type"), model.get("capabilities"))
    api_base = _base_with_optional_v1(_clean_text(provider.get("api_base")))
    api_key = _openai_compatible_api_key(_clean_text(provider.get("api_key")))
    endpoint = _endpoint_for_model_type(api_base, model_type, _clean_text(model.get("endpoint_path")))
    enabled = bool(provider.get("enabled", True) and model.get("enabled", True))
    resolved_model = _clean_text(model.get("model_id"))
    configured = bool(enabled and resolved_model and api_base)
    return ProviderModelBinding(
        provider_id=requested_provider,
        provider_type=_clean_text(provider.get("provider_type")),
        model_id=resolved_model,
        model_type=model_type,
        api_base=api_base,
        api_key=api_key,
        endpoint=endpoint,
        configured=configured,
        enabled=enabled,
        reason="" if configured else "provider or model is disabled or incomplete",
        request_kwargs={
            "model": resolved_model,
            "api_kind": _MODEL_TYPE_API_KINDS.get(model_type, "chat"),
            "endpoint_path": _clean_text(model.get("endpoint_path"))
            or _MODEL_TYPE_ENDPOINT_PATHS.get(model_type, "/chat/completions"),
            "headers": provider.get("headers") if isinstance(provider.get("headers"), dict) else {},
            "extra_body": provider.get("extra_body") if isinstance(provider.get("extra_body"), dict) else {},
            "default_params": model.get("default_params") if isinstance(model.get("default_params"), dict) else {},
        },
    )


def _llm_binding_from_provider(
    rt: RuntimeSettings,
    provider_id: str,
    model_id: str,
    stage: str,
) -> LLMBinding | None:
    provider_id = _canonical_provider_id(provider_id)
    if not provider_id or provider_id in {"local", "deepseek", "anthropic", "openai", "custom"}:
        return None
    binding = resolve_provider_model_binding(rt, provider_id, model_id, "llm")
    if not binding.configured:
        return LLMBinding(
            provider=provider_id,
            stage=_normalize_stage(stage),
            transport="litellm",
            model=binding.model_id,
            api_base=binding.api_base,
            api_key=binding.api_key,
            configured=False,
            reason=binding.reason,
            request_kwargs={},
        )
    return LLMBinding(
        provider=provider_id,
        stage=_normalize_stage(stage),
        transport="litellm",
        model=f"openai/{binding.model_id}" if binding.model_id else "",
        api_base=binding.api_base,
        api_key=binding.api_key,
        configured=True,
        request_kwargs={
            "model": f"openai/{binding.model_id}",
            "api_key": binding.api_key,
            "api_base": binding.api_base,
            "custom_llm_provider": "openai",
            "num_retries": 3,
            **binding.request_kwargs.get("default_params", {}),
        },
    )


def resolve_service_model_binding(
    rt: RuntimeSettings,
    connection_id: str,
    model_id: str,
) -> ServiceModelBinding:
    """Resolve a typed service registry model into an API endpoint snapshot."""

    requested_connection = _clean_text(connection_id)
    requested_model = _clean_text(model_id)
    settings_data = rt.model_dump()
    connections = settings_data.get("service_connections", [])
    models = settings_data.get("service_models", [])

    connection = next(
        (
            item
            for item in connections
            if isinstance(item, dict) and _clean_text(item.get("id")) == requested_connection
        ),
        None,
    )
    model = next(
        (
            item
            for item in models
            if (
                isinstance(item, dict)
                and _clean_text(item.get("connection_id")) == requested_connection
                and _clean_text(item.get("model_id")) == requested_model
            )
        ),
        None,
    )

    if connection is None or model is None:
        missing = "connection" if connection is None else "model"
        return ServiceModelBinding(
            connection_id=requested_connection,
            model_id=requested_model,
            model_type="llm",
            api_base="",
            api_key="",
            endpoint="",
            configured=False,
            enabled=False,
            reason=f"service {missing} not found",
        )

    model_type = _model_type_from_capabilities(model.get("model_type"), model.get("capabilities"))
    api_base = _base_with_optional_v1(_clean_text(connection.get("api_base")))
    api_key = _openai_compatible_api_key(_clean_text(connection.get("api_key")))
    endpoint = _endpoint_for_model_type(
        api_base,
        model_type,
        _clean_text(model.get("endpoint_path")),
    )
    enabled = bool(connection.get("enabled", True) and model.get("enabled", True))
    configured = bool(enabled and api_base and requested_model)
    return ServiceModelBinding(
        connection_id=requested_connection,
        model_id=requested_model,
        model_type=model_type,
        api_base=api_base,
        api_key=api_key,
        endpoint=endpoint,
        configured=configured,
        enabled=enabled,
        reason="" if configured else "service connection or model is disabled or incomplete",
        request_kwargs={
            "model": requested_model,
            "api_kind": _MODEL_TYPE_API_KINDS.get(model_type, "chat"),
            "endpoint_path": _MODEL_TYPE_ENDPOINT_PATHS.get(model_type, "/chat/completions"),
            "default_params": model.get("default_params") if isinstance(model.get("default_params"), dict) else {},
        },
    )


def resolve_asr_binding(
    rt: RuntimeSettings,
    task_options: dict[str, Any] | None = None,
    language: str | None = None,
) -> ASRBinding:
    """Resolve the ASR provider selected by task options and runtime settings."""

    options = task_options or {}
    option_provider = _normalize_provider(options.get("asr_provider"))
    asr_runtime_binding = _runtime_binding(rt, "asr")
    if option_provider:
        provider = option_provider
        source = "task_option"
    elif bool(options.get("api_flow", False)):
        provider = "siliconflow"
        source = "api_flow"
    elif asr_runtime_binding.get("provider_id"):
        provider = _canonical_provider_id(asr_runtime_binding.get("provider_id"))
        source = "runtime_binding"
    else:
        provider = _normalize_provider(rt.asr_provider)
        source = "settings"

    if provider not in ASR_PROVIDERS:
        supported = ", ".join(sorted(ASR_PROVIDERS))
        raise ValueError(f"Unsupported ASR provider '{provider}'. Supported providers: {supported}")

    num_speakers = options.get("num_speakers")
    if provider == "qwen3":
        model = rt.qwen3_asr_model_path or _DEFAULT_QWEN3_ASR_MODEL
        diarize = bool(rt.enable_diarization and not options.get("disable_diarization", False))
        return ASRBinding(
            provider="qwen3",
            source=source,
            model=model,
            language=language,
            diarize=diarize,
            num_speakers=num_speakers,
            configured=True,
            request_kwargs={
                "model_path": model,
                "aligner_model_path": rt.qwen3_aligner_model_path or None,
                "enable_timestamps": rt.qwen3_enable_timestamps,
                "batch_size": rt.qwen3_batch_size,
                "max_new_tokens": rt.qwen3_max_new_tokens,
                "device": rt.qwen3_device,
                "language": language,
                "diarize": diarize,
                "num_speakers": num_speakers,
            },
        )

    if provider == "qwen3_gguf":
        bound_model = _clean_text(asr_runtime_binding.get("model_id"))
        model_path = _clean_text(rt.qwen3_gguf_model_path)
        mmproj_path = _clean_text(rt.qwen3_gguf_mmproj_path)
        hf_repo = _clean_text(rt.qwen3_gguf_hf_repo) or _DEFAULT_QWEN3_GGUF_REPO
        if bound_model and not model_path and bound_model != _DEFAULT_QWEN3_GGUF_ALIAS:
            if _looks_like_gguf_path(bound_model):
                model_path = bound_model
            else:
                hf_repo = bound_model
        has_local_pair = bool(model_path and mmproj_path)
        has_partial_local = bool(model_path) != bool(mmproj_path)
        configured = has_local_pair or (not has_partial_local and bool(hf_repo))
        reason = ""
        if has_partial_local:
            reason = "qwen3_gguf_model_path and qwen3_gguf_mmproj_path must be set together"
        elif not configured:
            reason = "qwen3_gguf_hf_repo is empty"
        chunk_strategy = _normalize_provider(
            options.get("asr_chunk_strategy") or rt.qwen3_gguf_chunk_strategy
        )
        return ASRBinding(
            provider="qwen3_gguf",
            source=source,
            model=model_path or hf_repo,
            language=language,
            diarize=False,
            num_speakers=num_speakers,
            chunk_strategy=chunk_strategy,
            configured=configured,
            reason=reason,
            request_kwargs={
                "binary_path": rt.llama_cpp_binary_path,
                "model_path": model_path,
                "mmproj_path": mmproj_path,
                "hf_repo": hf_repo,
                "device": rt.qwen3_gguf_device,
                "ctx": rt.qwen3_gguf_ctx,
                "n_gpu_layers": rt.qwen3_gguf_n_gpu_layers,
                "timeout_sec": rt.qwen3_gguf_timeout_sec,
                "keepalive_sec": rt.qwen3_gguf_keepalive_sec,
                "chunk_strategy": chunk_strategy,
                "max_chunk_sec": 30.0,
                "silero_onnx_model_path": rt.silero_onnx_model_path,
                "alias": _DEFAULT_QWEN3_GGUF_ALIAS,
            },
        )

    provider_binding: ProviderModelBinding | None = None
    if (
        provider == "siliconflow"
        and _canonical_provider_id(asr_runtime_binding.get("provider_id")) == "siliconflow"
    ):
        provider_binding = resolve_provider_model_binding(
            rt,
            asr_runtime_binding["provider_id"],
            asr_runtime_binding.get("model_id", ""),
            "asr",
        )

    api_base = provider_binding.api_base if provider_binding else _normalize_siliconflow_base(rt.siliconflow_api_base)
    api_key = provider_binding.api_key if provider_binding else rt.siliconflow_api_key
    lang_hint = language or rt.siliconflow_asr_language or None
    chunk_strategy = _normalize_provider(
        options.get("asr_chunk_strategy") or rt.siliconflow_asr_chunk_strategy
    )
    bound_model = _clean_text(asr_runtime_binding.get("model_id"))
    model = provider_binding.model_id if provider_binding else (
        bound_model if provider == "siliconflow" and bound_model else rt.siliconflow_asr_model
    )
    endpoint = provider_binding.endpoint if provider_binding else (
        f"{api_base}/audio/transcriptions" if api_base else ""
    )
    default_params = (
        provider_binding.request_kwargs.get("default_params", {})
        if provider_binding
        else {}
    )
    binding_ready = provider_binding.configured if provider_binding else True
    configured = bool(binding_ready and api_base and api_key and model and endpoint)
    return ASRBinding(
        provider="siliconflow",
        source=source,
        model=model,
        api_base=api_base,
        api_key=api_key,
        language=lang_hint,
        diarize=False,
        num_speakers=num_speakers,
        chunk_strategy=chunk_strategy,
        configured=configured,
        reason="" if configured else (
            provider_binding.reason if provider_binding and provider_binding.reason else
            "siliconflow api_base, api_key, endpoint, or model is empty"
        ),
        request_kwargs={
            "endpoint": endpoint,
            "model": model,
            "language": lang_hint,
            "max_chunk_sec": rt.siliconflow_asr_max_chunk_sec,
            "timeout_sec": rt.siliconflow_asr_timeout_sec,
            "chunk_strategy": chunk_strategy,
            "diarize": False,
            "num_speakers": num_speakers,
            "default_params": default_params,
        },
    )


def resolve_vlm_binding(rt: RuntimeSettings) -> EndpointBinding:
    """Resolve the image-understanding model endpoint."""

    vision_binding = _runtime_binding(rt, "vision")
    if vision_binding.get("provider_id"):
        provider_binding = resolve_provider_model_binding(
            rt,
            vision_binding["provider_id"],
            vision_binding.get("model_id", ""),
            "vision",
        )
        if provider_binding.configured or provider_binding.reason != "provider not found":
            return EndpointBinding(
                capability="vlm",
                model=provider_binding.model_id,
                api_base=provider_binding.api_base,
                api_key=provider_binding.api_key,
                configured=provider_binding.configured,
                enabled=provider_binding.enabled,
                reason=provider_binding.reason,
                request_kwargs={
                    "max_tokens": rt.vlm_max_tokens,
                    "concurrency": rt.vlm_concurrency,
                    "timeout_sec": rt.vlm_timeout_sec,
                    **provider_binding.request_kwargs.get("default_params", {}),
                },
            )

    explicit_api_base = _clean_text(rt.vlm_api_base)
    explicit_model = _clean_text(rt.vlm_model)
    if explicit_api_base:
        api_base = _base_with_optional_v1(explicit_api_base)
        model = explicit_model
        api_key = _openai_compatible_api_key(_clean_text(rt.vlm_api_key))
        configured = bool(api_base and model)
        reason = "" if configured else "vlm_api_base or vlm_model is empty"
    else:
        api_base = _normalize_siliconflow_base(rt.siliconflow_api_base)
        model = explicit_model or _DEFAULT_SILICONFLOW_VLM_MODEL
        if model.lower() in _LEGACY_VLM_DEFAULT_MODELS:
            model = _DEFAULT_SILICONFLOW_VLM_MODEL
        api_key = _clean_text(rt.vlm_api_key) or _clean_text(rt.siliconflow_api_key)
        configured = bool(api_base and api_key and model)
        reason = "" if configured else "siliconflow api_base, api_key, or vlm_model is empty"

    return EndpointBinding(
        capability="vlm",
        model=model,
        api_base=api_base,
        api_key=api_key,
        configured=configured,
        enabled=configured,
        reason=reason,
        request_kwargs={
            "max_tokens": rt.vlm_max_tokens,
            "concurrency": rt.vlm_concurrency,
            "timeout_sec": rt.vlm_timeout_sec,
        },
    )


def resolve_embedding_binding(rt: RuntimeSettings) -> EndpointBinding:
    """Resolve the knowledge-base embedding endpoint."""

    enabled = bool(rt.kb_enabled)
    embedding_binding = _runtime_binding(rt, "embedding")
    if enabled and embedding_binding.get("provider_id"):
        provider_binding = resolve_provider_model_binding(
            rt,
            embedding_binding["provider_id"],
            embedding_binding.get("model_id", ""),
            "embedding",
        )
        if provider_binding.configured or provider_binding.reason != "provider not found":
            return EndpointBinding(
                capability="embedding",
                model=provider_binding.model_id,
                api_base=provider_binding.api_base,
                api_key=provider_binding.api_key,
                configured=provider_binding.configured,
                enabled=provider_binding.enabled,
                reason=provider_binding.reason,
                request_kwargs={
                    "dimension": rt.kb_embedding_dim,
                    "chunk_size_chars": rt.kb_chunk_size_chars,
                    "chunk_overlap_chars": rt.kb_chunk_overlap_chars,
                    **provider_binding.request_kwargs.get("default_params", {}),
                },
            )

    configured = bool(enabled and rt.kb_embedding_api_base and rt.kb_embedding_model)
    if not enabled:
        reason = "kb_enabled is false"
    elif not configured:
        reason = "kb_embedding_api_base or kb_embedding_model is empty"
    else:
        reason = ""
    return EndpointBinding(
        capability="embedding",
        model=rt.kb_embedding_model,
        api_base=rt.kb_embedding_api_base,
        api_key=_openai_compatible_api_key(rt.kb_embedding_api_key),
        configured=configured,
        enabled=enabled,
        reason=reason,
        request_kwargs={
            "dimension": rt.kb_embedding_dim,
            "chunk_size_chars": rt.kb_chunk_size_chars,
            "chunk_overlap_chars": rt.kb_chunk_overlap_chars,
        },
    )


def _analysis_stage_bindings(
    rt: RuntimeSettings,
    provider_override: str = "",
) -> dict[str, LLMBinding]:
    return {
        "analyze": resolve_llm_binding(rt, provider_override=provider_override, stage="analyze"),
        "summary": resolve_llm_binding(rt, provider_override=provider_override, stage="summary"),
        "mindmap": resolve_llm_binding(rt, provider_override=provider_override, stage="mindmap"),
        "detail": resolve_llm_binding(rt, provider_override=provider_override, stage="summary"),
    }


def resolve_pipeline_model_bindings(
    rt: RuntimeSettings,
    *,
    task_options: dict[str, Any] | None = None,
    content_subtype: str | None = None,
    has_platform_subtitle: bool = False,
    has_images: bool = False,
) -> PipelineModelBindings:
    """Resolve the model bindings implied by the pipeline branch.

    The function mirrors the model-facing branch choices in core.pipeline:
    note tasks skip audio work, subtitle tasks skip ASR, and normal media tasks
    select ASR from task options/runtime settings.
    """

    options = task_options or {}
    subtype = _clean_text(content_subtype).lower()
    vlm = resolve_vlm_binding(rt)
    embedding = resolve_embedding_binding(rt)
    run_kb_index = embedding.configured

    if subtype in {"image_note", "text_note"}:
        run_vlm = subtype == "image_note" and has_images and vlm.configured
        note_llm_provider = "deepseek" if resolve_deepseek_llm_binding(rt, "summary").configured else ""
        return PipelineModelBindings(
            branch=subtype,
            transcript_source="note",
            run_separation=False,
            run_asr=False,
            run_subtitle_processor=False,
            run_polish=False,
            run_analysis=True,
            run_vlm=run_vlm,
            run_kb_index=run_kb_index,
            llm_stages=_analysis_stage_bindings(rt, provider_override=note_llm_provider),
            vlm=vlm,
            embedding=embedding,
            notes={
                "content_subtype": subtype,
                "has_images": bool(has_images),
                "llm_provider_override": note_llm_provider,
            },
        )

    use_platform_subtitle = bool(has_platform_subtitle and not options.get("force_asr", False))
    if use_platform_subtitle:
        return PipelineModelBindings(
            branch="subtitle",
            transcript_source="platform",
            run_separation=False,
            run_asr=False,
            run_subtitle_processor=True,
            run_polish=True,
            run_analysis=True,
            run_vlm=False,
            run_kb_index=run_kb_index,
            polish=resolve_llm_binding(rt, stage="polish"),
            llm_stages=_analysis_stage_bindings(rt),
            vlm=vlm,
            embedding=embedding,
            notes={"has_platform_subtitle": True},
        )

    skip_separation = bool(options.get("skip_separation", False))
    api_flow = bool(options.get("api_flow", False))
    asr = resolve_asr_binding(rt, options)
    run_separation = not (skip_separation or api_flow)
    return PipelineModelBindings(
        branch="asr",
        transcript_source="asr",
        run_separation=run_separation,
        run_asr=True,
        run_subtitle_processor=False,
        run_polish=True,
        run_analysis=True,
        run_vlm=False,
        run_kb_index=run_kb_index,
        asr=asr,
        polish=resolve_polish_llm_binding(rt),
        llm_stages=_analysis_stage_bindings(rt),
        vlm=vlm,
        embedding=embedding,
        notes={
            "skip_separation": skip_separation,
            "api_flow": api_flow,
            "force_asr": bool(options.get("force_asr", False)),
        },
    )
