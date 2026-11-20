"""Inspect stored speaker evidence and assign labels in the current transcript."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.artifacts import _write_lock, get_artifact_store
from app.core.atomic_file import atomic_write_text
from app.core.database import get_task_store
from app.core.paths import managed_child, task_output_paths
from app.core.settings import get_runtime_settings
from app.core.workspace_lifecycle import uses_workspace
from app.models import Task
from app.services.analysis.speaker_quality import (
    finalize_speaker_quality,
    quality_report,
    speaker_labels,
)
from app.services.analysis.speaker_repair import repair_directory
from app.services.analysis.speaker_resolution import cue_acoustic_candidates, cue_voice_similarity
from app.services.analysis.transcript_outputs import (
    UNKNOWN_SPEAKER,
    _parse_srt,
    _segments_to_srt,
    _split_speaker_prefix,
    _timestamp_bounds,
    _timestamp_to_seconds,
    srt_to_markdown,
)
from app.services.voiceprint import get_voiceprint_store

TRANSCRIPT_FILES = ("transcript_polished.srt", "transcript.srt")
PREVIEW_FILE = "speaker_repair.preview.json"
_ASSIGNMENT_JOURNAL = ".speaker_assignment.pending.tmp"
_ASSIGNMENT_FILES = {*TRANSCRIPT_FILES, "transcript_polished.md", "metadata.json", PREVIEW_FILE}
_STALE_REASON = "人物归属已更新，已有摘要与导图待更新"
_ANONYMOUS = re.compile(r"^SPEAKER_\d+$|^unknown[-_]", re.I)
_IDENTITY_CUE = re.compile(
    r"我(?:的名字是|叫|是)|欢迎(?:来到|嘉宾)|有请|请问|(?:老师|先生|女士)[，、：]"
    r"|\b(?:my name is|i am|i['’]m|this is|please welcome|welcome,|(?:mr|ms|dr)\.)\b",
    re.I,
)
_CUE_GAP = re.compile(r"((?:\r\n|\r(?!\n)|\n)(?:[ \t]*(?:\r\n|\r(?!\n)|\n))+)")
_CUE_HEADER = re.compile(r"\A\ufeff?[ \t]*(\d+)[ \t]*(?:\r\n|\r|\n)[^\r\n]+(?:\r\n|\r|\n)")
_PREFIX = re.compile(r"\A\s*\[([^\]\r\n]+)\]")


class SpeakerAssignmentConflict(ValueError):
    pass


def _normalize_srt(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n")


def _raw_has_speaker_labels(extra: dict) -> bool:
    return extra.get("subtitle_source") == "asr" or extra.get("raw_speaker_label_contract") == 1


def _is_unlabeled_platform_track(source: str, extra: dict) -> bool:
    return (
        source == TRANSCRIPT_FILES[1]
        and not _raw_has_speaker_labels(extra)
        and (
            extra.get("subtitle_source") == "platform"
            or extra.get("raw_speaker_label_contract") == 0
        )
    )


def transcript_cues(content: str) -> list[dict]:
    normalized = re.sub(r"(?m)^[ \t]+$", "", _normalize_srt(content).removeprefix("\ufeff"))
    return _parse_srt(normalized)


def cue_interval(timestamp: str) -> tuple[float, float] | None:
    if "-->" not in timestamp:
        return None
    start, end = (_timestamp_to_seconds(value) for value in _timestamp_bounds(timestamp))
    if start is not None and end is not None and 0 <= start < end and math.isfinite(end):
        return start, end
    return None


def validate_speaker_name(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(
            char in "[]<>" or unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
            for char in value
        )
    ):
        raise ValueError("人物名称为空或含有无效字符")
    return value.strip()


def _read_text(task_id: UUID, directory: Path, filename: str) -> str | None:
    try:
        return get_artifact_store().read(task_id, directory, filename)["content"]
    except FileNotFoundError:
        return None


def read_task_json(task_id: UUID, directory: Path, filename: str) -> dict:
    content = _read_text(task_id, directory, filename)
    value = json.loads(content.removeprefix("\ufeff")) if content is not None else {}
    return value if isinstance(value, dict) else {}


def read_task_transcript(task_id: UUID, directory: Path) -> tuple[str, str]:
    for filename in TRANSCRIPT_FILES:
        if (path := managed_child(directory / filename, directory)).is_file():
            with path.open(encoding="utf-8", newline="") as source:
                return filename, source.read()
        content = _read_text(task_id, directory, filename)
        if content is not None:
            return filename, content
    raise LookupError("任务缺少字幕")


def _task_metadata(task: Task, directory: Path) -> dict:
    return (
        read_task_json(task.id, directory, "metadata.json")
        or (task.result or {}).get("metadata")
        or {}
    )


def _quote(cue: dict) -> dict:
    return {key: cue[key] for key in ("index", "timestamp", "text")}


def _identity_quotes(turns: list[dict], evidence: list[dict]) -> list[dict]:
    by_index = {turn["index"]: turn for turn in turns}
    quotes = []
    for row in evidence:
        turn, quote = by_index.get(row.get("index")), row.get("quote")
        if (
            turn
            and isinstance(quote, str)
            and quote
            and quote in turn["text"]
            and (not row.get("timestamp") or row["timestamp"] == turn["timestamp"])
        ):
            item = {**_quote(turn), "text": quote}
            if item not in quotes:
                quotes.append(item)
        if len(quotes) == 3:
            return quotes
    used = {row["index"] for row in quotes}
    for turn in turns:
        if turn["index"] not in used and _IDENTITY_CUE.search(turn["text"]):
            quotes.append(_quote(turn))
        if len(quotes) == 3:
            break
    return quotes


def _duration(cue: dict) -> float:
    interval = cue_interval(cue["timestamp"])
    return interval[1] - interval[0] if interval else 0.0


def _profile_rows(
    grouped: dict[str, list[dict]],
    total_duration: float,
    speaker_map: dict,
    links: list[dict],
    extra: dict,
    identity: dict,
    evidence: list[dict],
) -> list[dict]:
    suggestions = [*(identity.get("suggestions") or []), *(extra.get("speaker_suggestions") or [])]
    mappings = speaker_map.get("mappings") or []
    rows = []
    for name, turns in grouped.items():
        matching = [
            row for row in mappings if (row.get("current_name") or row.get("source_label")) == name
        ]
        mapping = matching[0] if matching else {}
        labels = {name, *(row.get("source_label") for row in matching)}
        voiceprint = next(
            (
                row
                for row in links
                if row.get("speaker_label") in labels or row.get("person_name") == name
            ),
            {},
        )
        duration = sum(map(_duration, turns))
        sample_turns = [
            turns[0],
            *sorted(
                (turn for turn in turns[1:] if any(char.isalnum() for char in turn["text"])),
                key=lambda turn: len(turn["text"]),
                reverse=True,
            )[:3],
        ]
        voices = []
        for suggestion in suggestions:
            if suggestion.get("source_label") in labels and suggestion not in voices:
                voices.append(suggestion)
        rows.append(
            {
                "name": name,
                "is_anonymous": bool(_ANONYMOUS.search(name)),
                "cue_count": len(turns),
                "duration_seconds": round(duration, 3),
                "share_pct": round(100 * duration / total_duration, 2) if total_duration else 0.0,
                "first_timestamp": _timestamp_bounds(turns[0]["timestamp"])[0],
                "last_timestamp": _timestamp_bounds(turns[-1]["timestamp"])[1],
                "source_label": mapping.get("source_label")
                or voiceprint.get("speaker_label")
                or name,
                "status": mapping.get("status") or ("voiceprint_linked" if voiceprint else None),
                "provenance": (extra.get("speaker_name_sources") or {}).get(name)
                or mapping.get("provenance")
                or mapping.get("reason")
                or voiceprint.get("person_name"),
                "identity_quotes": _identity_quotes(turns, evidence),
                "sample_quotes": [_quote(turn) for turn in sample_turns],
                "voiceprint_suggestions": voices,
            }
        )
    return rows


def _pending_cues(directory: Path, cues: list[dict]) -> list[dict]:
    pending = [position for position, cue in enumerate(cues) if not cue["speaker"]]
    if not pending:
        return []
    timed_srt = _segments_to_srt(
        [cues[position] for position in pending if cue_interval(cues[position]["timestamp"])]
    )
    acoustic = cue_acoustic_candidates(directory, timed_srt)
    voice = cue_voice_similarity(directory, timed_srt)
    rows = []
    for position in pending:
        cue = cues[position]
        overlap, similarity = acoustic.get(cue["index"], {}), voice.get(cue["index"], {})
        names = sorted(
            overlap.keys() | similarity.keys(),
            key=lambda name: (
                -similarity.get(name, -2.0),
                -overlap.get(name, 0.0),
                name,
            ),
        )
        rows.append(
            {
                **_quote(cue),
                "prev_cue": cues[position - 1] if position else None,
                "next_cue": cues[position + 1] if position + 1 < len(cues) else None,
                "candidates": [
                    {
                        "speaker": name,
                        "acoustic_seconds": overlap.get(name),
                        "voice_similarity": similarity.get(name),
                    }
                    for name in names
                ],
            }
        )
    return rows


@uses_workspace
def build_task_speaker_profile(task_id: UUID) -> dict[str, Any]:
    task = get_task_store().get(task_id)
    if task is None:
        raise LookupError("任务不存在")
    paths = task_output_paths(task)
    directory = next((path for path in paths if path.is_dir()), paths[0] if paths else None)
    if directory is None:
        raise LookupError("任务缺少归档目录")
    directory = managed_child(directory, get_runtime_settings().data_root)
    with _write_lock:
        source, srt = read_task_transcript(task_id, directory)
        metadata = _task_metadata(task, directory)
        extra = metadata.get("extra") or {}
        identity = read_task_json(task_id, directory, "speaker_identity.json")
        speaker_map = read_task_json(task_id, directory, "speaker_map.json")
        checkpoint = read_task_json(task_id, directory, "identity_evidence.checkpoint.json")
        visual = read_task_json(task_id, directory, "participant_evidence.json")
        evidence = (
            (checkpoint.get("report") or {}).get("evidence")
            or checkpoint.get("evidence")
            or (visual.get("text_evidence") or {}).get("evidence")
            or []
        )
        visual = visual or extra.get("participant_evidence") or {}
        preview = read_task_json(task_id, directory, PREVIEW_FILE)
    cues = transcript_cues(srt)
    grouped = defaultdict(list)
    platform_track = _is_unlabeled_platform_track(source, extra)
    for cue in cues:
        speaker, body = (
            (None, cue["text"]) if platform_track else _split_speaker_prefix(cue["text"])
        )
        cue.update(
            text=body,
            speaker=speaker if speaker and speaker.casefold() != UNKNOWN_SPEAKER else None,
        )
        if cue["speaker"]:
            grouped[speaker].append(cue)
    store = get_voiceprint_store()
    links = store.list_task_speakers(str(task_id)) if store.db_path.is_file() else []
    speakers = _profile_rows(
        grouped, sum(map(_duration, cues)), speaker_map, links, extra, identity, evidence
    )
    pending = _pending_cues(directory, cues)
    return {
        "task_id": str(task_id),
        "title": metadata.get("title") or directory.name,
        "source_file": source,
        "total_cues": len(cues),
        "speaker_count": len(speakers),
        "anonymous_count": sum(row["is_anonymous"] for row in speakers),
        "pending_count": len(pending),
        "derived_content_stale": bool(extra.get("derived_content_stale")),
        "derived_content_stale_reason": extra.get("derived_content_stale_reason"),
        "speaker_quality": extra.get("speaker_quality"),
        "visual_participants": {
            **{
                key: visual.get(key) or [] for key in ("participants", "conflicts", "uncertainties")
            },
            "count_estimate": visual.get("count_estimate"),
        },
        "speakers": speakers,
        "pending_cues": pending,
        "has_repair_preview": (directory / PREVIEW_FILE).is_file(),
        "repair_preview_status": preview.get("status"),
    }


def _preview_assignments(
    preview: dict,
    source: str,
    srt: str,
    cues: dict[int, dict],
    indices: list[int] | None,
) -> dict[int, str]:
    conflict = "字幕已变化，请重新运行人物复核"
    if preview.get("source_file") not in (None, source):
        raise SpeakerAssignmentConflict(conflict)
    baseline = preview.get("baseline_srt")
    current = _normalize_srt(srt)
    if baseline is not None and (
        not isinstance(baseline, str) or _normalize_srt(baseline) != current
    ):
        raise SpeakerAssignmentConflict(conflict)
    scope = preview.get("index_scope", "current_transcript")
    input_srt = preview.get("input_srt")
    if scope == "raw_cues":
        if not isinstance(input_srt, str) or _normalize_srt(input_srt) != current:
            raise SpeakerAssignmentConflict("候选使用原始字幕编号，请重新运行全量人物复核")
    elif scope != "current_transcript":
        raise SpeakerAssignmentConflict("无法核对候选字幕编号，请重新运行人物复核")
    elif input_srt is not None and (
        not isinstance(input_srt, str) or _normalize_srt(input_srt) != current
    ):
        raise SpeakerAssignmentConflict(conflict)
    candidates = preview.get("candidates", [])
    if not isinstance(candidates, list) or any(
        not isinstance(row, dict) or type(row.get("index")) is not int for row in candidates
    ):
        raise SpeakerAssignmentConflict("候选字幕编号无效，请重新运行人物复核")
    by_index = {row["index"]: row for row in candidates}
    if len(by_index) != len(candidates):
        raise SpeakerAssignmentConflict("候选字幕编号重复，请重新运行人物复核")
    selected = list(by_index) if indices is None else indices
    for index in selected:
        if type(index) is not int or index not in by_index:
            raise ValueError(f"字幕 #{index} 不在人物复核候选中")
    for index in by_index if baseline is None else selected:
        candidate, cue = by_index[index], cues.get(index)
        if cue is None or candidate.get("timestamp") not in (None, cue["timestamp"]):
            raise SpeakerAssignmentConflict(conflict)
        if baseline is None and (
            candidate.get("timestamp") != cue["timestamp"] or candidate.get("text") != cue["text"]
        ):
            raise SpeakerAssignmentConflict("无法核对候选所用字幕，请重新运行人物复核")
    return {
        index: validate_speaker_name(by_index[index].get("proposed_speaker")) for index in selected
    }


def _rewrite_labels(srt: str, updates: dict[int, str], *, replace_label: bool = True) -> str:
    blocks = _CUE_GAP.split(srt)
    rewritten = set()
    for position in range(0, len(blocks), 2):
        block = blocks[position]
        match = _CUE_HEADER.match(block)
        if not match or (index := int(match[1])) not in updates:
            continue
        body = block[match.end() :]
        prefix = _PREFIX.match(body) if replace_label else None
        if prefix:
            body = body[: prefix.start(1)] + updates[index] + body[prefix.end(1) :]
        else:
            body = f"[{updates[index]}] {body}"
        blocks[position] = block[: match.end()] + body
        rewritten.add(index)
    if rewritten != updates.keys():
        raise ValueError("所选字幕片段格式无效，无法安全改写人物标签")
    return "".join(blocks)


def _save_assignment(task: Task, directory: Path, journal_path: Path, journal: dict) -> None:
    entries = journal["entries"]
    if journal["task_id"] != str(task.id) or entries.keys() - _ASSIGNMENT_FILES:
        raise SpeakerAssignmentConflict("上次人物归属修改的恢复记录无效")
    store = get_artifact_store()
    expected = {}
    for filename, entry in entries.items():
        before, after = entry["before"], entry["after"]
        current = _read_text(task.id, directory, filename)
        versions = {
            _normalize_srt(value) if value is not None else None for value in (before, after)
        }
        if current is not None:
            current = _normalize_srt(current)
        if current not in versions:
            raise SpeakerAssignmentConflict(f"上次人物归属修改未完成，文件已另行修改：{filename}")
        expected[filename] = current if (directory / filename).is_file() else None
    for filename, entry in entries.items():
        store.write(
            task.id, directory, filename, entry["after"], expected_content=expected[filename]
        )
    result = dict(task.result or {})
    if "metadata.json" in entries:
        result["metadata"] = json.loads(entries["metadata.json"]["after"])
    if PREVIEW_FILE in entries:
        preview = json.loads(entries[PREVIEW_FILE]["after"])
        status = preview["status"]
        result["speaker_repair"] = {
            **(result.get("speaker_repair") or {}),
            "status": status,
            "candidates": len(preview["candidates"]),
        }
        if task.options.get("speaker_repair", {}).get("status") == "needs_review":
            task.options["speaker_repair"]["status"] = status
    task.result = result
    get_task_store().save(task)
    journal_path.unlink()


@uses_workspace
def assign_task_speaker_cues(
    task_id: UUID,
    *,
    assignments: list[dict[str, Any]] | None = None,
    apply_preview: bool = False,
    preview_indices: list[int] | None = None,
    expected_srt: str | None = None,
) -> dict[str, Any]:
    with _write_lock:
        task = get_task_store().get(task_id)
        if task is None:
            raise LookupError("任务不存在")
        if task.status != "completed":
            raise SpeakerAssignmentConflict("请等待任务完成后修改人物")
        if preview_indices is not None and not apply_preview:
            raise ValueError("请选择应用人物复核候选")
        if not assignments and not apply_preview:
            raise ValueError("请选择要修改的字幕片段")
        directory = repair_directory(task)
        journal_path = managed_child(directory / _ASSIGNMENT_JOURNAL, directory)
        request = {
            "assignments": assignments or [],
            "apply_preview": apply_preview,
            "preview_indices": preview_indices,
        }
        if journal_path.is_file():
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            if expected_srt is not None:
                _, current_srt = read_task_transcript(task_id, directory)
                if _normalize_srt(expected_srt) not in (
                    _normalize_srt(current_srt),
                    _normalize_srt(journal["baseline_srt"]),
                ):
                    raise SpeakerAssignmentConflict("字幕已更新，请刷新后重试")
            _save_assignment(task, directory, journal_path, journal)
            if request == journal["request"]:
                return journal["result"]
        source, srt = read_task_transcript(task_id, directory)
        if expected_srt is not None and _normalize_srt(expected_srt) != _normalize_srt(srt):
            raise SpeakerAssignmentConflict("字幕已更新，请刷新后重试")
        parsed = transcript_cues(srt)
        cues = {row["index"]: row for row in parsed}
        if len(cues) != len(parsed):
            raise ValueError("字幕片段编号重复，无法安全修改")
        updates = {}
        preview = {}
        if apply_preview:
            preview = read_task_json(task_id, directory, PREVIEW_FILE)
            if not preview:
                raise LookupError("人物复核候选尚未生成")
            updates = _preview_assignments(preview, source, srt, cues, preview_indices)
        consumed = set(updates)
        for row in assignments or []:
            index = row.get("index")
            if type(index) is not int or index not in cues:
                raise ValueError(f"字幕片段 #{index} 不存在")
            updates[index] = validate_speaker_name(row.get("speaker"))
        for index in updates:
            if cue_interval(cues[index]["timestamp"]) is None:
                raise ValueError(f"字幕片段 #{index} 的时间范围无效")
        metadata = _task_metadata(task, directory)
        extra = metadata.get("extra") or {}
        metadata["extra"] = extra
        platform_track = _is_unlabeled_platform_track(source, extra)
        changed = {
            index: name
            for index, name in updates.items()
            if platform_track or _split_speaker_prefix(cues[index]["text"])[0] != name
        }
        output_track, updated = source, srt
        payloads = {}
        if changed:
            if source == TRANSCRIPT_FILES[1] and not _raw_has_speaker_labels(extra):
                output_track = TRANSCRIPT_FILES[0]
                extra["speaker_label_contract"] = 1
            labels = changed
            if platform_track:
                labels = {
                    index: UNKNOWN_SPEAKER
                    for index, cue in cues.items()
                    if _split_speaker_prefix(cue["text"])[0]
                } | changed
            updated = _rewrite_labels(srt, labels, replace_label=not platform_track)
            quality_source = _segments_to_srt(transcript_cues(updated))
            target = (extra.get("speaker_quality") or {}).get(
                "target_count", task.options.get("num_speakers")
            )
            quality = quality_report(quality_source, {}, target)
            quality.pop("input_srt")
            extra["speaker_quality"] = quality
            finalize_speaker_quality(extra, quality_source, target)
            extra.update(
                speaker_revision=int(extra.get("speaker_revision") or 0) + 1,
                derived_content_stale=True,
                derived_content_stale_reason=_STALE_REASON,
            )
            payloads[output_track] = updated
            if output_track == TRANSCRIPT_FILES[0]:
                filename = "transcript_polished.md"
                previous = _read_text(task_id, directory, filename)
                title = str(metadata.get("title") or "")
                if (
                    previous is None
                    or previous.strip() == srt_to_markdown(_normalize_srt(srt), title).strip()
                ):
                    payloads[filename] = srt_to_markdown(_normalize_srt(updated), title)
            payloads["metadata.json"] = json.dumps(metadata, ensure_ascii=False, indent=2)
        if preview:
            remaining = [
                row
                for row in preview.get("candidates", [])
                if row["index"] not in consumed and row["index"] not in changed
            ]
            status = "needs_review" if remaining else "applied"
            preview.update(
                candidates=remaining,
                status=status,
                source_file=output_track,
                baseline_srt=updated,
                input_srt=updated,
                index_scope="current_transcript",
            )
            payloads[PREVIEW_FILE] = json.dumps(preview, ensure_ascii=False, indent=2)
        result = {
            "status": "updated",
            "updated_cues": len(changed),
            "indices": sorted(changed),
            "speakers": extra["speakers"]
            if changed
            else ([] if platform_track else speaker_labels(_segments_to_srt(parsed))),
            "derived_content_stale": bool(extra.get("derived_content_stale")),
        }
        if payloads:
            journal = {
                "task_id": str(task_id),
                "baseline_srt": srt,
                "request": request,
                "result": result,
                "entries": {
                    name: {
                        "before": srt if name == source else _read_text(task_id, directory, name),
                        "after": content,
                    }
                    for name, content in payloads.items()
                },
            }
            # A retry can finish publication after a file or its SQLite copy was saved.
            atomic_write_text(journal_path, json.dumps(journal, ensure_ascii=False))
            _save_assignment(task, directory, journal_path, journal)
        return result
