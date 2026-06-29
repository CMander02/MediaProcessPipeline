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
    assert _models_for_connection(data, "vision-default")[0]["model_id"] == "vision-flat"
    assert _models_for_connection(data, "embedding-default")[0]["model_id"] == "embed-flat"


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
