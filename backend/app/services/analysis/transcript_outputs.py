"""Pure transcript outputs used by the LLM service."""

import json
import logging
import re
from typing import Any

from app.core.logging_setup import log_event

logger = logging.getLogger(__name__)

_SPEAKER_PREFIX_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(.*)$", re.DOTALL)


_SENTENCE_SPLIT_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]*[”’）】》」』]*")


_SENTENCE_END_RE = re.compile(r"(?:[。！？!?；;]|(?<!\d)\.)[\"'”’）】》」』]*$")


def _timestamp_to_seconds(value: str | None) -> float | None:
    if not value:
        return None
    parts = value.replace(",", ".").split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (TypeError, ValueError):
        return None


def _seconds_to_srt_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _parse_srt(srt_content: str) -> list[dict]:
    """Parse SRT content into segments."""
    segments = []
    blocks = re.split(r"\n\n+", srt_content.strip())

    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) >= 3:
            try:
                index = int(lines[0])
                timestamp = lines[1]
                text = "\n".join(lines[2:])
                segments.append({"index": index, "timestamp": timestamp, "text": text})
            except (ValueError, IndexError):
                continue

    return segments


def _segments_to_srt(segments: list[dict]) -> str:
    """Convert segments back to SRT format."""
    result = []
    for seg in segments:
        result.append(f"{seg['index']}\n{seg['timestamp']}\n{seg['text']}")
    return "\n\n".join(result)


def _split_speaker_prefix(text: str) -> tuple[str | None, str]:
    """Return (speaker, body) for text starting with a [speaker] tag."""
    match = _SPEAKER_PREFIX_RE.match(text.strip())
    if not match:
        return None, text.strip()
    return match.group(1).strip(), match.group(2).strip()


def _timestamp_bounds(timestamp: str) -> tuple[str, str]:
    if "-->" not in timestamp:
        ts = timestamp.strip()
        return ts, ts
    start, end = timestamp.split("-->", 1)
    return start.strip(), end.strip()


def _join_turn_text(existing: str, new_text: str) -> str:
    """Join cue text fragments without inserting noisy spaces in Chinese."""
    existing = existing.strip()
    new_text = new_text.strip()
    if not existing:
        return new_text
    if not new_text:
        return existing
    prev_core = existing.rstrip("\"'”’）】》」』")
    prev = prev_core[-1] if prev_core else existing[-1]
    nxt = new_text[0]
    if (
        prev.isascii()
        and nxt.isascii()
        and (prev.isalnum() or prev in ".,!?;:)]}\"'")
        and (nxt.isalnum() or nxt in "([{\"'")
    ):
        return f"{existing} {new_text}"
    return f"{existing}{new_text}"


def _split_sentence_like(text: str) -> list[str]:
    """Split Chinese and English transcript text at likely sentence endings."""
    closers = "\"'”’）】》」』"
    pieces: list[str] = []
    start = 0
    i = 0
    while i < len(text):
        ch = text[i]
        boundary = ch in "。！？!?；;"
        if ch == ".":
            prev = text[i - 1] if i > 0 else ""
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if not (prev.isdigit() and nxt.isdigit()):
                j = i + 1
                while j < len(text) and text[j] in closers:
                    j += 1
                boundary = j >= len(text) or text[j].isspace()
        if boundary:
            end = i + 1
            while end < len(text) and text[end] in closers:
                end += 1
            piece = text[start:end].strip()
            if piece:
                pieces.append(piece)
            start = end
            i = end
            continue
        i += 1

    tail = text[start:].strip()
    if tail:
        pieces.append(tail)
    return pieces


