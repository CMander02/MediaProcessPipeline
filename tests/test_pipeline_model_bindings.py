import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.model_router import resolve_pipeline_model_bindings  # noqa: E402
from app.core.settings import RuntimeSettings  # noqa: E402


def _settings() -> RuntimeSettings:
    return RuntimeSettings(
        llm_provider="deepseek",
        deepseek_api_key="sk-deepseek",
        polish_provider="local",
        local_llm_model_path="D:/models/local-polish",
        asr_provider="qwen3",
        qwen3_asr_model_path="D:/models/qwen3-asr",
        vlm_api_base="https://vlm.example/v1",
        vlm_api_key="vlm-key",
        kb_enabled=True,
        kb_embedding_api_base="https://embed.example/v1",
        kb_embedding_api_key="embed-key",
    )


def test_pipeline_binding_for_platform_subtitle_uses_subtitle_processor():
    binding = resolve_pipeline_model_bindings(_settings(), has_platform_subtitle=True)

    assert binding.branch == "subtitle"
    assert binding.transcript_source == "platform"
    assert binding.run_separation is False
    assert binding.run_asr is False
    assert binding.run_subtitle_processor is True
    assert binding.run_polish is True
    assert binding.polish.provider == "deepseek"
    assert binding.run_kb_index is True


def test_pipeline_binding_without_subtitle_uses_asr_and_polish_provider():
    binding = resolve_pipeline_model_bindings(_settings(), has_platform_subtitle=False)

    assert binding.branch == "asr"
    assert binding.transcript_source == "asr"
    assert binding.run_separation is True
    assert binding.run_asr is True
    assert binding.run_subtitle_processor is False
    assert binding.asr.provider == "qwen3"
    assert binding.polish.provider == "local"


def test_pipeline_binding_for_image_note_uses_vlm_when_images_are_present():
    binding = resolve_pipeline_model_bindings(
        _settings(),
        content_subtype="image_note",
        has_images=True,
    )

    assert binding.branch == "image_note"
    assert binding.transcript_source == "note"
    assert binding.run_separation is False
    assert binding.run_asr is False
    assert binding.run_polish is False
    assert binding.run_analysis is True
    assert binding.run_vlm is True
    assert binding.vlm.model == "qwen2.5-vl-7b-instruct"


def test_pipeline_binding_for_text_note_skips_audio_and_vlm():
    binding = resolve_pipeline_model_bindings(
        _settings(),
        content_subtype="text_note",
        has_images=True,
    )

    assert binding.branch == "text_note"
    assert binding.transcript_source == "note"
    assert binding.run_separation is False
    assert binding.run_asr is False
    assert binding.run_polish is False
    assert binding.run_analysis is True
    assert binding.run_vlm is False
    assert binding.asr is None
