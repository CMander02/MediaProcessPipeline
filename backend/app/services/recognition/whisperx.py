"""WhisperX transcription service."""

import logging
from pathlib import Path
from typing import Any

# Fix PyTorch 2.6+ weights_only security issue for pyannote/whisperx models
import torch
try:
    import omegaconf
    torch.serialization.add_safe_globals([
        omegaconf.listconfig.ListConfig,
        omegaconf.dictconfig.DictConfig,
    ])
except (ImportError, AttributeError):
    pass

from app.core.config import get_settings
from app.api.routes.settings import get_runtime_settings
from app.models import TranscriptSegment

logger = logging.getLogger(__name__)


class WhisperXService:
    def __init__(self):
        self._model = None
        self._align_model = None
        self._align_model_lang = None  # Track language of loaded align model
        self._diarize_model = None
        self._current_model_path: str | None = None

    def _get_model_path(self) -> str:
        """Get whisper model path from runtime settings."""
        rt = get_runtime_settings()
        # 优先使用本地模型路径
        if rt.whisper_model_path:
            model_path = Path(rt.whisper_model_path)
            # 检查是否是目录（包含 model.safetensors 或 model.bin）
            if model_path.is_dir():
                # 检查是否有 model.safetensors 或 model.bin
                if (model_path / "model.safetensors").exists():
                    logger.info(f"Found safetensors model at: {model_path}")
                    return str(model_path)
                elif (model_path / "model.bin").exists():
                    logger.info(f"Found bin model at: {model_path}")
                    return str(model_path)
            return str(model_path)
        return rt.whisper_model

    def _ensure_init(self):
        """Initialize or reinitialize model with current settings."""
        rt = get_runtime_settings()
        model_path = self._get_model_path()

        # Check if we need to reinitialize (settings changed)
        if self._model is not None and self._current_model_path == model_path:
            return

        try:
            import whisperx

            logger.info(f"Loading WhisperX model: {model_path}")
            self._model = whisperx.load_model(
                model_path,
                device=rt.whisper_device,
                compute_type=rt.whisper_compute_type,
            )
            self._current_model_path = model_path
            # Reset align model when whisper model changes
            self._align_model = None
            self._align_model_lang = None
        except ImportError:
            logger.warning("whisperx not installed - mock mode")

    def _get_alignment_model_path(self, language: str) -> str | None:
        """Get alignment model path for specific language."""
        rt = get_runtime_settings()
        if language in ("zh", "cmn", "yue"):
            return rt.alignment_model_zh if rt.alignment_model_zh else None
        elif language in ("en", "eng"):
            return rt.alignment_model_en if rt.alignment_model_en else None
        return None

    def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        diarize: bool = True,
    ) -> dict[str, Any]:
        self._ensure_init()
        rt = get_runtime_settings()

        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise FileNotFoundError(f"File not found: {audio_path}")

        if self._model is None:
            logger.warning("Mock mode - returning placeholder")
            return {
                "language": "en",
                "segments": [{"start": 0.0, "end": 5.0, "text": "[Mock - WhisperX not installed]"}],
            }

        import whisperx

        logger.info(f"Transcribing: {audio_path}")
        audio = whisperx.load_audio(audio_path)
        result = self._model.transcribe(audio, batch_size=16, language=language)

        detected_lang = result.get("language", language or "unknown")

        # Alignment - reload if language changed
        if self._align_model is None or self._align_model_lang != detected_lang:
            align_model_path = self._get_alignment_model_path(detected_lang)
            if align_model_path:
                logger.info(f"Loading alignment model from: {align_model_path}")
                model_a, metadata = whisperx.load_align_model(
                    language_code=detected_lang,
                    device=rt.whisper_device,
                    model_name=align_model_path,
                )
            else:
                model_a, metadata = whisperx.load_align_model(
                    language_code=detected_lang, device=rt.whisper_device
                )
            self._align_model = (model_a, metadata)
            self._align_model_lang = detected_lang

        model_a, metadata = self._align_model
        result = whisperx.align(
            result["segments"], model_a, metadata, audio, rt.whisper_device
        )

        # Diarization
        if diarize and rt.enable_diarization and (rt.hf_token or rt.pyannote_model_path):
            if self._diarize_model is None:
                # 优先使用本地 pyannote 模型路径
                if rt.pyannote_model_path:
                    logger.info(f"Loading diarization model from: {rt.pyannote_model_path}")
                    self._diarize_model = whisperx.DiarizationPipeline(
                        model_name=rt.pyannote_model_path,
                        device=rt.whisper_device
                    )
                else:
                    self._diarize_model = whisperx.DiarizationPipeline(
                        use_auth_token=rt.hf_token, device=rt.whisper_device
                    )
            diarize_segments = self._diarize_model(audio)
            result = whisperx.assign_word_speakers(diarize_segments, result)

        return {"language": detected_lang, "segments": result.get("segments", [])}

    def to_segments(self, result: dict[str, Any]) -> list[TranscriptSegment]:
        return [
            TranscriptSegment(
                start=s.get("start", 0.0),
                end=s.get("end", 0.0),
                text=s.get("text", "").strip(),
                speaker=s.get("speaker"),
            )
            for s in result.get("segments", [])
        ]

    def to_srt(self, segments: list[TranscriptSegment]) -> str:
        lines = []
        for i, seg in enumerate(segments, 1):
            start = self._fmt_time(seg.start)
            end = self._fmt_time(seg.end)
            text = f"[{seg.speaker}] {seg.text}" if seg.speaker else seg.text
            lines.append(f"{i}\n{start} --> {end}\n{text}\n")
        return "\n".join(lines)

    def _fmt_time(self, seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


_service: WhisperXService | None = None


def get_whisperx_service() -> WhisperXService:
    global _service
    if _service is None:
        _service = WhisperXService()
    return _service


async def transcribe_audio(
    audio_path: str,
    language: str | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    service = get_whisperx_service()
    result = service.transcribe(audio_path, language)
    segments = service.to_segments(result)
    srt_content = service.to_srt(segments)

    # Save SRT file to output_dir if provided
    srt_path = None
    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        srt_path = output_dir / f"{Path(audio_path).stem}.srt"
        srt_path.write_text(srt_content, encoding="utf-8")
        logger.info(f"Saved SRT to: {srt_path}")

    return {
        "language": result["language"],
        "segments": [s.model_dump() for s in segments],
        "srt": srt_content,
        "srt_path": str(srt_path) if srt_path else None,
    }
