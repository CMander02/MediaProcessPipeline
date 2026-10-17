"""Persist and expose the semantic speaker-correction stage."""

from __future__ import annotations

import json
import logging

from app.core.logging_setup import log_event
from app.core.pipeline_steps.artifacts import _emit_file_ready, _write_text_artifact
from app.core.pipeline_steps.context import PipelineContext
from app.core.pipeline_steps.state import (
    PipelineStep,
    _emit_timeline_event,
    _raise_if_cancelled,
    _update_step,
    _update_step_progress,
)
from app.services.analysis.speaker_review import apply_speaker_changes, review_speakers
from app.services.analysis.subtitle_speakers import apply_subtitle_speakers, infer_subtitle_speakers
from app.services.analysis.transcript_outputs import UNKNOWN_SPEAKER, _parse_srt, _split_speaker_prefix

logger = logging.getLogger(__name__)


def _transcript_speaker_labels(srt: str) -> set[str]:
    return {
        speaker
        for cue in _parse_srt(srt)
        if (speaker := _split_speaker_prefix(cue["text"])[0])
        and speaker.casefold() != UNKNOWN_SPEAKER
    }


def _rename_speaker_fields(value, names: dict[str, str]) -> None:
    """Update explicit speaker fields while retaining source labels and prose."""
    if isinstance(value, list):
        for item in value:
            _rename_speaker_fields(item, names)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in {"speaker", "speaker_name"} and isinstance(item, str):
                value[key] = names.get(item, item)
            elif key == "speakers" and isinstance(item, list):
                for index, speaker in enumerate(item):
                    if isinstance(speaker, str):
                        item[index] = names.get(speaker, speaker)
                    elif isinstance(speaker, dict):
                        if isinstance(speaker.get("name"), str):
                            speaker["name"] = names.get(speaker["name"], speaker["name"])
                        _rename_speaker_fields(speaker, names)
            else:
                _rename_speaker_fields(item, names)


def _speaker_model_inputs(ctx):
    from app.core.model_router import resolve_polish_llm_binding
    binding = resolve_polish_llm_binding(ctx.rt)
    return {"model": binding.model, "provider": binding.provider,
            "parameters": {key: value for key, value in binding.request_kwargs.items() if key != "api_key"}}


def _identity_inputs(ctx):
    from app.services.analysis.prompts.speaker_identity import SPEAKER_IDENTITY_SYSTEM_PROMPT
    return {
        **_speaker_model_inputs(ctx), "prompt": SPEAKER_IDENTITY_SYSTEM_PROMPT,
        "source_context": ctx.source_context,
        "title": ctx.metadata.title,
        "uploader": ctx.metadata.uploader,
        "description": ctx.metadata.description,
        "mixed_speaker_cues": ctx.metadata.extra.get("mixed_speaker_cues", []),
        "num_speakers": ctx.task.options.get("num_speakers"),
    }


def _restore_identity_report(ctx: PipelineContext, srt: str) -> dict | None:
    path = ctx.task_dir / "speaker_identity.json"
    if not path.is_file():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(report, dict) or report.get("status") not in {"completed", "skipped"}:
        return None
    if report.get("context_stage") != "pre_summary_v2":
        return None
    if report.get("inputs") != _identity_inputs(ctx):
        return None
    if srt not in (report.get("input_srt"), report.get("output_srt")):
        return None
    return report


