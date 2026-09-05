"""Runtime capability bindings and legacy field projection."""

from typing import Any

from app.core.configuration.constants import _RUNTIME_BINDING_SPECS
from app.core.configuration.profiles import _coerce_custom_profiles, _str_value
from app.core.configuration.registry import (
    _canonical_provider_id,
    _custom_provider_id,
    _find_provider,
    _find_provider_model,
    _normalize_provider_id,
)
from pydantic import BaseModel


def _binding_record(provider_id: Any, model_id: Any, capability: str) -> dict[str, str]:
    return {
        "provider_id": _str_value(provider_id).strip(),
        "model_id": _str_value(model_id).strip(),
        "capability": capability,
    }


def _parse_binding_value(value: Any) -> tuple[str, str]:
    text = _str_value(value).strip()
    if ":" not in text:
        return "", text
    provider_id, model_id = text.split(":", 1)
    return _canonical_provider_id(provider_id), model_id.strip()


def _default_runtime_model_bindings(data: dict[str, Any]) -> dict[str, dict[str, str]]:
    bindings: dict[str, dict[str, str]] = {}
    llm_provider = _canonical_provider_id(data.get("llm_provider") or "deepseek")
    if llm_provider == "custom":
        llm_provider = _custom_provider_id(data.get("custom_active_profile_id") or "default")
    polish_provider = _canonical_provider_id(data.get("polish_provider") or llm_provider)
    if polish_provider == "custom":
        polish_provider = (
            llm_provider
            if llm_provider.startswith("custom-")
            else _custom_provider_id(data.get("custom_active_profile_id") or "default")
        )

    for key, (fallback_provider, model_field, capability) in _RUNTIME_BINDING_SPECS.items():
        provider_id = fallback_provider
        if capability == "llm":
            provider_id = (
                polish_provider
                if key in {"polish", "subtitle_polish", "subtitle_refine"}
                else llm_provider
            )
        if key == "asr":
            provider_id = _canonical_provider_id(data.get("asr_provider") or fallback_provider)

        model_id = _str_value(data.get(model_field)).strip() if model_field else ""
        if provider_id in {"qwen3", "qwen3_gguf"}:
            provider_id = "sherpa_onnx"
            model_id = _str_value(data.get("sherpa_model_id") or "qwen3-asr-1.7b-onnx").strip()
        elif provider_id == "sherpa_onnx":
            model_id = _str_value(
                data.get("sherpa_model_id") or model_id or "sensevoice-small-int8"
            ).strip()
        elif provider_id == "siliconflow" and key == "asr":
            model_id = _str_value(data.get("siliconflow_asr_model")).strip()
        elif provider_id.startswith("custom-") and capability == "llm":
            profile_id = provider_id.removeprefix("custom-")
            profile = next(
                (
                    item
                    for item in _coerce_custom_profiles(data.get("custom_llm_profiles"))
                    if _normalize_provider_id(item["id"]) == profile_id
                ),
                None,
            )
            if profile:
                model_id = profile.get("model", "")
        bindings[key] = _binding_record(provider_id, model_id, capability)

    purpose_aliases = {
        "purpose_subtitle_polish_model": "subtitle_polish",
        "purpose_subtitle_refine_model": "subtitle_refine",
        "purpose_analyze_model": "analyze",
        "purpose_summary_model": "summary",
        "purpose_mindmap_model": "mindmap",
        "purpose_asr_model": "asr",
        "purpose_vision_model": "vision",
        "purpose_embedding_model": "embedding",
    }
    for flat_key, binding_key in purpose_aliases.items():
        provider_id, model_id = _parse_binding_value(data.get(flat_key))
        if provider_id or model_id:
            capability = bindings.get(binding_key, {}).get("capability", "llm")
            bindings[binding_key] = _binding_record(provider_id, model_id, capability)
    return bindings


def _normalize_runtime_model_bindings(data: dict[str, Any]) -> None:
    current = data.get("runtime_model_bindings")
    normalized = _default_runtime_model_bindings(data)
    if isinstance(current, dict):
        for key, item in current.items():
            if isinstance(item, BaseModel):
                value = item.model_dump()
            elif isinstance(item, dict):
                value = item
            else:
                continue
            spec = _RUNTIME_BINDING_SPECS.get(key)
            capability = _str_value(value.get("capability") or (spec[2] if spec else "llm"))
            provider_id = value.get("provider_id")
            model_id = value.get("model_id")
            if key == "asr" and _canonical_provider_id(provider_id) in {"qwen3", "qwen3_gguf"}:
                provider_id = "sherpa_onnx"
                model_id = data.get("sherpa_model_id") or "qwen3-asr-1.7b-onnx"
            normalized[key] = _binding_record(provider_id, model_id, capability)
    data["runtime_model_bindings"] = normalized


def _sync_flat_from_runtime_model_bindings(data: dict[str, Any]) -> None:
    bindings = data.get("runtime_model_bindings")
    if not isinstance(bindings, dict):
        return

    def binding(key: str) -> dict[str, Any]:
        item = bindings.get(key)
        return item if isinstance(item, dict) else {}

    stage_fields = {
        "analyze": "deepseek_analyze_model",
        "polish": "deepseek_polish_model",
        "summary": "deepseek_summary_model",
        "mindmap": "deepseek_mindmap_model",
    }
    for key, field in stage_fields.items():
        item = binding(key)
        if item.get("provider_id") == "deepseek" and item.get("model_id"):
            data[field] = item["model_id"]

    polish = binding("polish") or binding("subtitle_polish")
    polish_provider = _canonical_provider_id(polish.get("provider_id"))
    if polish_provider:
        data["polish_provider"] = (
            "custom" if polish_provider.startswith("custom-") else polish_provider
        )

    summary = binding("summary")
    llm_provider = _canonical_provider_id(summary.get("provider_id")) or _canonical_provider_id(
        data.get("llm_provider")
    )
    if llm_provider and llm_provider not in {"qwen3", "siliconflow"}:
        data["llm_provider"] = "custom" if llm_provider.startswith("custom-") else llm_provider
        if llm_provider.startswith("custom-"):
            data["custom_active_profile_id"] = llm_provider.removeprefix("custom-")

    asr = binding("asr")
    if asr.get("provider_id") in {"sherpa_onnx", "siliconflow"}:
        data["asr_provider"] = asr["provider_id"]
        if asr["provider_id"] == "sherpa_onnx" and asr.get("model_id"):
            data["sherpa_model_id"] = asr["model_id"]
        elif asr["provider_id"] == "siliconflow" and asr.get("model_id"):
            data["siliconflow_asr_model"] = asr["model_id"]

    vision = binding("vision")
    provider = _find_provider(data, vision.get("provider_id"))
    model = _find_provider_model(provider, vision.get("model_id"), "vision")
    if provider and model:
        data["vlm_api_base"] = provider.get("api_base", "")
        data["vlm_api_key"] = provider.get("api_key", "")
        data["vlm_model"] = model.get("model_id", "")

    embedding = binding("embedding")
    provider = _find_provider(data, embedding.get("provider_id"))
    model = _find_provider_model(provider, embedding.get("model_id"), "embedding")
    if provider and model:
        data["kb_embedding_api_base"] = provider.get("api_base", "")
        data["kb_embedding_api_key"] = provider.get("api_key", "")
        data["kb_embedding_model"] = model.get("model_id", "")
