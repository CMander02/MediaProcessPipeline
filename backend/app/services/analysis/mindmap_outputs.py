"""Pure mindmap outputs used by the LLM service."""

import logging
import re
from typing import Any

from app.services.analysis.transcript_outputs import (
    _seconds_to_srt_timestamp,
    _timestamp_to_seconds,
)

logger = logging.getLogger(__name__)

_TIMESTAMP_RE = re.compile(
    r"\s*\[(\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d{1,3})?)"
    r"(?:\s*-\s*(\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d{1,3})?))?\]\s*$"
)


def _split_mindmap_line(line: str) -> tuple[int, str, float | None, float | None] | None:
    stripped = line.rstrip()
    marker = re.match(r"^(\s*)[-*]\s+(.+?)\s*$", stripped)
    heading = re.match(r"^(#{2,6})\s+(.+?)\s*$", stripped)
    if marker:
        depth = len(marker.group(1).replace("\t", "  ")) // 2
        title = marker.group(2).strip()
    elif heading:
        depth = len(heading.group(1)) - 2
        title = heading.group(2).strip()
    else:
        return None
    start = end = None
    ts_match = _TIMESTAMP_RE.search(title)
    if ts_match:
        start = _timestamp_to_seconds(ts_match.group(1))
        end = _timestamp_to_seconds(ts_match.group(2))
        title = title[: ts_match.start()].strip()
    return depth, title, start, end


def mindmap_markdown_without_timestamps(markdown: str) -> str:
    """Export a timed mindmap as an H2-H6 document hierarchy."""
    lines: list[str] = []
    for raw in markdown.splitlines():
        parsed = _split_mindmap_line(raw)
        if not parsed:
            continue
        depth, title, _start, _end = parsed
        heading_level = min(6, depth + 2)
        lines.append(f"{'#' * heading_level} {title}")
    return "\n".join(lines)


def mindmap_markdown_to_timed_tree(markdown: str) -> dict[str, Any]:
    """Parse `- node [start - end]` markdown into a frontend-friendly tree."""
    roots: list[dict[str, Any]] = []
    stack: list[tuple[int, dict[str, Any]]] = []
    for raw in markdown.splitlines():
        parsed = _split_mindmap_line(raw)
        if not parsed:
            continue
        depth, title, start, end = parsed
        node: dict[str, Any] = {"title": title, "children": []}
        if start is not None:
            node["start"] = start
        if end is not None:
            node["end"] = end
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if stack:
            stack[-1][1]["children"].append(node)
        else:
            roots.append(node)
        stack.append((depth, node))
    if not roots:
        return {"title": "Mindmap", "children": []}
    if len(roots) == 1:
        return roots[0]
    return {"title": "Mindmap", "children": roots}


def _compose_chapter_mindmap(
    chapters: list[dict],
    chapter_summaries: dict[tuple[int, float], str],
) -> str:
    """Use source chapters as immutable top-level mindmap nodes."""
    lines: list[str] = []
    for index, chapter in enumerate(chapters):
        title = str(chapter.get("title") or "").strip()
        if not title:
            continue
        try:
            start = float(chapter.get("start_time") or 0)
        except (TypeError, ValueError):
            start = 0
        timestamp = _seconds_to_srt_timestamp(start)
        if start == int(start):
            timestamp = timestamp.split(",", 1)[0]
        lines.append(f"- {title} [{timestamp}]")
        summary_value = chapter_summaries.get(_chapter_key(chapter, index))
        if summary_value is None:
            # Keep compatibility with callers that keyed unique chapters by
            # their display title before stable collision keys were added.
            summary_value = chapter_summaries.get(title, "")
        summary = _filter_mindmap_lines(summary_value)
        for raw_line in summary.splitlines():
            if raw_line.strip():
                lines.append(f"  {raw_line}")
    return "\n".join(lines)


