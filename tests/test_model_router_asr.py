import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.model_router import resolve_asr_binding  # noqa: E402
from app.core.settings import RuntimeSettings  # noqa: E402


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
