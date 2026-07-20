"""Recognition service entrypoint."""

import asyncio
import logging
from pathlib import Path
from typing import Any

from app.core.settings import get_runtime_settings
from app.services.recognition.base import ASRService

logger = logging.getLogger(__name__)

SUPPORTED_ASR_PROVIDERS = {"moss_cpp", "qwen3", "qwen3_gguf", "siliconflow"}

__all__ = [
    "SUPPORTED_ASR_PROVIDERS",
    "get_asr_service",
    "release_asr_models",
    "transcribe_audio",
]


def get_asr_service(provider: str | None = None) -> ASRService:
    """Get the configured singleton ASR service."""
    from app.core.model_router import resolve_asr_binding

    provider_id = resolve_asr_binding(
        get_runtime_settings(),
        task_options={"asr_provider": provider} if provider else None,
    ).provider
    if provider_id == "qwen3":
        from app.services.recognition.qwen3_asr import get_qwen3_service

        return get_qwen3_service()
    if provider_id == "qwen3_gguf":
        from app.services.recognition.qwen3_gguf_asr import get_qwen3_gguf_service

        return get_qwen3_gguf_service()
    if provider_id == "siliconflow":
        from app.services.recognition.siliconflow_asr import get_siliconflow_service

        return get_siliconflow_service()
    if provider_id == "moss_cpp":
        from app.services.recognition.moss_cpp_asr import get_moss_cpp_service

        return get_moss_cpp_service()
    supported = ", ".join(sorted(SUPPORTED_ASR_PROVIDERS))
    raise ValueError(f"Unsupported ASR provider '{provider_id}'. Supported providers: {supported}")


def release_asr_models() -> None:
    """Release ASR-owned GPU resources without binding queue.py to a provider."""
    from app.services.recognition.moss_cpp_asr import release_moss_cpp_service
    from app.services.recognition.qwen3_asr import release_qwen3_service
    from app.services.recognition.qwen3_gguf_asr import release_qwen3_gguf_service
    from app.services.recognition.siliconflow_asr import release_siliconflow_service

    release_moss_cpp_service()
    release_qwen3_gguf_service()
    release_qwen3_service()
    release_siliconflow_service()


async def transcribe_audio(
    audio_path: str,
    language: str | None = None,
    output_dir: Path | None = None,
    num_speakers: int | None = None,
    provider: str | None = None,
    diarize: bool = True,
    chunk_strategy: str | None = None,
    hotwords: list[str] | None = None,
    audio_processing_flow: str | None = None,
) -> dict[str, Any]:
    """Transcribe audio with the configured ASR provider and optionally write an SRT file."""
    from app.core.model_router import resolve_asr_binding

    options: dict[str, Any] = {}
    if provider:
        options["asr_provider"] = provider
    if audio_processing_flow:
        options["audio_processing_flow"] = audio_processing_flow
    if chunk_strategy:
        options["asr_chunk_strategy"] = chunk_strategy
    if num_speakers is not None:
        options["num_speakers"] = num_speakers
    if not diarize:
        options["disable_diarization"] = True

    binding = resolve_asr_binding(get_runtime_settings(), task_options=options, language=language)
    provider_id = binding.provider
    service = get_asr_service(provider_id)

    def _run_transcribe() -> dict[str, Any]:
        if provider_id == "qwen3_gguf":
            return service.transcribe(
                audio_path,
                language=binding.language,
                diarize=binding.diarize,
                num_speakers=binding.num_speakers,
                chunk_strategy=binding.chunk_strategy,
                hotwords=hotwords,
            )
        if provider_id == "siliconflow":
            return service.transcribe(
                audio_path,
                language=binding.language,
                diarize=binding.diarize,
                num_speakers=binding.num_speakers,
                chunk_strategy=binding.chunk_strategy,
            )
        if provider_id == "moss_cpp":
            return service.transcribe(
                audio_path,
                language=binding.language,
                diarize=True,
                num_speakers=binding.num_speakers,
                **binding.request_kwargs,
            )
        return service.transcribe(
            audio_path,
            language=binding.language,
            diarize=binding.diarize,
            num_speakers=binding.num_speakers,
        )

    result = await asyncio.to_thread(_run_transcribe)
    segments = service.to_segments(result)
    srt_content = service.to_srt(segments)
    detected_language = result.get("language", language or "unknown")
    speakers = result.get("speakers")
    if not isinstance(speakers, list):
        speakers = sorted({segment.speaker for segment in segments if segment.speaker})

    # Save SRT file to output_dir if provided
    srt_path = None
    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        srt_path = output_dir / f"{Path(audio_path).stem}.srt"
        srt_path.write_text(srt_content, encoding="utf-8")
        logger.info(f"Saved SRT to: {srt_path}")

    return {
        "provider": provider_id,
        "audio_processing_flow": "moss" if provider_id == "moss_cpp" else "asr",
        "language": detected_language or language or "unknown",
        "segments": [s.model_dump() if hasattr(s, 'model_dump') else s for s in segments],
        "speakers": speakers,
        "speaker_count": int(result.get("speaker_count", len(speakers))),
        "diarization": result.get("diarization", "pyannote" if binding.diarize else "none"),
        "srt": srt_content,
        "srt_path": str(srt_path) if srt_path else None,
    }