async def identify_transcript_speakers(ctx: PipelineContext) -> bool:
    """Finalize identity before polish/summary; completed outputs require manual edits."""
    if PipelineStep.POLISH in ctx.done or PipelineStep.ANALYZE in ctx.done:
        return False
    from app.core.pipeline_steps.transcript import _plain_text_from_srt
    from app.services.analysis.speaker_identity import apply_speaker_names, infer_speaker_names
    from app.services.analysis.speaker_identity_artifacts import (
        load_speaker_activity,
        persist_speaker_names,
    )

    srt = ctx.polished or ctx.reviewed_srt or ctx.srt
    labels = _transcript_speaker_labels(srt)
    await _raise_if_cancelled(ctx.task.id)
    report = _restore_identity_report(ctx, srt)
    restored = report is not None
    if report is None:
        context = {
            **ctx.source_context,
            "title": ctx.metadata.title,
            "description": ctx.metadata.description,
            "uploader": ctx.metadata.uploader,
            "analysis": {"content_type": ctx.source_context.get("content_type", "")},
            "speaker_activity": load_speaker_activity(ctx.task_dir, srt),
        }
        try:
            report = await infer_speaker_names(srt, context=context,
                                              checkpoint_path=ctx.task_dir / "speaker_identity.checkpoint.json")
        except Exception as exc:
            log_event(logger, logging.WARNING, "pipeline.speaker_identity.failed", error=exc)
            report = {"status": "failed", "mappings": [], "error": str(exc)}
        report = {
            **report, "input_labels": sorted(labels), "context_stage": "pre_summary_v2",
            "inputs": _identity_inputs(ctx), "input_srt": srt,
        }
    await _raise_if_cancelled(ctx.task.id)

    # A manually renamed edited transcript is authoritative over older raw labels.
    mappings = [
        item
        for item in (report.get("mappings", []) if report["status"] in {"completed", "partial"} else [])
        if item.get("source_label") in labels
        or (restored and item.get("current_name") in labels)
    ]
    raw_labeled = not (ctx.has_subtitle or ctx.subtitle_source == "platform")
    effective_report = {**report, "mappings": mappings, "raw_input_labeled": raw_labeled, "preserve_raw": True}
    names = {item["source_label"]: item["current_name"] for item in mappings}
    previous_srt = srt
    if mappings:
        ctx.reviewed_srt = apply_speaker_names(ctx.reviewed_srt, mappings)
        if ctx.polished:
            ctx.polished = apply_speaker_names(ctx.polished, mappings)
        ctx.transcript = _plain_text_from_srt(ctx.polished or ctx.reviewed_srt or ctx.srt)
        _rename_speaker_fields(ctx.recognition_segments, names)
        _rename_speaker_fields(ctx.metadata.extra, names)
        _rename_speaker_fields(ctx.analysis, names)
        if ctx.polished:
            from app.services.analysis import srt_to_markdown

            ctx.polished_md = srt_to_markdown(ctx.polished, ctx.metadata.title)

    effective_report["output_srt"] = ctx.polished or ctx.reviewed_srt or ctx.srt
    ctx.metadata.extra["speaker_identity"] = {
        "status": report["status"],
        "names_identified": len(report.get("mappings", [])),
    }
    from app.services.analysis.speaker_identity_artifacts import identity_hints, voice_suggestions
    try:
        # Anonymous voices that sound like another voice: offered as a merge, never applied.
        effective_report["suggestions"] = voice_suggestions(
            str(ctx.task.id), ctx.task_dir, effective_report["output_srt"], names,
        )
    except Exception as exc:
        log_event(logger, logging.WARNING, "pipeline.speaker_suggestions.failed", error=exc)
        effective_report["suggestions"] = []
    ctx.metadata.extra.update(identity_hints(effective_report, _transcript_speaker_labels(effective_report["output_srt"])))
    speakers = sorted(_transcript_speaker_labels(ctx.polished or ctx.reviewed_srt or ctx.srt))
    ctx.metadata.extra["speakers"] = speakers
    ctx.metadata.extra["speaker_count"] = len(speakers)
    if names:
        ctx.analysis["speakers_detected"] = len(speakers)

    if not restored or mappings:
        changed_files = persist_speaker_names(str(ctx.task.id), ctx.task_dir, effective_report)
        for filename in changed_files:
            await _emit_file_ready(ctx.task, filename, str(ctx.task_dir / filename))
    if report["status"] == "failed":
        await _emit_timeline_event(
            ctx.task,
            "speaker_identity.failed",
            stage="speaker_identity",
            step_id="speaker_review",
            level="warning",
            message="说话人姓名识别未完成，保留现有标签继续整理",
            data={"error": report.get("error") or report.get("reason")},
        )
    return previous_srt != (ctx.polished or ctx.reviewed_srt or ctx.srt)


