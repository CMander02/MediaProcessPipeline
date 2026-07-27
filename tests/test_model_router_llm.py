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


def test_local_llama_cpp_text_binding_uses_openai_compatible_runtime():
    settings = RuntimeSettings(
        llm_provider="local",
        local_llm_engine="llama_cpp",
        local_llm_name="Qwen3.5-9B-Q8",
        local_llm_model_path="D:/models/Qwen3.5-9B-Q8_0.gguf",
        local_llm_mmproj_path="D:/models/mmproj-BF16.gguf",
        local_llm_concurrency=2,
    )

    binding = resolve_llm_binding(settings, stage="summary")

    assert binding.provider == "local"
    assert binding.transport == "llama_cpp"
    assert binding.model == "Qwen3.5-9B-Q8"
    assert binding.request_kwargs["parallel"] == 2


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


def test_codex_oauth_provider_uses_cli_transport_without_api_credentials():
    settings = RuntimeSettings(
        runtime_model_bindings={
            "summary": {
                "provider_id": "codex-oauth",
                "model_id": "default",
                "capability": "llm",
            }
        },
        providers=[
            {
                "id": "codex-oauth",
                "name": "Codex OAuth",
                "provider_type": "codex_oauth",
                "enabled": True,
                "cli_path": "C:/Tools/codex.exe",
                "timeout_sec": 900,
                "models": [
                    {
                        "id": "codex-oauth:default",
                        "model_id": "default",
                        "model_type": "llm",
                        "capabilities": ["llm", "chat"],
                        "enabled": True,
                    }
                ],
            }
        ],
    )

    binding = resolve_llm_binding(settings, stage="summary")

    assert binding.provider == "codex-oauth"
    assert binding.transport == "codex_cli"
    assert binding.model == "default"
    assert binding.configured is True
    assert binding.request_kwargs["provider_type"] == "codex_oauth"
    assert binding.request_kwargs["cli_path"] == "C:/Tools/codex.exe"
    assert binding.request_kwargs["timeout_sec"] == 900


def test_agy_oauth_provider_uses_cli_transport_without_api_base():
    settings = RuntimeSettings(
        runtime_model_bindings={
            "analyze": {
                "provider_id": "agy-oauth",
                "model_id": "gemini-3.1-pro-high",
                "capability": "llm",
            }
        },
        providers=[
            {
                "id": "agy-oauth",
                "name": "Antigravity OAuth",
                "provider_type": "agy_oauth",
                "enabled": True,
                "models": [
                    {
                        "id": "agy-oauth:gemini-3.1-pro-high",
                        "model_id": "gemini-3.1-pro-high",
                        "cli_model_name": "Gemini 3.1 Pro (High)",
                        "model_type": "llm",
                        "capabilities": ["llm", "chat"],
                        "enabled": True,
                    }
                ],
            }
        ],
    )

    binding = resolve_llm_binding(settings, stage="analyze")

    assert binding.provider == "agy-oauth"
    assert binding.transport == "agy_cli"
    assert binding.model == "Gemini 3.1 Pro (High)"
    assert binding.configured is True
