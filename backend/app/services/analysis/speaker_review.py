"""Correct cue-level speaker attribution while preserving the source transcript."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging_setup import log_event
from app.core.model_router import resolve_polish_llm_binding
from app.core.settings import get_runtime_settings
from app.services.analysis.prompts.speaker_review import (
    SPEAKER_REVIEW_SYSTEM_PROMPT,
    get_speaker_review_prompt,
)
from app.services.analysis.transcript_outputs import (
    _parse_srt,
    _segments_to_srt,
    _split_speaker_prefix,
)

logger = logging.getLogger(__name__)


def _speaker_roster(segments: list[dict]) -> list[dict[str, Any]]:
    samples: dict[str, list[str]] = {}
    for segment in segments:
        speaker, body = _split_speaker_prefix(segment["text"])
        if speaker:
            samples.setdefault(speaker, []).append(body)
    return [
        {
            "id": speaker,
            "examples": [text[:160] for text in sorted(texts, key=len, reverse=True)[:2]],
        }
        for speaker, texts in samples.items()
    ]


def apply_speaker_changes(srt: str, changes: list[dict]) -> str:
    """Apply reviewed labels to their original cues, retaining all text and timing."""
    segments = _parse_srt(srt)
    allowed = {item["id"] for item in _speaker_roster(segments)}
    by_index = {item["index"]: item for item in changes}
    for segment in segments:
        change = by_index.get(segment["index"])
        if change is None:
            continue
        speaker, body = _split_speaker_prefix(segment["text"])
        if speaker == change.get("from_speaker") and change.get("speaker") in allowed:
            segment["text"] = f"[{change['speaker']}] {body}"
    return _segments_to_srt(segments) if segments else srt


def _parse_changes(
    response: str,
    targets: list[dict],
    surrounding: list[dict],
    allowed_speakers: set[str],
) -> list[dict]:
    # Some local models append a revised final JSON block after an initial draft.
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", response, flags=re.IGNORECASE)
    if fenced:
        response = fenced[-1].strip()
    start, end = response.find("["), response.rfind("]")
    if start < 0 or end < start:
        raise ValueError("Speaker review did not return a JSON array")
    items = json.loads(response[start : end + 1])
    if not isinstance(items, list):
        raise ValueError("Speaker review response must be an array")
    editable = {item["index"]: item for item in targets}
    visible = {item["index"] for item in surrounding}
    changes = []
    seen: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Speaker review change must be an object")
        index, speaker = item.get("index"), item.get("speaker")
        evidence = item.get("evidence_indexes")
        reason = item.get("reason")
        if (
            type(index) is not int
            or index not in editable
            or index in seen
            or not isinstance(speaker, str)
            or speaker not in allowed_speakers
            or not isinstance(evidence, list)
            or not evidence
            or any(type(value) is not int or value not in visible for value in evidence)
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise ValueError("Speaker review contains an invalid cue, speaker, or evidence")
        seen.add(index)
        original_speaker, _ = _split_speaker_prefix(editable[index]["text"])
        if original_speaker and original_speaker != speaker:
            changes.append(
                {
                    "index": index,
                    "from_speaker": original_speaker,
                    "speaker": speaker,
                    "evidence_indexes": evidence,
                    "reason": reason.strip(),
                }
            )
    return changes


async def review_speakers(
    srt: str,
    context: dict[str, Any] | None = None,
    *,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Review consecutive dialogue chunks with a shared roster and context overlap."""
    from app.services.analysis.llm import get_llm_service

    segments = _parse_srt(srt)
    roster = _speaker_roster(segments)
    report: dict[str, Any] = {
        "status": "skipped",
        "reason": "fewer_than_two_speakers",
        "input_segments": len(segments),
        "speakers": [item["id"] for item in roster],
        "changes": [],
        "failed_chunks": [],
    }
    if len(roster) < 2:
        log_event(logger, logging.INFO, "llm.speaker_review.skipped", reason=report["reason"])
        return report

    service = get_llm_service()
    # Share the configured polish model, including its provider binding and fallback.
    provider_override = resolve_polish_llm_binding(get_runtime_settings()).provider
    allowed = set(report["speakers"])
    # Each cue is editable once; surrounding cues provide read-only dialogue context.
    chunk_size, overlap = 24, 4
    total_chunks = (len(segments) + chunk_size - 1) // chunk_size
    report.pop("reason")
    report["chunks"] = total_chunks
    started = time.perf_counter()
    log_event(
        logger,
        logging.INFO,
        "llm.speaker_review.started",
        segments=len(segments),
        speakers=len(roster),
        chunks=total_chunks,
    )
    for chunk_number, start in enumerate(range(0, len(segments), chunk_size), 1):
        targets = segments[start : start + chunk_size]
        surrounding = segments[max(0, start - overlap) : start + chunk_size + overlap]
        cues = []
        for segment in surrounding:
            speaker, text = _split_speaker_prefix(segment["text"])
            cues.append({**segment, "speaker": speaker, "text": text})
        prompt = get_speaker_review_prompt(
            cues,
            [item["index"] for item in targets],
            roster,
            context or {},
        )
        try:
            log_event(logger, logging.INFO, "llm.speaker_review.chunk_started", chunk=chunk_number)
            response = await service._call(
                prompt,
                provider_override=provider_override,
                stage="speaker_review",
                system_prompt=SPEAKER_REVIEW_SYSTEM_PROMPT,
            )
            changes = _parse_changes(response, targets, surrounding, allowed)
            report["changes"].extend(changes)
            log_event(
                logger,
                logging.INFO,
                "llm.speaker_review.chunk_completed",
                chunk=chunk_number,
                changes=len(changes),
            )
        except Exception as exc:
            report["failed_chunks"].append({"chunk": chunk_number, "error": str(exc)})
            log_event(
                logger,
                logging.WARNING,
                "llm.speaker_review.chunk_failed",
                chunk=chunk_number,
                error=exc,
            )
        if on_progress:
            await on_progress(chunk_number, total_chunks)
    failures = len(report["failed_chunks"])
    report["status"] = (
        "failed" if failures == total_chunks else "partial" if failures else "completed"
    )
    log_event(
        logger,
        logging.WARNING if failures else logging.INFO,
        "llm.speaker_review.completed",
        status=report["status"],
        changes=len(report["changes"]),
        failed_chunks=failures,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )
    return report
