import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.pipeline import _require_audio_file  # noqa: E402
from app.core.settings import RuntimeSettings  # noqa: E402
from app.services.preprocessing import uvr as uvr_mod  # noqa: E402
from app.services.preprocessing.uvr import UVRService  # noqa: E402


class EmptySeparator:
    output_dir = ""

    def separate(self, audio_path: str) -> list[str]:
        return []


def test_require_audio_file_rejects_missing_path():
    with pytest.raises(RuntimeError, match="no audio path is available"):
        _require_audio_file(None, stage="ASR transcription")


def test_uvr_separation_empty_output_is_failure(tmp_path, monkeypatch):
    audio = tmp_path / "input.mp3"
    audio.write_bytes(b"fake mp3")

    service = UVRService()
    service._separator = EmptySeparator()
    monkeypatch.setattr(service, "_ensure_init", lambda: None)

    with pytest.raises(RuntimeError, match="did not produce any output"):
        service.separate(str(audio), output_dir=tmp_path)


def test_uvr_separator_uses_configured_chunk_duration(tmp_path, monkeypatch):
    model_dir = tmp_path / "models"
    mdx_dir = model_dir / "MDX_Net_Models"
    mdx_dir.mkdir(parents=True)
    (mdx_dir / "UVR-MDX-NET-Inst_HQ_3.onnx").write_bytes(b"fake model")

    created: list[dict] = []

    class FakeSeparator:
        def __init__(self, **kwargs):
            created.append(kwargs)

        def load_model(self, model_name: str) -> None:
            self.model_name = model_name

    monkeypatch.setattr("audio_separator.separator.Separator", FakeSeparator)
    monkeypatch.setattr(
        uvr_mod,
        "get_runtime_settings",
        lambda: SimpleNamespace(
            uvr_model="UVR-MDX-NET-Inst_HQ_3",
            uvr_model_dir=str(model_dir),
            uvr_chunk_duration_sec=300.0,
        ),
    )

    service = UVRService()
    service._ensure_init()

    assert created[0]["chunk_duration"] == 300.0


def test_uvr_chunk_duration_setting_is_non_negative():
    with pytest.raises(ValidationError):
        RuntimeSettings(uvr_chunk_duration_sec=-1)

    assert RuntimeSettings(uvr_chunk_duration_sec=0).uvr_chunk_duration_sec == 0
