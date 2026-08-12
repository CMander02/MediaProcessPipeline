"""Deterministic script normalization for generated Chinese text."""

from __future__ import annotations

from functools import lru_cache
from threading import RLock

_SIMPLIFIED_CHINESE = {
    "chinese",
    "simplified chinese",
    "zh",
    "zh-cn",
    "zh-hans",
    "zh-sg",
}
_TRADITIONAL_CHINESE = {
    "traditional chinese",
    "zh-hant",
    "zh-hk",
    "zh-mo",
    "zh-tw",
}
_converter_lock = RLock()


@lru_cache(maxsize=2)
def _opencc_converter(configuration: str):
    import opencc

    return opencc.OpenCC(configuration)


def _convert(text: str, configuration: str) -> str:
    with _converter_lock:
        return _opencc_converter(configuration).convert(text)


def _difference_count(left: str, right: str) -> int:
    return sum(a != b for a, b in zip(left, right)) + abs(len(left) - len(right))


def chinese_script(
    user_language: str | None,
    *,
    source_text: str = "",
) -> str | None:
    """Resolve the requested Chinese script, with conservative source inference."""
    language = (user_language or "").strip().lower().replace("_", "-")
    if language in _SIMPLIFIED_CHINESE:
        return "simplified"
    if language in _TRADITIONAL_CHINESE:
        return "traditional"
    if language and language not in {"auto", "unknown", "auto-detect from transcript"}:
        return None
    if not source_text:
        return None

    simplified = _convert(source_text, "t2s.json")
    traditional = _convert(source_text, "s2t.json")
    traditional_chars = _difference_count(source_text, simplified)
    simplified_chars = _difference_count(source_text, traditional)
    if simplified_chars >= 3 and simplified_chars > traditional_chars * 2:
        return "simplified"
    if traditional_chars >= 3 and traditional_chars > simplified_chars * 2:
        return "traditional"
    return None


def language_script_instruction(user_language: str | None) -> str:
    """Return a prompt instruction for an explicitly requested Chinese script."""
    script = chinese_script(user_language)
    if script == "simplified":
        return "- Use Simplified Chinese characters throughout all Chinese narrative text."
    if script == "traditional":
        return "- Use Traditional Chinese characters throughout all Chinese narrative text."
    return ""


def normalize_chinese_script(
    text: str,
    user_language: str | None,
    *,
    source_text: str = "",
) -> str:
    """Normalize generated text to the requested or inferred Chinese script."""
    if not text:
        return text
    script = chinese_script(user_language, source_text=source_text)
    if script == "simplified":
        return _convert(text, "t2s.json")
    if script == "traditional":
        return _convert(text, "s2t.json")
    return text
