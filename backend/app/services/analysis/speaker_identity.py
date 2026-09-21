"""Infer evidenced names for anonymous speaker labels without changing the dialogue."""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any

from app.core.logging_setup import log_event
from app.core.model_router import resolve_polish_llm_binding
from app.core.settings import get_runtime_settings
from app.services.analysis.prompts.speaker_identity import (
    LECTURE_IDENTITY_SYSTEM_PROMPT,
    SPEAKER_IDENTITY_SYSTEM_PROMPT,
    get_lecture_identity_prompt,
    get_speaker_identity_prompt,
)
from app.services.analysis.transcript_outputs import (
    _parse_srt,
    _split_speaker_prefix,
    _timestamp_bounds,
    _timestamp_to_seconds,
)

logger = logging.getLogger(__name__)

_ANONYMOUS_LABEL_RE = re.compile(
    r"(?:speaker[_ -]?\d+|unknown(?:[-_][\w-]+)?|"
    r"(?:说话人|说话者)[ _-]?\d+|未知(?:说话人|说话者)?)",
    re.IGNORECASE,
)
_SRT_LABEL_RE = re.compile(
    r"(^[ \t]*\d+[ \t]*\r?\n[^\r\n]*-->[^\r\n]*\r?\n[ \t]*\[)([^\]\r\n]+)(\])",
    re.MULTILINE,
)
_LECTURE_RE = re.compile(
    r"讲座|演讲|讲课|课堂|课程|主讲|技术分享|分享报告|报告会|"
    r"lecture|keynote|presentation|seminar|\btalk\b", re.I,
)
_DISCUSSION_RE = re.compile(r"访谈|采访|对谈|圆桌|辩论|interview|panel|debate|talk\s*show", re.I)


