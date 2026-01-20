"""WhisperX transcription service."""

import logging
from pathlib import Path
from typing import Any

# Fix PyTorch 2.6+ weights_only security issue for pyannote/whisperx models
# Patch lightning_fabric's _load to force weights_only=False
import torch
try:
    import lightning_fabric.utilities.cloud_io as cloud_io

    _original_lightning_load = cloud_io._load

    def _patched_lightning_load(path_or_url, map_location=None, **kwargs):
        # Force weights_only=False to load pyannote models
        kwargs['weights_only'] = False
        return torch.load(path_or_url, map_location=map_location, **kwargs)

    cloud_io._load = _patched_lightning_load
except (ImportError, AttributeError):
    pass

# Also add omegaconf classes for whisper model loading (silero VAD etc)
try:
    from omegaconf import ListConfig, DictConfig
    from omegaconf.base import ContainerMetadata, SCMode
    from omegaconf.nodes import ValueNode
    torch.serialization.add_safe_globals([
        ListConfig, DictConfig, ContainerMetadata, SCMode, ValueNode,
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
                vad_method="silero",  # Use Silero VAD to avoid PyTorch 2.6+ compatibility issues
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

    def _load_diarization_model(self, model_path: str, device: str):
        """Load diarization model from local path using pyannote directly.

        Returns a wrapper that accepts whisperx audio format (numpy array).
        Optimized for long audio files (2+ hours) with reduced batch sizes.
        """
        from pyannote.audio import Pipeline
        import pandas as pd
        import numpy as np

        path = Path(model_path)

        # If it's a directory, look for config.yaml inside
        if path.is_dir():
            config_file = path / "config.yaml"
            if config_file.exists():
                model_path = str(config_file)
            else:
                raise FileNotFoundError(f"config.yaml not found in {path}")

        logger.info(f"Loading diarization model from: {model_path}")
        pipeline = Pipeline.from_pretrained(model_path)
        pipeline = pipeline.to(torch.device(device))

        # Optimize for long audio: reduce batch sizes to prevent OOM
        # Get batch size from runtime settings
        rt = get_runtime_settings()
        diarization_batch_size = rt.diarization_batch_size if hasattr(rt, 'diarization_batch_size') else 16

        if hasattr(pipeline, '_segmentation') and hasattr(pipeline._segmentation, 'batch_size'):
            pipeline._segmentation.batch_size = diarization_batch_size
            logger.info(f"Set segmentation batch_size={diarization_batch_size}")
        if hasattr(pipeline, '_embedding') and hasattr(pipeline._embedding, 'batch_size'):
            pipeline._embedding.batch_size = diarization_batch_size
            logger.info(f"Set embedding batch_size={diarization_batch_size}")

        # Create a wrapper that converts whisperx audio format to pyannote format
        class DiarizationWrapper:
            SAMPLE_RATE = 16000  # whisperx default sample rate

            def __init__(self, pipeline):
                self._pipeline = pipeline

            def __call__(self, audio, **kwargs):
                # Convert numpy array to pyannote format
                if isinstance(audio, np.ndarray):
                    audio_data = {
                        "waveform": torch.from_numpy(audio[None, :]),
                        "sample_rate": self.SAMPLE_RATE
                    }
                else:
                    audio_data = audio

                # Run diarization with memory optimization
                # Clear CUDA cache before processing
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

                diarization = self._pipeline(audio_data, **kwargs)

                # Convert to DataFrame format expected by whisperx
                diarize_df = pd.DataFrame(
                    diarization.itertracks(yield_label=True),
                    columns=['segment', 'label', 'speaker']
                )
                diarize_df['start'] = diarize_df['segment'].apply(lambda x: x.start)
                diarize_df['end'] = diarize_df['segment'].apply(lambda x: x.end)

                # Clear cache after processing
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

                return diarize_df

        return DiarizationWrapper(pipeline)

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

        # Calculate audio duration for logging and batch size optimization
        audio_duration_sec = len(audio) / 16000  # 16kHz sample rate
        audio_duration_min = audio_duration_sec / 60
        logger.info(f"Audio duration: {audio_duration_min:.1f} minutes")

        # Get base batch size from settings
        base_batch_size = rt.whisper_batch_size if hasattr(rt, 'whisper_batch_size') else 16

        # Auto-reduce batch size for very long audio to prevent OOM
        if audio_duration_min > 60:
            batch_size = min(base_batch_size, 8)
            logger.info(f"Long audio (>{60}min), using batch_size={batch_size}")
        elif audio_duration_min > 30:
            batch_size = min(base_batch_size, 12)
        else:
            batch_size = base_batch_size

        # Clear CUDA cache before transcription
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        result = self._model.transcribe(audio, batch_size=batch_size, language=language)

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

        # Diarization (speaker identification)
        if diarize and rt.enable_diarization and (rt.hf_token or rt.pyannote_model_path):
            from whisperx.diarize import assign_word_speakers

            if self._diarize_model is None:
                # 优先使用本地 pyannote 模型路径
                if rt.pyannote_model_path:
                    self._diarize_model = self._load_diarization_model(rt.pyannote_model_path, rt.whisper_device)
                else:
                    logger.info("Loading diarization model from HuggingFace...")
                    from whisperx.diarize import DiarizationPipeline
                    self._diarize_model = DiarizationPipeline(
                        use_auth_token=rt.hf_token, device=rt.whisper_device
                    )
            diarize_segments = self._diarize_model(audio)
            result = assign_word_speakers(diarize_segments, result)

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
    """
    Transcribe audio file, automatically splitting long audio (>30min)
    at VAD silence points and merging results.
    """
    from app.services.preprocessing.vad_splitter import split_long_audio, merge_srt_segments

    service = get_whisperx_service()

    # Check if audio needs splitting (>30 minutes)
    audio_segments = await split_long_audio(audio_path, output_dir)

    if len(audio_segments) == 1 and audio_segments[0].get('is_original', True):
        # Short audio, process normally
        result = service.transcribe(audio_path, language)
        segments = service.to_segments(result)
        srt_content = service.to_srt(segments)
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
        "language": result.get("language", language or "unknown") if len(audio_segments) == 1 else detected_language,
        "segments": [s.model_dump() if hasattr(s, 'model_dump') else s for s in segments],
        "srt": srt_content,
        "srt_path": str(srt_path) if srt_path else None,
    }
