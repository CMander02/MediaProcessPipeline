"""Deterministic quality gates for ASR chunk and segment output."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any


def _repetition_ratio(text: str) -> float:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    if not normalized:
        return 0.0
    tokens = re.findall(r"[a-z]+|[\u4e00-\u9fff]|\d+", normalized)
    if len(tokens) < 6:
        return 0.0
    token_counts = Counter(tokens)
    token_ratio = token_counts.most_common(1)[0][1] / len(tokens)
    compact = re.sub(r"\s+", "", normalized)
    ngrams = [compact[index:index + 2] for index in range(max(0, len(compact) - 1))]
    ngram_ratio = (
        Counter(ngrams).most_common(1)[0][1] / len(ngrams)
        if ngrams
        else 0.0
    )
    return max(token_ratio, ngram_ratio)


def assess_asr_text(text: str, duration_sec: float) -> dict[str, Any]:
    compact = re.sub(r"\s+", "", str(text or ""))
    duration = max(0.1, float(duration_sec or 0.1))
    char_rate = len(compact) / duration
    repetition = _repetition_ratio(text)
    reasons: list[str] = []
    if char_rate > 30:
        reasons.append("character_rate")
    if len(compact) >= 80 and repetition >= 0.42:
        reasons.append("repetition")
    if re.search(r"(?:\b(?:la|we|ah|oh)\b[\s,，.!！?？]*){10,}", text, re.I):
        reasons.append("music_or_hallucination_pattern")
    return {
        "valid": not reasons,
        "reasons": reasons,
        "chars": len(compact),
        "duration_sec": round(duration, 3),
        "chars_per_sec": round(char_rate, 3),
        "repetition_ratio": round(repetition, 3),
    }


def filter_asr_segments(
    segments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        start = float(segment.get("start") or 0)
        end = float(segment.get("end") or start)
        assessment = assess_asr_text(str(segment.get("text") or ""), end - start)
        if assessment["valid"]:
            accepted.append(segment)
            continue
        diagnostics.append(
            {
                **assessment,
                "segment_index": index,
                "start": start,
                "end": end,
                "action": "isolated",
            }
        )
    return accepted, diagnostics
