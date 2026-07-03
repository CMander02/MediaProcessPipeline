import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.api.routes import settings as settings_route  # noqa: E402
from app.core import settings as core_settings  # noqa: E402


@pytest.fixture
def isolated_settings_file(tmp_path, monkeypatch):
    settings_file = tmp_path / "config.json"
    monkeypatch.setattr(core_settings, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(settings_route, "SETTINGS_FILE", settings_file)
    core_settings._runtime_settings = None
    yield settings_file
    core_settings._runtime_settings = None


def _write_config(settings_file: Path, data: dict[str, Any]) -> None:
    settings_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _read_config(settings_file: Path) -> dict[str, Any]:
    return json.loads(settings_file.read_text(encoding="utf-8"))


def _by_id(items: list[dict[str, Any]], item_id: str) -> dict[str, Any]:
    return next(item for item in items if item["id"] == item_id)


def _models_for_connection(data: dict[str, Any], connection_id: str) -> list[dict[str, Any]]:
    return [
        item
        for item in data["service_models"]
        if item.get("connection_id") == connection_id
    ]


def _models_for_provider(data: dict[str, Any], provider_id: str) -> list[dict[str, Any]]:
    provider = _by_id(data["providers"], provider_id)
    return provider.get("models", [])


def test_siliconflow_model_url_normalizes_bare_host_and_v1():
    assert (
        settings_route._siliconflow_models_url("https://api.siliconflow.cn")
        == "https://api.siliconflow.cn/v1/models"
    )
    assert (
        settings_route._siliconflow_models_url("https://api.siliconflow.cn/v1/")
        == "https://api.siliconflow.cn/v1/models"
    )


def test_jina_reader_key_is_masked():
    data = settings_route._mask_settings(
        core_settings.RuntimeSettings(jina_reader_api_key="jina-secret-1234")
    )

    assert data["jina_reader_api_key"].startswith("***")
    assert data["jina_reader_api_key"].endswith("1234")


def test_siliconflow_model_type_inference_uses_model_id_keywords():
    cases = {
        "BAAI/bge-reranker-v2-m3": "rerank",
        "BAAI/bge-m3": "embedding",
        "text-embedding-v3": "embedding",
        "FunAudioLLM/SenseVoiceSmall": "asr",
        "openai/whisper-large-v3": "asr",
        "Qwen/Qwen2.5-VL-72B-Instruct": "vlm",
        "deepseek-ai/DeepSeek-V3.2": "llm",
    }

    for model_id, model_type in cases.items():
        assert settings_route._infer_siliconflow_model_type(model_id) == model_type


@pytest.mark.asyncio
async def test_siliconflow_models_route_reads_settings_and_maps_payload(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_fetch(api_base: str, api_key: str) -> dict[str, Any]:
        captured["api_base"] = api_base
        captured["api_key"] = api_key
        return {
            "data": [
                {"id": "Qwen/Qwen3.5-8B", "display_name": "Qwen 3.5 8B"},
                {"id": "BAAI/bge-m3"},
                {"id": "FunAudioLLM/SenseVoiceSmall"},
            ]
        }

    monkeypatch.setattr(
        settings_route,
        "get_runtime_settings",
        lambda: core_settings.RuntimeSettings(
            siliconflow_api_base="https://api.siliconflow.cn",
            siliconflow_api_key="sk-sf",
        ),
    )
    monkeypatch.setattr(settings_route, "_fetch_siliconflow_models_payload", fake_fetch)

    result = await settings_route.list_siliconflow_models()

    assert captured == {
        "api_base": "https://api.siliconflow.cn",
        "api_key": "sk-sf",
    }
    assert result == {
        "models": [
            {
                "id": "Qwen/Qwen3.5-8B",
                "display_name": "Qwen 3.5 8B",
                "model_type": "llm",
            },
            {
                "id": "BAAI/bge-m3",
                "display_name": "BAAI/bge-m3",
                "model_type": "embedding",
            },
            {
                "id": "FunAudioLLM/SenseVoiceSmall",
                "display_name": "FunAudioLLM/SenseVoiceSmall",
                "model_type": "asr",
            },
        ]
    }


@pytest.mark.asyncio
async def test_provider_model_catalog_returns_remote_and_allowed_models(monkeypatch):
    async def fake_fetch(provider: dict[str, Any]) -> dict[str, Any]:
        assert provider["id"] == "custom-1"
        return {
            "data": [
                {"id": "Qwen/Qwen3.5-8B"},
                {"id": "BAAI/bge-m3"},
                {"id": "BAAI/bge-reranker-v2-m3"},
            ]
        }

    monkeypatch.setattr(
        settings_route,
        "get_runtime_settings",
        lambda: core_settings.RuntimeSettings(
            providers=[
                {
                    "id": "custom-1",
                    "name": "Custom 1",
                    "provider_type": "openai_compatible",
                    "enabled": True,
                    "api_base": "https://example.com/v1",
                    "api_key": "sk-test",
                    "models": [
                        {
                            "id": "custom-1:Qwen/Qwen3.5-8B",
                            "model_id": "Qwen/Qwen3.5-8B",
                            "display_name": "Qwen/Qwen3.5-8B",
                            "model_type": "llm",
                            "enabled": True,
                            "capabilities": ["llm", "chat"],
                        },
                        {
                            "id": "custom-1:BAAI/bge-m3",
                            "model_id": "BAAI/bge-m3",
                            "display_name": "BAAI/bge-m3",
                            "model_type": "embedding",
                            "enabled": False,
                            "capabilities": ["embedding"],
                        },
                    ],
                }
            ],
        ),
    )
    monkeypatch.setattr(settings_route, "_fetch_provider_models_payload", fake_fetch)

    result = await settings_route.list_provider_model_catalog("custom-1", capability="llm")

    assert result["provider_id"] == "custom-1"
    assert result["source"] == "remote"
    assert [model["model_id"] for model in result["models"]] == ["Qwen/Qwen3.5-8B"]
    assert [model["model_id"] for model in result["allowed_models"]] == ["Qwen/Qwen3.5-8B"]
    assert result["error"] is None


@pytest.mark.asyncio
async def test_provider_model_payload_uses_urllib_fallback_on_httpx_timeout(monkeypatch):
    import httpx

    captured: dict[str, Any] = {}

    class TimeoutClient:
        def __init__(self, timeout: float, **_kwargs):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url: str, headers: dict[str, str]):
            raise httpx.ConnectTimeout("connect timeout")

    async def fake_urllib_fetch(
        url: str,
        headers: dict[str, str],
        *,
        timeout: float,
        label: str,
    ) -> dict[str, Any]:
        captured.update({
            "url": url,
            "headers": headers,
            "timeout": timeout,
            "label": label,
        })
        return {"data": [{"id": "deepseek-v4-flash"}]}

    monkeypatch.setattr(httpx, "AsyncClient", TimeoutClient)
    monkeypatch.setattr(settings_route, "_fetch_json_with_urllib", fake_urllib_fetch)

    payload = await settings_route._fetch_provider_models_payload({
        "id": "deepseek",
        "provider_type": "deepseek",
        "api_base": "https://api.deepseek.com",
        "api_key": "sk-test",
        "headers": {},
    })

    assert payload == {"data": [{"id": "deepseek-v4-flash"}]}
    assert captured == {
        "url": "https://api.deepseek.com/v1/models",
        "headers": {"Authorization": "Bearer sk-test"},
        "timeout": 30.0,
        "label": "Provider models",
    }


def test_flat_config_load_migrates_default_service_registry(isolated_settings_file):
    _write_config(
        isolated_settings_file,
        {
            "llm_provider": "deepseek",
            "deepseek_api_key": "sk-deepseek",
            "deepseek_api_base": "https://deepseek.example/v1",
            "deepseek_analyze_model": "deepseek-analyze",
            "deepseek_polish_model": "deepseek-polish",
            "deepseek_summary_model": "deepseek-summary",
            "deepseek_mindmap_model": "deepseek-mindmap",
            "openai_model": "gpt-flat",
            "anthropic_model": "claude-flat",
            "siliconflow_asr_model": "SenseVoiceFlat",
            "vlm_model": "vision-flat",
            "kb_embedding_model": "embed-flat",
            "data_root": str(isolated_settings_file.parent / "data"),
        },
    )

    settings = core_settings._load_settings_from_file()
    data = settings.model_dump()

    deepseek = _by_id(data["service_connections"], "deepseek")
    assert deepseek["api_key"] == "sk-deepseek"
    assert deepseek["api_base"] == "https://deepseek.example/v1"

    deepseek_models = {
        item["model_id"] for item in _models_for_connection(data, "deepseek")
    }
    assert deepseek_models == {
        "deepseek-analyze",
        "deepseek-polish",
        "deepseek-summary",
        "deepseek-mindmap",
    }
    assert _models_for_connection(data, "openai")[0]["model_id"] == "gpt-flat"
    assert _models_for_connection(data, "anthropic")[0]["model_id"] == "claude-flat"
    assert _models_for_connection(data, "siliconflow-asr")[0]["model_id"] == "SenseVoiceFlat"
    assert _models_for_connection(data, "siliconflow-asr")[0]["model_type"] == "asr"
    assert _models_for_connection(data, "siliconflow-asr")[0]["endpoint_path"] == "/audio/transcriptions"
    assert _models_for_connection(data, "vision-default")[0]["model_id"] == "vision-flat"
    assert _models_for_connection(data, "vision-default")[0]["model_type"] == "vlm"
    assert _models_for_connection(data, "vision-default")[0]["endpoint_path"] == "/chat/completions"
    assert _models_for_connection(data, "embedding-default")[0]["model_id"] == "embed-flat"
    assert _models_for_connection(data, "embedding-default")[0]["model_type"] == "embedding"
    assert _models_for_connection(data, "embedding-default")[0]["endpoint_path"] == "/embeddings"

    deepseek_provider = _by_id(data["providers"], "deepseek")
    assert deepseek_provider["api_key"] == "sk-deepseek"
    assert deepseek_provider["api_base"] == "https://deepseek.example/v1"
    assert {
        item["model_id"]
        for item in _models_for_provider(data, "deepseek")
    } == {
        "deepseek-analyze",
        "deepseek-polish",
        "deepseek-summary",
        "deepseek-mindmap",
    }
    assert _models_for_provider(data, "siliconflow")[0]["model_id"] == "SenseVoiceFlat"
    assert _models_for_provider(data, "siliconflow")[0]["model_type"] == "asr"
    assert data["runtime_model_bindings"]["summary"] == {
        "provider_id": "deepseek",
        "model_id": "deepseek-summary",
        "capability": "llm",
    }
    assert data["runtime_model_bindings"]["asr"] == {
        "provider_id": "qwen3_gguf",
        "model_id": "ggml-org/Qwen3-ASR-1.7B-GGUF:Q8_0",
        "capability": "asr",
    }


def test_service_model_array_patch_normalizes_model_type_and_endpoint(isolated_settings_file):
    _write_config(
        isolated_settings_file,
        {
            "siliconflow_api_base": "https://api.siliconflow.cn/v1",
            "siliconflow_api_key": "sk-sf",
            "data_root": str(isolated_settings_file.parent / "data"),
        },
    )
    core_settings._runtime_settings = core_settings._load_settings_from_file()

    updated = core_settings.patch_runtime_settings(
        {
            "service_models": [
                {
                    "id": "siliconflow-asr:baai-bge-reranker-v2-m3",
                    "connection_id": "siliconflow-asr",
                    "model_id": "BAAI/bge-reranker-v2-m3",
                    "display_name": "BAAI/bge-reranker-v2-m3",
                    "model_type": "rerank",
                    "enabled": True,
                }
            ]
        }
    )

    model = _models_for_connection(updated.model_dump(), "siliconflow-asr")[0]
    assert model["model_type"] == "rerank"
    assert model["capabilities"] == ["rerank"]
    assert model["endpoint_path"] == "/rerank"


def test_siliconflow_provider_model_metadata_carries_official_endpoint_defaults():
    rerank = settings_route._configured_provider_model_record(
        "siliconflow",
        {
            "id": "siliconflow:BAAI/bge-reranker-v2-m3",
            "model_id": "BAAI/bge-reranker-v2-m3",
            "model_type": "rerank",
            "enabled": True,
        },
    )
    asr = settings_route._configured_provider_model_record(
        "siliconflow",
        {
            "id": "siliconflow:TeleAI/TeleSpeechASR",
            "model_id": "TeleAI/TeleSpeechASR",
            "model_type": "asr",
            "enabled": True,
        },
    )

    assert rerank is not None
    assert rerank["endpoint_path"] == "/rerank"
    assert rerank["default_params"]["request_format"] == "json"
    assert rerank["default_params"]["documents_field"] == "documents"
    assert rerank["default_params"]["max_chunks_per_doc"] == 1024
    assert asr is not None
    assert asr["endpoint_path"] == "/audio/transcriptions"
    assert asr["default_params"]["request_format"] == "multipart"
    assert asr["default_params"]["file_field"] == "file"
    assert asr["default_params"]["model_field"] == "model"
    assert asr["default_params"]["max_file_mb"] == 50


def test_save_preserves_document_registry_and_unknown_top_level_keys(isolated_settings_file):
    preserved_connections = [
        {
            "id": "deepseek",
            "name": "DeepSeek Custom",
            "service_scope": "api",
            "provider": "deepseek",
            "endpoint_type": "deepseek_native",
            "api_base": "https://saved.deepseek.example",
            "api_key": "sk-saved",
            "headers": {"X-Test": "1"},
            "enabled": True,
            "timeout_sec": 77,
            "max_concurrency": 2,
            "status": "ok",
            "last_checked_at": "2026-06-29T00:00:00Z",
        }
    ]
    preserved_models = [
        {
            "id": "deepseek:kept-model",
            "connection_id": "deepseek",
            "model_id": "kept-model",
            "display_name": "Kept Model",
            "capabilities": ["chat"],
            "enabled": True,
            "default_params": {"temperature": 0},
        }
    ]
    _write_config(
        isolated_settings_file,
        {
            "deepseek_api_key": "sk-saved",
            "deepseek_api_base": "https://saved.deepseek.example",
            "deepseek_summary_effort": "max",
            "service_connections": preserved_connections,
            "service_models": preserved_models,
            "flow_profiles": [{"id": "flow-a", "name": "Flow A"}],
            "active_flow_defaults": {"url": "webpage"},
            "unknown_top_level": {"keep": True},
            "data_root": str(isolated_settings_file.parent / "data"),
        },
    )

    core_settings._runtime_settings = core_settings._load_settings_from_file()
    core_settings.patch_runtime_settings({"deepseek_summary_effort": "high"})
    saved = _read_config(isolated_settings_file)

    assert saved["service_connections"] == preserved_connections
    assert saved["service_models"] == preserved_models
    assert saved["flow_profiles"] == [{"id": "flow-a", "name": "Flow A"}]
    assert saved["active_flow_defaults"] == {"url": "webpage"}
    assert saved["unknown_top_level"] == {"keep": True}
    assert saved["deepseek_summary_effort"] == "high"


def test_flat_deepseek_patch_syncs_registry_mirror(isolated_settings_file):
    _write_config(
        isolated_settings_file,
        {
            "deepseek_api_key": "sk-old",
            "deepseek_api_base": "https://old.deepseek.example",
            "deepseek_summary_model": "deepseek-old",
            "data_root": str(isolated_settings_file.parent / "data"),
        },
    )
    core_settings._runtime_settings = core_settings._load_settings_from_file()

    updated = core_settings.patch_runtime_settings(
        {
            "deepseek_api_key": "sk-new",
            "deepseek_api_base": "https://new.deepseek.example",
            "deepseek_summary_model": "deepseek-new",
        }
    )
    data = updated.model_dump()

    deepseek = _by_id(data["service_connections"], "deepseek")
    assert deepseek["api_key"] == "sk-new"
    assert deepseek["api_base"] == "https://new.deepseek.example"
    assert any(
        item["model_id"] == "deepseek-new"
        for item in _models_for_connection(data, "deepseek")
    )


def test_dot_path_patch_updates_service_connection_and_flat_mirror(isolated_settings_file):
    _write_config(
        isolated_settings_file,
        {
            "deepseek_api_key": "sk-old",
            "deepseek_api_base": "https://old.deepseek.example",
            "data_root": str(isolated_settings_file.parent / "data"),
        },
    )
    core_settings._runtime_settings = core_settings._load_settings_from_file()

    updated = core_settings.patch_runtime_settings(
        {
            "service_connections.deepseek.api_key": "sk-dot",
            "service_connections.deepseek.api_base": "https://dot.deepseek.example",
        }
    )
    data = updated.model_dump()
    saved = _read_config(isolated_settings_file)

    deepseek = _by_id(data["service_connections"], "deepseek")
    assert deepseek["api_key"] == "sk-dot"
    assert deepseek["api_base"] == "https://dot.deepseek.example"
    assert data["deepseek_api_key"] == "sk-dot"
    assert data["deepseek_api_base"] == "https://dot.deepseek.example"
    assert "service_connections.deepseek.api_key" not in saved


def test_provider_patch_updates_flat_mirror(isolated_settings_file):
    _write_config(
        isolated_settings_file,
        {
            "deepseek_api_key": "sk-old",
            "deepseek_api_base": "https://old.deepseek.example",
            "deepseek_summary_model": "deepseek-old",
            "data_root": str(isolated_settings_file.parent / "data"),
        },
    )
    core_settings._runtime_settings = core_settings._load_settings_from_file()

    current = core_settings._runtime_settings.model_dump()
    providers = current["providers"]
    deepseek = _by_id(providers, "deepseek")
    deepseek["api_key"] = "sk-provider"
    deepseek["api_base"] = "https://provider.deepseek.example/v1"
    deepseek["models"] = [
        {
            "id": "deepseek:deepseek-provider",
            "model_id": "deepseek-provider",
            "display_name": "deepseek-provider",
            "model_type": "llm",
            "capabilities": ["llm", "chat", "reasoning", "json"],
            "endpoint_path": "/chat/completions",
            "enabled": True,
            "default_params": {},
        }
    ]

    updated = core_settings.patch_runtime_settings({"providers": providers})
    data = updated.model_dump()

    assert data["deepseek_api_key"] == "sk-provider"
    assert data["deepseek_api_base"] == "https://provider.deepseek.example/v1"
    assert _by_id(data["service_connections"], "deepseek")["api_key"] == "sk-provider"


def test_runtime_model_binding_patch_updates_flat_selection(isolated_settings_file):
    _write_config(
        isolated_settings_file,
        {
            "asr_provider": "qwen3",
            "siliconflow_asr_model": "SenseVoiceOld",
            "data_root": str(isolated_settings_file.parent / "data"),
        },
    )
    core_settings._runtime_settings = core_settings._load_settings_from_file()

    updated = core_settings.patch_runtime_settings(
        {
            "runtime_model_bindings": {
                "asr": {
                    "provider_id": "siliconflow",
                    "model_id": "SenseVoiceNew",
                    "capability": "asr",
                },
                "summary": {
                    "provider_id": "deepseek",
                    "model_id": "deepseek-bound-summary",
                    "capability": "llm",
                },
            }
        }
    )
    data = updated.model_dump()

    assert data["asr_provider"] == "siliconflow"
    assert data["siliconflow_asr_model"] == "SenseVoiceNew"
    assert data["deepseek_summary_model"] == "deepseek-bound-summary"


def test_settings_api_masks_and_restores_nested_service_connection_secrets():
    current = core_settings.RuntimeSettings(
        deepseek_api_key="sk-current",
        service_connections=[
            {
                "id": "deepseek",
                "name": "DeepSeek",
                "service_scope": "api",
                "provider": "deepseek",
                "endpoint_type": "deepseek_native",
                "api_base": "https://api.deepseek.com",
                "api_key": "sk-current",
                "headers": {},
                "enabled": True,
                "timeout_sec": 120,
                "max_concurrency": 4,
                "status": "unknown",
                "last_checked_at": "",
            }
        ],
    )

    masked = settings_route._mask_settings(current)
    assert masked["service_connections"][0]["api_key"].startswith("***...")

    restored = settings_route._restore_secrets(
        {"service_connections": masked["service_connections"]},
        current,
    )

    assert restored["service_connections"][0]["api_key"] == "sk-current"


def test_settings_api_masks_and_restores_nested_provider_secrets():
    current = core_settings.RuntimeSettings(
        providers=[
            {
                "id": "siliconflow",
                "name": "SiliconFlow",
                "provider_type": "siliconflow",
                "api_base": "https://api.siliconflow.cn/v1",
                "api_key": "sk-provider-current",
                "enabled": True,
                "models": [],
            }
        ],
    )

    masked = settings_route._mask_settings(current)
    assert masked["providers"][0]["api_key"].startswith("***...")

    restored = settings_route._restore_secrets(
        {"providers": masked["providers"]},
        current,
    )

    assert restored["providers"][0]["api_key"] == "sk-provider-current"


def test_settings_api_restores_masked_provider_secret_from_flat_key():
    current = core_settings.RuntimeSettings(
        deepseek_api_key="sk-flat-current",
        providers=[
            {
                "id": "deepseek",
                "name": "DeepSeek",
                "provider_type": "deepseek",
                "api_base": "https://api.deepseek.com",
                "api_key": "***...rent",
                "enabled": True,
                "models": [],
            }
        ],
    )

    restored = settings_route._restore_secrets(
        {
            "providers": [
                {
                    "id": "deepseek",
                    "name": "DeepSeek",
                    "provider_type": "deepseek",
                    "api_base": "https://api.deepseek.com",
                    "api_key": "***...rent",
                    "enabled": True,
                    "models": [],
                }
            ]
        },
        current,
    )

    assert restored["providers"][0]["api_key"] == "sk-flat-current"


def test_masked_provider_secret_does_not_overwrite_flat_key(isolated_settings_file):
    _write_config(
        isolated_settings_file,
        {
            "deepseek_api_key": "sk-flat-real",
            "deepseek_api_base": "https://api.deepseek.com",
            "providers": [
                {
                    "id": "deepseek",
                    "name": "DeepSeek",
                    "provider_type": "deepseek",
                    "api_base": "https://api.deepseek.com",
                    "api_key": "***...real",
                    "enabled": True,
                    "models": [],
                }
            ],
            "data_root": str(isolated_settings_file.parent / "data"),
        },
    )

    settings = core_settings._load_settings_from_file()
    data = settings.model_dump()

    assert data["deepseek_api_key"] == "sk-flat-real"
    assert _by_id(data["providers"], "deepseek")["api_key"] == "sk-flat-real"


def test_deleted_provider_ids_suppress_default_provider_regeneration(isolated_settings_file):
    _write_config(
        isolated_settings_file,
        {
            "vlm_api_base": "https://vision.example/v1",
            "vlm_api_key": "sk-vision",
            "vlm_model": "vision-model",
            "kb_embedding_api_base": "https://embedding.example/v1",
            "kb_embedding_api_key": "sk-embedding",
            "kb_embedding_model": "embedding-model",
            "deleted_provider_ids": [
                "custom-vision-default",
                "custom-embedding-default",
                "custom-2",
            ],
            "providers": [
                {
                    "id": "custom-2",
                    "name": "Custom 3",
                    "provider_type": "openai_compatible",
                    "api_base": "",
                    "api_key": "",
                    "enabled": True,
                    "models": [],
                }
            ],
            "data_root": str(isolated_settings_file.parent / "data"),
        },
    )

    settings = core_settings._load_settings_from_file()
    data = settings.model_dump()
    ids = {provider["id"] for provider in data["providers"]}

    assert "custom-vision-default" not in ids
    assert "custom-embedding-default" not in ids
    assert "custom-2" not in ids
    assert data["deleted_provider_ids"] == [
        "custom-2",
        "custom-embedding-default",
        "custom-vision-default",
    ]
