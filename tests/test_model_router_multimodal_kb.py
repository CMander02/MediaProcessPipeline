import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.model_router import (  # noqa: E402
    EndpointBinding,
    resolve_embedding_binding,
    resolve_vlm_binding,
)
from app.core.settings import RuntimeSettings  # noqa: E402
from app.services.analysis import coding_plan_cli  # noqa: E402
from app.services.analysis.vlm import VLMService  # noqa: E402


def test_vlm_binding_resolves_openai_compatible_endpoint():
    settings = RuntimeSettings(
        vlm_api_base="https://vlm.example/v1",
        vlm_api_key="vlm-key",
        vlm_model="qwen2.5-vl-72b-instruct",
        vlm_max_tokens=2048,
        vlm_concurrency=5,
        vlm_timeout_sec=240,
    )

    binding = resolve_vlm_binding(settings)

    assert binding.capability == "vlm"
    assert binding.configured is True
    assert binding.enabled is True
    assert binding.model == "qwen2.5-vl-72b-instruct"
    assert binding.api_base == "https://vlm.example/v1"
    assert binding.api_key == "vlm-key"
    assert binding.request_kwargs == {"max_tokens": 2048, "concurrency": 5, "timeout_sec": 240}


def test_vlm_binding_uses_siliconflow_defaults_when_vlm_endpoint_is_empty():
    settings = RuntimeSettings(
        vlm_api_base="",
        vlm_api_key="",
        vlm_model="qwen2.5-vl-7b-instruct",
        siliconflow_api_base="https://api.siliconflow.cn",
        siliconflow_api_key="sf-key",
    )

    binding = resolve_vlm_binding(settings)

    assert binding.configured is True
    assert binding.model == "Qwen/Qwen3.5-4B"
    assert binding.api_base == "https://api.siliconflow.cn/v1"
    assert binding.api_key == "sf-key"


def test_vlm_binding_resolves_local_llama_cpp_multimodal_model():
    settings = RuntimeSettings(
        runtime_model_bindings={
            "vision": {
                "provider_id": "local",
                "model_id": "Qwen3.5-9B-Q8",
                "capability": "vision",
            }
        },
        llama_cpp_binary_path="D:/models/llama-server.exe",
        local_llm_engine="llama_cpp",
        local_llm_model_path="D:/models/Qwen3.5-9B-Q8_0.gguf",
        local_llm_mmproj_path="D:/models/mmproj-BF16.gguf",
        local_llm_concurrency=2,
    )

    binding = resolve_vlm_binding(settings)

    assert binding.configured is True
    assert binding.model == "Qwen3.5-9B-Q8"
    assert binding.api_base == "local://llama_cpp"
    assert binding.request_kwargs["local_engine"] == "llama_cpp"
    assert binding.request_kwargs["parallel"] == 2


def test_vlm_binding_resolves_codex_oauth_luna_max():
    settings = RuntimeSettings(
        providers=[
            {
                "id": "codex-oauth",
                "name": "Codex OAuth",
                "provider_type": "codex_oauth",
                "enabled": True,
                "cli_path": "C:/tools/codex.exe",
                "models": [
                    {
                        "id": "codex-oauth:default",
                        "model_id": "default",
                        "display_name": "GPT-5.6 Luna (Max)",
                        "model_type": "llm",
                        "capabilities": ["llm", "chat", "json", "vision"],
                        "cli_model_name": "gpt-5.6-luna",
                        "enabled": True,
                        "default_params": {"reasoning_effort": "max"},
                    }
                ],
            }
        ],
        runtime_model_bindings={
            "vision": {
                "provider_id": "codex-oauth",
                "model_id": "default",
                "capability": "vision",
            }
        },
    )

    binding = resolve_vlm_binding(settings)

    assert binding.configured is True
    assert binding.model == "gpt-5.6-luna"
    assert binding.api_base == ""
    assert binding.request_kwargs["transport"] == "oauth_cli"
    assert binding.request_kwargs["provider_type"] == "codex_oauth"
    assert binding.request_kwargs["cli_path"] == "C:/tools/codex.exe"
    assert binding.request_kwargs["reasoning_effort"] == "max"


def test_vlm_service_passes_image_to_oauth_cli(monkeypatch, tmp_path):
    Image = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (32, 24), color=(24, 80, 160)).save(image_path)
    captured = {}

    async def fake_call(provider_type, **kwargs):
        captured["provider_type"] = provider_type
        captured.update(kwargs)
        return "KIND: content\n一张蓝色测试图片。"

    monkeypatch.setattr(coding_plan_cli, "call_coding_plan_cli", fake_call)
    binding = EndpointBinding(
        capability="vlm",
        model="gpt-5.6-luna",
        api_base="",
        api_key="",
        configured=True,
        request_kwargs={
            "transport": "oauth_cli",
            "provider_type": "codex_oauth",
            "cli_path": "C:/tools/codex.exe",
            "reasoning_effort": "max",
            "timeout_sec": 120,
        },
    )

    result = VLMService().describe_image(image_path, binding)

    assert result["kind"] == "content"
    assert result["text"] == "一张蓝色测试图片。"
    assert captured["provider_type"] == "codex_oauth"
    assert captured["model"] == "gpt-5.6-luna"
    assert captured["reasoning_effort"] == "max"
    assert captured["image_paths"] == [image_path]


def test_embedding_binding_tracks_kb_enablement_and_vector_settings():
    disabled = RuntimeSettings(
        kb_enabled=False,
        kb_embedding_api_base="https://embed.example/v1",
        kb_embedding_model="qwen3-embedding",
    )

    disabled_binding = resolve_embedding_binding(disabled)

    assert disabled_binding.enabled is False
    assert disabled_binding.configured is False
    assert disabled_binding.reason == "kb_enabled is false"

    enabled = RuntimeSettings(
        kb_enabled=True,
        kb_embedding_api_base="https://embed.example/v1",
        kb_embedding_api_key="embed-key",
        kb_embedding_model="qwen3-embedding",
        kb_embedding_dim=2048,
        kb_chunk_size_chars=800,
        kb_chunk_overlap_chars=100,
    )

    binding = resolve_embedding_binding(enabled)

    assert binding.capability == "embedding"
    assert binding.configured is True
    assert binding.api_key == "embed-key"
    assert binding.request_kwargs["dimension"] == 2048
    assert binding.request_kwargs["chunk_size_chars"] == 800
    assert binding.request_kwargs["chunk_overlap_chars"] == 100
