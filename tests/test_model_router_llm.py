import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.model_router import (  # noqa: E402
    resolve_deepseek_llm_binding,
    resolve_llm_binding,
    resolve_polish_llm_binding,
)
from app.core.settings import CustomLLMProfile, RuntimeSettings  # noqa: E402


def test_deepseek_summary_binding_enables_thinking_and_effort():
    settings = RuntimeSettings(
        llm_provider="deepseek",
        deepseek_api_key="sk-deepseek",
        deepseek_api_base="https://deepseek.example/v1",
        deepseek_summary_model="deepseek-v4-pro",
        deepseek_summary_thinking="enabled",
        deepseek_summary_effort="max",
    )

    binding = resolve_llm_binding(settings, stage="summary")

    assert binding.provider == "deepseek"
    assert binding.stage == "summary"
    assert binding.transport == "openai_sdk"
    assert binding.model == "deepseek-v4-pro"
    assert binding.request_kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert binding.request_kwargs["reasoning_effort"] == "max"


def test_deepseek_disabled_thinking_drops_reasoning_effort():
    settings = RuntimeSettings(
        deepseek_api_key="sk-deepseek",
        deepseek_polish_model="deepseek-v4-flash",
        deepseek_polish_thinking="disabled",
        deepseek_polish_effort="high",
    )

    binding = resolve_deepseek_llm_binding(settings, stage="polish")

    assert binding.request_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in binding.request_kwargs


def test_custom_binding_uses_active_profile_from_profile_list():
    settings = RuntimeSettings(
        llm_provider="custom",
        custom_llm_profiles=[
            CustomLLMProfile(
                id="fast",
                name="Fast",
                api_base="https://fast.example/v1",
                model="fast-model",
                api_key="fast-key",
            ),
            CustomLLMProfile(
                id="quality",
                name="Quality",
                api_base="https://quality.example/v1",
                model="quality-model",
                api_key="quality-key",
            ),
        ],
        custom_active_profile_id="quality",
    )

    binding = resolve_llm_binding(settings, stage="analyze")

    assert binding.provider == "custom"
    assert binding.model == "openai/quality-model"
    assert binding.api_base == "https://quality.example/v1"
    assert binding.api_key == "quality-key"
    assert binding.request_kwargs["custom_llm_provider"] == "openai"


def test_polish_provider_local_uses_configured_local_model_path():
    settings = RuntimeSettings(
        llm_provider="deepseek",
        deepseek_api_key="sk-deepseek",
        polish_provider="local",
        local_llm_model_path="D:/models/qwen-local",
    )

    binding = resolve_polish_llm_binding(settings)

    assert binding.provider == "local"
    assert binding.transport == "local"
    assert binding.model == "D:/models/qwen-local"
    assert binding.configured is True


def test_polish_provider_local_falls_back_to_main_provider_when_path_is_empty():
    settings = RuntimeSettings(
        llm_provider="openai",
        openai_api_key="sk-openai",
        openai_model="gpt-4.1",
        polish_provider="local",
        local_llm_model_path="",
    )

    binding = resolve_polish_llm_binding(settings)

    assert binding.provider == "openai"
    assert binding.fallback_from == "local"
    assert binding.model == "openai/gpt-4.1"
