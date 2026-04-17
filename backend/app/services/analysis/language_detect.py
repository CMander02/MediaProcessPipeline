"""LLM-based language detection for transcripts.

Samples head/middle/tail from a transcript and asks the LLM to identify
the primary spoken language. Used to pick which subtitle track to polish
when multiple language tracks are available.
"""

import logging
import re

logger = logging.getLogger(__name__)


# Canonical language codes the detector is allowed to return. The LLM is
# instructed to pick one of these exactly; anything else is treated as
# unknown and the caller falls back to the first available track.
CANONICAL_LANGS = [
    "zh", "zh-Hant", "en", "ja", "ko",
    "fr", "de", "es", "ru", "pt", "it", "ar", "hi", "vi", "th", "id",
]


def _sample_segments_from_srt(srt: str, samples_per_region: int = 5) -> list[str]:
    """Pull 3 regions (start/middle/end) * samples_per_region consecutive cue texts."""
    if not srt:
        return []
    blocks = [b.strip() for b in re.split(r"\n\n+", srt.strip()) if b.strip()]
    texts: list[str] = []
    for block in blocks:
        lines = block.split("\n")
        text_lines = [l for l in lines if "-->" not in l and not l.strip().isdigit()]
        t = " ".join(l.strip() for l in text_lines if l.strip())
        if t:
            texts.append(t)
    if not texts:
        return []

    n = len(texts)
    if n <= samples_per_region * 3:
        return texts

    mid_start = max(0, (n // 2) - samples_per_region // 2)
    regions = [
        texts[:samples_per_region],
        texts[mid_start:mid_start + samples_per_region],
        texts[-samples_per_region:],
    ]
    return [t for region in regions for t in region]


def _sample_segments_from_text(text: str, chars_per_region: int = 400) -> list[str]:
    """Fallback: pull head/middle/tail chunks from a flat transcript string."""
    if not text:
        return []
    n = len(text)
    if n <= chars_per_region * 3:
        return [text]
    mid_start = (n // 2) - (chars_per_region // 2)
    return [
        text[:chars_per_region],
        text[mid_start:mid_start + chars_per_region],
        text[-chars_per_region:],
    ]


def _build_prompt(samples: list[str]) -> str:
    lang_list = ", ".join(CANONICAL_LANGS)
    joined = "\n---\n".join(samples)
    return f"""Identify the primary spoken language of the transcript below.

Return EXACTLY ONE of these codes on a single line, nothing else:
{lang_list}

If mixed, pick the dominant one. If none match, return "unknown".

Transcript samples (taken from the beginning, middle, and end):
{joined}

Answer with just the code:"""


def _parse_response(response: str) -> str:
    if not response:
        return "unknown"
    first = response.strip().split("\n")[0].strip().strip('"\'`.,;')
    first_l = first.lower()
    for canon in CANONICAL_LANGS:
        if first_l == canon.lower():
            return canon
    # partial match (LLM wrote e.g. "zh-Hans")
    for canon in CANONICAL_LANGS:
        if first_l.startswith(canon.lower()):
            return canon
    return "unknown"


async def detect_transcript_language(
    srt: str = "",
    text: str = "",
) -> str:
    """Detect the primary language of a transcript using LLM.

    Prefer `srt` (better boundaries); fall back to `text`. Returns a canonical
    code from ``CANONICAL_LANGS`` or ``"unknown"``.
    """
    from app.services.analysis.llm import get_llm_service

    samples: list[str] = []
    if srt:
        samples = _sample_segments_from_srt(srt)
    if not samples and text:
        samples = _sample_segments_from_text(text)
    if not samples:
        return "unknown"

    prompt = _build_prompt(samples)
    try:
        llm = get_llm_service()
        response = await llm._call(prompt)
    except Exception as e:
        logger.warning(f"Language detection LLM call failed: {e}")
        return "unknown"

    lang = _parse_response(response)
    logger.info(f"Detected transcript language: {lang} (raw='{response.strip()[:80]}')")
    return lang


def match_track_by_language(
    tracks: list[dict],
    target_lang: str,
) -> dict | None:
    """Find the subtitle track whose lang best matches target_lang.

    Matches are case-insensitive; accepts prefix match (e.g. ``zh`` matches
    ``zh-Hans``, ``zh-CN``). Returns ``None`` when no track matches.
    """
    if not tracks or not target_lang or target_lang == "unknown":
        return None
    target_l = target_lang.lower()
    # Exact match first
    for t in tracks:
        if (t.get("lang") or "").lower() == target_l:
            return t
    # Prefix match — target is family, track is variant (or vice versa)
    for t in tracks:
        track_l = (t.get("lang") or "").lower()
        if track_l.startswith(target_l) or target_l.startswith(track_l):
            return t
    return None
