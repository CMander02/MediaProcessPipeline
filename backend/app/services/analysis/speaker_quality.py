"""One definition of identified people and unresolved transcript coverage."""

from __future__ import annotations

from app.services.analysis.transcript_outputs import (
    UNKNOWN_SPEAKER, _parse_srt, _split_speaker_prefix, _timestamp_bounds, _timestamp_to_seconds,
)


def speaker_labels(srt: str) -> list[str]:
    return sorted({
        label for cue in _parse_srt(srt)
        if (label := _split_speaker_prefix(cue["text"])[0]) and label.casefold() != UNKNOWN_SPEAKER
    })


def finalize_speaker_quality(extra: dict, srt: str, target_count: int | None = None) -> None:
    """Check the published track after naming and polish; retain source cue ranges."""
    labels = speaker_labels(srt)
    if target_count is not None and len(labels) > target_count:
        raise ValueError(f"人物归属超过用户人数约束：目标 {target_count}，当前 {len(labels)}；结果未发布")
    extra.update(speakers=labels, speaker_count=len(labels))
    quality = dict(extra.get("speaker_quality") or {})
    quality.update(target_count=target_count, observed_count=len(labels), speakers=labels)
    if target_count is not None and len(labels) < target_count:
        quality.update(status="constraint_conflict", reason=f"目标 {target_count} 人，当前分配 {len(labels)} 人")
    elif quality.get("failed_chunks") or quality.get("unassigned_segments"):
        quality["status"] = "needs_review"
    else:
        if quality.get("status") == "constraint_conflict":
            quality["status"] = "completed"
            quality.pop("reason", None)
        else:
            quality.setdefault("status", "completed")
    extra["speaker_quality"] = quality


def quality_report(srt: str, inference: dict, target_count: int | None = None) -> dict:
    labels = speaker_labels(srt)
    decisions = {row["index"]: row for row in inference.get("assignments", [])}
    unresolved = []
    events = []
    for cue in _parse_srt(srt):
        label, _ = _split_speaker_prefix(cue["text"])
        decision = decisions.get(cue["index"], {})
        start, end = _timestamp_bounds(cue["timestamp"])
        row = {"source_index": cue["index"], "start": _timestamp_to_seconds(start),
               "end": _timestamp_to_seconds(end)}
        if decision.get("kind") in {"non_speech", "mixed", "uncertain"} and decision.get("event_description"):
            events.append({**row, "description": decision.get("event_description", ""),
                           "kind": decision["kind"],
                           "source": decision.get("event_source", "subtitle_inference")})
        if decision.get("kind") != "non_speech" and (not label or label.casefold() == UNKNOWN_SPEAKER):
            unresolved.append(row)
    failed = inference.get("failed_chunks", [])
    conflict = target_count is not None and len(labels) != target_count
    status = "constraint_conflict" if conflict else "needs_review" if unresolved or failed else "completed"
    return {"version": 1, "status": status, "target_count": target_count,
            "observed_count": len(labels), "speakers": labels,
            "unassigned_segments": len(unresolved), "unassigned_ranges": unresolved,
            "sound_events": events, "failed_chunks": len(failed),
            "failed_ranges": [row.get("indexes", []) for row in failed],
            "input_srt": srt}
