"""Infer evidenced names for anonymous speaker labels without changing the dialogue."""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any

from app.core.logging_setup import log_event
from app.core.model_router import resolve_polish_llm_binding
from app.core.settings import get_runtime_settings
from app.services.analysis.long_text import compact_metadata, prompt_budget, record_windows
from app.services.analysis.participants import same_person_name, source_participants
from app.services.analysis.prompts.speaker_identity import (
    LECTURE_IDENTITY_SYSTEM_PROMPT,
    PARTICIPANT_BINDING_SYSTEM_PROMPT,
    SPEAKER_IDENTITY_SYSTEM_PROMPT,
    get_lecture_identity_prompt,
    get_participant_binding_prompt,
    get_speaker_identity_prompt,
)
from app.services.analysis.transcript_outputs import (
    UNKNOWN_SPEAKER,
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
        str(context.get(key) or "") for key in ("title", "description")
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

    for source in ("description", "title"):
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
        and item["source_label"].strip().casefold() != UNKNOWN_SPEAKER
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
        else:
            from app.services.analysis.speaker_evidence import name_has_direct_evidence
            own_quotes = [by_index[index]["text"] for index in evidence if by_index[index]["speaker"] == label]
            if not name_has_direct_evidence(name, own_quotes, context):
                continue
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


_QUESTION_RE = re.compile(r"(?:[?？]|吗|呢|么)\s*[”\"'’]?\s*$")
_SELF_INTRODUCTION_RE = r"(?:我是|我叫|本人是|my name is|i am|i['’]m|this is)\s*"


def _name_tokens(name: str) -> list[str]:
    """How a person is called in speech: the full name, and for Latin names each part ("Eric")."""
    tokens = [name]
    if re.fullmatch(r"[A-Za-z .'’-]+", name):
        tokens += [part for part in re.split(r"[\s.]+", name) if len(part) >= 3]
    return list(dict.fromkeys(tokens))


def _name_pattern(token: str) -> str:
    escaped = re.escape(token)
    return rf"(?<![A-Za-z]){escaped}(?![A-Za-z])" if re.search(r"[A-Za-z]", token) else escaped


def _addresses(text: str, name: str) -> bool:
    """The line speaks to the person: "What's your opinion, Eric?", "Thank you, Eric", "王老师，您怎么看"."""
    for token in _name_tokens(name):
        pattern = _name_pattern(token)
        if re.search(rf"{_SELF_INTRODUCTION_RE}{pattern}", text, re.I):
            continue
        if re.search(
            rf"(?:^|[,，、]\s*){pattern}(?:老师|先生|女士)?\s*[?？!！,，。.]"
            rf"|(?:thank you|thanks|hi|hello|hey|welcome|谢谢|感谢|欢迎)[,，]?\s*{pattern}"
            rf"|{pattern}(?:老师|先生|女士)?[,，]\s*(?:你|您|what|how|why|do you|can you|could you)",
            text, re.I,
        ):
            return True
    return False


def _mentions(text: str, name: str) -> bool:
    return any(re.search(_name_pattern(token), text, re.I) for token in _name_tokens(name)) \
        and not re.search(rf"{_SELF_INTRODUCTION_RE}(?:{'|'.join(_name_pattern(t) for t in _name_tokens(name))})", text, re.I)


def _voice_profiles(
    cues: list[dict[str, Any]], labels: list[str], activity: list[dict[str, Any]], names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Speaking share, questions, names said and a few lines per voice: enough to tell host from guest."""
    shares = {row["speaker"]: row for row in activity}
    voices = []
    for label in labels:
        spoken = [cue for cue in cues if cue["speaker"] == label]
        if not spoken:
            continue
        questions = [cue for cue in spoken if _QUESTION_RE.search(cue["text"])]
        naming = [cue for cue in spoken if any(_mentions(cue["text"], name) for name in names or [])]
        longest = sorted(spoken, key=lambda cue: -len(cue["text"]))[:3]
        picked = {cue["index"]: cue for cue in (*spoken[:2], *naming[:2], *questions[:2], *longest)}
        voices.append({
            "id": label,
            "share": round(float(shares.get(label, {}).get("share") or 0), 3),
            "cues": len(spoken),
            "questions": len(questions),
            # Whom this voice calls by name, or talks about; nobody calls themselves this way.
            "says_names": {name: sum(_mentions(cue["text"], name) for cue in spoken) for name in names or []
                           if any(_mentions(cue["text"], name) for cue in spoken)},
            "addresses": sorted({name for name in names or [] for cue in spoken if _addresses(cue["text"], name)}),
            "samples": [{"index": index, "text": cue["text"][:200]} for index, cue in sorted(picked.items())][:8],
        })
    return voices


def _parse_bindings(
    response: str, voices: list[dict[str, Any]], participants: list[dict[str, str]], cues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", response, flags=re.IGNORECASE)
    response = blocks[-1] if blocks else response
    start, end = response.find("{"), response.rfind("}")
    if start < 0 or end < start:
        raise ValueError("参与者对应需要返回 JSON 对象")
    data = json.loads(response[start : end + 1])
    if not isinstance(data, dict) or not isinstance(data.get("mappings", []), list):
        raise ValueError("mappings 必须是数组")
    by_id = {voice["id"]: voice for voice in voices}
    spoken_by = {cue["index"]: cue["speaker"] for cue in cues}

    def participant(name):
        return next((row for row in participants if isinstance(name, str) and same_person_name(name, row["name"])), None)

    mappings: list[dict[str, Any]] = []
    for item in data.get("mappings") or []:
        # One bad row costs only that row; the rest of the answer still counts.
        if not isinstance(item, dict):
            continue
        label, person = item.get("source_label"), participant(item.get("current_name"))
        voice = by_id.get(label)
        # Whether two voices are one person is a question for the voiceprints, not the text.
        if (voice is None or person is None or any(row["source_label"] == label for row in mappings)
                or any(same_person_name(row["current_name"], person["name"]) for row in mappings)):
            continue
        reason = str(item.get("reason") or "").strip()[:240]
        # A few stray words are not enough to put a name on a voice, and nobody calls themselves by name.
        if (voice["share"] < 0.03 and voice["cues"] < 3) or person["name"] in voice.get("addresses", []):
            continue
        evidence = [index for index in item.get("evidence_indexes") or [] if type(index) is int and spoken_by.get(index) == label]
        mappings.append({
            "source_label": label, "current_name": person["name"], "evidence_indexes": evidence,
            "reason": f"{person['basis']}；{reason}" if reason else person["basis"], "basis": "source_participants",
        })
    return mappings


async def _bind_participants(
    cues: list[dict[str, Any]], anonymous: list[str], named: list[str], source: dict[str, Any],
) -> list[dict[str, Any]]:
    """Put the names the source gives (title collaboration, uploader, listed guests) on the voices."""
    from app.services.analysis.llm import get_llm_service

    participants = [row for row in source_participants(source) if not any(same_person_name(row["name"], name) for name in named)]
    voices = _voice_profiles(cues, anonymous, source.get("speaker_activity") or [], [row["name"] for row in participants])
    if not participants or not voices:
        return []
    labels = [voice["id"] for voice in voices]
    item = {
        "type": "object", "additionalProperties": False, "required": ["source_label", "current_name", "reason"],
        "properties": {
            "source_label": {"type": "string", "enum": labels},
            "current_name": {"type": "string", "enum": [row["name"] for row in participants]},
            "evidence_indexes": {"type": "array", "maxItems": 8, "items": {"type": "integer"}},
            "reason": {"type": "string", "maxLength": 200},
        },
    }
    schema = {"type": "object", "required": ["mappings"], "additionalProperties": False, "properties": {
        "mappings": {"type": "array", "maxItems": len(labels), "items": item},
    }}
    llm_binding = resolve_polish_llm_binding(get_runtime_settings())

    async def call(value):
        return await get_llm_service()._call(
            value, provider_override=llm_binding.provider, stage="polish",
            system_prompt=PARTICIPANT_BINDING_SYSTEM_PROMPT, max_retries=0, response_schema=schema,
        )

    prompt = get_participant_binding_prompt(participants, voices, source)
    try:
        return _parse_bindings(await call(prompt), voices, participants, cues)
    except ValueError as exc:
        repair = f"{prompt}\n上次输出无法解析，只返回符合格式的 JSON。错误：{str(exc)[:120]}"
        return _parse_bindings(await call(repair), voices, participants, cues)


async def infer_speaker_names(
    srt: str, context: dict[str, Any] | None = None, *, checkpoint_path: Path | None = None,
) -> dict[str, Any]:
    """Resolve lecture identities compactly; use complete dialogue for other content."""
    from app.services.analysis.llm import get_llm_service

    cues = []
    labels = []
    for segment in _parse_srt(srt.replace("\r\n", "\n").replace("\r", "\n")):
        speaker, text = _split_speaker_prefix(segment["text"])
        cues.append({"index": segment["index"], "speaker": speaker, "text": text})
        if speaker and speaker.casefold() != UNKNOWN_SPEAKER and speaker not in labels:
            labels.append(speaker)
    anonymous = [label for label in labels if is_placeholder_speaker(label)]
    if not anonymous:
        return {"status": "skipped", "mappings": []}
    named = [label for label in labels if label not in anonymous]
    source = {
        key: (context or {}).get(key, [] if key == "speaker_candidates" else "")
        for key in ("title", "description", "uploader", "speaker_candidates", "participant_evidence", "entities")
    }
    source["analysis"] = (context or {}).get("analysis") or {}
    source["speaker_activity"] = (
        (context or {}).get("speaker_activity") or build_speaker_activity(srt)
    )
    all_cues, all_anonymous = cues, anonymous
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
    window_error: Exception | None = None
    failed_windows: list[dict[str, Any]] = []
    try:
        binding = resolve_polish_llm_binding(get_runtime_settings())
        provider = binding.provider
        system = LECTURE_IDENTITY_SYSTEM_PROMPT if main is not None else SPEAKER_IDENTITY_SYSTEM_PROMPT
        # Dialogue naming needs source identities, not the full analysis vocabulary.
        prompt_source = {**source, "analysis": {"content_type": source["analysis"].get("content_type", "")}}
        if main is None:
            prompt_source.pop("summary", None)

        prompt_source = compact_metadata(prompt_source, max(512, prompt_budget(provider, "polish", system) // 3))

        def prompt(targets, visible):
            return (get_lecture_identity_prompt(visible, main["speaker"], named, prompt_source)
                    if main is not None else get_speaker_identity_prompt(visible, anonymous, named, prompt_source))

        async def call(value):
            return await get_llm_service()._call(
                value, provider_override=provider, stage="polish", system_prompt=system,
                max_retries=0,
                response_schema={
                    "type": "object", "required": ["mappings"], "additionalProperties": False,
                    "properties": {"mappings": {
                        "type": "array", "maxItems": len(anonymous),
                        "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["source_label", "current_name", "evidence_indexes", "reason"],
                            "properties": {
                                "source_label": {"type": "string", "enum": anonymous},
                                "current_name": {"type": "string", "maxLength": 80},
                                "evidence_indexes": {"type": "array", "maxItems": 12, "items": {"type": "integer"}},
                                "reason": {"type": "string", "maxLength": 240},
                            },
                        },
                    }},
                },
            )

        from app.services.analysis.speaker_checkpoint import load_checkpoint, save_checkpoint
        inputs = {"srt": srt, "context": context, "version": 2, "model": binding.model,
                  "provider": provider, "parameters": {k: v for k, v in binding.request_kwargs.items() if k != "api_key"},
                  "system": system}
        saved = load_checkpoint(checkpoint_path, inputs)
        from app.services.analysis.speaker_checkpoint import begin_checkpoint_attempt
        begin_checkpoint_attempt(checkpoint_path, inputs, "speaker_identity")
        completed = set(saved["completed_indexes"]) if saved else set()
        candidates = saved["report"].get("candidates", []) if saved else []
        failed_windows = []
        successful_windows = 1 if completed else 0
        failed_ids = set()
        state = {}
        async for targets, visible, response in record_windows(
            cues, prompt, call, budget=prompt_budget(provider, "polish", system),
            chunk_size=80, keep_errors=True,
            skip_indexes=completed, window_state=state, max_calls=max(32, len(cues) * 4),
            validate=lambda response, targets, visible: _parse_mappings(
                response, visible, anonymous, named, prompt_source, main,
            ),
        ):
            if isinstance(response, Exception):
                failed_ids.update(row["index"] for row in targets)
                completed.difference_update(failed_ids)
                failed_windows.append({"indexes": [row["index"] for row in targets], "error": str(response)})
                save_checkpoint(checkpoint_path, inputs, {"candidates": candidates, "failed_windows": failed_windows}, completed)
                if not isinstance(response, ValueError):
                    raise response
                continue
            successful_windows += 1
            candidates.extend(response)
            completed.update(set(state.get("completed_indexes", [])) - failed_ids)
            save_checkpoint(checkpoint_path, inputs, {"candidates": candidates, "failed_windows": failed_windows}, completed)
        if failed_windows and not successful_windows:
            raise ValueError(failed_windows[0]["error"])
        # Windows vote only with validated source evidence. Conflicts stay anonymous.
        by_label = {}
        for item in candidates:
            if any(index in failed_ids for index in item["evidence_indexes"]):
                continue
            by_label.setdefault(item["source_label"], {}).setdefault(item["current_name"].casefold(), []).append(item)
        mappings = []
        for label, names in by_label.items():
            if len(names) != 1:
                continue
            rows = next(iter(names.values()))
            name = rows[0]["current_name"].casefold()
            if any(name in other for key, other in by_label.items() if key != label):
                continue
            mappings.append({**rows[0], "evidence_indexes": sorted({i for row in rows for i in row["evidence_indexes"]})})
        conflicts = [label for label in by_label if label not in {m["source_label"] for m in mappings}]
        if conflicts:
            method["conflicting_labels"] = conflicts
        if failed_windows:
            method["failed_windows"] = failed_windows
    except Exception as exc:
        log_event(logger, logging.WARNING, "llm.speaker_identity.failed", error=exc)
        window_error, mappings = exc, []
    # Voices the dialogue did not name take the names the source gives: a 【主持×嘉宾】 title,
    # the uploader among them, listed guests. The model matches them by who asks and who answers.
    remaining = [label for label in all_anonymous if label not in {item["source_label"] for item in mappings}]
    if remaining:
        try:
            mappings.extend(await _bind_participants(
                all_cues, remaining, named + [item["current_name"] for item in mappings], source,
            ))
        except Exception as exc:
            log_event(logger, logging.WARNING, "llm.speaker_identity.binding_failed", error=exc)
            method["participant_binding_error"] = str(exc)
    participants = source_participants(source)
    if participants:
        # Offered as names when the user renames a voice.
        method["participants"] = [{"name": row["name"], "role": row["role"]} for row in participants]
    if window_error is not None:
        return {"status": "partial" if mappings else "failed", "mappings": mappings, "error": str(window_error), **method}
    log_event(logger, logging.INFO, "llm.speaker_identity.completed", mappings=len(mappings))
    return {"status": "partial" if failed_windows else "completed", "mappings": mappings, **method}
