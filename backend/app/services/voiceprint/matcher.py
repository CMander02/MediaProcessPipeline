"""Match extracted voiceprints against the library and persist outcomes.

Threshold behaviour (per user spec):
  sim >= match_threshold     → auto-assign to matched person (add new sample)
  suggest <= sim < match     → create new Unknown-{hash} person (user can merge later)
  sim < suggest              → create new Unknown-{hash} person

The suggest tier doesn't auto-merge to avoid false positives. The sample's
high similarity is still recorded so the UI can surface "possible match" hints.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

import numpy as np

from app.services.voiceprint.extractor import ExtractedVoiceprint
from app.services.voiceprint.store import VoiceprintStore, MatchResult

logger = logging.getLogger(__name__)


@dataclass
class SpeakerResolution:
    speaker_label: str       # original diarization label, e.g. "SPEAKER_00"
    person_id: str
    person_name: str
    sample_id: str
    is_new_person: bool
    match: MatchResult | None  # best match at decision time (may be below threshold)


def _unknown_name(embedding: np.ndarray) -> str:
    h = hashlib.sha1(embedding.astype(np.float32).tobytes()).hexdigest()[:4]
    return f"Unknown-{h}"


def resolve_speakers(
    task_id: str,
    voiceprints: list[ExtractedVoiceprint],
    store: VoiceprintStore,
    match_threshold: float,
    suggest_threshold: float,
) -> dict[str, SpeakerResolution]:
    """For each extracted voiceprint, decide person assignment and persist.

    Returns {speaker_label: SpeakerResolution}. Callers typically use this
    to rewrite recognition_segments[*]["speaker"] = resolution.person_name.
    """
    resolutions: dict[str, SpeakerResolution] = {}

    for vp in voiceprints:
        best = store.match_best_person(vp.embedding, top_k=5)

        if best and best.similarity >= match_threshold:
            # Auto-match: add as additional sample under existing person
            person_id = best.person_id
            person_name = best.person_name
            is_new = False
            logger.info(
                f"{vp.speaker_label}: matched {person_name} (sim={best.similarity:.3f} "
                f">= {match_threshold:.2f})"
            )
        else:
            # Create new person (Unknown-xxx)
            name = _unknown_name(vp.embedding)
            # Avoid collisions with existing Unknown-xxx of same hash (rare but possible)
            existing = store.find_person_by_name(name)
            if existing:
                name = f"{name}-{hashlib.sha1(task_id.encode()).hexdigest()[:3]}"
            new_person = store.create_person(name=name)
            person_id = new_person.id
            person_name = new_person.name
            is_new = True
            if best:
                logger.info(
                    f"{vp.speaker_label}: new person {person_name} "
                    f"(best sim={best.similarity:.3f}, threshold={match_threshold:.2f})"
                )
            else:
                logger.info(f"{vp.speaker_label}: new person {person_name} (library empty)")

        sample_id = store.add_sample(
            person_id=person_id,
            embedding=vp.embedding,
            task_id=task_id,
            duration_sec=vp.duration_sec,
            quality_score=vp.quality_score,
            audio_clip_path=vp.clip_path,
        )
        store.set_task_speaker(
            task_id=task_id,
            speaker_label=vp.speaker_label,
            sample_id=sample_id,
            person_id=person_id,
        )

        resolutions[vp.speaker_label] = SpeakerResolution(
            speaker_label=vp.speaker_label,
            person_id=person_id,
            person_name=person_name,
            sample_id=sample_id,
            is_new_person=is_new,
            match=best,
        )

    return resolutions


def apply_to_segments(
    segments: list[dict],
    resolutions: dict[str, SpeakerResolution],
) -> list[dict]:
    """Rewrite segment 'speaker' field with canonical person names."""
    for seg in segments:
        lbl = seg.get("speaker")
        if lbl and lbl in resolutions:
            seg["speaker"] = resolutions[lbl].person_name
    return segments
