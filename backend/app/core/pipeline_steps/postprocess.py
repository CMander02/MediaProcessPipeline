"""Postprocess responsibilities for the media pipeline."""

from __future__ import annotations

import asyncio
import logging

from app.core.logging_setup import log_event
from app.core.pipeline_steps.artifacts import (
    _emit_file_ready,
    _schedule_kb_index,
    _write_detail_file,
    _write_mindmap_files,
    _write_summary_files,
    _write_text_artifact,
    write_metadata_json,
)
from app.core.pipeline_steps.context import PipelineContext
from app.core.pipeline_steps.state import PipelineStep, _raise_if_cancelled, _update_step
from app.core.pipeline_steps.transcript import _plain_text_from_srt, _user_language_hint

logger = logging.getLogger(__name__)


async def postprocess(ctx: PipelineContext) -> None:
    from app.services.analysis import (
        analyze_content,
        generate_detail,
        generate_mindmap,
        polish_text,
        summarize_text,
    )
    from app.services.archiving import archive_result

    # Guard: skip LLM if transcript is empty or trivially short
    if not ctx.transcript or len(ctx.transcript.strip()) < 10:
        log_event(
            logger,
            logging.WARNING,
            "pipeline.llm.skipped",
            reason="transcript_too_short",
            chars=len(ctx.transcript),
        )
        await _update_step(ctx.task, PipelineStep.POLISH, completed=True)
        await _update_step(ctx.task, PipelineStep.ANALYZE, completed=True)

        await _update_step(ctx.task, PipelineStep.ARCHIVE)
        empty_analysis = {
            "language": "unknown",
            "content_type": "unknown",
            "main_topics": [],
            "keywords": [],
            "proper_nouns": [],
            "speakers_detected": 0,
            "tone": "unknown",
        }
        empty_summary = {
            "tldr": "未检测到有效语音内容",
            "key_facts": [],
            "action_items": [],
            "topics": [],
        }
        archive = await archive_result(
            ctx.metadata,
            polished_srt="",
            summary=empty_summary,
            mindmap="",
            original_srt=ctx.srt,
            work_dir=ctx.task_dir,
            task_id=str(ctx.task.id),
            analysis=empty_analysis,
        )
        write_metadata_json(ctx.task_dir, ctx.metadata, status="completed")
        await _update_step(ctx.task, PipelineStep.ARCHIVE, completed=True)

        ctx.task.result = {
            "metadata": ctx.metadata.model_dump(mode="json"),
            "transcript_segments": 0,
            "archive": archive,
            "output_dir": str(ctx.task_dir),
            "analysis": empty_analysis,
            "warning": "未检测到有效语音内容，跳过 LLM 分析",
        }
        return

    # ── Step 4: Polish transcript (CPU/network) ────────────────────────────
    polish_ran = False
    if PipelineStep.POLISH in ctx.done:
        log_event(
            logger,
            logging.INFO,
            "pipeline.step.skipped",
            step=PipelineStep.POLISH,
            reason="already_done",
        )
        ctx.restore_transcript()  # picks up polished if present
    else:
        await _update_step(ctx.task, PipelineStep.POLISH)
        if ctx.has_subtitle:
            log_event(
                logger,
                logging.INFO,
                "pipeline.step.skipped",
                step=PipelineStep.POLISH,
                reason="platform_subtitle_prepolished",
            )
        else:
            from app.services.analysis.source_context import merge_hotwords

            hotwords = merge_hotwords(ctx.task.options.get("hotwords"), ctx.source_context)
            if hotwords and ctx.analysis:
                existing = ctx.analysis.get("proper_nouns", []) or []
                ctx.analysis["proper_nouns"] = list(set(existing + hotwords))
            elif hotwords:
                ctx.analysis = {"proper_nouns": hotwords}
            ctx.polished = await polish_text(ctx.srt, context=ctx.analysis)
            await _raise_if_cancelled(ctx.task.id)
        if not ctx.has_subtitle and ctx.polished:
            from app.services.analysis import srt_to_markdown

            await _write_text_artifact(
                ctx.task, ctx.task_dir, "transcript_polished.srt", ctx.polished
            )
            polished_md_content = srt_to_markdown(ctx.polished, ctx.metadata.title)
            await _write_text_artifact(
                ctx.task, ctx.task_dir, "transcript_polished.md", polished_md_content
            )
            polish_ran = True
        await _update_step(ctx.task, PipelineStep.POLISH, completed=True)
        await _raise_if_cancelled(ctx.task.id)
    # end if POLISH not in done

    # ── Step 5: Analyze + Summarize + Mindmap from polished text ─────────────
    # If an older interrupted task already completed ANALYZE before POLISH,
    # regenerate analysis outputs now so summary/mindmap reflect the polished SRT.
    if PipelineStep.ANALYZE in ctx.done and not polish_ran:
        log_event(
            logger,
            logging.INFO,
            "pipeline.step.skipped",
            step=PipelineStep.ANALYZE,
            reason="already_done",
        )
        ctx.restore_analysis()
        ctx.restore_summary()
        ctx.restore_mindmap()
    else:
        await _update_step(ctx.task, PipelineStep.ANALYZE)
        video_metadata = {
            "uploader": ctx.metadata.uploader,
            "description": ctx.metadata.description,
            "tags": ctx.metadata.tags,
            "chapters": [
                {"title": ch.title, "start_time": ch.start_time} for ch in ctx.metadata.chapters
            ]
            if ctx.metadata.chapters
            else None,
            "source_context": ctx.source_context,
        }
        source_timeline = list(ctx.source_context.get("timeline") or [])
        mindmap_metadata = {
            "title": ctx.metadata.title,
            "uploader": ctx.metadata.uploader,
            "description": ctx.metadata.description,
            "chapters": (
                [{"title": item["title"], "start_time": item["start"]} for item in source_timeline]
                if source_timeline
                else [
                    {"title": ch.title, "start_time": ch.start_time} for ch in ctx.metadata.chapters
                ]
                if ctx.metadata.chapters
                else None
            ),
            "source_context": ctx.source_context,
        }

        analysis_text = _plain_text_from_srt(ctx.polished) if ctx.polished else ctx.transcript
        mindmap_text = ctx.polished or ctx.srt or ctx.transcript

        from app.services.analysis.source_context import (
            canonicalize_text,
            merge_analysis_with_source,
        )

        ctx.analysis = await analyze_content(
            analysis_text, ctx.metadata.title, metadata=video_metadata
        )
        ctx.analysis = merge_analysis_with_source(ctx.analysis, ctx.source_context)
        user_language = _user_language_hint(ctx.analysis)

        import json as _json

        if ctx.analysis:
            await _write_text_artifact(
                ctx.task,
                ctx.task_dir,
                "analysis.json",
                _json.dumps(ctx.analysis, indent=2, ensure_ascii=False),
            )

        tasks = [
            summarize_text(
                analysis_text,
                user_language=user_language,
                source_context=ctx.source_context,
            ),
            generate_mindmap(mindmap_text, metadata=mindmap_metadata, user_language=user_language),
        ]
        if ctx.rt.generate_video_detail:
            tasks.append(generate_detail(mindmap_text, user_language=user_language))
        results = await asyncio.gather(*tasks)
        ctx.summary = results[0]
        ctx.mindmap = results[1]
        ctx.mindmap = canonicalize_text(ctx.mindmap, ctx.source_context)
        from app.services.analysis.text_locale import normalize_chinese_script

        ctx.mindmap = normalize_chinese_script(
            ctx.mindmap,
            user_language,
            source_text=mindmap_text,
        )
        ctx.detail = canonicalize_text(results[2], ctx.source_context) if len(results) > 2 else ""
        await _raise_if_cancelled(ctx.task.id)

        if ctx.summary:
            await _write_summary_files(ctx.task, ctx.task_dir, ctx.metadata, ctx.summary)
        if ctx.mindmap:
            await _write_mindmap_files(ctx.task, ctx.task_dir, ctx.mindmap)
        if ctx.detail:
            await _write_detail_file(ctx.task, ctx.task_dir, ctx.detail)

        await _update_step(ctx.task, PipelineStep.ANALYZE, completed=True)
    # end if ANALYZE not in done

    # Step 6: Archive (finalize — writes any missing files, sets status to completed)
    await _raise_if_cancelled(ctx.task.id)
    await _update_step(ctx.task, PipelineStep.ARCHIVE)
    archive = await archive_result(
        ctx.metadata,
        polished_srt=ctx.polished or "",
        summary=ctx.summary,
        mindmap=ctx.mindmap,
        original_srt=ctx.srt,
        work_dir=ctx.task_dir,
        task_id=str(ctx.task.id),
        analysis=ctx.analysis,
    )

    # Update metadata status to completed
    meta_path = write_metadata_json(ctx.task_dir, ctx.metadata, status="completed")
    await _emit_file_ready(ctx.task, "metadata.json", str(meta_path))

    await _update_step(ctx.task, PipelineStep.ARCHIVE, completed=True)

    ctx.task.result = {
        "metadata": ctx.metadata.model_dump(mode="json"),
        "transcript_segments": len(ctx.recognition_segments),
        "archive": archive,
        "output_dir": str(ctx.task_dir),
        "analysis": ctx.analysis,
        "subtitle_source": ctx.subtitle_source,
    }

    # Async KB indexing (fail-soft)
    _schedule_kb_index(str(ctx.task.id), str(ctx.task_dir))
