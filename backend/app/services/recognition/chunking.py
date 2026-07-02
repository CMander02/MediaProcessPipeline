"""Shared ASR audio chunking helpers."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioChunk:
    start: float
    end: float

    def as_dict(self) -> dict[str, float]:
        return {"start": self.start, "end": self.end}


class ASRChunker:
    """Chunk audio for ASR providers using Silero VAD or fixed ffmpeg slices."""

    def __init__(self, *, silero_onnx_model_path: str = "") -> None:
        self.silero_onnx_model_path = silero_onnx_model_path
        self._torch_vad_model = None
        self._torch_vad_utils = None

    def chunks(
        self,
        audio_path: str | Path,
        *,
        strategy: str,
        max_duration: float = 30.0,
        allow_fallback: bool = True,
    ) -> list[AudioChunk]:
        audio = Path(audio_path)
        normalized = self._normalize_strategy(strategy)
        try:
            if normalized == "silero_onnx":
                return self._silero_onnx_chunks(audio, max_duration)
            if normalized == "silero_torch":
                return self._silero_torch_chunks(audio, max_duration)
            return self.fixed_chunks(audio, max_duration)
        except Exception:
            if not allow_fallback:
                raise
            logger.warning(
                "ASR chunking strategy %s failed; falling back to ffmpeg fixed chunks",
                normalized,
                exc_info=True,
            )
            return self.fixed_chunks(audio, max_duration)

    def fixed_chunks(self, audio_path: str | Path, max_duration: float) -> list[AudioChunk]:
        duration = self.probe_duration(audio_path)
        if duration <= 0:
            return [AudioChunk(0.0, round(max_duration, 3))]

        chunks: list[AudioChunk] = []
        start = 0.0
        while start < duration:
            end = min(start + max_duration, duration)
            chunks.append(AudioChunk(round(start, 3), round(end, 3)))
            start = end
        return chunks or [AudioChunk(0.0, round(duration, 3))]

    def export_wav(self, audio_path: str | Path, chunk: AudioChunk, wav_path: str | Path) -> None:
        duration = max(0.0, chunk.end - chunk.start)
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{chunk.start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            str(wav_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except FileNotFoundError as e:
            raise RuntimeError("ffmpeg not found in PATH; install FFmpeg for ASR chunking") from e
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode(errors="replace") if e.stderr else ""
            raise RuntimeError(f"ffmpeg failed to export ASR chunk: {stderr[:500]}") from e

    def probe_duration(self, audio_path: str | Path) -> float:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return float(result.stdout.strip())
        except FileNotFoundError as e:
            raise RuntimeError("ffprobe not found in PATH; install FFmpeg for ASR chunking") from e
        except (subprocess.CalledProcessError, ValueError) as e:
            raise RuntimeError(f"Failed to probe audio duration: {audio_path}") from e

    @staticmethod
    def _normalize_strategy(strategy: str | None) -> str:
        normalized = (strategy or "ffmpeg").strip().lower()
        aliases = {
            "vad": "silero_torch",
            "torch": "silero_torch",
            "silero": "silero_torch",
            "onnx": "silero_onnx",
            "auto": "silero_onnx",
            "fixed": "ffmpeg",
        }
        return aliases.get(normalized, normalized)

    def _silero_torch_chunks(self, audio_path: Path, max_duration: float) -> list[AudioChunk]:
        import torch
        import torchaudio

        if self._torch_vad_model is None:
            torch.serialization.add_safe_globals([torch.torch_version.TorchVersion])
            model, utils = torch.hub.load("snakers4/silero-vad", "silero_vad", trust_repo=True)
            self._torch_vad_model = model
            self._torch_vad_utils = utils

        waveform, sample_rate = torchaudio.load(str(audio_path))
        if waveform.shape[0] > 1:
            waveform = waveform.mean(dim=0, keepdim=True)
        if sample_rate != 16000:
            waveform = torchaudio.functional.resample(waveform, sample_rate, 16000)
            sample_rate = 16000

        get_ts = self._torch_vad_utils[0]
        timestamps = get_ts(
            waveform.squeeze(),
            self._torch_vad_model,
            sampling_rate=sample_rate,
            return_seconds=True,
        )
        if not timestamps:
            duration = waveform.shape[1] / sample_rate
            return [AudioChunk(0.0, round(float(duration), 3))]

        raw: list[AudioChunk] = []
        for ts in timestamps:
            start = ts.get("start", ts.get("start_sec", 0))
            end = ts.get("end", ts.get("end_sec", 0))
            if isinstance(start, int) and start > 1000:
                start = start / sample_rate
                end = end / sample_rate
            raw.append(AudioChunk(float(start), float(end)))
        return self._merge_and_split(raw, max_duration)

    def _silero_onnx_chunks(self, audio_path: Path, max_duration: float) -> list[AudioChunk]:
        model_path = Path(self.silero_onnx_model_path).expanduser()
        if not self.silero_onnx_model_path or not model_path.exists():
            raise FileNotFoundError(
                "Silero ONNX model path is empty or missing; configure silero_onnx_model_path"
            )

        import numpy as np
        import onnxruntime as ort

        waveform = self._load_audio_float32(audio_path)
        if waveform.size == 0:
            return self.fixed_chunks(audio_path, max_duration)

        session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        input_names = [item.name for item in session.get_inputs()]
        output_names = [item.name for item in session.get_outputs()]
        input_name = "input" if "input" in input_names else input_names[0]
        state_name = "state" if "state" in input_names else None
        sr_name = "sr" if "sr" in input_names else None

        sample_rate = 16000
        window = 512
        threshold = 0.5
        neg_threshold = 0.35
        min_silence = int(0.5 * sample_rate)
        speech_pad = int(0.1 * sample_rate)
        state = np.zeros((2, 1, 128), dtype=np.float32)

        raw: list[AudioChunk] = []
        triggered = False
        speech_start = 0
        silence_start: int | None = None

        for offset in range(0, waveform.size, window):
            frame = waveform[offset:offset + window]
            if frame.size < window:
                frame = np.pad(frame, (0, window - frame.size))
            feed: dict[str, Any] = {input_name: frame.reshape(1, -1).astype(np.float32)}
            if state_name:
                feed[state_name] = state
            if sr_name:
                feed[sr_name] = np.array(sample_rate, dtype=np.int64)

            outputs = session.run(output_names or None, feed)
            prob = float(np.ravel(outputs[0])[0])
            if state_name and len(outputs) > 1:
                candidate = outputs[1]
                if getattr(candidate, "shape", None) == state.shape:
                    state = candidate

            if prob >= threshold and not triggered:
                triggered = True
                speech_start = max(0, offset - speech_pad)
                silence_start = None
            elif prob < neg_threshold and triggered:
                if silence_start is None:
                    silence_start = offset
                if offset - silence_start >= min_silence:
                    raw.append(
                        AudioChunk(
                            round(speech_start / sample_rate, 3),
                            round(min(waveform.size, silence_start + speech_pad) / sample_rate, 3),
                        )
                    )
                    triggered = False
                    silence_start = None
            elif prob >= threshold:
                silence_start = None

        if triggered:
            raw.append(
                AudioChunk(
                    round(speech_start / sample_rate, 3),
                    round(waveform.size / sample_rate, 3),
                )
            )
        if not raw:
            return self.fixed_chunks(audio_path, max_duration)
        return self._merge_and_split(raw, max_duration)

    @staticmethod
    def _merge_and_split(raw_segments: list[AudioChunk], max_duration: float) -> list[AudioChunk]:
        merge_gap = 0.3
        merged: list[AudioChunk] = []
        for segment in raw_segments:
            if merged and (segment.start - merged[-1].end) < merge_gap:
                merged[-1] = AudioChunk(merged[-1].start, segment.end)
            else:
                merged.append(AudioChunk(segment.start, segment.end))

        final: list[AudioChunk] = []
        for chunk in merged:
            duration = chunk.end - chunk.start
            if duration <= max_duration:
                final.append(AudioChunk(round(chunk.start, 3), round(chunk.end, 3)))
                continue

            internal = [
                segment for segment in raw_segments
                if segment.start >= chunk.start and segment.end <= chunk.end
            ]
            if len(internal) < 2:
                final.extend(ASRChunker._split_evenly(chunk, max_duration))
                continue

            current = chunk.start
            while chunk.end - current > max_duration:
                target = current + max_duration
                gap = min(
                    (
                        (internal[i].end + internal[i + 1].start) / 2
                        for i in range(len(internal) - 1)
                        if current < (internal[i].end + internal[i + 1].start) / 2 < chunk.end
                    ),
                    key=lambda value: abs(value - target),
                    default=target,
                )
                final.append(AudioChunk(round(current, 3), round(gap, 3)))
                current = gap
            if chunk.end - current >= 0.1:
                final.append(AudioChunk(round(current, 3), round(chunk.end, 3)))

        return final

    @staticmethod
    def _split_evenly(chunk: AudioChunk, max_duration: float) -> list[AudioChunk]:
        duration = chunk.end - chunk.start
        if duration <= max_duration:
            return [chunk]
        n_slices = int(-(-duration // max_duration))
        slice_duration = duration / n_slices
        pieces: list[AudioChunk] = []
        for index in range(n_slices):
            start = chunk.start + index * slice_duration
            end = chunk.start + (index + 1) * slice_duration if index < n_slices - 1 else chunk.end
            pieces.append(AudioChunk(round(start, 3), round(end, 3)))
        return pieces

    @staticmethod
    def _load_audio_float32(audio_path: Path) -> Any:
        import numpy as np

        with tempfile.NamedTemporaryFile(suffix=".f32", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "f32le",
            str(tmp_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return np.fromfile(tmp_path, dtype=np.float32)
        except FileNotFoundError as e:
            raise RuntimeError("ffmpeg not found in PATH; install FFmpeg for ASR chunking") from e
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode(errors="replace") if e.stderr else ""
            raise RuntimeError(f"ffmpeg failed to prepare audio for Silero ONNX: {stderr[:500]}") from e
        finally:
            tmp_path.unlink(missing_ok=True)