def _split_text_for_readable_turns(text: str, max_chars: int) -> list[str]:
    """Split text into sentence-ish pieces, with a comma/length fallback."""
    normalized = re.sub(r"\s+", " ", text.strip())
    normalized = re.sub(
        r"((?<!\d)[.!?][\"'”’）】》」』]*)(?=[A-Z])",
        r"\1 ",
        normalized,
    )
    if not normalized:
        return []

    pieces = _split_sentence_like(normalized) or [normalized]

    result: list[str] = []
    hard_limit = max(max_chars, 80)
    soft_limit = max(80, min(hard_limit, max_chars))
    for piece in pieces:
        if len(piece) <= hard_limit:
            result.append(piece)
            continue
        comma_parts = [part.strip() for part in re.split(r"(?<=[，,、：:])", piece) if part.strip()]
        current = ""
        for part in comma_parts or [piece]:
            if current and len(current) + len(part) > soft_limit:
                result.append(current)
                current = part
            else:
                current = current + part
        if current:
            while len(current) > hard_limit:
                result.append(current[:hard_limit])
                current = current[hard_limit:]
            if current:
                result.append(current)
    return result


def _sentence_count(text: str) -> int:
    count = len(re.findall(r"[。！？!?；;]|(?<!\d)\.(?=\s|$|[\"'”’）】》」』])", text))
    return max(1, count) if text.strip() else 0


def _ends_sentence(text: str) -> bool:
    return bool(_SENTENCE_END_RE.search(text.strip()))


def _segment_to_readable_events(
    seg: dict[str, Any],
    *,
    max_chars: int,
) -> list[dict[str, Any]]:
    speaker, body = _split_speaker_prefix(str(seg.get("text", "")))
    pieces = _split_text_for_readable_turns(body, max_chars=max_chars)
    if not pieces:
        return []

    start_ts, end_ts = _timestamp_bounds(str(seg.get("timestamp", "")))
    start_s = _timestamp_to_seconds(start_ts)
    end_s = _timestamp_to_seconds(end_ts)
    if start_s is None or end_s is None or end_s <= start_s or len(pieces) == 1:
        return [
            {
                "speaker": speaker,
                "start": start_ts,
                "end": end_ts,
                "start_s": start_s,
                "end_s": end_s,
                "text": pieces[0] if len(pieces) == 1 else "".join(pieces),
            }
        ]

    total_chars = max(1, sum(len(piece) for piece in pieces))
    cursor_chars = 0
    events: list[dict[str, Any]] = []
    for piece in pieces:
        piece_start_s = start_s + (end_s - start_s) * (cursor_chars / total_chars)
        cursor_chars += len(piece)
        piece_end_s = start_s + (end_s - start_s) * (cursor_chars / total_chars)
        events.append(
            {
                "speaker": speaker,
                "start": _seconds_to_srt_timestamp(piece_start_s),
                "end": _seconds_to_srt_timestamp(piece_end_s),
                "start_s": piece_start_s,
                "end_s": piece_end_s,
                "text": piece,
            }
        )
    return events


