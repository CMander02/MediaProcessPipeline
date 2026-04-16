"""Recognition service - Qwen3-ASR transcription entrypoint."""

import asyncio
import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.recognition.qwen3_asr import Qwen3ASRService

logger = logging.getLogger(__name__)

__all__ = [
    "get_asr_service",
    "transcribe_audio",
]


def get_asr_service() -> "Qwen3ASRService":
    """Get the singleton Qwen3-ASR service."""
    from app.services.recognition.qwen3_asr import get_qwen3_service
    return get_qwen3_service()


async def transcribe_audio(
    audio_path: str,
    language: str | None = None,
    output_dir: Path | None = None,
    num_speakers: int | None = None,
) -> dict[str, Any]:
    """Transcribe audio with Qwen3-ASR and optionally write an SRT file."""
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
