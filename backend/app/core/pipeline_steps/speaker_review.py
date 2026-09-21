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
from app.services.analysis.transcript_outputs import _parse_srt, _split_speaker_prefix

logger = logging.getLogger(__name__)


def _transcript_speaker_labels(srt: str) -> set[str]:
    return {
        speaker
        for cue in _parse_srt(srt)
        if (speaker := _split_speaker_prefix(cue["text"])[0])
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


def _restore_identity_report(ctx: PipelineContext, labels: set[str]) -> dict | None:
    # A freshly generated summary may supply identity evidence for the same labels.
    if PipelineStep.ANALYZE not in ctx.done:
        return None
    path = ctx.task_dir / "speaker_identity.json"
    if not path.is_file():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(report, dict) or report.get("status") not in {"completed", "skipped"}:
        return None
    if report.get("context_stage") != "analyzed":
        return None
    original_labels = set(report.get("input_labels") or [])
    if not original_labels:
        return None
    mapped_labels = {
        item.get("current_name")
        for item in report.get("mappings", [])
        if isinstance(item, dict)
    }
    if labels == original_labels or (
        report["status"] == "completed" and labels <= original_labels | mapped_labels
    ):
        return report
    return None


async def identify_transcript_speakers(ctx: PipelineContext) -> bool:
    """Resolve anonymous labels from the complete edited transcript and persist names."""
    from app.core.pipeline_steps.transcript import _plain_text_from_srt
    from app.services.analysis.speaker_identity import apply_speaker_names, infer_speaker_names
    from app.services.analysis.speaker_identity_artifacts import (
        _replace_references,
        load_speaker_activity,
        persist_speaker_names,
    )

    srt = ctx.polished or ctx.reviewed_srt or ctx.srt
    labels = _transcript_speaker_labels(srt)
    await _raise_if_cancelled(ctx.task.id)
    report = _restore_identity_report(ctx, labels)
    restored = report is not None
    if report is None:
        context = {
            **ctx.source_context,
            **ctx.analysis,
            "title": ctx.metadata.title,
            "description": ctx.metadata.description,
            "analysis": ctx.analysis,
            "summary": ctx.summary,
            "speaker_activity": load_speaker_activity(ctx.task_dir, srt),
        }
        try:
            report = await infer_speaker_names(srt, context=context)
        except Exception as exc:
            log_event(logger, logging.WARNING, "pipeline.speaker_identity.failed", error=exc)
            report = {"status": "failed", "mappings": [], "error": str(exc)}
        report = {**report, "input_labels": sorted(labels), "context_stage": "analyzed"}
    await _raise_if_cancelled(ctx.task.id)

    # A manually renamed edited transcript is authoritative over older raw labels.
    mappings = [
        item
        for item in (report.get("mappings", []) if report["status"] == "completed" else [])
        if item.get("source_label") in labels
        or (restored and item.get("current_name") in labels)
    ]
    effective_report = {**report, "mappings": mappings}
    names = {item["source_label"]: item["current_name"] for item in mappings}
    previous_srt = srt
    if mappings:
        ctx.srt = apply_speaker_names(ctx.srt, mappings)
        ctx.reviewed_srt = apply_speaker_names(ctx.reviewed_srt, mappings)
        if ctx.polished:
            ctx.polished = apply_speaker_names(ctx.polished, mappings)
        ctx.transcript = _plain_text_from_srt(ctx.polished or ctx.reviewed_srt or ctx.srt)
        _rename_speaker_fields(ctx.recognition_segments, names)
        _rename_speaker_fields(ctx.metadata.extra, names)
        _rename_speaker_fields(ctx.analysis, names)
        ctx.analysis = _replace_references(ctx.analysis, names)
        ctx.summary = _replace_references(ctx.summary, names)
        ctx.mindmap = _replace_references(ctx.mindmap, names)
        ctx.detail = _replace_references(ctx.detail, names)
        if ctx.polished:
            from app.services.analysis import srt_to_markdown

            ctx.polished_md = srt_to_markdown(ctx.polished, ctx.metadata.title)

    ctx.metadata.extra["speaker_identity"] = {
        "status": report["status"],
        "names_identified": len(report.get("mappings", [])),
    }
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
            step_id="analyze",
            level="warning",
            message="说话人姓名识别未完成，保留现有标签继续整理",
            data={"error": report.get("error") or report.get("reason")},
        )
    return previous_srt != (ctx.polished or ctx.reviewed_srt or ctx.srt)


async def _apply_report(ctx: PipelineContext, report: dict, caption: bool) -> None:
    if not caption:
        ctx.reviewed_srt = apply_speaker_changes(ctx.srt, report["changes"])
        ctx.metadata.extra["speaker_review"] = {
            "status": report["status"],
            "changes": len(report["changes"]),
            "failed_chunks": len(report["failed_chunks"]),
        }
        return
    ctx.reviewed_srt = apply_subtitle_speakers(ctx.srt, report)
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
    caption = (
        ctx.has_subtitle
        or ctx.subtitle_source == "platform"
        or ctx.metadata.extra.get("speaker_source") == "subtitle_inference"
    )
    report_name = "subtitle_speakers.json" if caption else "speaker_review.json"
    report_path = ctx.task_dir / report_name
    if PipelineStep.SPEAKER_REVIEW in ctx.done and report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        await _apply_report(ctx, report, caption)
        log_event(logger, logging.INFO, "pipeline.speaker_review.restored")
        return
    if PipelineStep.POLISH in ctx.done:
        log_event(
            logger,
            logging.INFO,
            "pipeline.speaker_review.skipped",
            reason="already_polished",
        )
        await _update_step(ctx.task, PipelineStep.SPEAKER_REVIEW, completed=True)
        return

    await _raise_if_cancelled(ctx.task.id)
    await _update_step(ctx.task, PipelineStep.SPEAKER_REVIEW)

    async def progress(completed: int, total: int) -> None:
        await _raise_if_cancelled(ctx.task.id)
        await _update_step_progress(
            ctx.task,
            PipelineStep.SPEAKER_REVIEW,
            completed / total,
            f"{'推断字幕说话人' if caption else '说话人修正'}：{completed}/{total} 段",
        )

    context = {
        **ctx.source_context,
        **ctx.analysis,
        "title": ctx.metadata.title,
        "description": ctx.metadata.description,
    }
    if caption:
        from app.services.analysis.source_context import speaker_constraints

        exact, _, maximum = speaker_constraints(ctx.source_context, ctx.task.options)
        report = await infer_subtitle_speakers(
            ctx.srt,
            context=context,
            speaker_limit=exact or maximum,
            on_progress=progress,
        )
    else:
        report = await review_speakers(ctx.srt, context=context, on_progress=progress)
    await _raise_if_cancelled(ctx.task.id)
    await _apply_report(ctx, report, caption)
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
            message=f"{len(report['failed_chunks'])} 批说话人处理未完成，保留这些片段的原文与标签",
            data=ctx.metadata.extra["speaker_inference" if caption else "speaker_review"],
        )
    await _update_step(ctx.task, PipelineStep.SPEAKER_REVIEW, completed=True)