def merge_consecutive_speaker_segments(
    srt_content: str,
    *,
    max_chars: int = 180,
    max_duration: float = 30.0,
    max_sentences: int = 3,
    max_gap: float = 2.0,
) -> str:
    """Merge adjacent SRT cues by speaker, then split into readable turns.

    The LLM polishing step intentionally preserves cue count while it fixes
    words and punctuation. This deterministic pass converts those polished
    cues into dialogue turns: adjacent same-speaker cues are combined, but
    the result is cut at sentence boundaries when a turn gets too long.
    Empty speaker-only cues are ignored so they do not split a turn.
    """
    segments = _parse_srt(srt_content)
    if not segments:
        return srt_content

    events: list[dict[str, Any]] = []
    for seg in segments:
        events.extend(_segment_to_readable_events(seg, max_chars=max_chars))
    if not events:
        return srt_content

    readable: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush_current() -> None:
        nonlocal current
        if current is not None and str(current.get("text", "")).strip():
            readable.append(current)
        current = None

    def duration_with(event: dict[str, Any]) -> float:
        if current is None:
            return 0.0
        start_s = current.get("start_s")
        end_s = event.get("end_s")
        if start_s is None or end_s is None:
            return 0.0
        return float(end_s) - float(start_s)

    for event in events:
        speaker = event.get("speaker")
        if not speaker:
            flush_current()
            readable.append(event)
            continue
        if current is not None and (
            speaker != current.get("speaker")
            or (
                event.get("start_s") is not None
                and current.get("end_s") is not None
                and float(event["start_s"]) - float(current["end_s"]) > max_gap
            )
        ):
            flush_current()

        if current is not None:
            projected = _join_turn_text(str(current["text"]), str(event["text"]))
            current_can_end = _ends_sentence(str(current["text"]))
            projected_duration = duration_with(event)
            hard_split = len(projected) > max_chars or projected_duration > max_duration
            sentence_split = current_can_end and (
                int(current.get("sentences", 0)) >= max_sentences or hard_split
            )
            should_split = hard_split or sentence_split
            if should_split:
                flush_current()

        if current is None:
            current = {
                "speaker": speaker,
                "start": event["start"],
                "end": event["end"],
                "start_s": event.get("start_s"),
                "end_s": event.get("end_s"),
                "text": str(event["text"]).strip(),
                "sentences": _sentence_count(str(event["text"])),
            }
        else:
            current["end"] = event["end"]
            current["end_s"] = event.get("end_s")
            current["text"] = _join_turn_text(
                str(current["text"]),
                str(event["text"]),
            )
            current["sentences"] = int(current.get("sentences", 0)) + _sentence_count(
                str(event["text"])
            )

        current_duration = 0.0
        if current.get("start_s") is not None and current.get("end_s") is not None:
            current_duration = float(current["end_s"]) - float(current["start_s"])
        if _ends_sentence(str(current["text"])) and (
            int(current.get("sentences", 0)) >= max_sentences
            or len(str(current["text"])) >= max_chars
            or current_duration >= max_duration
        ):
            flush_current()

    flush_current()

    if not readable:
        return srt_content

    output_segments: list[dict[str, Any]] = []
    for index, item in enumerate(readable, 1):
        speaker = item.get("speaker")
        text = str(item["text"]).strip()
        if speaker:
            text = f"[{speaker}] {text}"
        output_segments.append(
            {
                "index": index,
                "timestamp": f"{item['start']} --> {item['end']}",
                "text": text,
            }
        )
    return _segments_to_srt(output_segments)


def _parse_polish_response(response: str, fallback_segments: list[dict]) -> list[dict]:
    """Parse an LLM polish response into segments.

    Tries, in order:
    1. JSON array of {index, timestamp, text} (the prompt-requested format),
       tolerating markdown fences and leading/trailing prose.
    2. Raw SRT blocks (legacy / when the model insists on SRT).
    3. Empty list — caller will align/fallback to input segments.
    """
    # Strip markdown code fences first
    text = response.strip()
    fence_match = re.match(
        r"^```(?:json|JSON)?\s*\n(.*?)\n```\s*$",
        text,
        flags=re.DOTALL,
    )
    if fence_match:
        text = fence_match.group(1).strip()

    # Try JSON: find the first '[' and the matching last ']' so we
    # tolerate leading/trailing junk like "好的，这是结果："
    try:
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            arr = json.loads(text[start : end + 1])
            if isinstance(arr, list):
                segs: list[dict] = []
                for item in arr:
                    if not isinstance(item, dict):
                        continue
                    idx = item.get("index")
                    ts = item.get("timestamp")
                    txt = item.get("text")
                    if idx is None or not ts or txt is None:
                        continue
                    segs.append(
                        {
                            "index": int(idx),
                            "timestamp": str(ts).strip(),
                            "text": str(txt).strip(),
                        }
                    )
                if segs:
                    return segs
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        log_event(logger, logging.DEBUG, "llm.polish.parse_json_failed", error=e)

    # Fall back to SRT block parse
    srt_segs = _parse_srt(text)
    if srt_segs:
        return srt_segs

    # Nothing usable
    return []


