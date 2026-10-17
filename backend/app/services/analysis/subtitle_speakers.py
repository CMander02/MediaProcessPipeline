"""Caption-only speaker inference with a shared, evidence-backed roster."""

import json
import logging
import re
from pathlib import Path

from app.core.logging_setup import log_event
from app.core.model_router import resolve_polish_llm_binding
from app.core.settings import get_runtime_settings
from app.services.analysis.long_text import compact_metadata, is_length_error, prompt_budget, record_windows
from app.services.analysis.speaker_checkpoint import load_checkpoint, save_checkpoint
from app.services.analysis.speaker_evidence import name_has_direct_evidence
from app.services.analysis.prompts.subtitle_speakers import (
    SUBTITLE_SPEAKERS_SYSTEM_PROMPT,
    get_subtitle_speakers_prompt,
)
from app.services.analysis.transcript_outputs import (
    UNKNOWN_SPEAKER,
    _parse_srt,
    _segments_to_srt,
    _split_speaker_prefix,
)

logger = logging.getLogger(__name__)


def _next_id(roster):
    number = 0
    while f"SPEAKER_{number:02d}" in roster:
        number += 1
    return f"SPEAKER_{number:02d}"


def _parse_inference(response, targets, surrounding, roster, context, speaker_limit):
    blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", response, flags=re.IGNORECASE)
    response = blocks[-1] if blocks else response
    start, end = response.find("{"), response.rfind("}")
    if start < 0 or end < start:
        raise ValueError("字幕说话人推断需要返回 JSON 对象")
    data = json.loads(response[start : end + 1])
    people, assignments = data.get("speakers"), data.get("assignments")
    if not isinstance(people, list) or not isinstance(assignments, list):
        raise ValueError("speakers 和 assignments 必须是数组")
    updated = {key: dict(value) for key, value in roster.items()}
    visible = {cue["index"]: cue for cue in surrounding}
    for person in people:
        if not isinstance(person, dict):
            raise ValueError("人物记录必须是对象")
        speaker = person.get("id")
        name, role = person.get("name", ""), person.get("role", "")
        evidence, reason = person.get("evidence_indexes"), person.get("reason")
        if (
            not isinstance(speaker, str)
            or not isinstance(name, str)
            or not isinstance(role, str)
            or not isinstance(evidence, list)
            or any(type(index) is not int for index in evidence)
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise ValueError("人物 ID、姓名或字幕证据无效")
        name = name.strip()
        if speaker not in updated and speaker != _next_id(updated):
            raise ValueError("新人物必须按连续的全局 ID 编号")
        previous = updated.get(speaker, {})
        rejected_name = ""
        if previous.get("name") and name and name != previous["name"]:
            rejected_name, name = name, previous["name"]
        # Repeating an unchanged roster entry adds no claim to validate. Keep
        # its original evidence when the model omits or echoes historical cues.
        if (
            previous
            and name in {"", previous.get("name", "")}
            and role.strip() in {"", previous.get("role", "")}
            and set(evidence) <= set(previous.get("evidence_indexes", []))
        ):
            continue
        if not evidence:
            raise ValueError(f"人物 {speaker} 的新增信息需要当前字幕证据")
        invalid_evidence = [index for index in evidence if index not in visible]
        if invalid_evidence:
            raise ValueError(f"人物 {speaker} 的证据字幕序号 {invalid_evidence} 不在当前上下文中")
        name = name or previous.get("name", "")
        if name and name != previous.get("name") and not name_has_direct_evidence(
            name, [visible[i]["text"] for i in evidence], context,
        ):
            rejected_name, name = name, ""
        if name and any(p.get("name") == name for key, p in updated.items() if key != speaker):
            raise ValueError("同名人物应复用已有 ID")
        updated[speaker] = {
            **previous,
            "id": speaker,
            "name": name,
            "role": role.strip() or previous.get("role", ""),
            "evidence_indexes": evidence,
            "reason": reason.strip(),
            **({"rejected_name": rejected_name} if rejected_name else {}),
        }
    editable = {cue["index"] for cue in targets}
    seen = set()
    accepted = []
    for assignment in assignments:
        if not isinstance(assignment, dict):
            raise ValueError("字幕归属必须是对象")
        index, speaker = assignment.get("index"), assignment.get("speaker")
        kind = assignment.get("kind", "speech" if speaker is not None else "uncertain")
        if kind not in {"speech", "non_speech", "mixed", "uncertain"}:
            raise ValueError("声音类型必须是 speech、non_speech、mixed 或 uncertain")
        event = assignment.get("event_description", "")
        if not isinstance(event, str):
            raise ValueError("声音事件描述必须是文字")
        if kind == "non_speech":
            speaker = None
        if type(index) is not int:
            raise ValueError(f"字幕序号必须是整数，收到 {index!r}")
        if index not in visible:
            raise ValueError(f"字幕序号 {index} 不在本批可见字幕中：{sorted(visible)}")
        # The model may also label the supplied read-only context. Only target
        # assignments belong to this batch; context labels never overwrite it.
        if index not in editable:
            continue
        if index in seen:
            raise ValueError(f"重复字幕序号 {index}，每条目标字幕只能返回一次")
        if speaker is not None and (not isinstance(speaker, str) or speaker not in updated):
            raise ValueError(
                f"字幕 {index} 引用了未知人物 ID {speaker!r}，可用人物 ID：{list(updated)}",
            )
        seen.add(index)
        accepted.append({
            "index": index, "speaker": speaker, "kind": kind,
            **({"event_description": event.strip(), "event_source": "subtitle_inference"} if event.strip() else {}),
        })
    if seen != editable:
        raise ValueError(f"缺少字幕序号 {sorted(editable - seen)}，必须覆盖全部目标字幕")
    used = set(roster) | {row["speaker"] for row in accepted if row["speaker"] is not None}
    updated = {key: person for key, person in updated.items() if key in used}
    if speaker_limit and len(updated) > speaker_limit:
        raise ValueError("推断人数超过指定人数")
    return updated, accepted


def apply_subtitle_speakers(srt: str, report: dict, *, input_labeled: bool | None = None) -> str:
    roster = {person["id"]: person for person in report.get("speakers", [])}
    assignments = {item["index"]: item["speaker"] for item in report.get("assignments", [])}
    cues = _parse_srt(srt)
    if input_labeled is None:
        input_labeled = report.get("input_labeled", True)
    for cue in cues:
        existing, body = _split_speaker_prefix(cue["text"]) if input_labeled else (None, cue["text"])
        if cue["index"] in assignments:
            speaker = assignments[cue["index"]]
            name = (
                (roster[speaker].get("name") or speaker) if speaker in roster else UNKNOWN_SPEAKER
            )
        else:
            name = existing or UNKNOWN_SPEAKER
        cue["text"] = f"[{name}] {body}"
    return _segments_to_srt(cues) if cues else srt


class _RequestFailure(Exception):
    def __init__(self, cause):
        self.cause = cause
        super().__init__(str(cause))


async def infer_subtitle_speakers(
    srt, context=None, *, speaker_limit=None, on_progress=None,
    checkpoint_path=None, input_labeled=False, max_calls=None, checkpoint_execution=None,
):
    from app.services.analysis.llm import get_llm_service

    context = context or {}
    cues = _parse_srt(srt)
    binding = resolve_polish_llm_binding(get_runtime_settings())
    provider = binding.provider
    checkpoint_path = Path(checkpoint_path) if checkpoint_path is not None else None
    inputs = {
        "srt": srt, "context": context, "speaker_limit": speaker_limit,
        "input_labeled": input_labeled, "prompt_version": 2,
        "model": binding.model, "provider": provider,
        "parameters": {k: v for k, v in binding.request_kwargs.items() if k != "api_key"},
        "system_prompt": SUBTITLE_SPEAKERS_SYSTEM_PROMPT,
    }
    saved = load_checkpoint(checkpoint_path, inputs)
    from app.services.analysis.speaker_checkpoint import begin_checkpoint_attempt
    begin_checkpoint_attempt(checkpoint_path, inputs, "subtitle_speakers", checkpoint_execution)
    report = saved["report"] if saved else {
        "status": "skipped", "source": "subtitle_inference",
        "input_segments": len(cues), "input_labeled": input_labeled,
        "speakers": [], "assignments": [], "failed_chunks": [],
    }
    completed = set(saved.get("completed_indexes", [])) if saved else set()
    # An interrupted split cue must be reconsidered as a whole. Its completed
    # neighbours remain reusable, while partial fragments cannot win by accident.
    report["assignments"] = [row for row in report["assignments"] if row["index"] in completed]
    prior_failures = list(report.get("failed_chunks", []))
    report["previous_failures"] = prior_failures
    report["failed_chunks"] = []
    roster = {person["id"]: person for person in report["speakers"]}
    if not cues:
        return report
    service = get_llm_service()
    budget = prompt_budget(provider, "subtitle_speakers", SUBTITLE_SPEAKERS_SYSTEM_PROMPT)
    context = compact_metadata(context, max(512, budget // 3))
    chunk_size, overlap = 24, 4
    total = max(1, (len(cues) - len(completed) + chunk_size - 1) // chunk_size)
    if on_progress:
        await on_progress(0, total)

    def build_prompt(targets, surrounding):
        return get_subtitle_speakers_prompt(
            surrounding, [cue["index"] for cue in targets], roster, context, speaker_limit,
        )

    async def call(prompt):
        try:
            return await service._call(
                prompt, provider_override=provider, stage="subtitle_speakers",
                system_prompt=SUBTITLE_SPEAKERS_SYSTEM_PROMPT, max_retries=0,
            )
        except Exception as exc:
            if not is_length_error(exc):
                log_event(logger, logging.ERROR, "llm.subtitle_speakers.failed", error=exc)
                raise _RequestFailure(exc) from exc
            raise

    report["status"] = "running"
    save_checkpoint(checkpoint_path, inputs, report, completed)
    state = {}
    failed_ids = set()
    batch = 0
    async for targets, surrounding, result in record_windows(
        cues, build_prompt, call, budget=budget, chunk_size=chunk_size, overlap=overlap,
        keep_errors=True, skip_indexes=completed, window_state=state,
        max_calls=max_calls if max_calls is not None else max(32, len(cues) * 6),
        validate=lambda response, targets, visible: _parse_inference(
            response, targets, visible, roster, context, speaker_limit,
        ),
    ):
        batch += 1
        indexes = [row["index"] for row in targets]
        if isinstance(result, Exception):
            failed_ids.update(indexes)
            report["failed_chunks"].append({
                "chunk": batch, "indexes": indexes, "error": str(result),
            })
        else:
            roster, assignments = result
            by_index = {cue["index"]: cue for cue in targets}
            for item in assignments:
                if item["speaker"] is not None:
                    person = roster[item["speaker"]]
                    examples = list(person.get("examples") or [])
                    example = by_index[item["index"]]["text"][:160]
                    if example not in examples:
                        examples.append(example)
                    person["examples"] = examples[:2]
            report["assignments"].extend(assignments)
            completed.update(set(state["completed_indexes"]) - failed_ids)
        report["speakers"] = list(roster.values())
        report["calls"] = state["calls"]
        save_checkpoint(checkpoint_path, inputs, report, completed)
        if isinstance(result, _RequestFailure):
            # A stopped service cannot be repaired by spending the remaining
            # batch budget. Persist progress and let task recovery retry later.
            raise result.cause
        if on_progress:
            await on_progress(batch, max(batch, total))

    grouped = {}
    for assignment in report["assignments"]:
        grouped.setdefault(assignment["index"], []).append(assignment)
    report["assignments"] = [
        rows[0] if index not in failed_ids and len({row["speaker"] for row in rows}) == 1
        else {**rows[0], "speaker": None, "kind": "uncertain", "reason": "字幕片段归属冲突或未全部完成"}
        for index, rows in sorted(grouped.items())
    ]
    failures = len(report["failed_chunks"])
    report["status"] = ("partial" if completed else "failed") if failures else "completed"
    report["unassigned_segments"] = len(cues) - sum(
        item["speaker"] is not None or item.get("kind") == "non_speech"
        for item in report["assignments"]
    )
    report["event_segments"] = sum(
        item.get("kind") in {"non_speech", "mixed"} for item in report["assignments"]
    )
    save_checkpoint(checkpoint_path, inputs, report, completed)
    log_event(
        logger, logging.INFO, "llm.subtitle_speakers.completed", status=report["status"],
        speakers=len(roster), failed_chunks=failures, unassigned=report["unassigned_segments"],
    )
    return report
