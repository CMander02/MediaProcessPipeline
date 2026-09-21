"""Caption-only speaker inference with a shared, evidence-backed roster."""

import json
import logging
import re

from app.core.logging_setup import log_event
from app.core.model_router import resolve_polish_llm_binding
from app.core.settings import get_runtime_settings
from app.services.analysis.prompts.subtitle_speakers import (
    SUBTITLE_SPEAKERS_SYSTEM_PROMPT,
    get_subtitle_speakers_prompt,
)
from app.services.analysis.transcript_outputs import (
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
    context_text = json.dumps(context, ensure_ascii=False)
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
            or not evidence
            or any(type(index) is not int or index not in visible for index in evidence)
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise ValueError("人物 ID、姓名或字幕证据无效")
        name = name.strip()
        if speaker not in updated and speaker != _next_id(updated):
            raise ValueError("新人物必须按连续的全局 ID 编号")
        previous = updated.get(speaker, {})
        if previous.get("name") and name and name != previous["name"]:
            raise ValueError("已有说话人的姓名发生漂移")
        name = name or previous.get("name", "")
        if (
            name
            and name != previous.get("name")
            and name not in context_text
            and not any(name in visible[i]["text"] for i in evidence)
        ):
            raise ValueError("人物姓名没有字幕或来源信息依据")
        if name and any(p.get("name") == name for key, p in updated.items() if key != speaker):
            raise ValueError("同名人物应复用已有 ID")
        updated[speaker] = {
            **previous,
            "id": speaker,
            "name": name,
            "role": role.strip() or previous.get("role", ""),
            "evidence_indexes": evidence,
            "reason": reason.strip(),
        }
    if speaker_limit and len(updated) > speaker_limit:
        raise ValueError("推断人数超过指定人数")
    editable = {cue["index"] for cue in targets}
    seen = set()
    for assignment in assignments:
        if not isinstance(assignment, dict):
            raise ValueError("字幕归属必须是对象")
        index, speaker = assignment.get("index"), assignment.get("speaker")
        if (
            type(index) is not int
            or index not in editable
            or index in seen
            or (speaker is not None and (not isinstance(speaker, str) or speaker not in updated))
        ):
            raise ValueError("字幕序号或人物 ID 无效")
        seen.add(index)
    if seen != editable:
        raise ValueError("字幕归属必须完整覆盖本批条目")
    return updated, [{"index": a["index"], "speaker": a["speaker"]} for a in assignments]


def apply_subtitle_speakers(srt: str, report: dict) -> str:
    roster = {person["id"]: person for person in report.get("speakers", [])}
    assignments = {item["index"]: item["speaker"] for item in report.get("assignments", [])}
    cues = _parse_srt(srt)
    for cue in cues:
        speaker = assignments.get(cue["index"])
        if speaker not in roster:
            continue
        _, body = _split_speaker_prefix(cue["text"])
        name = roster[speaker].get("name") or speaker
        cue["text"] = f"[{name}] {body}"
    return _segments_to_srt(cues) if cues else srt


async def infer_subtitle_speakers(srt, context=None, *, speaker_limit=None, on_progress=None):
    from app.services.analysis.llm import get_llm_service

    context = context or {}
    cues = _parse_srt(srt)
    roster = {}
    for cue in cues:
        label, body = _split_speaker_prefix(cue["text"])
        if label and label not in roster:
            roster[label] = {"id": label, "name": "", "role": "", "examples": [body[:160]]}
    report = {
        "status": "skipped",
        "source": "subtitle_inference",
        "input_segments": len(cues),
        "speakers": list(roster.values()),
        "assignments": [],
        "failed_chunks": [],
    }
    if not cues:
        return report
    service = get_llm_service()
    provider = resolve_polish_llm_binding(get_runtime_settings()).provider
    chunk_size, overlap = 24, 4
    total = (len(cues) + chunk_size - 1) // chunk_size
    log_event(
        logger, logging.INFO, "llm.subtitle_speakers.started", chunks=total, segments=len(cues)
    )
    for batch, offset in enumerate(range(0, len(cues), chunk_size), 1):
        if on_progress:
            await on_progress(batch - 1, total)
        targets = cues[offset : offset + chunk_size]
        surrounding = cues[max(0, offset - overlap) : offset + chunk_size + overlap]
        prompt = get_subtitle_speakers_prompt(
            surrounding,
            [cue["index"] for cue in targets],
            roster,
            context,
            speaker_limit,
        )
        log_event(
            logger, logging.INFO, "llm.subtitle_speakers.chunk_started", chunk=batch, chunks=total
        )
        for attempt in range(2):
            try:
                response = await service._call(
                    prompt,
                    provider_override=provider,
                    stage="subtitle_speakers",
                    system_prompt=SUBTITLE_SPEAKERS_SYSTEM_PROMPT,
                )
                updated, assignments = _parse_inference(
                    response,
                    targets,
                    surrounding,
                    roster,
                    context,
                    speaker_limit,
                )
                # Keep short text examples for identity continuity in later batches.
                by_index = {cue["index"]: cue for cue in targets}
                for item in assignments:
                    if item["speaker"] is not None:
                        person = updated[item["speaker"]]
                        examples = list(person.get("examples") or [])
                        examples.append(by_index[item["index"]]["text"][:160])
                        person["examples"] = examples[:2]
                roster = updated
                report["assignments"].extend(assignments)
                log_event(
                    logger,
                    logging.INFO,
                    "llm.subtitle_speakers.chunk_completed",
                    chunk=batch,
                    speakers=len(roster),
                )
                break
            except Exception as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "llm.subtitle_speakers.chunk_failed",
                    chunk=batch,
                    attempt=attempt + 1,
                    error=exc,
                )
                if attempt == 0:
                    prompt += f"\n上次输出校验失败：{exc}。请检查并返回完整的最终 JSON。"
                else:
                    report["failed_chunks"].append({"chunk": batch, "error": str(exc)})
        if on_progress:
            await on_progress(batch, total)
    failures = len(report["failed_chunks"])
    report["status"] = "failed" if failures == total else "partial" if failures else "completed"
    report["speakers"] = list(roster.values())
    report["unassigned_segments"] = len(cues) - sum(
        item["speaker"] is not None for item in report["assignments"]
    )
    log_event(
        logger,
        logging.INFO,
        "llm.subtitle_speakers.completed",
        status=report["status"],
        speakers=len(roster),
        unassigned=report["unassigned_segments"],
    )
    return report
