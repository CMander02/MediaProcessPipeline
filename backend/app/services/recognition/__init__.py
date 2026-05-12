"""Recognition service entrypoint.

Qwen3-ASR is the only supported ASR provider today. The provider boundary keeps
the rest of the pipeline from importing Qwen3 directly, so a future wholesale
model switch has one clear integration point.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

from app.core.settings import get_runtime_settings
from app.services.recognition.base import ASRService

logger = logging.getLogger(__name__)

SUPPORTED_ASR_PROVIDERS = {"qwen3"}

__all__ = [
    "SUPPORTED_ASR_PROVIDERS",
    "get_asr_service",
    "release_asr_models",
    "transcribe_audio",
]


def get_asr_service(provider: str | None = None) -> ASRService:
    """Get the configured singleton ASR service."""
    provider_id = (provider or get_runtime_settings().asr_provider).strip().lower()
    if provider_id == "qwen3":
        from app.services.recognition.qwen3_asr import get_qwen3_service

        return get_qwen3_service()
    supported = ", ".join(sorted(SUPPORTED_ASR_PROVIDERS))
    raise ValueError(f"Unsupported ASR provider '{provider_id}'. Supported providers: {supported}")


def release_asr_models() -> None:
    """Release ASR-owned GPU resources without binding queue.py to a provider."""
    from app.services.recognition.qwen3_asr import release_qwen3_service

    release_qwen3_service()


async def transcribe_audio(
    audio_path: str,
    language: str | None = None,
    output_dir: Path | None = None,
    num_speakers: int | None = None,
) -> dict[str, Any]:
    """Transcribe audio with the configured ASR provider and optionally write an SRT file."""
    service = get_asr_service()
    result = await asyncio.to_thread(service.transcribe, audio_path, language, num_speakers=num_speakers)
    segments = service.to_segments(result)
    srt_content = service.to_srt(segments)
    detected_language = result.get("language", language or "unknown")

    # Save SRT file to output_dir if provided
    srt_path = None
    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        srt_path = output_dir / f"{Path(audio_path).stem}.srt"
        srt_path.write_text(srt_content, encoding="utf-8")
        logger.info(f"Saved SRT to: {srt_path}")

    return {
        "language": detected_language or language or "unknown",
        "segments": [s.model_dump() if hasattr(s, 'model_dump') else s for s in segments],
        "srt": srt_content,
        "srt_path": str(srt_path) if srt_path else None,
    }
