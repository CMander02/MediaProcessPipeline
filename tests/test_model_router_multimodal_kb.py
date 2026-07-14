import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.model_router import resolve_embedding_binding, resolve_vlm_binding  # noqa: E402
from app.core.settings import RuntimeSettings  # noqa: E402


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