async def _apply_report(ctx: PipelineContext, report: dict, caption: bool) -> None:
    from app.services.analysis.speaker_resolution import apply_resolutions

    if not caption:
        ctx.reviewed_srt = apply_resolutions(apply_speaker_changes(ctx.srt, report["changes"]),
                                             report.get("resolved_unassigned") or [])
        ctx.metadata.extra["speaker_review"] = {
            "status": report["status"],
            "changes": len(report["changes"]),
            "failed_chunks": len(report["failed_chunks"]),
            "resolved_unassigned": len(report.get("resolved_unassigned") or []),
        }
        return
    ctx.reviewed_srt = apply_resolutions(apply_subtitle_speakers(ctx.srt, report), report.get("resolved_unassigned") or [])
    ctx.metadata.extra["speaker_source"] = "subtitle_inference"
    ctx.metadata.extra["speaker_inference"] = {
        "status": report["status"],
        "speakers": len(report["speakers"]),
        "unassigned_segments": report.get("unassigned_segments", 0),
        "failed_chunks": len(report["failed_chunks"]),
    }
    await _write_text_artifact(
        ctx.task,
        ctx.task_dir,
        "speaker_map.json",
        json.dumps(
            {
                "version": 1,
                "source": "subtitle_inference",
                "confirmed": False,
                "source_candidates": ctx.source_context.get("speaker_candidates", []),
                "mappings": [
                    {
                        "source_label": person["id"],
                        "current_name": person.get("name") or person["id"],
                        "status": "subtitle_inferred",
                    }
                    for person in report["speakers"]
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
    )


async def review_transcript_speakers(ctx: PipelineContext) -> None:
    from app.core.pipeline_steps.participant_evidence import prepare_participant_evidence
    await prepare_participant_evidence(ctx)
    caption = (
        ctx.has_subtitle
        or ctx.subtitle_source == "platform"
        or ctx.metadata.extra.get("speaker_source") == "subtitle_inference"
    )
    report_name = "subtitle_speakers.json" if caption else "speaker_review.json"
    report_path = ctx.task_dir / report_name
    from app.services.analysis.source_context import speaker_constraints

    context = {
        **ctx.source_context,
        "title": ctx.metadata.title,
        "uploader": ctx.metadata.uploader,
        "description": ctx.metadata.description,
        "mixed_speaker_cues": ctx.metadata.extra.get("mixed_speaker_cues", []),
    }
    exact, _, maximum = speaker_constraints(ctx.source_context, ctx.task.options)
    contract = {"version": 2, "srt": ctx.srt, "context": context, "speaker_limit": exact or maximum}
    from app.services.analysis.prompts.subtitle_speakers import SUBTITLE_SPEAKERS_SYSTEM_PROMPT
    from app.services.analysis.prompts.speaker_review import SPEAKER_REVIEW_SYSTEM_PROMPT
    contract.update(**_speaker_model_inputs(ctx), prompt=SUBTITLE_SPEAKERS_SYSTEM_PROMPT if caption else SPEAKER_REVIEW_SYSTEM_PROMPT)
    report = None
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    restored = bool(
        isinstance(report, dict)
        and report.get("input_contract") == contract
        and report.get("status") in {"completed", "skipped"}
    )
    await _raise_if_cancelled(ctx.task.id)
    if restored:
        if PipelineStep.POLISH not in ctx.done:
            ctx.polished = None
            ctx.polished_md = None
        log_event(logger, logging.INFO, "pipeline.speaker_review.restored")
    elif PipelineStep.POLISH in ctx.done:
        # Preserve edited/finished outputs. Explicit repair uses its own input
        # and must not silently reinterpret an old caption report by cue number.
        ctx.metadata.extra["speaker_quality"] = {
            "status": "needs_review", "reason": "旧人物报告缺少匹配的输入记录或存在未完成批次",
        }
        log_event(
            logger,
            logging.INFO,
            "pipeline.speaker_review.skipped",
            reason="already_polished",
        )
        await _update_step(ctx.task, PipelineStep.SPEAKER_REVIEW, completed=True)
        return

    else:
        ctx.polished = None
        ctx.polished_md = None
        await _update_step(ctx.task, PipelineStep.SPEAKER_REVIEW)

        async def progress(processed: int, total: int) -> None:
            await _raise_if_cancelled(ctx.task.id)
            await _update_step_progress(
                ctx.task,
                PipelineStep.SPEAKER_REVIEW,
                processed / total,
                f"{'推断字幕说话人' if caption else '说话人修正'}：已处理 {processed}/{total} 批",
            )

        if caption:
            report = await infer_subtitle_speakers(
                ctx.srt,
                context=context,
                speaker_limit=exact or maximum,
                on_progress=progress,
                checkpoint_path=ctx.task_dir / "subtitle_speakers.checkpoint.json",
                input_labeled=False,
            )
        else:
            report = await review_speakers(ctx.srt, context=context, on_progress=progress,
                                          checkpoint_path=ctx.task_dir / "speaker_review.checkpoint.json")
    report["input_contract"] = contract
    await _raise_if_cancelled(ctx.task.id)
    if not restored and "resolved_unassigned" not in report:
        # Lines left without a speaker (two voices in one line, or none detected) take the
        # most likely voice from the audio overlap and the lines around them.
        from app.services.analysis.speaker_resolution import (
            cue_acoustic_candidates, cue_voice_similarity, resolve_unassigned,
        )

        labeled = apply_subtitle_speakers(ctx.srt, report) if caption else apply_speaker_changes(ctx.srt, report["changes"])
        events = {row["index"] for row in report.get("assignments", []) if row.get("kind") == "non_speech"}
        try:
            report["resolved_unassigned"] = await resolve_unassigned(
                labeled, acoustic=None if caption else cue_acoustic_candidates(ctx.task_dir, labeled),
                voice=None if caption else cue_voice_similarity(ctx.task_dir, labeled), skip=events,
            )
        except Exception as exc:
            log_event(logger, logging.WARNING, "pipeline.speaker_resolution.failed", error=exc)
            report["resolved_unassigned"] = []
    await _apply_report(ctx, report, caption)
    from app.services.analysis.speaker_quality import quality_report
    quality = quality_report(ctx.reviewed_srt or ctx.srt, report, exact)
    ctx.metadata.extra["speaker_quality"] = {key: value for key, value in quality.items() if key != "input_srt"}
    ctx.metadata.extra["speaker_label_contract"] = 1
    ctx.metadata.extra["raw_speaker_label_contract"] = 0 if caption else 1
    ctx.metadata.extra["subtitle_source"] = "platform" if caption else "asr"
    await _write_text_artifact(ctx.task, ctx.task_dir, "speaker_quality.json", json.dumps(quality, ensure_ascii=False, indent=2))
    if not restored:
        await _write_text_artifact(
            ctx.task,
            ctx.task_dir,
            report_name,
            json.dumps(report, ensure_ascii=False, indent=2),
        )
    if report["failed_chunks"]:
        await _emit_timeline_event(
            ctx.task,
            "speaker_review.partial_failure",
            stage="speaker_review",
            step_id="speaker_review",
            level="warning",
            message=f"{len(report['failed_chunks'])} 批说话人处理未完成，保留这些片段的原文与标签，继续后续处理",
            data=ctx.metadata.extra["speaker_inference" if caption else "speaker_review"],
        )
    if not restored or PipelineStep.SPEAKER_REVIEW not in ctx.done:
        await _update_step(ctx.task, PipelineStep.SPEAKER_REVIEW, completed=True)
