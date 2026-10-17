"""Correct cue-level speaker attribution while preserving the source transcript."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.core.logging_setup import log_event
from app.core.model_router import resolve_polish_llm_binding
from app.core.settings import get_runtime_settings
from app.services.analysis.long_text import compact_metadata, prompt_budget, record_windows
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
        if speaker and speaker.casefold() != "unknown":
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
    protected_indexes: set[int] | None = None,
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
    ignored = 0
    for item in items:
        index = item.get("index") if isinstance(item, dict) else None
        speaker = item.get("speaker") if isinstance(item, dict) else None
        evidence = item.get("evidence_indexes") if isinstance(item, dict) else None
        reason = item.get("reason") if isinstance(item, dict) else None
        # An unsupported row keeps that cue's label; the rest of the batch still counts.
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
            ignored += 1
            continue
        seen.add(index)
        if index in (protected_indexes or set()):
            continue
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
    if ignored:
        log_event(logger, logging.INFO, "llm.speaker_review.rows_ignored", rows=ignored)
    return changes


async def review_speakers(
    srt: str,
    context: dict[str, Any] | None = None,
    *,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    checkpoint_path: Path | None = None,
    checkpoint_execution: dict | None = None,
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
    binding = resolve_polish_llm_binding(get_runtime_settings())
    provider_override = binding.provider
    from app.services.analysis.speaker_checkpoint import load_checkpoint, save_checkpoint
    inputs = {"srt": srt, "context": context, "prompt_version": 2,
              "system_prompt": SPEAKER_REVIEW_SYSTEM_PROMPT,
              "model": binding.model, "provider": binding.provider,
              "parameters": {k: v for k, v in binding.request_kwargs.items() if k != "api_key"}}
    saved = load_checkpoint(checkpoint_path, inputs)
    from app.services.analysis.speaker_checkpoint import begin_checkpoint_attempt
    begin_checkpoint_attempt(checkpoint_path, inputs, "speaker_review", checkpoint_execution)
    completed = set(saved["completed_indexes"]) if saved else set()
    if saved:
        report = saved["report"]
        report["failed_chunks"] = []
    protected_indexes = set((context or {}).get("mixed_speaker_cues", []))
    context = compact_metadata(context or {}, max(512, prompt_budget(provider_override, "speaker_review", SPEAKER_REVIEW_SYSTEM_PROMPT) // 3))
    allowed = set(report["speakers"])
    # Each cue is editable once; surrounding cues provide read-only dialogue context.
    chunk_size, overlap = 24, 4
    total_chunks = (len(segments) + chunk_size - 1) // chunk_size
    report.pop("reason", None)
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
    def build_prompt(targets, surrounding):
        cues = []
        for segment in surrounding:
            speaker, text = _split_speaker_prefix(segment["text"])
            cues.append({**segment, "speaker": speaker, "text": text})
        return get_speaker_review_prompt(cues, [item["index"] for item in targets], roster, context or {})

    async def call(prompt):
        return await service._call(prompt, provider_override=provider_override,
                                   stage="speaker_review", system_prompt=SPEAKER_REVIEW_SYSTEM_PROMPT, max_retries=0)

    chunk_number = 0
    decisions = {int(key): value for key, value in report.get("decisions", {}).items() if int(key) in completed}
    state = {}
    failed_ids = set()
    async for targets, surrounding, response in record_windows(
        segments, build_prompt, call,
        budget=prompt_budget(provider_override, "speaker_review", SPEAKER_REVIEW_SYSTEM_PROMPT),
        chunk_size=chunk_size, overlap=overlap, keep_errors=True,
        skip_indexes=completed, window_state=state, max_calls=max(32, len(segments) * 4),
        validate=lambda response, targets, visible: _parse_changes(response, targets, visible, allowed,
                                                                   protected_indexes),
    ):
        chunk_number += 1
        try:
            log_event(logger, logging.INFO, "llm.speaker_review.chunk_started", chunk=chunk_number)
            if isinstance(response, Exception):
                raise response
            changes = response
            changes_by_id = {item["index"]: item for item in changes}
            for target in targets:
                decisions.setdefault(target["index"], []).append(changes_by_id.get(target["index"]))
            completed.update(set(state.get("completed_indexes", [])) - failed_ids)
            log_event(
                logger,
                logging.INFO,
                "llm.speaker_review.chunk_completed",
                chunk=chunk_number,
                changes=len(changes),
            )
        except Exception as exc:
            failed_ids.update(target["index"] for target in targets)
            completed.difference_update(failed_ids)
            for target in targets:
                decisions.setdefault(target["index"], []).append(None)
            report["failed_chunks"].append({
                "chunk": chunk_number, "indexes": [row["index"] for row in targets], "error": str(exc),
            })
            log_event(
                logger,
                logging.WARNING,
                "llm.speaker_review.chunk_failed",
                chunk=chunk_number,
                error=exc,
            )
        report["decisions"] = decisions
        report["status"] = "running"
        save_checkpoint(checkpoint_path, inputs, report, completed)
        if on_progress:
            await on_progress(chunk_number, max(chunk_number, total_chunks))
        if isinstance(response, Exception) and not isinstance(response, ValueError):
            break
    total_chunks = chunk_number
    report["chunks"] = total_chunks
    # A split cue changes label only when all its fragments agree.
    report["changes"] = [rows[0] for index, rows in decisions.items() if index in completed
                         if all(row is not None for row in rows)
                         and len({row["speaker"] for row in rows}) == 1]
    failures = len(report["failed_chunks"])
    report["status"] = (
        "failed" if not completed else "partial" if len(completed) < len(segments) else "completed"
    )
    report["decisions"] = decisions
    save_checkpoint(checkpoint_path, inputs, report, completed)
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
