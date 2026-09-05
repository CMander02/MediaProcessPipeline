"""Transcript responsibilities for the media pipeline."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from app.core.logging_setup import log_event

logger = logging.getLogger(__name__)

_LANG_NAME = {
    "zh": "Simplified Chinese",
    "zh-cn": "Simplified Chinese",
    "zh-hans": "Simplified Chinese",
    "zh-hant": "Traditional Chinese",
    "zh-tw": "Traditional Chinese",
    "en": "English",
    "en-us": "English",
    "en-gb": "English",
    "ja": "Japanese",
    "ja-jp": "Japanese",
    "ko": "Korean",
    "ko-kr": "Korean",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "ru": "Russian",
    "pt": "Portuguese",
    "it": "Italian",
}


def _render_recognition_srt(segments: list[dict[str, Any]]) -> str:
    def fmt_time(seconds: float) -> str:
        total_ms = max(0, int(round(seconds * 1000)))
        hours, remainder = divmod(total_ms, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, millis = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    lines: list[str] = []
    for index, segment in enumerate(segments, 1):
        start = fmt_time(float(segment.get("start") or 0))
        end = fmt_time(float(segment.get("end") or 0))
        text = str(segment.get("text") or "").strip()
        speaker = str(segment.get("speaker") or "").strip()
        if speaker:
            text = f"[{speaker}] {text}"
        lines.append(f"{index}\n{start} --> {end}\n{text}")
    return "\n\n".join(lines)


def _user_language_hint(analysis: dict | None) -> str | None:
    """Pull a human-readable primary-language name out of analysis output.

    Falls back to whatever the analyze step returned (raw code/name) when we
    don't have a mapping, and to ``None`` when analysis is empty.
    """
    if not analysis:
        return None
    raw = str(analysis.get("language") or "").strip()
    if not raw or raw.lower() == "unknown":
        return None
    return _LANG_NAME.get(raw.lower(), raw)


def _extract_internal_asr_error(recognition_segments: list[dict[str, Any]] | None) -> str | None:
    """Detect mock/error placeholder text emitted by ASR backends.

    These placeholders are useful in isolated service tests, but they should
    not be treated as a valid transcript for downstream LLM analysis.
    """
    if not recognition_segments:
        return None

    texts = [str(seg.get("text", "")).strip() for seg in recognition_segments if seg.get("text")]
    if not texts:
        return None

    prefixes = (
        "[Qwen3-ASR error:",
        "[Mock - Qwen3-ASR not installed]",
    )
    for text in texts:
        if text.startswith(prefixes):
            return text.strip("[]")
    return None


def _plain_text_from_srt(srt_content: str) -> str:
    """Extract readable transcript text from SRT content."""
    return " ".join(
        line.strip()
        for line in srt_content.splitlines()
        if line.strip() and not line.strip().isdigit() and "-->" not in line
    )


async def _select_polish_track(
    tracks: list[dict],
    srt_text_hint: str = "",
) -> tuple[dict, str]:
    """Pick the subtitle track to polish based on LLM-detected language.

    Detects the video's spoken language from a sample of the first (best)
    track's subtitle content; then matches the detected language against
    the available tracks. Falls back to the first track when no match or
    detection fails.

    Returns (selected_track, detected_lang). detected_lang may be "unknown".
    """
    from app.services.analysis.language_detect import (
        detect_transcript_language,
        match_track_by_language,
    )

    if not tracks:
        raise ValueError("no tracks to select from")
    if len(tracks) == 1:
        # No point running LLM — still detect lang so we can tag metadata
        try:
            sample_srt = Path(tracks[0]["path"]).read_text(encoding="utf-8", errors="ignore")
            detected = await detect_transcript_language(srt=sample_srt)
        except Exception:
            detected = tracks[0].get("lang") or "unknown"
        return tracks[0], detected

    # Use the first (best — CC before AI) track's content as detection sample
    # if no external srt_text_hint was provided.
    sample = srt_text_hint
    if not sample:
        try:
            sample = Path(tracks[0]["path"]).read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            log_event(logger, logging.WARNING, "subtitle.lang_detect.sample_read_failed", error=e)
            return tracks[0], tracks[0].get("lang") or "unknown"

    detected = await detect_transcript_language(srt=sample)
    if detected == "unknown":
        log_event(logger, logging.INFO, "subtitle.track.select_default", reason="unknown_language")
        return tracks[0], detected

    matched = match_track_by_language(tracks, detected)
    if matched:
        log_event(
            logger,
            logging.INFO,
            "subtitle.track.selected",
            lang=matched.get("lang"),
            detected_lang=detected,
        )
        return matched, detected

    log_event(
        logger,
        logging.INFO,
        "subtitle.track.select_default",
        reason="no_match",
        detected_lang=detected,
    )
    return tracks[0], detected


def _save_all_tracks_as_transcripts(tracks: list[dict], task_dir: Path) -> list[dict]:
    """Copy each platform subtitle track into task_dir as transcript.{lang}.srt.

    The one chosen for polish will additionally get transcript_polished.srt
    (written elsewhere). Returns a list of manifest entries for metadata.json.
    """
    from shutil import copyfile

    manifest: list[dict] = []
    for t in tracks:
        src = Path(t["path"])
        if not src.exists():
            continue
        lang = t.get("lang") or "unknown"
        dest = task_dir / f"transcript.{lang}.srt"
        try:
            if str(src.resolve()) != str(dest.resolve()):
                copyfile(src, dest)
        except Exception as e:
            log_event(
                logger, logging.WARNING, "subtitle.track.copy_failed", src=src, dest=dest, error=e
            )
            continue
        manifest.append(
            {
                "lang": lang,
                "type": t.get("type") or "cc",
                "filename": dest.name,
                "polished": False,
                "source_engine": t.get("source_engine"),
                "validation": t.get("validation"),
            }
        )
    return manifest


def _is_transcript_too_short_for_uvr_fallback(transcript: str, *, min_chars: int = 30) -> bool:
    normalized = re.sub(r"\s+", "", transcript or "")
    return len(normalized) < min_chars


def _require_audio_file(path: str | None, *, stage: str) -> str:
    """Return a concrete audio path or raise a stage-specific error."""
    if not path:
        raise RuntimeError(f"{stage} requires an audio file, but no audio path is available")
    audio_file = Path(path)
    if not audio_file.exists():
        raise FileNotFoundError(f"{stage} audio file does not exist: {path}")
    if not audio_file.is_file():
        raise ValueError(f"{stage} audio path is not a file: {path}")
    return str(audio_file)
