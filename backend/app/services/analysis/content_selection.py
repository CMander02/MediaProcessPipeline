"""Model-assisted selection of the real main content from transcript input.

This module only produces a filtered input for summary/mind-map stages.  It never
changes the archived source subtitles.  The model can remove a contiguous prefix
when the prefix is a teaser whose content is repeated later; malformed or uncertain
responses keep the complete source text.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from difflib import SequenceMatcher
from typing import Any

from app.services.analysis.prompts.content_selection import (
    CONTENT_SELECTION_RESPONSE_SCHEMA,
    CONTENT_SELECTION_SYSTEM_PROMPT,
    get_content_selection_prompt,
)
from app.services.analysis.transcript_outputs import _parse_srt

_SRT_TIMESTAMP_RE = re.compile(
    r"^\s*(?P<start>[^-]+?)\s*-->\s*(?P<end>[^\s]+)(?:\s+.*)?$"
)
_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_NON_WORD_RE = re.compile(r"[^\w\u3400-\u9fff]+", re.UNICODE)

SelectionCall = Callable[[str], Awaitable[Any]]
_SELECTION_PROMPT_BUDGET = 9000


def _timestamp_seconds(value: str | None) -> float | None:
    if not value:
        return None
    raw = value.replace(",", ".").strip()
    parts = raw.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (TypeError, ValueError):
        return None


def _split_timestamp(value: str) -> tuple[str, str]:
    match = _SRT_TIMESTAMP_RE.match(value)
    if not match:
        return value.strip(), value.strip()
    return match.group("start"), match.group("end")


def _parse_srt_records(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    occurrences: Counter[int] = Counter()
    for segment in _parse_srt(text):
        index = int(segment.get("index", len(records) + 1))
        occurrences[index] += 1
        suffix = "" if occurrences[index] == 1 else f":{occurrences[index]}"
        start, end = _split_timestamp(str(segment.get("timestamp") or ""))
        records.append(
            {
                "id": f"cue:{index}{suffix}",
                "text": str(segment.get("text") or "").strip(),
                "index": index,
                "timestamp": str(segment.get("timestamp") or "").strip(),
                "start": _timestamp_seconds(start),
                "end": _timestamp_seconds(end),
                "removable": True,
                "kind": "cue",
            }
        )
    return records


def _parse_plain_records(text: str) -> list[dict[str, Any]]:
    """Parse paragraph input without losing a stable source-unit identity."""

    raw_blocks = re.split(r"(?:\r?\n){2,}", text.strip()) if text.strip() else []
    records: list[dict[str, Any]] = []
    for position, block in enumerate(raw_blocks, 1):
        value = block.strip()
        if not value:
            continue
        is_heading = bool(_MARKDOWN_HEADING_RE.match(value))
        records.append(
            {
                "id": f"para:{position}",
                "text": value,
                "paragraph": position,
                "removable": not is_heading,
                "kind": "heading" if is_heading else "paragraph",
            }
        )
    return records


def _source_records(text: str) -> tuple[str, list[dict[str, Any]]]:
    srt_records = _parse_srt_records(text)
    if srt_records:
        return "srt", srt_records
    return "plain", _parse_plain_records(text)


def _normalise_for_similarity(text: str) -> str:
    value = text.casefold()
    # Speaker labels and markdown syntax do not establish repetition.
    value = re.sub(r"\[[^\]]+\]|\*+|^#+\s*", " ", value)
    return _NON_WORD_RE.sub("", value)


def _shingles(text: str) -> set[str]:
    normalised = _normalise_for_similarity(text)
    if len(normalised) < 2:
        return {normalised} if normalised else set()
    return {normalised[i : i + 2] for i in range(len(normalised) - 1)}


def _similarity(left: str, right: str) -> float:
    left_shingles = _shingles(left)
    right_shingles = _shingles(right)
    if not left_shingles or not right_shingles:
        return 0.0
    overlap = len(left_shingles & right_shingles) / len(left_shingles | right_shingles)
    sequence = SequenceMatcher(
        None,
        _normalise_for_similarity(left),
        _normalise_for_similarity(right),
    ).ratio()
    return max(overlap, sequence)


def _opening_candidates(
    records: list[dict[str, Any]],
    *,
    max_units: int = 24,
    max_bytes: int = 3000,
) -> list[dict[str, Any]]:
    """Choose a bounded prefix for model review, without deciding removal."""

    candidates: list[dict[str, Any]] = []
    used = 0
    for record in records:
        if not record.get("removable", True):
            continue
        encoded = len(str(record.get("text") or "").encode("utf-8"))
        if candidates and (len(candidates) >= max_units or used + encoded > max_bytes):
            break
        candidate = dict(record)
        if encoded > 900:
            candidate["prompt_text"] = f"{record.get('text', '')[:900]} …"
        candidates.append(candidate)
        used += encoded
    return candidates


def _related_evidence(
    records: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    max_items: int = 12,
    max_text: int = 360,
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    record_positions = {str(record.get("id")): position for position, record in enumerate(records)}
    opening_end = max(
        (record_positions.get(str(item.get("id")), -1) for item in candidates),
        default=-1,
    )
    later = records[opening_end + 1 :]
    scored: list[tuple[float, dict[str, Any], str]] = []
    for later_record in later:
        text = str(later_record.get("text") or "")
        if len(_normalise_for_similarity(text)) < 12:
            continue
        matches = [
            (candidate["id"], _similarity(str(candidate.get("text") or ""), text))
            for candidate in candidates
        ]
        matches = [(item_id, score) for item_id, score in matches if score >= 0.36]
        if not matches:
            continue
        best = max(score for _, score in matches)
        related_to = [item_id for item_id, score in matches if score >= max(0.36, best - 0.08)]
        scored.append(
            (
                best,
                {
                    "id": later_record["id"],
                    "text": text[:max_text],
                    "related_to": related_to,
                },
                text,
            )
        )
    scored.sort(key=lambda item: item[0], reverse=True)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _score, item, _text in scored:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        selected.append(item)
        if len(selected) >= max_items:
            break
    return selected


def _compact_metadata(
    metadata: dict[str, Any] | None,
    *,
    max_bytes: int = 1200,
) -> dict[str, Any]:
    if not metadata:
        return {}
    allowed = {
        key: metadata[key]
        for key in ("title", "language", "content_type", "main_topics", "chapters", "timeline")
        if key in metadata
    }
    value = json.dumps(allowed, ensure_ascii=False, separators=(",", ":"))
    if len(value.encode("utf-8")) <= max_bytes:
        return allowed
    # Keep chapter titles as hints, but bound auxiliary input deterministically.
    reduced: dict[str, Any] = {}
    for key in ("title", "language", "content_type", "main_topics"):
        if key in allowed:
            reduced[key] = allowed[key]
    for key in ("chapters", "timeline"):
        if key in allowed and isinstance(allowed[key], list):
            reduced[key] = allowed[key][:8]
    def size(value: dict[str, Any]) -> int:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    # Every iteration either drops list items or shortens a string.  This keeps
    # one-item/one-field metadata from getting stuck at the same size forever.
    for _ in range(32):
        if size(reduced) <= max_bytes:
            return reduced
        changed = False
        for key in ("chapters", "timeline", "main_topics"):
            value = reduced.get(key)
            if isinstance(value, list) and value:
                reduced[key] = value[: len(value) // 2]
                changed = True
                break
        if changed:
            continue
        for key, value in list(reduced.items()):
            if isinstance(value, str) and value:
                reduced[key] = value[: max(0, len(value) // 2)]
                changed = True
                break
        if not changed:
            return {}
    return reduced


def _prompt_record(record: dict[str, Any], max_text: int, *, evidence: bool) -> dict[str, Any]:
    result = dict(record)
    source = str(record.get("text") or "")
    if len(source) > max_text:
        source = f"{source[:max_text]} …"
    if evidence:
        result["text"] = source
    else:
        result["prompt_text"] = source
    return result


def _bounded_prompt_inputs(
    source_type: str,
    metadata: dict[str, Any],
    candidates: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Fit the request by dropping evidence and shortening text in order.

    IDs remain intact in every candidate/evidence row.  The returned lists are
    exactly the rows visible to the model and therefore the only IDs accepted
    during response validation.
    """

    candidate_counts = list(dict.fromkeys(
        [len(candidates), 20, 16, 12, 8, 4, 1] if candidates else [0]
    ))
    evidence_counts = list(dict.fromkeys(
        [len(evidence), 10, 8, 6, 4, 2, 0] if evidence else [0]
    ))
    for max_text in (900, 600, 400, 280, 180, 120):
        for evidence_count in evidence_counts:
            visible_evidence = [
                _prompt_record(item, max_text, evidence=True)
                for item in evidence[:evidence_count]
            ]
            for candidate_count in candidate_counts:
                visible_candidates = [
                    _prompt_record(item, max_text, evidence=False)
                    for item in candidates[:candidate_count]
                ]
                prompt = get_content_selection_prompt(
                    source_type=source_type,
                    metadata=metadata,
                    candidates=visible_candidates,
                    evidence=visible_evidence,
                )
                if len(prompt.encode("utf-8")) <= _SELECTION_PROMPT_BUDGET:
                    return prompt, visible_candidates, visible_evidence
    return None


