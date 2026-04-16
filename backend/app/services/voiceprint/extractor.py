"""Extract speaker embeddings by reusing pyannote's embedding model.

Given audio and per-speaker time intervals (from diarization), returns one
embedding per speaker label plus a short representative audio clip saved
to disk for human audit.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

CLIP_SECONDS = 5.0  # duration of audit wav per speaker
MIN_EMBED_SECONDS = 1.5  # below this we skip (too short for a reliable embedding)
SAMPLE_RATE = 16000


@dataclass
class ExtractedVoiceprint:
    speaker_label: str                # e.g. "SPEAKER_00"
    embedding: np.ndarray             # shape (N,), float32
    duration_sec: float               # total speech duration aggregated
    quality_score: float              # 0..1, currently log(duration) mapped
    clip_path: str | None             # path to saved 5s wav


def _load_audio_mono_16k(audio_path: str | Path) -> np.ndarray:
    import soundfile as sf
    p = Path(audio_path)
    if not p.exists():
        raise FileNotFoundError(f"Audio file not found for voiceprint extraction: {p}")
    data, sr = sf.read(str(p))
    if data.ndim > 1:
        data = np.mean(data, axis=1)
    if sr != SAMPLE_RATE:
        import librosa
        data = librosa.resample(data.astype(np.float32), orig_sr=sr, target_sr=SAMPLE_RATE)
    return data.astype(np.float32)


def _quality_score(duration_sec: float) -> float:
    """Simple duration-based quality: 0 at 0s, ~1 at 30s+."""
    if duration_sec <= 0:
        return 0.0
    return float(min(1.0, np.log1p(duration_sec) / np.log1p(30.0)))


def _pick_best_interval(
    intervals: list[tuple[float, float]],
    clip_seconds: float,
) -> tuple[float, float] | None:
    """Pick a contiguous stretch closest to clip_seconds, prefer the longest."""
    if not intervals:
        return None
    # Longest interval wins; if shorter than target, use as-is
    longest = max(intervals, key=lambda x: x[1] - x[0])
    dur = longest[1] - longest[0]
    if dur <= clip_seconds:
        return longest
    # Take center clip_seconds of the longest interval
    mid = (longest[0] + longest[1]) / 2
    return (mid - clip_seconds / 2, mid + clip_seconds / 2)


def _save_clip(
    audio: np.ndarray,
    start: float,
    end: float,
    out_path: Path,
) -> None:
    import soundfile as sf
    s = max(0, int(start * SAMPLE_RATE))
    e = min(len(audio), int(end * SAMPLE_RATE))
    if e > s:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_path), audio[s:e], SAMPLE_RATE)


def _get_embedding_model(pyannote_pipeline: Any) -> Any:
    """Pull the embedding model out of a loaded pyannote SpeakerDiarization pipeline."""
    emb = getattr(pyannote_pipeline, "_embedding", None)
    if emb is None:
        raise RuntimeError("pyannote pipeline has no _embedding attribute; can't reuse model")
    return emb


def extract_voiceprints(
    audio_path: str | Path,
    diarize_df: Any,                 # pandas DataFrame with columns: start, end, speaker
    pyannote_pipeline: Any,
    clips_dir: Path,
    sample_id_prefix: str = "",
) -> list[ExtractedVoiceprint]:
    """Extract one embedding per unique speaker label in diarize_df.

    Uses pyannote's embedding model loaded in pyannote_pipeline._embedding.
    Saves a representative audio clip per speaker to clips_dir.
    """
    import torch

    audio_np = _load_audio_mono_16k(audio_path)
    total_len = len(audio_np) / SAMPLE_RATE

    # Group intervals by speaker label
    by_speaker: dict[str, list[tuple[float, float]]] = {}
    for _, row in diarize_df.iterrows():
        lbl = str(row["speaker"])
        s, e = float(row["start"]), float(row["end"])
        if e <= s:
            continue
        by_speaker.setdefault(lbl, []).append((s, min(e, total_len)))

    embedding_model = _get_embedding_model(pyannote_pipeline)

    results: list[ExtractedVoiceprint] = []
    for lbl, intervals in by_speaker.items():
        total_speech = sum(e - s for s, e in intervals)
        if total_speech < MIN_EMBED_SECONDS:
            logger.info(f"Skipping {lbl}: only {total_speech:.2f}s of speech")
            continue

        # Concatenate up to ~20s of the longest intervals as input to the embedding model
        intervals_sorted = sorted(intervals, key=lambda x: x[1] - x[0], reverse=True)
        budget = 20.0
        chunks = []
        used = 0.0
        for s, e in intervals_sorted:
            if used >= budget:
                break
            dur = e - s
            take = min(dur, budget - used)
            s_i = max(0, int(s * SAMPLE_RATE))
            e_i = min(len(audio_np), int((s + take) * SAMPLE_RATE))
            if e_i > s_i:
                chunks.append(audio_np[s_i:e_i])
                used += (e_i - s_i) / SAMPLE_RATE
        if not chunks:
            continue
        concat = np.concatenate(chunks)

        # pyannote 3.4 speaker embedding wrappers expect (batch, channel, samples).
        waveform = torch.from_numpy(concat).float().unsqueeze(0).unsqueeze(0)
        try:
            emb_tensor = embedding_model(waveform)
        except Exception as e:
            logger.error(f"Could not extract embedding for {lbl}: {e}")
            continue

        if hasattr(emb_tensor, "detach"):
            emb_np = emb_tensor.detach().cpu().numpy()
        elif isinstance(emb_tensor, np.ndarray):
            emb_np = emb_tensor
        else:
            emb_np = np.array(emb_tensor)

        emb_np = np.asarray(emb_np, dtype=np.float32)
        expected_dim = int(getattr(embedding_model, "dimension", 0) or 0)
        if emb_np.ndim == 2 and emb_np.shape[0] == 1:
            emb_np = emb_np[0]
        elif emb_np.ndim != 1:
            if expected_dim and emb_np.size == expected_dim:
                emb_np = emb_np.reshape(expected_dim)
            else:
                logger.error(
                    f"Unexpected embedding shape for {lbl}: {emb_np.shape}, expected (*, {expected_dim})"
                )
                continue

        if expected_dim and emb_np.shape != (expected_dim,):
            logger.error(
                f"Unexpected embedding shape for {lbl}: {emb_np.shape}, expected ({expected_dim},)"
            )
            continue

        # Normalize (store.add_sample also normalizes, but do it here too for sanity)
        n = np.linalg.norm(emb_np)
        if n < 1e-8:
            logger.warning(f"Zero embedding for {lbl}, skipping")
            continue
        emb_np = emb_np / n

        # Save a representative clip
        clip_path: str | None = None
        pick = _pick_best_interval(intervals, CLIP_SECONDS)
        if pick is not None:
            fname = f"{sample_id_prefix}{lbl.replace('/', '_')}.wav"
            out = clips_dir / fname
            try:
                _save_clip(audio_np, pick[0], pick[1], out)
                clip_path = str(out)
            except Exception as e:
                logger.warning(f"Could not save clip for {lbl}: {e}")

        results.append(ExtractedVoiceprint(
            speaker_label=lbl,
            embedding=emb_np,
            duration_sec=total_speech,
            quality_score=_quality_score(total_speech),
            clip_path=clip_path,
        ))

    return results
