import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

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
    monkeypatch.setattr(service, "_ensure_init", lambda _settings=None: None)

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

    separator_module = ModuleType("audio_separator.separator")
    separator_module.Separator = FakeSeparator
    package_module = ModuleType("audio_separator")
    package_module.separator = separator_module
    monkeypatch.setitem(sys.modules, "audio_separator", package_module)
    monkeypatch.setitem(sys.modules, "audio_separator.separator", separator_module)
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: False),
            device=lambda name: SimpleNamespace(type=name),
        ),
    )
    monkeypatch.setattr(
        uvr_mod,
        "get_runtime_settings",
        lambda: SimpleNamespace(
            uvr_model="UVR-MDX-NET-Inst_HQ_3",
            uvr_model_dir=str(model_dir),
            uvr_device="cpu",
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


def test_uvr_device_setting_is_validated():
    assert RuntimeSettings(uvr_device="CUDA").uvr_device == "cuda"
    assert RuntimeSettings(uvr_device="cpu").uvr_device == "cpu"

    with pytest.raises(ValidationError):
        RuntimeSettings(uvr_device="automatic")


def test_uvr_cuda_requires_onnx_cuda_provider(monkeypatch):
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True),
        device=lambda name: SimpleNamespace(type=name),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(uvr_mod, "_available_onnx_providers", lambda: ["CPUExecutionProvider"])

    with pytest.raises(RuntimeError, match="CUDAExecutionProvider"):
        UVRService()._configure_separator_device(
            SimpleNamespace(),
            "cuda",
            onnx_model=True,
        )


def test_uvr_device_configuration_selects_requested_provider(monkeypatch):
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True),
        device=lambda name: SimpleNamespace(type=name),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(
        uvr_mod,
        "_available_onnx_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )

    cuda_separator = SimpleNamespace()
    cuda_provider = UVRService()._configure_separator_device(
        cuda_separator,
        "cuda",
        onnx_model=True,
    )
    assert cuda_provider == "CUDAExecutionProvider"
    assert cuda_separator.torch_device.type == "cuda"
    assert cuda_separator.onnx_execution_provider == ["CUDAExecutionProvider"]

    cpu_separator = SimpleNamespace()
    cpu_provider = UVRService()._configure_separator_device(
        cpu_separator,
        "cpu",
        onnx_model=True,
    )
    assert cpu_provider == "CPUExecutionProvider"
    assert cpu_separator.torch_device.type == "cpu"
    assert cpu_separator.onnx_execution_provider == ["CPUExecutionProvider"]


def test_uvr_chunk_logs_emit_structured_progress(tmp_path, monkeypatch):
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"fake audio")
    vocals = tmp_path / "input_(Vocals).wav"
    progress_logger = logging.getLogger("tests.uvr.progress")
    progress_logger.setLevel(logging.INFO)

    class ProgressSeparator:
        output_dir = ""
        logger = progress_logger

        def separate(self, audio_path: str) -> list[str]:
            progress_logger.info("Splitting 900.0s audio into 3 chunks of 300.0s each")
            progress_logger.info("Processing chunk 1/3: chunk_0000.wav")
            progress_logger.info("Processing chunk 2/3: chunk_0001.wav")
            progress_logger.info("Processing chunk 3/3: chunk_0002.wav")
            progress_logger.info("Merging 3 chunks for stem: Vocals")
            vocals.write_bytes(b"vocals")
            return [str(vocals)]

    service = UVRService()
    service._separator = ProgressSeparator()
    service._current_device = "cuda"
    service._execution_provider = "CUDAExecutionProvider"
    monkeypatch.setattr(service, "_ensure_init", lambda _settings=None: None)
    settings_calls = 0

    def get_settings():
        nonlocal settings_calls
        settings_calls += 1
        return SimpleNamespace(uvr_model="UVR-MDX-NET-Inst_HQ_3")

    monkeypatch.setattr(uvr_mod, "get_runtime_settings", get_settings)

    updates: list[dict] = []
    result = service.separate(
        str(audio),
        output_dir=tmp_path,
        progress_callback=updates.append,
    )

    assert result["execution_provider"] == "CUDAExecutionProvider"
    assert settings_calls == 1
    assert [update["phase"] for update in updates] == [
        "starting",
        "splitting",
        "separating",
        "separating",
        "separating",
        "merging",
    ]
    assert updates[3]["progress"] == pytest.approx(1 / 3)
    assert updates[-1]["progress"] == pytest.approx(0.99)
    assert all(
        not isinstance(handler, uvr_mod._UVRProgressLogHandler)
        for handler in progress_logger.handlers
    )
