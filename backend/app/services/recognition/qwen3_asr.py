"""Qwen3-ASR transcription service."""

import logging
import os
import sys
from pathlib import Path
from typing import Any

import torch

from app.api.routes.settings import get_runtime_settings
from app.models import TranscriptSegment

logger = logging.getLogger(__name__)


def _get_short_path_win32(long_path: str) -> str:
    """Convert long path to short (8.3) path on Windows.

    This helps with non-ASCII paths that some libraries can't handle.
    """
    if sys.platform != "win32":
        return long_path

    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        buf_size = kernel32.GetShortPathNameW(long_path, None, 0)
        if buf_size == 0:
            return long_path
        buf = ctypes.create_unicode_buffer(buf_size)
        kernel32.GetShortPathNameW(long_path, buf, buf_size)
        return buf.value
    except Exception:
        return long_path


def _check_unicode_path_issue() -> bool:
    """Check if we're in a path with non-ASCII characters that could cause issues.

    Returns True if there's a potential Unicode path issue.
    """
    if sys.platform != "win32":
        return False

    # Check if the current working directory or site-packages has non-ASCII
    try:
        import site
        for path in site.getsitepackages():
            if not path.isascii():
                return True
        return not os.getcwd().isascii()
    except Exception:
        return False


# Check for Unicode path issues at module load time
_has_unicode_path_issue = _check_unicode_path_issue()
if _has_unicode_path_issue:
    logger.warning(
        "Detected non-ASCII characters in Python path. "
        "Qwen3-ASR may fail due to nagisa/dynet Unicode path limitations. "
        "Consider using WhisperX backend or moving project to an ASCII-only path."
    )


