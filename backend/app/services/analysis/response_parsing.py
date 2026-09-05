"""Pure response parsing used by the LLM service."""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _sample_analysis_text(text: str, limit: int = 8000) -> str:
    """Sample opening, closing, and evenly spaced body regions."""
    if len(text) <= limit:
        return text
    window = 1000
    samples = [text[:2000]]
    body_start = 2000
    body_end = max(body_start, len(text) - 1000)
    slots = 5
    for index in range(slots):
        ratio = index / max(1, slots - 1)
        start = int(body_start + (body_end - body_start - window) * ratio)
        samples.append(text[max(body_start, start) : max(body_start, start) + window])
    samples.append(text[-1000:])
    return "\n\n--- transcript sample ---\n\n".join(samples)[:limit]


def _parse_summary_json(response: str) -> dict[str, Any] | None:
    start, end = response.find("{"), response.rfind("}") + 1
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(response[start:end])
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None