def _align_polished_to_input(polished: list[dict], original: list[dict]) -> list[dict]:
    """Align polished segments back to the original cue list.

    Guarantees one output per input cue, preserving every original
    index+timestamp. For each input cue, picks the polished segment with
    the matching timestamp if present, else matching index, else falls
    back to the original text. This way we never lose cues even if the
    LLM dropped or duplicated some.
    """
    by_ts = {seg.get("timestamp"): seg for seg in polished if seg.get("timestamp")}
    by_idx = {seg.get("index"): seg for seg in polished if seg.get("index") is not None}
    aligned: list[dict] = []
    for orig in original:
        match = by_ts.get(orig["timestamp"]) or by_idx.get(orig["index"])
        if match and match.get("text"):
            aligned.append(
                {
                    "index": orig["index"],
                    "timestamp": orig["timestamp"],
                    "text": match["text"],
                }
            )
        else:
            aligned.append(dict(orig))
    return aligned


def _enforce_polish_constraints(
    polished: list[dict],
    original: list[dict],
    context: dict[str, Any],
) -> list[dict]:
    """Restore read-only cue fields and canonical source spellings."""
    from app.services.analysis.source_context import canonicalize_text

    aligned = _align_polished_to_input(polished, original)
    constrained: list[dict] = []
    for output, source in zip(aligned, original, strict=True):
        source_speaker, _source_body = _split_speaker_prefix(str(source.get("text") or ""))
        _output_speaker, output_body = _split_speaker_prefix(str(output.get("text") or ""))
        body = canonicalize_text(output_body, context)
        text = f"[{source_speaker}] {body}" if source_speaker else body
        constrained.append(
            {
                "index": source["index"],
                "timestamp": source["timestamp"],
                "text": text.strip(),
            }
        )
    return constrained


def _polish_timeline_context(
    context: dict[str, Any],
    chunk_segments: list[dict],
) -> str:
    timeline = context.get("timeline") or []
    if not timeline or not chunk_segments:
        return ""
    start_ts, end_ts = _timestamp_bounds(str(chunk_segments[0].get("timestamp") or ""))
    chunk_start = _timestamp_to_seconds(start_ts)
    _last_start, last_end = _timestamp_bounds(str(chunk_segments[-1].get("timestamp") or ""))
    chunk_end = _timestamp_to_seconds(last_end)
    if chunk_start is None:
        return ""
    labels: list[str] = []
    for index, item in enumerate(timeline):
        try:
            item_start = float(item.get("start") or 0)
        except (AttributeError, TypeError, ValueError):
            continue
        next_start = (
            float(timeline[index + 1].get("start") or 1e18) if index + 1 < len(timeline) else 1e18
        )
        if (
            item_start <= (chunk_end if chunk_end is not None else chunk_start)
            and next_start > chunk_start
        ):
            labels.append(f"{item_start:g}s {item.get('title', '')}")
    return " / ".join(labels)


def srt_to_markdown(srt_content: str, title: str = "") -> str:
    """
    Convert polished SRT to a clean Markdown document.
    Preserves SRT cue boundaries as readable paragraphs.
    """
    segments = _parse_srt(srt_content)
    if not segments:
        return srt_content

    paragraphs: list[dict[str, str | None]] = []
    for seg in segments:
        text = seg["text"].strip()
        speaker, text = _split_speaker_prefix(text)
        if text:
            paragraphs.append({"speaker": speaker, "text": text})

    # Build markdown
    lines = []
    if title:
        lines.append(f"# {title}")
        lines.append("")

    # Check if there are multiple speakers
    speakers = set(p["speaker"] for p in paragraphs if p["speaker"])
    multi_speaker = len(speakers) > 1

    for para in paragraphs:
        if multi_speaker and para["speaker"]:
            # Show speaker label for multi-speaker content
            lines.append(f"**[{para['speaker']}]** {para['text']}")
        else:
            lines.append(para["text"])
        lines.append("")

    return "\n".join(lines)
