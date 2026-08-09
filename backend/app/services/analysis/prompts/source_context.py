"""System and user prompts for source-context tagging."""

from __future__ import annotations

import json
from typing import Any

SOURCE_CONTEXT_SYSTEM_PROMPT = """You are a source-grounded metadata tagger for a media
processing pipeline.

Your output is machine-consumed JSON. Follow these rules exactly:
- Source metadata is evidence. Preserve names, timestamps, spellings, and roles found there.
- Never invent an entity, speaker, role, or speaker count without cited source evidence.
- `timeline` is maintained by deterministic code. Do not return or modify it.
- Canonical names must use the most complete spelling present in the source.
- Aliases contain only spellings that occur in the source or obvious ASR variants explicitly shown.
- Speaker-count hints need an evidence string and confidence from 0 to 1.
- Return one JSON object and no surrounding prose or markdown fences.
"""


def get_source_context_prompt(
    source_metadata: dict[str, Any],
    deterministic_context: dict[str, Any],
) -> str:
    schema = {
        "language_hint": "zh-CN",
        "content_type": "podcast_interview",
        "entities": [
            {
                "canonical": "Evolvent AI",
                "aliases": ["Evolve AI"],
                "type": "organization",
                "evidence": "title",
                "confidence": 0.98,
            }
        ],
        "speaker_candidates": [
            {
                "name": "孟繁青",
                "role": "guest",
                "evidence": "title",
                "confidence": 0.98,
            }
        ],
        "speaker_count_hint": {
            "exact": 2,
            "min": 2,
            "max": 2,
            "confidence": 0.95,
            "evidence": "title identifies a one-on-one interview",
        },
    }
    return "\n\n".join(
        [
            "## Source metadata\n" + json.dumps(source_metadata, ensure_ascii=False, indent=2),
            "## Deterministic baseline\n"
            + json.dumps(deterministic_context, ensure_ascii=False, indent=2),
            "## Required output schema\n" + json.dumps(schema, ensure_ascii=False, indent=2),
            (
                "Return a conservative evidence-backed patch. Keep arrays empty "
                "when evidence is insufficient."
            ),
        ]
    )


def get_source_context_repair_prompt(
    original_prompt: str,
    invalid_response: str,
    validation_error: str,
) -> str:
    return f"""Repair the invalid JSON response using the original evidence and schema.

## Original request
{original_prompt}

## Invalid response
{invalid_response}

## Validation error
{validation_error}

Return only the repaired JSON object."""
