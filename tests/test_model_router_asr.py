import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core import pipeline as pipeline_core  # noqa: E402
from app.core.model_router import resolve_asr_binding  # noqa: E402
from app.core.settings import RuntimeSettings  # noqa: E402
from app.models import Task, TaskType  # noqa: E402


def _install_fake_sherpa_model(root: Path, model_id: str = "qwen3-asr-1.7b-onnx") -> Path:
    directory = root / model_id
    directory.mkdir(parents=True)
    for name in ("conv_frontend.onnx", "encoder.int8.onnx", "decoder.int8.onnx"):
        (directory / name).write_bytes(b"model")
    (directory / "tokenizer").mkdir()
    files = ("conv_frontend.onnx", "encoder.int8.onnx", "decoder.int8.onnx")
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "id": model_id,
                "source": "https://example.test/model",
                "license": "Apache-2.0",
                "checksums": {
                    name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                    for name in files
                },
            }
        ),
        encoding="utf-8",
    )
    return directory


def test_asr_task_option_override_beats_runtime_provider():
    settings = RuntimeSettings(
        siliconflow_api_base="https://api.siliconflow.cn",
        siliconflow_api_key="sf-key",
        siliconflow_asr_model="FunAudioLLM/SenseVoiceSmall",
        siliconflow_asr_chunk_strategy="ffmpeg",
    )

    binding = resolve_asr_binding(
        settings,
        task_options={"asr_provider": "siliconflow", "asr_chunk_strategy": "vad"},
        language="zh",
    )

    assert binding.provider == "siliconflow"
    assert binding.source == "task_option"
    assert binding.api_base == "https://api.siliconflow.cn/v1"
    assert binding.language == "zh"
    assert binding.chunk_strategy == "vad"
    assert binding.request_kwargs["endpoint"].endswith("/audio/transcriptions")


def test_asr_api_flow_selects_siliconflow_and_disables_diarization():
    settings = RuntimeSettings(
        siliconflow_api_base="https://asr.example/v1",
        siliconflow_api_key="sf-key",
        siliconflow_asr_model="asr-model",
        siliconflow_asr_language="en",
    )

    binding = resolve_asr_binding(settings, task_options={"api_flow": True})

    assert binding.provider == "siliconflow"
    assert binding.source == "api_flow"
    assert binding.diarize is False
    assert binding.language == "en"
    assert binding.configured is True


def test_sherpa_binding_includes_model_runtime_and_diarization_flags(tmp_path):
    _install_fake_sherpa_model(tmp_path)
    settings = RuntimeSettings(
        sherpa_model_id="qwen3-asr-1.7b-onnx",
        sherpa_model_root=str(tmp_path),
        sherpa_device="cuda",
        sherpa_num_threads=6,
        sherpa_vad_model_path="D:/models/silero-vad.onnx",
        qwen3_aligner_model_path="D:/models/qwen3-aligner",
        enable_diarization=True,
        pyannote_model_path="D:/models/pyannote",
    )

    binding = resolve_asr_binding(
        settings,
        task_options={
            "num_speakers": 2,
            "disable_diarization": True,
            "asr_timestamp_mode": "qwen_forced",
        },
    )

    assert binding.provider == "sherpa_onnx"
    assert binding.model == "qwen3-asr-1.7b-onnx"
    assert binding.configured is True
    assert binding.diarize is False
    assert binding.num_speakers == 2
    assert binding.request_kwargs["device"] == "cuda"
    assert binding.request_kwargs["num_threads"] == 6
    assert binding.request_kwargs["timestamp_mode"] == "qwen_forced"
    assert binding.request_kwargs["aligner_model_path"] == "D:/models/qwen3-aligner"


def test_sherpa_default_binding_reports_missing_model_bundle():
    settings = RuntimeSettings(sherpa_model_root="Z:/missing/sherpa-models")

    binding = resolve_asr_binding(settings)

    assert binding.provider == "sherpa_onnx"
    assert binding.model == "sensevoice-small-int8"
    assert binding.configured is False
    assert "not installed" in binding.reason
    assert binding.chunk_strategy == "vad"