def _chapter_key(chapter: dict, index: int) -> tuple[int, float]:
    """Return a collision-free key while keeping chapter titles for display."""
    try:
        start_value = chapter.get("start_time")
        if start_value is None:
            start_value = chapter.get("start", 0)
        start = float(start_value or 0)
    except (TypeError, ValueError):
        start = 0.0
    return index, start


def _split_segments_by_chapters(
    segments: list[dict],
    chapters: list[dict],
) -> dict[tuple[int, float], str]:
    """Split SRT segments into chapter-keyed text blocks."""

    def ts_to_seconds(ts_str: str) -> float:
        """Parse HH:MM:SS or MM:SS or seconds to float."""
        ts_str = str(ts_str).strip()
        parts = ts_str.replace(",", ".").split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(ts_str)

    def seg_start_seconds(seg: dict) -> float:
        ts_line = seg["timestamp"]
        start = ts_line.split("-->")[0].strip()
        return ts_to_seconds(start)

    seg_starts = [
        (
            seg_start_seconds(seg),
            f"[{seg['timestamp']}] {seg['text']}",
        )
        for seg in segments
    ]

    chapter_texts: dict[tuple[int, float], str] = {}
    for i, ch in enumerate(chapters):
        start_s = ts_to_seconds(ch.get("start_time", 0))
        end_s = ts_to_seconds(chapters[i + 1]["start_time"]) if i + 1 < len(chapters) else 1e9
        texts = [text for s, text in seg_starts if start_s <= s < end_s]
        chapter_texts[_chapter_key(ch, i)] = "\n".join(texts)

    return chapter_texts


def _split_plain_by_chapters(
    text: str,
    chapters: list[dict],
) -> dict[tuple[int, float], str]:
    """Rough split of plain text by chapter proportion."""
    total_len = len(text)
    n = len(chapters)
    chunk = total_len // max(n, 1)
    result: dict[tuple[int, float], str] = {}
    for i, ch in enumerate(chapters):
        start = i * chunk
        end = (i + 1) * chunk if i + 1 < n else total_len
        result[_chapter_key(ch, i)] = text[start:end]
    return result


def _build_global_context(
    metadata: dict[str, Any],
    chapters: list[dict],
) -> str:
    """Build a concise global context string from metadata."""
    parts = []
    if metadata.get("title"):
        parts.append(f"标题: {metadata['title']}")
    if metadata.get("uploader"):
        parts.append(f"作者: {metadata['uploader']}")
    if chapters:
        ch_list = " / ".join(f"{ch.get('start_time', 0)}s {ch.get('title', '')}" for ch in chapters)
        parts.append(f"章节: {ch_list}")
    desc = metadata.get("description", "")
    if desc:
        parts.append(f"简介: {desc[:300]}")
    source_context = metadata.get("source_context") or {}
    entities = source_context.get("entities") or []
    if entities:
        parts.append(
            "规范实体: "
            + " / ".join(
                str(item.get("canonical") or "")
                for item in entities
                if isinstance(item, dict) and item.get("canonical")
            )
        )
    return "\n".join(parts)


def _filter_mindmap_lines(resp: str) -> str:
    """Filter response to plain text list lines, strip any markdown formatting."""
    lines = [
        line
        for line in resp.strip().split("\n")
        if line.strip().startswith("-") or line.strip().startswith("*")
    ]
    # Normalize * to -
    lines = [
        line.replace("* ", "- ", 1) if line.lstrip().startswith("* ") else line
        for line in lines
    ]
    # Strip markdown formatting: bold, italic, code, links
    cleaned = []
    for line in lines:
        line = line.replace("**", "").replace("__", "")  # bold
        line = line.replace("*", "").replace("_", "")  # italic (careful: only standalone)
        line = re.sub(r"`([^`]*)`", r"\1", line)  # inline code
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)  # links
        line = re.sub(r"^(\s*- )#+\s*", r"\1", line)  # heading markers after bullet
        cleaned.append(line)
    return "\n".join(cleaned) if cleaned else resp
