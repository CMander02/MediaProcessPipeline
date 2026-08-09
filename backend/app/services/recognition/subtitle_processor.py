"""
Platform subtitle processor — cue-preserving punctuation and correction via LLM.

When platform subtitles are available (YouTube auto/manual, Bilibili, local SRT),
this module processes them through the same constrained polish path as ASR.
Speaker and timestamp fields remain read-only.

Output is compatible with the ASR path (segments + SRT format).
"""

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.core.logging_setup import log_event
from app.models import MediaMetadata

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Subtitle parsing
# ---------------------------------------------------------------------------

def parse_json3(path: str | Path) -> list[dict]:
    """Parse YouTube json3 subtitle format into fine-grained segments."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    events = data.get("events", [])

    segments = []
    for e in events:
        segs = e.get("segs", [])
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text:
            continue
        segments.append({
            "start_ms": e.get("tStartMs", 0),
            "end_ms": e.get("tStartMs", 0) + e.get("dDurationMs", 0),
            "text": text,
        })
    return segments


def parse_srt(path_or_content: str | Path) -> list[dict]:
    """Parse SRT subtitle into segments.

    Accepts either a file path or raw SRT content string.
    """
    p = Path(path_or_content)
    if p.exists():
        content = p.read_text(encoding="utf-8")
    else:
        content = str(path_or_content)

    segments = []
    blocks = re.split(r"\n\n+", content.strip())

    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 2:
            continue

        # Find the timestamp line
        ts_line = None
        text_lines = []
        for line in lines:
            if "-->" in line:
                ts_line = line
            elif ts_line is not None:
                text_lines.append(line)

        if not ts_line or not text_lines:
            continue

        # Parse timestamp: 00:00:01,022 --> 00:00:02,042
        ts_match = re.match(
            r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})",
            ts_line.strip(),
        )
        if not ts_match:
            continue

        g = ts_match.groups()
        start_ms = int(g[0]) * 3600000 + int(g[1]) * 60000 + int(g[2]) * 1000 + int(g[3])
        end_ms = int(g[4]) * 3600000 + int(g[5]) * 60000 + int(g[6]) * 1000 + int(g[7])

        text = " ".join(text_lines).strip()
        # Remove HTML tags if present
        text = re.sub(r"<[^>]+>", "", text)
        if text:
            segments.append({"start_ms": start_ms, "end_ms": end_ms, "text": text})

    return segments


def parse_vtt(path: str | Path) -> list[dict]:
    """Parse WebVTT subtitle into segments (similar to SRT but with header)."""
    content = Path(path).read_text(encoding="utf-8")
    # Remove WebVTT header
    content = re.sub(r"^WEBVTT.*?\n\n", "", content, flags=re.DOTALL)
    return parse_srt(content)


def parse_subtitle_file(path: str, fmt: str) -> list[dict]:
    """Parse subtitle file based on format."""
    if fmt == "json3":
        return parse_json3(path)
    elif fmt == "srt":
        return parse_srt(path)
    elif fmt == "vtt":
        return parse_vtt(path)
    else:
        raise ValueError(f"Unsupported subtitle format: {fmt}")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _ts_to_seconds(ts: str) -> float:
    parts = ts.replace(",", ".").split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return 0


# ---------------------------------------------------------------------------
# Main processing function
# ---------------------------------------------------------------------------

async def process_subtitles(
    subtitle_path: str,
    subtitle_format: str,
    metadata: MediaMetadata,
    on_progress: Any = None,
    source_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Process platform subtitles through cue-preserving LLM correction.

    Args:
        subtitle_path: Path to the subtitle file
        subtitle_format: Format of the subtitle file ("json3", "srt", "vtt")
        metadata: Video metadata for context
        on_progress: Optional callback for progress updates

    Returns:
        Dict compatible with ASR output:
        {
            "language": str,
            "segments": list[dict],
            "srt": str,              # Original subtitle as SRT
            "polished_srt": str,     # Corrected SRT with cue fields preserved
            "polished_md": str,      # Markdown version
            "speakers": list[str],
            "subtitle_source": "platform",
        }
    """
    from app.services.analysis.llm import get_llm_service

    log_event(
        logger,
        logging.INFO,
        "subtitle.process.started",
        path=subtitle_path,
        format=subtitle_format,
    )

    # Step 1: Parse subtitle file
    segments = parse_subtitle_file(subtitle_path, subtitle_format)
    if not segments:
        raise ValueError(f"No segments found in subtitle file: {subtitle_path}")

    log_event(logger, logging.INFO, "subtitle.parse.completed", segments=len(segments))

    # Build original SRT from raw segments
    original_srt = _segments_to_original_srt(segments)

    # Platform captions do not carry trusted speaker identity. Route them
    # through the same cue-preserving polish path as ASR subtitles so the LLM
    # cannot invent speaker labels or rewrite timestamps.
    llm_service = get_llm_service()
    context: dict[str, Any] = {}
    if source_context:
        from app.services.analysis.source_context import source_context_to_analysis

        context = source_context_to_analysis(source_context)
    if not context:
        context = {
            "language": "unknown",
            "content_type": metadata.content_subtype or str(metadata.media_type),
            "proper_nouns": [],
        }
    try:
        polished_srt = await llm_service.polish(original_srt, context=context)
    except Exception as exc:
        log_event(logger, logging.ERROR, "subtitle.polish.failed", error=exc)
        polished_srt = original_srt
    if on_progress:
        await on_progress(1.0)
    polished_md = llm_service.srt_to_markdown(polished_srt, metadata.title)

    result_segments: list[dict[str, Any]] = []
    known_speakers: list[str] = []
    for cue in llm_service._parse_srt(polished_srt):
        start_text, end_text = cue["timestamp"].split("-->", 1)
        speaker, body = llm_service._split_speaker_prefix(cue["text"])
        if speaker and speaker not in known_speakers:
            known_speakers.append(speaker)
        result_segments.append(
            {
                "start": _ts_to_seconds(start_text.strip()),
                "end": _ts_to_seconds(end_text.strip()),
                "text": body,
                "speaker": speaker,
            }
        )

    log_event(
        logger,
        logging.INFO,
        "subtitle.process.completed",
        segments=len(result_segments),
        speakers=len(known_speakers),
    )

    return {
        "language": "zh",
        "segments": result_segments,
        "srt": original_srt,
        "polished_srt": polished_srt,
        "polished_md": polished_md,
        "speakers": known_speakers,
        "subtitle_source": "platform",
    }


def _segments_to_original_srt(segments: list[dict]) -> str:
    """Convert raw segments to basic SRT format (without speaker labels)."""
    blocks = []
    for i, seg in enumerate(segments, 1):
        start = _ms_to_srt_ts(seg["start_ms"])
        end = _ms_to_srt_ts(seg["end_ms"])
        blocks.append(f"{i}\n{start} --> {end}\n{seg['text']}")
    return "\n\n".join(blocks)


def _ms_to_srt_ts(ms: int) -> str:
    """Convert milliseconds to SRT timestamp format: HH:MM:SS,mmm"""
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
