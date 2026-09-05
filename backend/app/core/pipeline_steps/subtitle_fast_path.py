"""Subtitle fast path responsibilities for the media pipeline."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.core.logging_setup import log_event
from app.core.pipeline_steps.artifacts import (
    _prepare_source_context,
    _write_detail_file,
    _write_mindmap_files,
    _write_summary_files,
    _write_text_artifact,
)
from app.core.pipeline_steps.state import PipelineStep, _raise_if_cancelled, _update_step
from app.core.pipeline_steps.transcript import (
    _plain_text_from_srt,
    _save_all_tracks_as_transcripts,
    _select_polish_track,
    _user_language_hint,
)
from app.core.settings import get_runtime_settings
from app.models import MediaMetadata, Task

logger = logging.getLogger(__name__)


async def _run_subtitle_fast_path(
    task: Task,
    task_dir: Path,
    platform_subtitle: dict,
    metadata: "MediaMetadata",
) -> dict:
    """Run subtitle processing + LLM analysis — no GPU needed.

    This is the 'Branch A' of the fast path: processes platform subtitles
    through LLM for polish/analysis/summary/mindmap. Runs concurrently with
    video download (Branch B).

    Returns the text-related portion of the task result.
    """
    from app.services.analysis import (
        analyze_content,
        generate_detail,
        generate_mindmap,
        summarize_text,
    )
    from app.services.analysis.source_context import (
        canonicalize_text,
        merge_analysis_with_source,
    )
    from app.services.recognition.subtitle_processor import process_subtitles

    source_context = await _prepare_source_context(task, task_dir, metadata)

    # -- SEPARATE: skip (no audio to separate) --
    await _raise_if_cancelled(task.id)
    await _update_step(task, PipelineStep.SEPARATE, completed=True)

    # -- TRANSCRIBE: process platform subtitle --
    await _update_step(task, PipelineStep.TRANSCRIBE)

    # Multi-track handling: save every track as transcript.{lang}.srt and
    # pick the one matching the video's spoken language for polish.
    tracks = platform_subtitle.get("tracks") or []
    if not tracks and platform_subtitle.get("subtitle_path"):
        # Legacy single-track shape — synthesize a 1-item list
        tracks = [
            {
                "path": platform_subtitle["subtitle_path"],
                "lang": platform_subtitle.get("subtitle_lang") or "unknown",
                "format": platform_subtitle.get("subtitle_format") or "srt",
                "type": "cc",
            }
        ]
    tracks_manifest = _save_all_tracks_as_transcripts(tracks, task_dir)

    selected_track, detected_lang = await _select_polish_track(tracks)
    for entry in tracks_manifest:
        if entry["lang"] == (selected_track.get("lang") or "unknown"):
            entry["polished"] = True
    # Attach tracks + detected language to metadata for archive/UI
    metadata.extra["subtitle_tracks"] = tracks_manifest
    metadata.extra["detected_language"] = detected_lang
    metadata.extra["subtitle_engine"] = platform_subtitle.get("subtitle_engine")
    metadata.extra["subtitle_diagnostics"] = platform_subtitle.get("diagnostics") or []

    sub_result = await process_subtitles(
        subtitle_path=selected_track["path"],
        subtitle_format=selected_track.get("format") or "srt",
        metadata=metadata,
        source_context=source_context,
    )
    await _raise_if_cancelled(task.id)
    transcript = " ".join(s["text"] for s in sub_result.get("segments", []))
    srt = sub_result.get("srt", "")
    polished = sub_result.get("polished_srt", "")
    polished_md = sub_result.get("polished_md", "")
    recognition_segments = sub_result.get("segments", [])

    # Write transcript files
    if srt:
        await _write_text_artifact(task, task_dir, "transcript.srt", srt)
    if polished:
        await _write_text_artifact(task, task_dir, "transcript_polished.srt", polished)
        if polished_md:
            await _write_text_artifact(task, task_dir, "transcript_polished.md", polished_md)

    await _update_step(task, PipelineStep.TRANSCRIBE, completed=True)

    # Guard: skip LLM if transcript is empty
    if not transcript or len(transcript.strip()) < 10:
        log_event(
            logger,
            logging.WARNING,
            "pipeline.llm.skipped",
            reason="fast_path_transcript_too_short",
            chars=len(transcript),
        )
        await _update_step(task, PipelineStep.POLISH, completed=True)
        await _update_step(task, PipelineStep.ANALYZE, completed=True)
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
        return {
            "transcript": transcript,
            "srt": srt,
            "polished": polished,
            "polished_md": polished_md,
            "recognition_segments": recognition_segments,
            "analysis": empty_analysis,
            "summary": empty_summary,
            "mindmap": "",
            "subtitle_source": "platform",
        }

    # -- POLISH: platform subtitle was polished by process_subtitles above. --
    await _update_step(task, PipelineStep.POLISH, completed=True)
    await _raise_if_cancelled(task.id)

    # -- ANALYZE: analyze after polish so summary/mindmap use the cleaned text.
    # Analyze still runs first within this step (cheap, ~8k-char prompt) so the detected
    # language can be injected into the summarize+mindmap prompts. Running
    # analyze serially before the other two adds ~1-2s but prevents the
    # summarize/mindmap steps from collapsing multilingual transcripts into
    # one language.
    await _update_step(task, PipelineStep.ANALYZE)
    video_metadata = {
        "uploader": metadata.uploader,
        "description": metadata.description,
        "tags": metadata.tags,
        "chapters": [{"title": ch.title, "start_time": ch.start_time} for ch in metadata.chapters]
        if metadata.chapters
        else None,
        "source_context": source_context,
    }
    source_timeline = list(source_context.get("timeline") or [])
    mindmap_metadata = {
        "title": metadata.title,
        "uploader": metadata.uploader,
        "description": metadata.description,
        "chapters": (
            [{"title": item["title"], "start_time": item["start"]} for item in source_timeline]
            if source_timeline
            else [{"title": ch.title, "start_time": ch.start_time} for ch in metadata.chapters]
            if metadata.chapters
            else None
        ),
        "source_context": source_context,
    }

    analysis_text = _plain_text_from_srt(polished) if polished else transcript
    mindmap_text = polished or srt or transcript
    rt = get_runtime_settings()

    analysis = await analyze_content(analysis_text, metadata.title, metadata=video_metadata)
    analysis = merge_analysis_with_source(analysis, source_context)
    await _raise_if_cancelled(task.id)
    user_language = _user_language_hint(analysis)

    # Write analysis first so the frontend can surface language/topics early
    import json as _json

    if analysis:
        await _write_text_artifact(
            task, task_dir, "analysis.json", _json.dumps(analysis, indent=2, ensure_ascii=False)
        )

    tasks = [
        summarize_text(
            analysis_text,
            user_language=user_language,
            source_context=source_context,
        ),
        generate_mindmap(mindmap_text, metadata=mindmap_metadata, user_language=user_language),
    ]
    if rt.generate_video_detail:
        tasks.append(generate_detail(mindmap_text, user_language=user_language))
    results = await asyncio.gather(*tasks)
    summary = results[0]
    mindmap = results[1]
    mindmap = canonicalize_text(mindmap, source_context)
    from app.services.analysis.text_locale import normalize_chinese_script

    mindmap = normalize_chinese_script(
        mindmap,
        user_language,
        source_text=mindmap_text,
    )
    detail = canonicalize_text(results[2], source_context) if len(results) > 2 else ""
    await _raise_if_cancelled(task.id)

    if summary:
        await _write_summary_files(task, task_dir, metadata, summary)
    if mindmap:
        await _write_mindmap_files(task, task_dir, mindmap)
    if detail:
        await _write_detail_file(task, task_dir, detail)

    await _update_step(task, PipelineStep.ANALYZE, completed=True)

    return {
        "transcript": transcript,
        "srt": srt,
        "polished": polished,
        "polished_md": polished_md,
        "recognition_segments": recognition_segments,
        "analysis": analysis,
        "summary": summary,
        "mindmap": mindmap,
        "subtitle_source": "platform",
    }
