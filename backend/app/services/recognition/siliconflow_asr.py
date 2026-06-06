"""SiliconFlow (OpenAI-compatible) ASR provider.

Uploads audio chunks serially to the OpenAI-compatible /audio/transcriptions
endpoint. The default chunker uses ffmpeg so API-only installs do not need
torch/torchaudio; Silero VAD chunking remains available when local model deps
are installed.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx

from app.core.settings import get_runtime_settings
from app.models import TranscriptSegment

logger = logging.getLogger(__name__)


class SiliconFlowASRService:
    """ASR via OpenAI-compatible /audio/transcriptions."""

    def __init__(self) -> None:
        self._vad_model = None
        self._vad_utils = None

    def release(self) -> None:
        self._vad_model = None
        self._vad_utils = None

    # No diarization pipeline owned by this provider.
    def get_pyannote_pipeline(self):
        return None

    def get_last_diarization(self):
        return None, None

    def _load_vad(self):
        if self._vad_model is not None:
            return
        import torch
        # PyTorch 2.6+ defaults weights_only=True which breaks Silero's checkpoint
        torch.serialization.add_safe_globals([torch.torch_version.TorchVersion])
        model, utils = torch.hub.load("snakers4/silero-vad", "silero_vad", trust_repo=True)
        self._vad_model = model
        self._vad_utils = utils

    def _vad_chunks(
        self,
        audio_path: str,
        max_duration: float,
    ) -> tuple[list[dict[str, float]], Any, int]:
        """Return list of {start, end} chunks (sec), waveform tensor, sample_rate.

        Mirrors qwen3_asr._transcribe_with_vad_chunks segmenting strategy: merge
        small gaps, split overlong chunks at the largest internal gap, then
        force-split anything still over the cap into equal slices.
        """
        import torch  # noqa: F401  (torchaudio depends on torch import order on Win)
        import torchaudio

        self._load_vad()
        get_ts = self._vad_utils[0]

        waveform, sample_rate = torchaudio.load(str(audio_path))
        if waveform.shape[0] > 1:
            waveform = waveform.mean(dim=0, keepdim=True)
        if sample_rate != 16000:
            waveform = torchaudio.functional.resample(waveform, sample_rate, 16000)
            sample_rate = 16000

        timestamps = get_ts(
            waveform.squeeze(), self._vad_model, sampling_rate=sample_rate, return_seconds=True
        )

        if not timestamps:
            duration = waveform.shape[1] / sample_rate
            return [{"start": 0.0, "end": float(duration)}], waveform, sample_rate

        raw = []
        for ts in timestamps:
            start = ts.get("start", ts.get("start_sec", 0))
            end = ts.get("end", ts.get("end_sec", 0))
            if isinstance(start, int) and start > 1000:
                start = start / sample_rate
                end = end / sample_rate
            raw.append({"start": float(start), "end": float(end)})

        merge_gap = 0.3
        merged: list[dict[str, float]] = []
        for seg in raw:
            if merged and (seg["start"] - merged[-1]["end"]) < merge_gap:
                merged[-1]["end"] = seg["end"]
            else:
                merged.append({"start": seg["start"], "end": seg["end"]})

        intermediate: list[dict[str, float]] = []
        for chunk in merged:
            duration = chunk["end"] - chunk["start"]
            if duration <= max_duration:
                intermediate.append(chunk)
                continue
            internal = [
                s for s in raw if s["start"] >= chunk["start"] and s["end"] <= chunk["end"]
            ]
            if len(internal) < 2:
                intermediate.append(chunk)
                continue
            mid = chunk["start"] + duration / 2
            best_idx, best_dist = 0, float("inf")
            for i in range(len(internal) - 1):
                gap_mid = (internal[i]["end"] + internal[i + 1]["start"]) / 2
                dist = abs(gap_mid - mid)
                if dist < best_dist:
                    best_dist = dist
                    best_idx = i
            split = (internal[best_idx]["end"] + internal[best_idx + 1]["start"]) / 2
            intermediate.append({"start": chunk["start"], "end": split})
            intermediate.append({"start": split, "end": chunk["end"]})

        final: list[dict[str, float]] = []
        for chunk in intermediate:
            duration = chunk["end"] - chunk["start"]
            if duration <= max_duration:
                final.append(chunk)
                continue
            n = int(-(-duration // max_duration))
            slice_dur = duration / n
            for k in range(n):
                s = chunk["start"] + k * slice_dur
                e = chunk["start"] + (k + 1) * slice_dur if k < n - 1 else chunk["end"]
                final.append({"start": s, "end": e})

        return final, waveform, sample_rate

    def _probe_duration(self, audio_path: str) -> float:
        """Return media duration in seconds via ffprobe."""
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
            raise RuntimeError(
                "ffprobe not found in PATH; install FFmpeg for API ASR chunking"
            ) from e
        except (subprocess.CalledProcessError, ValueError) as e:
            raise RuntimeError(f"Failed to probe audio duration: {audio_path}") from e

    def _fixed_chunks(self, audio_path: str, max_duration: float) -> list[dict[str, float]]:
        """Split by fixed wall-clock duration without local ML dependencies."""
        duration = self._probe_duration(audio_path)
        if duration <= 0:
            return [{"start": 0.0, "end": max_duration}]
        chunks: list[dict[str, float]] = []
        start = 0.0
        while start < duration:
            end = min(start + max_duration, duration)
            chunks.append({"start": round(start, 3), "end": round(end, 3)})
            start = end
        return chunks or [{"start": 0.0, "end": duration}]

    def _export_chunk_ffmpeg(
        self,
        audio_path: str,
        chunk: dict[str, float],
        wav_path: str,
    ) -> None:
        """Export one mono 16 kHz WAV chunk via ffmpeg."""
        duration = max(0.0, float(chunk["end"]) - float(chunk["start"]))
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{float(chunk['start']):.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            wav_path,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except FileNotFoundError as e:
            raise RuntimeError(
                "ffmpeg not found in PATH; install FFmpeg for API ASR chunking"
            ) from e
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode(errors="replace") if e.stderr else ""
            raise RuntimeError(f"ffmpeg failed to export ASR chunk: {stderr[:500]}") from e

    def _post_chunk(
        self,
        client: httpx.Client,
        url: str,
        api_key: str,
        model: str,
        language: str | None,
        wav_path: str,
    ) -> str:
        """POST one chunk to /audio/transcriptions and return text.

        SiliconFlow expects model (and optional language) as multipart parts,
        not regular form fields — match their reference snippet exactly.
        """
        headers = {"Authorization": f"Bearer {api_key}"}
        with open(wav_path, "rb") as f:
            files: list[tuple[str, Any]] = [
                ("file", (Path(wav_path).name, f, "audio/wav")),
                ("model", (None, model)),
            ]
            if language:
                files.append(("language", (None, language)))
            resp = client.post(url, headers=headers, files=files)

        if resp.status_code >= 400:
            raise RuntimeError(
                f"SiliconFlow ASR API error {resp.status_code}: {resp.text[:500]}"
            )
        payload = resp.json()
        # OpenAI-compatible: {"text": "..."}
        text = payload.get("text", "")
        if not text and isinstance(payload.get("results"), list) and payload["results"]:
            # Some servers return {"results": [{"text": "..."}]}
            text = payload["results"][0].get("text", "")
        return str(text).strip()

    def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        diarize: bool = True,  # noqa: ARG002 — API does not provide diarization
        num_speakers: int | None = None,  # noqa: ARG002
        chunk_strategy: str | None = None,
    ) -> dict[str, Any]:
        rt = get_runtime_settings()
        if not rt.siliconflow_api_key:
            raise RuntimeError(
                "siliconflow_api_key is empty — configure it in Settings before using this "
                "ASR provider"
            )
        base = rt.siliconflow_api_base.rstrip("/")
        if not base.endswith("/v1"):
            # Tolerate users entering the bare host
            base = base + "/v1" if "/v" not in base else base
        url = f"{base}/audio/transcriptions"
        model = rt.siliconflow_asr_model
        lang_hint = language or (rt.siliconflow_asr_language or None)
        max_chunk = float(rt.siliconflow_asr_max_chunk_sec or 30.0)
        timeout = float(rt.siliconflow_asr_timeout_sec or 120.0)
        strategy = (chunk_strategy or rt.siliconflow_asr_chunk_strategy or "ffmpeg").strip().lower()

        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise FileNotFoundError(f"File not found: {audio_path}")

        logger.info(
            f"SiliconFlow ASR: {audio_path} (model={model}, lang={lang_hint or 'auto'}, "
            f"max_chunk={max_chunk}s, chunk_strategy={strategy})"
        )

        waveform = None
        sample_rate = None
        use_torchaudio_export = False
        if strategy in {"vad", "auto"}:
            try:
                chunks, waveform, sample_rate = self._vad_chunks(str(audio_file), max_chunk)
                use_torchaudio_export = True
                logger.info(f"VAD produced {len(chunks)} chunks; uploading serially")
            except ImportError as e:
                if strategy == "vad":
                    raise RuntimeError(
                        "SiliconFlow VAD chunking requires local model dependencies. "
                        "Install them with `uv sync --extra local-asr`, or set "
                        "siliconflow_asr_chunk_strategy=ffmpeg."
                    ) from e
                logger.info("VAD chunking unavailable; falling back to ffmpeg fixed chunks")
                chunks = self._fixed_chunks(str(audio_file), max_chunk)
            except Exception:
                if strategy == "vad":
                    raise
                logger.warning(
                    "VAD chunking failed; falling back to ffmpeg fixed chunks",
                    exc_info=True,
                )
                chunks = self._fixed_chunks(str(audio_file), max_chunk)
        else:
            chunks = self._fixed_chunks(str(audio_file), max_chunk)
        if not use_torchaudio_export:
            logger.info(f"ffmpeg fixed chunking produced {len(chunks)} chunks; uploading serially")

        segments: list[dict[str, Any]] = []
        with httpx.Client(timeout=timeout) as client:
            for i, chunk in enumerate(chunks):
                chunk_duration = float(chunk["end"]) - float(chunk["start"])
                if chunk_duration < 0.1:
                    continue

                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp_path = tmp.name
                try:
                    if use_torchaudio_export:
                        import torchaudio

                        start_sample = int(chunk["start"] * sample_rate)
                        end_sample = int(chunk["end"] * sample_rate)
                        chunk_wav = waveform[:, start_sample:end_sample]
                        if chunk_wav.shape[1] < 1600:  # < 0.1s at 16 kHz
                            continue
                        torchaudio.save(tmp_path, chunk_wav, sample_rate)
                    else:
                        self._export_chunk_ffmpeg(str(audio_file), chunk, tmp_path)
                    text = self._post_chunk(
                        client, url, rt.siliconflow_api_key, model, lang_hint, tmp_path
                    )
                    if text:
                        segments.append(
                            {
                                "start": round(chunk["start"], 3),
                                "end": round(chunk["end"], 3),
                                "text": text,
                            }
                        )
                    logger.info(
                        f"  chunk {i + 1}/{len(chunks)} "
                        f"[{chunk['start']:.1f}s-{chunk['end']:.1f}s]: "
                        f"{len(text)} chars"
                    )
                except Exception as e:
                    logger.warning(f"Chunk {i} failed: {e}")
                finally:
                    Path(tmp_path).unlink(missing_ok=True)

        logger.info(f"SiliconFlow ASR done: {len(segments)} segments")
        return {"language": lang_hint or "unknown", "segments": segments}

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

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


_service: SiliconFlowASRService | None = None


def get_siliconflow_service() -> SiliconFlowASRService:
    global _service
    if _service is None:
        _service = SiliconFlowASRService()
    return _service


def release_siliconflow_service() -> None:
    global _service
    if _service is not None:
        _service.release()