def build_speaker_activity(
    srt: str,
    turns: list[dict[str, Any]] | None = None,
    speaker_map: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Measure speaking time from acoustic turns, falling back to timed SRT cues."""
    rows = speaker_map.get("mappings", []) if isinstance(speaker_map, dict) else speaker_map or []
    names = {
        row["source_label"]: row.get("current_name") or row["source_label"]
        for row in rows
        if isinstance(row, dict) and row.get("source_label")
    }
    intervals: dict[str, list[tuple[float, float]]] = {}
    cue_counts: dict[str, int] = {}
    cues = _parse_srt(srt.replace("\r\n", "\n").replace("\r", "\n"))
    for cue in cues:
        speaker, _ = _split_speaker_prefix(cue["text"])
        if speaker:
            speaker = names.get(speaker, speaker)
            cue_counts[speaker] = cue_counts.get(speaker, 0) + 1

    def add_interval(speaker, start, end):
        if not isinstance(speaker, str) or not speaker:
            return
        try:
            start, end = float(start), float(end)
        except (ValueError, TypeError):
            return
        if not math.isfinite(start) or not math.isfinite(end) or end <= max(start, 0):
            return
        speaker = names.get(speaker, speaker)
        intervals.setdefault(speaker, []).append((max(start, 0), end))

    for turn in turns or []:
        add_interval(turn.get("speaker"), turn.get("start"), turn.get("end"))
    if not intervals:
        for cue in cues:
            speaker, _ = _split_speaker_prefix(cue["text"])
            start, end = _timestamp_bounds(cue["timestamp"])
            add_interval(speaker, _timestamp_to_seconds(start), _timestamp_to_seconds(end))

    durations = {}
    for speaker, ranges in intervals.items():
        ranges.sort()
        start, end = ranges[0]
        duration = 0.0
        for next_start, next_end in ranges[1:]:
            if next_start <= end:
                end = max(end, next_end)
            else:
                duration += end - start
                start, end = next_start, next_end
        durations[speaker] = duration + end - start
    total = sum(durations.values())
    return [
        {
            "speaker": speaker,
            "duration_seconds": round(duration, 3),
            "share": round(duration / total, 6),
            "cue_count": cue_counts.get(speaker, 0),
        }
        for speaker, duration in sorted(durations.items(), key=lambda item: (-item[1], item[0]))
    ]


def _dominant_lecture_speaker(context: dict[str, Any]) -> dict[str, Any] | None:
    content_type = str((context.get("analysis") or {}).get("content_type") or "")
    if _DISCUSSION_RE.search(content_type):
        return None
    source_classification = " ".join(
        str(context.get(key) or "") for key in ("title", "description", "summary")
    )
    if not _LECTURE_RE.search(content_type) and (
        _DISCUSSION_RE.search(source_classification)
        or not _LECTURE_RE.search(source_classification)
    ):
        return None
    activity = sorted(context.get("speaker_activity") or [], key=lambda item: -item["share"])
    if not activity:
        return None
    main = activity[0]
    runner_share = activity[1]["share"] if len(activity) > 1 else 0
    return main if main["share"] >= 0.7 and runner_share <= 0.2 else None


def _lecture_excerpts(cues: list[dict[str, Any]], speaker: str) -> list[dict[str, Any]]:
    spoken = [cue for cue in cues if cue["speaker"] == speaker]
    positions = {0, 1, 2, len(spoken) // 2, len(spoken) - 2, len(spoken) - 1}
    return [
        {**spoken[index], "text": spoken[index]["text"][:600]}
        for index in sorted(positions)
        if 0 <= index < len(spoken)
    ]


def _lecture_identity_evidence(name: str, cues: list[dict], context: dict) -> str | None:
    """Require the cited person to be introduced as the lecturer or to introduce themselves."""
    escaped = re.escape(name)
    role = re.compile(
        rf"(?:主讲(?:人|者|嘉宾)?|演讲(?:者|人|嘉宾)|分享嘉宾|讲者|报告人|讲师|"
        rf"speaker|presenter|lecturer)\s*(?:是|为|[:：]|is)?\s*{escaped}", re.I,
    )
    narration = re.compile(
        rf"{escaped}(?:博士|教授|老师|先生|女士)?\s*"
        rf"(?:主讲|演讲|讲解|分享|梳理|介绍|阐述|讲述|展示|分析|探讨|解读|"
        rf"presents?\b|explains?\b|discusses?\b|delivers?\b)", re.I,
    )
    titled_lecture = re.compile(rf"{escaped}(?:博士|教授|老师)?的(?:技术)?(?:分享|讲座|演讲)")

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)

    for source in ("description", "summary", "title"):
        for text in strings(context.get(source)):
            for sentence in re.split(r"[。！？\n]", text):
                if role.search(sentence):
                    return f"{source}：{sentence[:240]}"
                match = narration.search(sentence) or (
                    titled_lecture.search(sentence) if source == "title" else None
                )
                if match and not re.search(
                    r"导师|作者|引用|提到|提及|mentor|author|cited",
                    sentence[max(0, match.start() - 12):match.start()], re.I,
                ):
                    return f"{source}：{sentence[:240]}"
    for candidate in context.get("speaker_candidates") or []:
        if candidate.get("name") == name and str(candidate.get("role", "")).lower() in {
            "speaker", "lecturer", "presenter", "主讲", "主讲人", "讲师", "演讲者",
        }:
            return f"speaker_candidates：{name} 为{candidate['role']}"
    introduction = re.compile(
        rf"(?:我是|我叫|我的名字是|my name is|I am|I'm)\s*{escaped}", re.I,
    )
    for cue in cues:
        if introduction.search(cue["text"]):
            return f"字幕 {cue['index']}：{cue['text'][:240]}"
    return None


def is_placeholder_speaker(label: str) -> bool:
    """Whether a speaker label still represents an unnamed person."""
    return bool(_ANONYMOUS_LABEL_RE.fullmatch(label.strip()))


def _safe_name(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not is_placeholder_speaker(value)
        and not any(ord(char) < 32 or char in "[]<>\u2028\u2029" for char in value)
    )


def apply_speaker_names(srt: str, mappings: list[dict]) -> str:
    """Replace only cue-leading anonymous labels, preserving the original SRT bytes."""
    names = {
        item["source_label"]: item["current_name"].strip()
        for item in mappings
        if isinstance(item, dict)
        and isinstance(item.get("source_label"), str)
        and is_placeholder_speaker(item["source_label"])
        and _safe_name(item.get("current_name"))
    }
    if not names:
        return srt

    def replace(match: re.Match[str]) -> str:
        label = match.group(2).strip()
        if label not in names:
            return match.group(0)
        return f"{match.group(1)}{names[label]}{match.group(3)}"

    return _SRT_LABEL_RE.sub(replace, srt)


def _parse_mappings(
    response: str,
    cues: list[dict[str, Any]],
    anonymous: list[str],
    named: list[str],
    context: dict[str, Any],
    lecture_activity: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", response, flags=re.IGNORECASE)
    response = blocks[-1] if blocks else response
    start, end = response.find("{"), response.rfind("}")
    if start < 0 or end < start:
        raise ValueError("说话人姓名推断需要返回 JSON 对象")
    data = json.loads(response[start : end + 1])
    if not isinstance(data, dict) or not isinstance(data.get("mappings"), list):
        raise ValueError("mappings 必须是数组")
    by_index = {cue["index"]: cue for cue in cues}
    source_text = json.dumps(context, ensure_ascii=False)
    used_labels: set[str] = set()
    used_names = {name.casefold() for name in named}
    mappings = []
    for item in data["mappings"]:
        if not isinstance(item, dict):
            raise ValueError("姓名映射必须是对象")
        label, name = item.get("source_label"), item.get("current_name")
        evidence, reason = item.get("evidence_indexes"), item.get("reason")
        if (
            not isinstance(label, str)
            or label not in anonymous
            or label in used_labels
            or not _safe_name(name)
            or not isinstance(evidence, list)
            or (not evidence and lecture_activity is None)
            or any(type(index) is not int or index not in by_index for index in evidence)
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise ValueError("匿名标签、姓名或字幕证据无效")
        name = name.strip()
        if name.casefold() in used_names:
            raise ValueError("姓名与其他说话人冲突")
        if evidence and not any(by_index[index]["speaker"] == label for index in evidence):
            raise ValueError("证据需要包含该说话人的发言")
        if name not in source_text and not any(
            name in by_index[index]["text"] for index in evidence
        ):
            raise ValueError("姓名没有字幕或来源信息依据")
        if lecture_activity is not None:
            identity_evidence = _lecture_identity_evidence(name, cues, context)
            if not identity_evidence:
                raise ValueError("姓名缺少明确的主讲身份依据")
            reason = (
                f"{reason.strip()}；主讲身份依据：{identity_evidence}；"
                f"{label} 发言 {lecture_activity['duration_seconds']:.1f} 秒，"
                f"占有效说话时长 {lecture_activity['share']:.1%}"
            )
        used_labels.add(label)
        used_names.add(name.casefold())
        mappings.append(
            {
                "source_label": label,
                "current_name": name,
                "evidence_indexes": evidence,
                "reason": reason.strip(),
            }
        )
    return mappings


async def infer_speaker_names(
    srt: str, context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Resolve lecture identities compactly; use complete dialogue for other content."""
    from app.services.analysis.llm import get_llm_service

    cues = []
    labels = []
    for segment in _parse_srt(srt.replace("\r\n", "\n").replace("\r", "\n")):
        speaker, text = _split_speaker_prefix(segment["text"])
        cues.append({"index": segment["index"], "speaker": speaker, "text": text})
        if speaker and speaker not in labels:
            labels.append(speaker)
    anonymous = [label for label in labels if is_placeholder_speaker(label)]
    if not anonymous:
        return {"status": "skipped", "mappings": []}
    named = [label for label in labels if label not in anonymous]
    source = {
        key: (context or {}).get(key, [] if key == "speaker_candidates" else "")
        for key in ("title", "description", "speaker_candidates")
    }
    source["analysis"] = (context or {}).get("analysis") or {}
    source["summary"] = (context or {}).get("summary") or {}
    source["speaker_activity"] = (
        (context or {}).get("speaker_activity") or build_speaker_activity(srt)
    )
    main = _dominant_lecture_speaker(source)
    method = {
        "method": "dominant_lecture", "speaker_activity": source["speaker_activity"],
    } if main is not None else {}
    if main is not None and main["speaker"] in named:
        return {"status": "skipped", "mappings": [], **method}
    if main is not None and main["speaker"] not in anonymous:
        main, method = None, {}
    if main is not None:
        anonymous = [main["speaker"]]
        cues = _lecture_excerpts(cues, main["speaker"])
    log_event(
        logger,
        logging.INFO,
        "llm.speaker_identity.started",
        speakers=len(anonymous),
        cues=len(cues),
        method=method.get("method", "full_transcript"),
    )
    try:
        provider = resolve_polish_llm_binding(get_runtime_settings()).provider
        response = await get_llm_service()._call(
            get_lecture_identity_prompt(cues, main["speaker"], named, source)
            if main is not None else get_speaker_identity_prompt(cues, anonymous, named, source),
            provider_override=provider,
            stage="polish",
            system_prompt=LECTURE_IDENTITY_SYSTEM_PROMPT
            if main is not None else SPEAKER_IDENTITY_SYSTEM_PROMPT,
        )
        mappings = _parse_mappings(response, cues, anonymous, named, source, main)
    except Exception as exc:
        log_event(logger, logging.WARNING, "llm.speaker_identity.failed", error=exc)
        return {"status": "failed", "mappings": [], "error": str(exc), **method}
    log_event(logger, logging.INFO, "llm.speaker_identity.completed", mappings=len(mappings))
    return {"status": "completed", "mappings": mappings, **method}