def test_sherpa_runtime_binding_precedes_flat_model_field(tmp_path):
    _install_fake_sherpa_model(tmp_path)
    settings = RuntimeSettings(
        sherpa_model_id="sensevoice-small-int8",
        sherpa_model_root=str(tmp_path),
        runtime_model_bindings={
            "asr": {
                "provider_id": "sherpa_onnx",
                "model_id": "qwen3-asr-1.7b-onnx",
                "capability": "asr",
            }
        },
    )

    binding = resolve_asr_binding(settings)

    assert binding.configured is True
    assert binding.model == "qwen3-asr-1.7b-onnx"


def test_sherpa_task_model_override_selects_installed_bundle(tmp_path):
    model = tmp_path / "sensevoice-small-int8"
    model.mkdir()
    (model / "model.int8.onnx").write_bytes(b"model")
    (model / "tokens.txt").write_text("token", encoding="utf-8")
    (model / "manifest.json").write_text(
        json.dumps(
            {
                "id": "sensevoice-small-int8",
                "source": "https://example.test/model",
                "license": "Apache-2.0",
                "checksums": {
                    "model.int8.onnx": hashlib.sha256(b"model").hexdigest(),
                    "tokens.txt": hashlib.sha256(b"token").hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )
    settings = RuntimeSettings(sherpa_model_root=str(tmp_path))

    binding = resolve_asr_binding(
        settings,
        task_options={"asr_model": "sensevoice-small-int8", "asr_chunk_strategy": "fixed"},
    )

    assert binding.configured is True
    assert binding.model == "sensevoice-small-int8"
    assert binding.chunk_strategy == "fixed"


def test_moss_audio_flow_selects_cpp_engine_and_model(tmp_path):
    binary = tmp_path / "moss-transcribe.exe"
    model = tmp_path / "moss-transcribe-q5_k.gguf"
    binary.write_bytes(b"binary")
    model.write_bytes(b"model")
    settings = RuntimeSettings(
        audio_processing_flow="moss",
        moss_cpp_binary_path=str(binary),
        moss_cpp_model_path=str(model),
        moss_cpp_device="cpu",
        moss_cpp_threads=6,
    )

    binding = resolve_asr_binding(
        settings,
        task_options={"num_speakers": 3},
        language="zh",
    )

    assert binding.provider == "moss_cpp"
    assert binding.source == "audio_flow"
    assert binding.model == str(model.resolve())
    assert binding.diarize is True
    assert binding.num_speakers == 3
    assert binding.request_kwargs["binary_path"] == str(binary.resolve())
    assert binding.request_kwargs["device"] == "cpu"
    assert binding.request_kwargs["threads"] == 6
    assert binding.request_kwargs["max_new_tokens"] == 8192


def test_explicit_sherpa_provider_overrides_moss_audio_flow(tmp_path):
    _install_fake_sherpa_model(tmp_path)
    settings = RuntimeSettings(
        audio_processing_flow="moss",
        sherpa_model_id="qwen3-asr-1.7b-onnx",
        sherpa_model_root=str(tmp_path),
    )

    binding = resolve_asr_binding(
        settings,
        task_options={"asr_provider": "sherpa_onnx"},
    )

    assert binding.provider == "sherpa_onnx"
    assert binding.source == "task_option"
    assert binding.configured is True


def test_sherpa_binding_enables_global_pyannote_diarization(tmp_path):
    _install_fake_sherpa_model(tmp_path)
    settings = RuntimeSettings(
        sherpa_model_root=str(tmp_path),
        enable_diarization=True,
        pyannote_model_path="D:/models/pyannote-speaker-diarization-3.1",
    )

    binding = resolve_asr_binding(settings, task_options={"num_speakers": 2})

    assert binding.diarize is True
    assert binding.num_speakers == 2
    assert binding.request_kwargs["diarize"] is True


def test_asr_api_binding_enables_global_pyannote_diarization():
    settings = RuntimeSettings(
        asr_provider="siliconflow",
        siliconflow_api_base="https://asr.example/v1",
        siliconflow_api_key="sf-key",
        siliconflow_asr_model="asr-model",
        enable_diarization=True,
        pyannote_model_path="D:/models/pyannote-speaker-diarization-3.1",
    )

    binding = resolve_asr_binding(settings, task_options={"num_speakers": 2})

    assert binding.diarize is True
    assert binding.request_kwargs["diarize"] is True


def test_asr_runtime_binding_uses_siliconflow_provider_model_metadata():
    settings = RuntimeSettings(
        siliconflow_api_key="flat-key",
        providers=[
            {
                "id": "siliconflow",
                "name": "SiliconFlow",
                "provider_type": "siliconflow",
                "api_base": "https://api.siliconflow.cn/v1",
                "api_key": "provider-key",
                "enabled": True,
                "models": [
                    {
                        "id": "siliconflow:TeleAI/TeleSpeechASR",
                        "model_id": "TeleAI/TeleSpeechASR",
                        "model_type": "asr",
                        "capabilities": ["asr"],
                        "endpoint_path": "/audio/transcriptions",
                        "enabled": True,
                        "default_params": {
                            "request_format": "multipart",
                            "file_field": "file",
                            "model_field": "model",
                            "max_file_mb": 50,
                            "max_duration_sec": 3600,
                        },
                    }
                ],
            }
        ],
        runtime_model_bindings={
            "asr": {
                "provider_id": "siliconflow",
                "model_id": "TeleAI/TeleSpeechASR",
                "capability": "asr",
            }
        },
    )

    binding = resolve_asr_binding(settings)

    assert binding.provider == "siliconflow"
    assert binding.source == "runtime_binding"
    assert binding.model == "TeleAI/TeleSpeechASR"
    assert binding.api_key == "provider-key"
    assert binding.request_kwargs["endpoint"] == "https://api.siliconflow.cn/v1/audio/transcriptions"
    assert binding.request_kwargs["default_params"]["request_format"] == "multipart"


def test_url_asr_fallback_prefers_configured_siliconflow(monkeypatch):
    settings = RuntimeSettings(
        siliconflow_api_base="https://api.siliconflow.cn",
        siliconflow_api_key="sf-key",
        siliconflow_asr_model="FunAudioLLM/SenseVoiceSmall",
    )
    monkeypatch.setattr(pipeline_core, "get_runtime_settings", lambda: settings)
    task = Task(task_type=TaskType.PIPELINE, source="https://example.com/video.mp4")

    provider, reason, is_api = pipeline_core._select_asr_provider_for_fallback(task)

    assert provider == "siliconflow"
    assert reason == "siliconflow_configured"
    assert is_api is True


def test_url_asr_fallback_uses_sherpa_when_api_provider_missing(monkeypatch):
    settings = RuntimeSettings(siliconflow_api_key="")
    monkeypatch.setattr(pipeline_core, "get_runtime_settings", lambda: settings)
    task = Task(task_type=TaskType.PIPELINE, source="https://example.com/video.mp4")

    provider, reason, is_api = pipeline_core._select_asr_provider_for_fallback(task)

    assert provider == "sherpa_onnx"
    assert reason == "default_asr_provider"
    assert is_api is False


def test_url_asr_fallback_preserves_moss_audio_flow(monkeypatch):
    settings = RuntimeSettings(audio_processing_flow="moss", siliconflow_api_key="sf-key")
    monkeypatch.setattr(pipeline_core, "get_runtime_settings", lambda: settings)
    task = Task(task_type=TaskType.PIPELINE, source="https://example.com/video.mp4")

    provider, reason, is_api = pipeline_core._select_asr_provider_for_fallback(task)

    assert provider == "moss_cpp"
    assert reason == "audio_processing_flow"
    assert is_api is False
