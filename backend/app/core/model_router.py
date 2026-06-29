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
ASR_PROVIDERS = {"qwen3", "siliconflow"}

_DEFAULT_QWEN3_ASR_MODEL = "Qwen/Qwen3-ASR-1.7B"
_DEFAULT_DEEPSEEK_API_BASE = "https://api.deepseek.com"


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
    provider = _normalize_provider(provider_override) or _normalize_provider(rt.llm_provider)

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
        model_name = rt.anthropic_model or "claude-sonnet-4-6"
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
        model_name = rt.openai_model or "gpt-4o"
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
        model_name = profile["model"]
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


def resolve_asr_binding(
    rt: RuntimeSettings,
    task_options: dict[str, Any] | None = None,
    language: str | None = None,
) -> ASRBinding:
    """Resolve the ASR provider selected by task options and runtime settings."""

    options = task_options or {}
    option_provider = _normalize_provider(options.get("asr_provider"))
    if option_provider:
        provider = option_provider
        source = "task_option"
    elif bool(options.get("api_flow", False)):
        provider = "siliconflow"
        source = "api_flow"
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

    api_base = _normalize_siliconflow_base(rt.siliconflow_api_base)
    lang_hint = language or rt.siliconflow_asr_language or None
    chunk_strategy = _normalize_provider(
        options.get("asr_chunk_strategy") or rt.siliconflow_asr_chunk_strategy
    )
    configured = bool(api_base and rt.siliconflow_api_key and rt.siliconflow_asr_model)
    return ASRBinding(
        provider="siliconflow",
        source=source,
        model=rt.siliconflow_asr_model,
        api_base=api_base,
        api_key=rt.siliconflow_api_key,
        language=lang_hint,
        diarize=False,
        num_speakers=num_speakers,
        chunk_strategy=chunk_strategy,
        configured=configured,
        reason="" if configured else "siliconflow api_base, api_key, or model is empty",
        request_kwargs={
            "endpoint": f"{api_base}/audio/transcriptions" if api_base else "",
            "model": rt.siliconflow_asr_model,
            "language": lang_hint,
            "max_chunk_sec": rt.siliconflow_asr_max_chunk_sec,
            "timeout_sec": rt.siliconflow_asr_timeout_sec,
            "chunk_strategy": chunk_strategy,
            "diarize": False,
            "num_speakers": num_speakers,
        },
    )


def resolve_vlm_binding(rt: RuntimeSettings) -> EndpointBinding:
    """Resolve the image-understanding model endpoint."""

    configured = bool(rt.vlm_api_base and rt.vlm_model)
    return EndpointBinding(
        capability="vlm",
        model=rt.vlm_model,
        api_base=rt.vlm_api_base,
        api_key=_openai_compatible_api_key(rt.vlm_api_key),
        configured=configured,
        enabled=configured,
        reason="" if configured else "vlm_api_base or vlm_model is empty",
        request_kwargs={
            "max_tokens": rt.vlm_max_tokens,
            "concurrency": rt.vlm_concurrency,
        },
    )


def resolve_embedding_binding(rt: RuntimeSettings) -> EndpointBinding:
    """Resolve the knowledge-base embedding endpoint."""

    enabled = bool(rt.kb_enabled)
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


def _analysis_stage_bindings(rt: RuntimeSettings) -> dict[str, LLMBinding]:
    return {
        "analyze": resolve_llm_binding(rt, stage="analyze"),
        "summary": resolve_llm_binding(rt, stage="summary"),
        "mindmap": resolve_llm_binding(rt, stage="mindmap"),
        "detail": resolve_llm_binding(rt, stage="summary"),
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
            llm_stages=_analysis_stage_bindings(rt),
            vlm=vlm,
            embedding=embedding,
            notes={
                "content_subtype": subtype,
                "has_images": bool(has_images),
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