def _extract_json(response: Any) -> dict[str, Any] | None:
    if isinstance(response, dict):
        return response
    if not isinstance(response, str):
        return None
    text = response.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _normalise_ids(value: Any) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return None
    return list(dict.fromkeys(item.strip() for item in value if item.strip()))


def _canonicalise_model_ids(values: list[str], visible_ids: set[str]) -> list[str]:
    """Accept unprefixed numeric IDs when their visible form is unambiguous.

    Some providers omit the ``cue:``/``para:`` prefix even though the prompt
    shows it.  Mapping only an exact numeric suffix, and only when it resolves
    to one visible ID, preserves fail-closed behaviour for duplicate cues or
    otherwise malformed responses.
    """

    canonical: list[str] = []
    for value in values:
        if value.isdigit():
            matches = [
                item
                for item in visible_ids
                if len(item.split(":")) >= 2 and item.split(":")[1] == value
            ]
            if len(matches) == 1:
                value = matches[0]
        canonical.append(value)
    return list(dict.fromkeys(canonical))


def _prefix_exclusions(
    records: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    requested: list[str],
    body_start_id: str | None = None,
) -> list[str] | None:
    candidate_ids = [item["id"] for item in candidates]
    candidate_set = set(candidate_ids)
    if body_start_id is not None and body_start_id not in candidate_set:
        return None
    if any(item not in candidate_set for item in requested):
        return None
    if body_start_id is not None:
        body_index = candidate_ids.index(body_start_id)
        inferred = candidate_ids[:body_index]
        requested_set = set(requested)
        # A body boundary is the model's explicit sequence-level decision.  It
        # may provide only one example ID in exclude_ids for compatibility, but
        # it cannot contradict the inferred contiguous prefix.
        if any(item not in set(inferred) for item in requested_set):
            return None
        return inferred
    if not requested:
        return []
    requested_set = set(requested)
    prefix: list[str] = []
    for item in candidates:
        if item["id"] not in requested_set:
            break
        prefix.append(item["id"])
    # A hole or a later-only selection is unsafe: keep the source unchanged.
    if len(prefix) != len(requested_set):
        return None
    # Never let a model remove the complete source.  A valid selection must
    # leave at least one source unit for downstream summary generation.
    if len(prefix) >= len(records):
        return None
    return prefix


