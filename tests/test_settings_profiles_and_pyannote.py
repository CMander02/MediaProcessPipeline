import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.api.routes import settings as settings_route  # noqa: E402
from app.core.settings import (  # noqa: E402
    CustomLLMProfile,
    RuntimeSettings,
    _normalize_custom_profile_state,
)
from app.services.recognition.qwen3_asr import Qwen3ASRService  # noqa: E402


def test_custom_profile_state_normalizes_active_profile():
    data = {
        "custom_llm_profiles": [
            {
                "id": "fast",
                "name": "Fast",
                "api_base": "http://fast.example/v1",
                "model": "fast-model",
                "api_key": "fast-key",
            },
            {
                "id": "quality",
                "name": "Quality",
                "api_base": "http://quality.example/v1",
                "model": "quality-model",
                "api_key": "quality-key",
            },
        ],
        "custom_active_profile_id": "quality",
    }

    _normalize_custom_profile_state(data, prefer_profiles=True)

    assert data["custom_name"] == "Quality"
    assert data["custom_api_base"] == "http://quality.example/v1"
    assert data["custom_model"] == "quality-model"
    assert data["custom_api_key"] == "quality-key"


def test_legacy_custom_fields_update_active_profile():
    data = {
        "custom_llm_profiles": [
            {
                "id": "default",
                "name": "Old",
                "api_base": "http://old.example/v1",
                "model": "old-model",
                "api_key": "old-key",
            },
        ],
        "custom_active_profile_id": "default",
        "custom_name": "New",
        "custom_api_base": "http://new.example/v1",
        "custom_model": "new-model",
        "custom_api_key": "new-key",
    }

    _normalize_custom_profile_state(data, prefer_profiles=False)

    profile = data["custom_llm_profiles"][0]
    assert profile["name"] == "New"
    assert profile["api_base"] == "http://new.example/v1"
    assert profile["model"] == "new-model"
    assert profile["api_key"] == "new-key"


def test_settings_api_masks_and_restores_nested_profile_secrets():
    current = RuntimeSettings(
        deepseek_api_key="sk-current",
        hf_proxy="http://user:pass@127.0.0.1:7897",
        custom_llm_profiles=[
            CustomLLMProfile(
                id="default",
                name="Default",
                api_base="http://example.test/v1",
                model="example-model",
                api_key="profile-secret",
            )
        ],
    )

    masked = settings_route._mask_settings(current)

    assert masked["deepseek_api_key"] == "********"
    assert masked["hf_proxy"] == "********"
    assert masked["custom_llm_profiles"][0]["api_key"] == "********"

    restored = settings_route._restore_secrets(
        {
            "deepseek_api_key": masked["deepseek_api_key"],
            "custom_llm_profiles": masked["custom_llm_profiles"],
        },
        current,
    )

    assert "deepseek_api_key" not in restored
    assert restored["custom_llm_profiles"][0]["api_key"] == "profile-secret"


def test_pyannote_config_rewrites_hub_ids_to_local_paths(tmp_path):
    pytest.importorskip("yaml")
    import yaml

    diarization_dir = tmp_path / "pyannote-speaker-diarization-3.1"
    segmentation_dir = tmp_path / "pyannote-segmentation-3.0"
    embedding_dir = tmp_path / "pyannote_wespeaker-voxceleb-resnet34-LM"
    diarization_dir.mkdir()
    segmentation_dir.mkdir()
    embedding_dir.mkdir()

    config_file = diarization_dir / "config.yaml"
    config_file.write_text(
        "\n".join(
            [
                "version: 3.1.0",
                "pipeline:",
                "  name: pyannote.audio.pipelines.SpeakerDiarization",
                "  params:",
                "    segmentation: pyannote/segmentation-3.0",
                "    embedding: pyannote/wespeaker-voxceleb-resnet34-LM",
                "params: {}",
            ]
        ),
        encoding="utf-8",
    )

    service = Qwen3ASRService()
    rt = RuntimeSettings(
        data_root=str(tmp_path / "data"),
        pyannote_segmentation_path=str(segmentation_dir),
        pyannote_embedding_path=str(embedding_dir),
    )

    resolved_config, local_dependencies = service._prepare_pyannote_config(str(config_file), rt)

    assert local_dependencies is True
    data = yaml.safe_load(Path(resolved_config).read_text(encoding="utf-8"))
    params = data["pipeline"]["params"]
    assert params["segmentation"] == str(segmentation_dir.resolve())
    assert params["embedding"] == str(embedding_dir.resolve())
