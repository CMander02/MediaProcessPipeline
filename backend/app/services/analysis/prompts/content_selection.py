"""Prompt construction for selecting opening transcript content.

The selector is deliberately narrower than a summarizer.  It may identify an
opening montage or preview when later source text proves that the same material
is repeated, while preserving a genuine opening argument or introduction.
"""

from __future__ import annotations

import json
from typing import Any

CONTENT_SELECTION_SYSTEM_PROMPT = """You select source units for a downstream summary.
Use the transcript text as the primary evidence. Metadata and chapter labels are
supporting hints only. Return only the requested JSON object.

You may exclude units only from the supplied opening candidates. Exclude a unit
only when it is clearly a teaser/highlight montage or promotional preview. A
montage should have later transcript evidence showing the same claims or topics;
a promotional preview may cite its own opening wording as evidence when its
preview framing is explicit. Keep a real opening argument, framing statement,
host introduction, and first question even when they are elaborated later. Do
not use fixed duration or title-only rules. Cite visible source unit IDs for every
exclusion. An empty exclusion list is the preferred result when repetition or
preview framing is uncertain. Judge the opening candidates as a sequence: several
short, unrelated high-signal statements followed by a host/guest introduction or
formal first question are often one montage. Find the transition to the real body
before deciding how many units to remove."""

CONTENT_SELECTION_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "exclude_ids": {"type": "array", "items": {"type": "string"}},
        "body_start_id": {"type": ["string", "null"]},
        "reason": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["exclude_ids", "reason", "evidence"],
}


def get_content_selection_prompt(
    *,
    source_type: str,
    metadata: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> str:
    """Build the bounded prompt used by :func:`select_main_content`.

    Records already have stable ``id`` values.  The prompt intentionally keeps
    the candidate and evidence lists separate so the model cannot mistake later
    evidence for removable source units.
    """

    metadata_text = json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":"))
    candidate_text = "\n".join(
        f"- {item['id']}: {item.get('prompt_text', item.get('text', ''))}"
        for item in candidates
    ) or "(none)"
    evidence_text = "\n".join(
        f"- {item['id']} (related to {', '.join(item.get('related_to') or [])}): "
        f"{item.get('text', '')}"
        for item in evidence
    ) or "(none found by local similarity search)"

    return f"""Review the opening of this {source_type} transcript for downstream
summary and mind-map generation.

## Supporting metadata (read-only hints; never sufficient by itself)
{metadata_text}

## Opening candidates (the only units that may be excluded)
{candidate_text}

## Later transcript evidence (read-only; cite IDs when it proves repetition)
{evidence_text}

Return JSON only in this shape:
{{
  "exclude_ids": ["opening-unit-id"],
  "body_start_id": "first-unit-of-the-real-main-content-or-null",
  "reason": "brief source-grounded explanation, or an empty string",
  "evidence": ["later-unit-id"]
}}

Rules:
1. Judge the candidate sequence jointly. Identify the boundary where the real
   conversation/article begins. If units 1 through 5 form one multi-topic teaser,
   remove the whole prefix and set body_start_id to unit 6; do not stop after the
   first line merely because one later match is strongest.
2. Exclude only a contiguous prefix of the opening candidates. If body_start_id
   is supplied, exclude every candidate before it and preserve it and everything
   after it. The caller rejects IDs outside the visible candidate list.
3. A candidate is removable when it functions as a teaser/highlight montage and
   its claims are supported by later evidence, or when it is explicitly framed as
   promotional preview. A sentence that opens the actual discussion remains
   content even if it returns later.
4. A source chapter hint such as an opening-views chapter followed by a guest
   introduction may support the transition, but the transcript wording must show
   the boundary. Chapter metadata alone never justifies removal.
5. Cite one or more visible source unit IDs for every excluded unit. For a
   repeated montage, cite later evidence; for an explicit promotional preview,
   candidate IDs showing the preview framing are valid evidence.
6. An exclusion with an empty evidence array is invalid. Do not exclude based
   only on a chapter title, speaker name, short greeting, or fixed timestamp.
7. Copy IDs exactly as shown, including the ``cue:`` or ``para:`` prefix. Do
   not return bare numeric IDs.
"""