def _filtered_text(
    source_type: str,
    text: str,
    records: list[dict[str, Any]],
    excluded: set[str],
) -> str:
    if not excluded:
        return text
    if source_type == "srt":
        segments = _parse_srt(text)
        kept: list[str] = []
        occurrences: Counter[int] = Counter()
        for segment in segments:
            index = int(segment.get("index", len(kept) + 1))
            occurrences[index] += 1
            suffix = "" if occurrences[index] == 1 else f":{occurrences[index]}"
            if f"cue:{index}{suffix}" in excluded:
                continue
            kept.append(
                f"{segment['index']}\n{segment['timestamp']}\n{segment['text']}"
            )
        return "\n\n".join(kept)

    blocks = re.split(r"(?:\r?\n){2,}", text.strip()) if text.strip() else []
    kept_blocks: list[str] = []
    for position, block in enumerate(blocks, 1):
        if f"para:{position}" not in excluded:
            kept_blocks.append(block.strip())
    return "\n\n".join(kept_blocks)


def _excluded_ranges(
    source_type: str,
    records: list[dict[str, Any]],
    excluded: list[str],
) -> list[dict[str, Any]]:
    """Return source spans for downstream timeline/metadata filtering."""

    if source_type != "srt" or not excluded:
        return []
    excluded_set = set(excluded)
    selected = [record for record in records if record["id"] in excluded_set]
    if not selected:
        return []
    ranges: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for record in selected:
        start = record.get("start")
        end = record.get("end")
        if start is None or end is None:
            continue
        if current is not None and float(start) <= float(current["end"]) + 1.0:
            current["end"] = max(float(current["end"]), float(end))
            current["ids"].append(record["id"])
            current["end_time"] = _split_timestamp(record["timestamp"])[1]
            continue
        current = {
            "start": float(start),
            "end": float(end),
            "start_time": _split_timestamp(record["timestamp"])[0],
            "end_time": _split_timestamp(record["timestamp"])[1],
            "ids": [record["id"]],
        }
        ranges.append(current)
    return ranges


