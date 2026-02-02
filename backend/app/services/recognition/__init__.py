"""Recognition service - ASR transcription with backend selection."""

import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

from app.api.routes.settings import get_runtime_settings
from app.services.recognition.whisperx import WhisperXService, get_whisperx_service

# Lazy import for Qwen3ASR to avoid nagisa import issues at module load time
if TYPE_CHECKING:
    from app.services.recognition.qwen3_asr import Qwen3ASRService

logger = logging.getLogger(__name__)

__all__ = [
    "WhisperXService",
    "Qwen3ASRService",
    "get_asr_service",
    "transcribe_audio",
]


def get_asr_service() -> "WhisperXService | Qwen3ASRService":
    """Get ASR service based on runtime settings.

    Returns WhisperXService or Qwen3ASRService depending on asr_backend setting.
    """
    rt = get_runtime_settings()

    if rt.asr_backend == "qwen3":
        logger.debug("Using Qwen3-ASR backend")
        try:
            from app.services.recognition.qwen3_asr import get_qwen3_service
            return get_qwen3_service()
        except Exception as e:
            logger.error(f"Failed to load Qwen3-ASR backend: {e}")
            logger.warning("Falling back to WhisperX backend")
            return get_whisperx_service()
    else:
        logger.debug("Using WhisperX backend")
        return get_whisperx_service()


async def transcribe_audio(
    audio_path: str,
    language: str | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Transcribe audio file using the configured ASR backend.

    Automatically splits long audio (>30min) at VAD silence points and merges results.

    Args:
        audio_path: Path to audio file
        language: Language hint (None = auto-detect)
        output_dir: Directory to save SRT file

    Returns:
        dict with keys: language, segments, srt, srt_path
    """
    from app.services.preprocessing.vad_splitter import split_long_audio, merge_srt_segments

    rt = get_runtime_settings()
    service = get_asr_service()

    # Check if audio needs splitting (>30 minutes)
    audio_segments = await split_long_audio(audio_path, output_dir)

    if len(audio_segments) == 1 and audio_segments[0].get('is_original', True):
        # Short audio, process normally
        result = service.transcribe(audio_path, language)
        segments = service.to_segments(result)
        srt_content = service.to_srt(segments)
        detected_language = result.get("language", language or "unknown")
    else:
        # Long audio, process segments and merge
        logger.info(f"Processing {len(audio_segments)} audio segments")
        all_srt_contents = []
        all_segments = []
        detected_language = None

        for i, seg in enumerate(audio_segments):
            logger.info(f"Transcribing segment {i+1}/{len(audio_segments)}: {seg['path']}")
            result = service.transcribe(seg['path'], language)
            segments = service.to_segments(result)
            srt_content = service.to_srt(segments)

            all_srt_contents.append(srt_content)
            all_segments.extend(segments)

            if detected_language is None:
                detected_language = result.get("language")

        # Merge SRT with corrected timestamps
        srt_content = merge_srt_segments(audio_segments, all_srt_contents)
        segments = all_segments

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