class Qwen3ASRService:
    """Qwen3-ASR based transcription service with optional forced alignment."""

    def __init__(self):
        self._model = None
        self._aligner = None
        self._current_model_path: str | None = None
        self._current_aligner_path: str | None = None
        self._diarize_model = None
        self._load_error: str | None = None

    def _get_model_path(self) -> str:
        """Get Qwen3-ASR model path from runtime settings."""
        rt = get_runtime_settings()
        if rt.qwen3_asr_model_path:
            return rt.qwen3_asr_model_path
        # Default to HuggingFace model ID
        return "Qwen/Qwen3-ASR-1.7B"

    def _get_aligner_path(self) -> str | None:
        """Get Qwen3 ForcedAligner model path from runtime settings."""
        rt = get_runtime_settings()
        if rt.qwen3_aligner_model_path:
            return rt.qwen3_aligner_model_path
        return None

    def _ensure_init(self):
        """Initialize or reinitialize model with current settings.

        Note: We no longer load ForcedAligner by default since we use VAD-based
        segmentation which provides sentence-level timestamps. This makes loading
        faster and uses less VRAM.
        """
        rt = get_runtime_settings()
        model_path = self._get_model_path()

        # Check if we need to reinitialize (settings changed)
        if self._model is not None and self._current_model_path == model_path:
            return

        try:
            # Import qwen_asr - may fail on Windows with non-ASCII paths due to nagisa
            from qwen_asr import Qwen3ASRModel

            logger.info(f"Loading Qwen3-ASR model: {model_path}")

            # Determine dtype based on device
            dtype = torch.bfloat16 if rt.qwen3_device.startswith("cuda") else torch.float32

            # Build model kwargs - no ForcedAligner needed with VAD-based approach
            model_kwargs = {
                "dtype": dtype,
                "device_map": rt.qwen3_device,
                "max_inference_batch_size": rt.qwen3_batch_size,
                "max_new_tokens": rt.qwen3_max_new_tokens,
            }

            # Note: We don't load ForcedAligner anymore since we use VAD for segmentation
            # This is faster and uses less VRAM while producing sentence-level output
            # similar to WhisperX

            self._model = Qwen3ASRModel.from_pretrained(model_path, **model_kwargs)
            self._current_model_path = model_path
            self._current_aligner_path = None  # Not used with VAD approach

            logger.info("Qwen3-ASR model loaded (VAD mode, no ForcedAligner)")

        except ImportError as e:
            logger.warning(f"qwen-asr not installed - mock mode: {e}")
            self._model = None
            self._load_error = f"qwen-asr not installed: {e}"
        except RuntimeError as e:
            self._load_error = f"Qwen3-ASR failed to load: {e}"
            logger.error(self._load_error)
            self._model = None
        except Exception as e:
            self._load_error = f"Qwen3-ASR failed to load: {e}"
            logger.error(self._load_error)
            self._model = None

    def _load_diarization_model(self, model_path: str, device: str):
        """Load diarization model from local path using pyannote directly.

        Returns a wrapper that accepts audio format (numpy array).
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
        rt = get_runtime_settings()
        diarization_batch_size = getattr(rt, 'diarization_batch_size', 16)

        if hasattr(pipeline, '_segmentation') and hasattr(pipeline._segmentation, 'batch_size'):
            pipeline._segmentation.batch_size = diarization_batch_size
            logger.info(f"Set segmentation batch_size={diarization_batch_size}")
        if hasattr(pipeline, '_embedding') and hasattr(pipeline._embedding, 'batch_size'):
            pipeline._embedding.batch_size = diarization_batch_size
            logger.info(f"Set embedding batch_size={diarization_batch_size}")

        # Create a wrapper that converts numpy audio to pyannote format
        class DiarizationWrapper:
            SAMPLE_RATE = 16000

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

                # Clear CUDA cache before processing
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

                diarization = self._pipeline(audio_data, **kwargs)

                # Convert to DataFrame format
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
        """Transcribe audio using Qwen3-ASR.

        Uses VAD-based segmentation for sentence-level timestamps (like WhisperX),
        instead of ForcedAligner's character-level timestamps.

        Args:
            audio_path: Path to audio file
            language: Language hint (None = auto-detect)
            diarize: Whether to perform speaker diarization

        Returns:
            dict with "language" and "segments" keys
        """
        self._ensure_init()
        rt = get_runtime_settings()

        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise FileNotFoundError(f"File not found: {audio_path}")

        if self._model is None:
            error_msg = self._load_error or "qwen-asr not available"
            logger.warning(f"Mock mode - returning placeholder: {error_msg}")
            return {
                "language": language or "zh",
                "segments": [{"start": 0.0, "end": 5.0, "text": f"[Qwen3-ASR error: {error_msg}]"}],
            }

        logger.info(f"Transcribing with Qwen3-ASR: {audio_path}")

        # Clear CUDA cache before transcription
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        # Use VAD-based segmentation for sentence-level timestamps
        # This is more like WhisperX behavior - VAD provides segment boundaries,
        # ASR provides text for each segment
        segments, detected_lang = self._transcribe_with_vad(audio_path, language)

        # Speaker diarization using Pyannote (if enabled)
        if diarize and rt.enable_diarization and rt.pyannote_model_path:
            segments = self._apply_diarization(audio_path, segments, rt)

        return {"language": detected_lang, "segments": segments}

    def _transcribe_with_vad(
        self,
        audio_path: str,
        language: str | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        """Transcribe audio using VAD segmentation + Qwen3-ASR.

        This approach:
        1. Uses Silero VAD to detect speech segments
        2. Extracts each segment as a separate audio chunk
        3. Transcribes each chunk with Qwen3-ASR (no ForcedAligner needed)
        4. Combines results with VAD-provided timestamps

        This is similar to how WhisperX works and produces sentence-level output.
        """
        import numpy as np
        import soundfile as sf
        import tempfile

        from app.services.preprocessing.vad_splitter import get_vad_splitter

        logger.info("Using VAD-based segmentation for Qwen3-ASR")

        # Load audio
        audio_data, sample_rate = sf.read(audio_path)

        # Ensure mono
        if len(audio_data.shape) > 1:
            audio_data = np.mean(audio_data, axis=1)

        # Get VAD speech timestamps
        vad = get_vad_splitter()
        vad._load_model()

        # Resample to 16kHz for VAD if needed
        if sample_rate != 16000:
            import torchaudio
            waveform = torch.from_numpy(audio_data).unsqueeze(0).float()
            resampler = torchaudio.transforms.Resample(sample_rate, 16000)
            waveform_16k = resampler(waveform).squeeze().numpy()
        else:
            waveform_16k = audio_data

        # Get speech timestamps using VAD
        logger.info("Running VAD to detect speech segments...")
        get_speech_ts = vad._utils[0]
        speech_timestamps = get_speech_ts(
            torch.from_numpy(waveform_16k),
            vad._model,
            sampling_rate=16000,
            return_seconds=False,
            # Merge short segments, split long ones
            min_speech_duration_ms=250,
            max_speech_duration_s=30,
            min_silence_duration_ms=300,
        )
        logger.info(f"VAD detected {len(speech_timestamps)} speech segments")

        if not speech_timestamps:
            # No speech detected, transcribe whole file
            logger.warning("No speech detected by VAD, transcribing whole file")
            results = self._model.transcribe(
                audio=audio_path,
                language=language,
                return_time_stamps=False,
            )
            if results and hasattr(results[0], 'text'):
                return [{
                    "start": 0.0,
                    "end": len(audio_data) / sample_rate,
                    "text": results[0].text.strip(),
                }], results[0].language if hasattr(results[0], 'language') else "unknown"
            return [], "unknown"

        # Process each VAD segment
        segments = []
        detected_lang = None

        with tempfile.TemporaryDirectory() as tmpdir:
            for i, ts in enumerate(speech_timestamps):
                start_sample = ts['start']
                end_sample = ts['end']

                # Convert to original sample rate
                start_sample_orig = int(start_sample * sample_rate / 16000)
                end_sample_orig = int(end_sample * sample_rate / 16000)

                # Extract segment
                segment_audio = audio_data[start_sample_orig:end_sample_orig]

                if len(segment_audio) < 100:  # Skip very short segments
                    continue

                # Save segment to temp file
                segment_path = Path(tmpdir) / f"segment_{i:04d}.wav"
                sf.write(str(segment_path), segment_audio, sample_rate)

                # Transcribe segment (no timestamps needed - VAD provides them)
                try:
                    results = self._model.transcribe(
                        audio=str(segment_path),
                        language=language,
                        return_time_stamps=False,
                    )

                    if results and hasattr(results[0], 'text') and results[0].text.strip():
                        start_sec = start_sample / 16000
                        end_sec = end_sample / 16000

                        segments.append({
                            "start": start_sec,
                            "end": end_sec,
                            "text": results[0].text.strip(),
                        })

                        if detected_lang is None and hasattr(results[0], 'language'):
                            detected_lang = results[0].language

                        if (i + 1) % 10 == 0:
                            logger.info(f"Transcribed {i + 1}/{len(speech_timestamps)} segments")

                except Exception as e:
                    logger.warning(f"Failed to transcribe segment {i}: {e}")
                    continue

        logger.info(f"VAD transcription complete: {len(segments)} segments")
        return segments, detected_lang or "unknown"

    def _transcribe_with_forced_aligner(
        self,
        audio_path: str,
        language: str | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        """Transcribe using ForcedAligner for character-level timestamps.

        This is the original approach - returns character/word level timestamps.
        Use _transcribe_with_vad for sentence-level output.
        """
        rt = get_runtime_settings()

        logger.info(f"Starting Qwen3-ASR transcribe with ForcedAligner...")
        results = self._model.transcribe(
            audio=str(audio_path),
            language=language,
            return_time_stamps=rt.qwen3_enable_timestamps,
        )
        logger.info(f"Qwen3-ASR transcribe complete, got {len(results)} result(s)")

        # Parse result
        segments, detected_lang = self._parse_result(results, rt.qwen3_enable_timestamps)
        logger.info(f"Parsed {len(segments)} segments, detected language: {detected_lang}")

        # Merge character-level segments into sentences
        if rt.qwen3_enable_timestamps and segments and len(segments) > 1:
            avg_text_len = sum(len(s.get("text", "")) for s in segments) / len(segments)
            if avg_text_len < 3:  # Likely character-level
                segments = self._merge_character_segments(segments)

        return segments, detected_lang

    def _parse_result(
        self, results: list[Any], return_time_stamps: bool
    ) -> tuple[list[dict[str, Any]], str]:
        """Parse Qwen3-ASR result into segment list.

        Qwen3ASRModel.transcribe() returns a list of ASRTranscription objects with:
        - .language: detected language
        - .text: full transcription text
        - .time_stamps: ForcedAlignResult (iterable of ForcedAlignItem objects)
                        Each ForcedAlignItem has: .text, .start_time, .end_time

        Returns:
            (segments, detected_language)
        """
        segments = []
        detected_lang = "unknown"

        for result in results:
            # Get detected language from first result
            if hasattr(result, 'language') and result.language:
                detected_lang = result.language

            if return_time_stamps and hasattr(result, 'time_stamps') and result.time_stamps:
                # time_stamps is a ForcedAlignResult containing ForcedAlignItem objects
                # Each ForcedAlignItem has .text, .start_time, .end_time attributes
                for item in result.time_stamps:
                    # ForcedAlignItem object with attributes
                    if hasattr(item, 'start_time') and hasattr(item, 'end_time'):
                        segments.append({
                            "start": float(item.start_time),
                            "end": float(item.end_time),
                            "text": str(item.text).strip() if hasattr(item, 'text') else "",
                        })
                    # Fallback: dict format
                    elif isinstance(item, dict):
                        segments.append({
                            "start": float(item.get("start_time", item.get("start", 0.0))),
                            "end": float(item.get("end_time", item.get("end", 0.0))),
                            "text": str(item.get("text", "")).strip(),
                        })
                    # Fallback: tuple format (start, end, text)
                    elif isinstance(item, (list, tuple)) and len(item) >= 3:
                        segments.append({
                            "start": float(item[0]),
                            "end": float(item[1]),
                            "text": str(item[2]).strip(),
                        })
            elif hasattr(result, 'text') and result.text:
                # No timestamps, just full text
                segments.append({
                    "start": 0.0,
                    "end": 0.0,
                    "text": result.text.strip(),
                })

        return segments, detected_lang

    def _merge_character_segments(
        self,
        segments: list[dict[str, Any]],
        max_gap: float = 0.5,
        max_duration: float = 10.0,
    ) -> list[dict[str, Any]]:
        """Merge character-level segments into sentence-level segments.

        For Chinese text, ForcedAligner returns character-level timestamps.
        This function merges consecutive characters into sentences based on:
        - Time gap between characters (if gap > max_gap, start new segment)
        - Maximum segment duration (if duration > max_duration, start new segment)
        - Punctuation marks (。！？；：，、) as natural break points

        Args:
            segments: List of character-level segments
            max_gap: Maximum time gap (seconds) between characters to merge
            max_duration: Maximum duration (seconds) for a single segment

        Returns:
            List of merged sentence-level segments
        """
        if not segments:
            return segments

        # Chinese sentence-ending punctuation
        sentence_end_punct = set("。！？；")
        # Chinese clause punctuation (can break but not required)
        clause_punct = set("，、：")

        merged = []
        current = {
            "start": segments[0].get("start", 0.0),
            "end": segments[0].get("end", 0.0),
            "text": segments[0].get("text", ""),
            "speaker": segments[0].get("speaker"),
        }

        for seg in segments[1:]:
            seg_start = seg.get("start", 0.0)
            seg_end = seg.get("end", 0.0)
            seg_text = seg.get("text", "")
            seg_speaker = seg.get("speaker")

            # Calculate gap from previous segment
            gap = seg_start - current["end"]
            current_duration = current["end"] - current["start"]

            # Decide whether to start a new segment
            should_break = False

            # Break on large time gap
            if gap > max_gap:
                should_break = True
            # Break if current segment is too long
            elif current_duration > max_duration:
                should_break = True
            # Break on sentence-ending punctuation
            elif current["text"] and current["text"][-1] in sentence_end_punct:
                should_break = True
            # Break on speaker change
            elif seg_speaker and current.get("speaker") and seg_speaker != current["speaker"]:
                should_break = True

            if should_break:
                # Save current segment if it has content
                if current["text"].strip():
                    merged.append(current)
                # Start new segment
                current = {
                    "start": seg_start,
                    "end": seg_end,
                    "text": seg_text,
                    "speaker": seg_speaker,
                }
            else:
                # Merge into current segment
                current["end"] = seg_end
                current["text"] += seg_text
                # Keep speaker from first segment with speaker info
                if seg_speaker and not current.get("speaker"):
                    current["speaker"] = seg_speaker

        # Don't forget the last segment
        if current["text"].strip():
            merged.append(current)

        logger.info(f"Merged {len(segments)} character segments into {len(merged)} sentence segments")
        return merged

    def _apply_diarization(
        self,
        audio_path: str,
        segments: list[dict[str, Any]],
        rt: Any,
    ) -> list[dict[str, Any]]:
        """Apply speaker diarization to segments using Pyannote."""
        import numpy as np
        import soundfile as sf

        # Load diarization model if not cached
        if self._diarize_model is None:
            self._diarize_model = self._load_diarization_model(
                rt.pyannote_model_path,
                rt.qwen3_device,
            )

        # Load audio for diarization
        logger.info(f"Loading audio for diarization: {audio_path}")
        audio_data, sample_rate = sf.read(audio_path)
        logger.info(f"Loaded audio: shape={audio_data.shape}, sample_rate={sample_rate}")

        # Ensure mono first (before resampling for efficiency)
        if len(audio_data.shape) > 1:
            logger.info("Converting to mono...")
            audio_data = np.mean(audio_data, axis=1)

        if sample_rate != 16000:
            # Resample to 16kHz
            import librosa
            logger.info(f"Resampling audio from {sample_rate}Hz to 16000Hz (this may take a moment)...")
            audio_data = librosa.resample(audio_data, orig_sr=sample_rate, target_sr=16000)
            logger.info(f"Resampling complete, new shape: {audio_data.shape}")

        duration_sec = len(audio_data) / 16000
        logger.info(f"Running diarization on {duration_sec:.1f}s audio...")
        # Run diarization
        diarize_df = self._diarize_model(audio_data.astype(np.float32))
        logger.info(f"Diarization complete: found {len(diarize_df)} speaker segments")

        # Assign speakers to segments based on overlap
        for seg in segments:
            seg_start = seg.get("start", 0.0)
            seg_end = seg.get("end", 0.0)

            if seg_start == 0.0 and seg_end == 0.0:
                # No timestamps, skip speaker assignment
                continue

            # Find speaker with maximum overlap
            max_overlap = 0.0
            best_speaker = None

            for _, row in diarize_df.iterrows():
                overlap_start = max(seg_start, row["start"])
                overlap_end = min(seg_end, row["end"])
                overlap = max(0.0, overlap_end - overlap_start)

                if overlap > max_overlap:
                    max_overlap = overlap
                    best_speaker = row["speaker"]

            seg["speaker"] = best_speaker

        return segments

    def to_segments(self, result: dict[str, Any]) -> list[TranscriptSegment]:
        """Convert transcription result to TranscriptSegment list."""
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
        """Generate SRT subtitle content from segments."""
        lines = []
        for i, seg in enumerate(segments, 1):
            start = self._fmt_time(seg.start)
            end = self._fmt_time(seg.end)
            text = f"[{seg.speaker}] {seg.text}" if seg.speaker else seg.text
            lines.append(f"{i}\n{start} --> {end}\n{text}\n")
        return "\n".join(lines)

    def _fmt_time(self, seconds: float) -> str:
        """Format seconds to SRT timestamp format."""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# Singleton instance
_service: Qwen3ASRService | None = None


def get_qwen3_service() -> Qwen3ASRService:
    """Get singleton Qwen3ASRService instance."""
    global _service
    if _service is None:
        _service = Qwen3ASRService()
    return _service
