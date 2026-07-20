import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core import pipeline as pipeline_core  # noqa: E402
from app.core.model_router import resolve_asr_binding  # noqa: E402
from app.core.settings import RuntimeSettings  # noqa: E402
from app.models import Task, TaskType  # noqa: E402


def test_asr_task_option_override_beats_runtime_provider():
    settings = RuntimeSettings(
        asr_provider="qwen3",
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
        asr_provider="qwen3",
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


def test_asr_settings_qwen3_binding_includes_model_and_diarization_flags():
    settings = RuntimeSettings(
        asr_provider="qwen3",
        qwen3_asr_model_path="D:/models/qwen3-asr",
        qwen3_aligner_model_path="D:/models/qwen3-aligner",
        qwen3_device="cuda",
        enable_diarization=True,
    )

    binding = resolve_asr_binding(
        settings,
        task_options={"num_speakers": 2, "disable_diarization": True},
    )

    assert binding.provider == "qwen3"
    assert binding.source == "settings"
    assert binding.model == "D:/models/qwen3-asr"
    assert binding.diarize is False
    assert binding.num_speakers == 2
    assert binding.request_kwargs["aligner_model_path"] == "D:/models/qwen3-aligner"


def test_asr_default_binding_uses_qwen3_gguf_hf_repo():
    settings = RuntimeSettings()

    binding = resolve_asr_binding(settings)

    assert binding.provider == "qwen3_gguf"
    assert binding.source == "settings"
    assert binding.model == "ggml-org/Qwen3-ASR-1.7B-GGUF:Q8_0"
    assert binding.diarize is False
    assert binding.chunk_strategy == "ffmpeg"
    assert binding.request_kwargs["hf_repo"] == "ggml-org/Qwen3-ASR-1.7B-GGUF:Q8_0"
    assert binding.request_kwargs["alias"] == "Qwen3-ASR-1.7B"


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


def test_explicit_asr_provider_overrides_moss_audio_flow():
    settings = RuntimeSettings(audio_processing_flow="moss", asr_provider="qwen3")

    binding = resolve_asr_binding(settings, task_options={"asr_provider": "qwen3"})

    assert binding.provider == "qwen3"
    assert binding.source == "task_option"


def test_asr_qwen3_gguf_binding_uses_local_model_pair_and_cpu():
    settings = RuntimeSettings(
        asr_provider="qwen3_gguf",
        qwen3_gguf_model_path="D:/models/Qwen3-ASR-1.7B-Q8_0.gguf",
        qwen3_gguf_mmproj_path="D:/models/mmproj-Qwen3-ASR-1.7B-Q8_0.gguf",
        qwen3_gguf_device="cpu",
        qwen3_gguf_chunk_strategy="ffmpeg",
        qwen3_gguf_ctx=2048,
        qwen3_gguf_n_gpu_layers=0,
        llama_cpp_binary_path="D:/tools/llama-server.exe",
    )

    binding = resolve_asr_binding(settings)

    assert binding.provider == "qwen3_gguf"
    assert binding.configured is True
    assert binding.model == "D:/models/Qwen3-ASR-1.7B-Q8_0.gguf"
    assert binding.chunk_strategy == "ffmpeg"
    assert binding.request_kwargs["model_path"] == "D:/models/Qwen3-ASR-1.7B-Q8_0.gguf"
    assert binding.request_kwargs["mmproj_path"] == "D:/models/mmproj-Qwen3-ASR-1.7B-Q8_0.gguf"
    assert binding.request_kwargs["device"] == "cpu"
    assert binding.request_kwargs["ctx"] == 2048
    assert binding.request_kwargs["n_gpu_layers"] == 0
    assert binding.request_kwargs["binary_path"] == "D:/tools/llama-server.exe"


def test_asr_qwen3_gguf_binding_rejects_partial_local_model_pair():
    settings = RuntimeSettings(
        asr_provider="qwen3_gguf",
        qwen3_gguf_model_path="D:/models/Qwen3-ASR-1.7B-Q8_0.gguf",
        qwen3_gguf_mmproj_path="",
    )

    binding = resolve_asr_binding(settings)

    assert binding.provider == "qwen3_gguf"
    assert binding.configured is False
    assert "must be set together" in binding.reason


def test_asr_qwen3_gguf_runtime_binding_local_path_uses_model_pair():
    settings = RuntimeSettings(
        qwen3_gguf_mmproj_path="D:/models/mmproj-Qwen3-ASR-1.7B-Q8_0.gguf",
        runtime_model_bindings={
            "asr": {
                "provider_id": "qwen3_gguf",
                "model_id": "D:/models/Qwen3-ASR-1.7B-Q8_0.gguf",
            }
        },
    )

    binding = resolve_asr_binding(settings)

    assert binding.provider == "qwen3_gguf"
    assert binding.configured is True
    assert binding.model == "D:/models/Qwen3-ASR-1.7B-Q8_0.gguf"
    assert binding.request_kwargs["model_path"] == "D:/models/Qwen3-ASR-1.7B-Q8_0.gguf"
    assert binding.request_kwargs["mmproj_path"] == "D:/models/mmproj-Qwen3-ASR-1.7B-Q8_0.gguf"


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
    assert binding.request_kwargs["default_params"]["max_file_mb"] == 50


def test_url_asr_fallback_prefers_configured_siliconflow(monkeypatch):
    settings = RuntimeSettings(
        asr_provider="qwen3",
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


def test_url_asr_fallback_uses_default_when_api_provider_missing(monkeypatch):
    settings = RuntimeSettings(asr_provider="qwen3", siliconflow_api_key="")
    monkeypatch.setattr(pipeline_core, "get_runtime_settings", lambda: settings)
    task = Task(task_type=TaskType.PIPELINE, source="https://example.com/video.mp4")

    provider, reason, is_api = pipeline_core._select_asr_provider_for_fallback(task)

    assert provider == "qwen3"
    assert reason == "default_asr_provider"
    assert is_api is False


def test_url_asr_fallback_preserves_moss_audio_flow(monkeypatch):
    settings = RuntimeSettings(
        audio_processing_flow="moss",
        siliconflow_api_key="sf-key",
    )
    monkeypatch.setattr(pipeline_core, "get_runtime_settings", lambda: settings)
    task = Task(task_type=TaskType.PIPELINE, source="https://example.com/video.mp4")

    provider, reason, is_api = pipeline_core._select_asr_provider_for_fallback(task)

    assert provider == "moss_cpp"
    assert reason == "audio_processing_flow"
    assert is_api is False