async def select_main_content(
    text: str,
    metadata: dict[str, Any] | None,
    call: SelectionCall,
) -> dict[str, Any]:
    """Select the main transcript input while preserving the archived source.

    ``call`` is intentionally a one-argument async function.  The pipeline's
    root LLM wrapper supplies :data:`CONTENT_SELECTION_SYSTEM_PROMPT` when it
    invokes the provider.
    """

    source_type, records = _source_records(text)
    report: dict[str, Any] = {
        "source_type": source_type,
        "source_unit_count": len(records),
        "candidate_ids": [],
        "body_start_id": None,
        "excluded_ids": [],
        "excluded_ranges": [],
        "reason": "",
        "error": "",
        "evidence": [],
        "status": "unchanged",
    }
    if not text or not records:
        report["status"] = "fallback"
        report["reason"] = "empty_or_unparseable_source"
        return {"report": report, "filtered_text": text}

    candidates = _opening_candidates(records)
    report["candidate_ids"] = [item["id"] for item in candidates]
    if not candidates:
        report["status"] = "unchanged"
        report["reason"] = "no_removable_opening_candidates"
        return {"report": report, "filtered_text": text}

    evidence = _related_evidence(records, candidates)
    request = _bounded_prompt_inputs(
        source_type,
        _compact_metadata(metadata),
        candidates,
        evidence,
    )
    if request is None:
        report["status"] = "fallback"
        report["reason"] = "selection_prompt_budget_exceeded"
        return {"report": report, "filtered_text": text}
    prompt, visible_candidates, visible_evidence = request
    # Validate against the rows that actually made it into the bounded prompt.
    report["candidate_ids"] = [item["id"] for item in visible_candidates]

    try:
        response = await call(prompt)
        parsed = _extract_json(response)
        if parsed is None:
            raise ValueError("invalid_selection_json")
        requested = _normalise_ids(parsed.get("exclude_ids", parsed.get("excluded_ids")))
        if requested is None:
            raise ValueError("invalid_selection_ids")
        body_start_id = parsed.get("body_start_id")
        if body_start_id is not None and not isinstance(body_start_id, str):
            raise ValueError("invalid_body_start_id")
        body_start_id = body_start_id.strip() if body_start_id else None
        visible_ids = {item["id"] for item in visible_candidates}
        visible_ids.update(item["id"] for item in visible_evidence)
        requested = _canonicalise_model_ids(requested, visible_ids)
        if body_start_id is not None:
            body_start_id = _canonicalise_model_ids([body_start_id], visible_ids)[0]
        excluded = _prefix_exclusions(
            records,
            visible_candidates,
            requested,
            body_start_id=body_start_id,
        )
        if excluded is None:
            raise ValueError("selection_outside_opening_prefix")
        reason = parsed.get("reason", "")
        evidence_ids = _normalise_ids(parsed.get("evidence"))
        if not isinstance(reason, str) or evidence_ids is None:
            raise ValueError("invalid_selection_explanation")
        evidence_ids = _canonicalise_model_ids(evidence_ids, visible_ids)
        evidence_set = set(evidence_ids)
        if not evidence_set.issubset(visible_ids):
            raise ValueError("evidence_not_visible_in_prompt")
        if excluded:
            if not evidence_ids or not reason.strip():
                raise ValueError("excluded_content_requires_reason_and_evidence")
            later_ids = {item["id"] for item in visible_evidence}
            preview_words = (
                "preview", "teaser", "highlight", "promotional", "promo",
                "预告", "片头", "宣传", "开场精华", "预览",
            )
            is_preview = any(word in reason.casefold() for word in preview_words)
            if not (evidence_set & later_ids) and not is_preview:
                raise ValueError("repeated_montage_requires_later_evidence")
            # A reason containing "preview" is not evidence that a real host
            # introduction may be deleted. Anchor both ends of the removable
            # prefix in repeated source text or explicit preview wording.
            supported = {
                candidate_id
                for item in visible_evidence if item["id"] in evidence_set
                for candidate_id in item.get("related_to", [])
            }
            visible_by_id = {item["id"]: item for item in visible_candidates + visible_evidence}
            after_prefix = [visible_by_id[item_id] for item_id in evidence_ids
                            if item_id not in excluded]
            for item in visible_candidates:
                if item["id"] not in excluded:
                    continue
                body = str(item.get("text") or "")
                if any(_similarity(body, str(other.get("text") or "")) >= 0.36
                       for other in after_prefix):
                    supported.add(item["id"])
                if item["id"] in evidence_set and re.search(
                    r"预告|片头精华|精彩抢先看|本期你将看到|\b(?:preview|teaser|coming up)\b",
                    body, re.IGNORECASE,
                ):
                    supported.add(item["id"])
            if excluded[0] not in supported:
                raise ValueError("opening_exclusion_has_no_source_anchor")
            proposed = list(excluded)
            while excluded and excluded[-1] not in supported:
                excluded.pop()
            if excluded != proposed:
                report["boundary_guard"] = {
                    "model_excluded_ids": proposed,
                    "retained_ids": proposed[len(excluded):],
                    "reason": "unsupported_exclusion_end",
                }
                body_start_id = proposed[len(excluded)]
    except Exception as exc:
        report["status"] = "fallback"
        report["reason"] = "model_selection_failed"
        detail = str(exc).strip()
        report["error"] = (
            f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
        )
        return {"report": report, "filtered_text": text}

    report["excluded_ids"] = excluded
    report["body_start_id"] = body_start_id
    report["excluded_ranges"] = _excluded_ranges(source_type, records, excluded)
    report["reason"] = reason.strip()
    report["evidence"] = list(dict.fromkeys(evidence_ids))
    if excluded:
        report["status"] = "filtered"
    return {
        "report": report,
        "filtered_text": _filtered_text(source_type, text, records, set(excluded)),
    }


__all__ = [
    "CONTENT_SELECTION_SYSTEM_PROMPT",
    "CONTENT_SELECTION_RESPONSE_SCHEMA",
    "get_content_selection_prompt",
    "select_main_content",
]
