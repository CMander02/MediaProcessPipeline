"""Download responsibilities for the media pipeline."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path

from app.core.database import get_task_store
from app.core.logging_setup import log_event
from app.core.paths import get_workspace_paths
from app.core.pipeline_steps.artifacts import (
    _emit_file_ready,
    _prepare_source_context,
    _rename_task_dir_to_title,
    _rewrite_ingest_paths_after_task_dir_move,
    _sync_task_from_metadata,
    create_task_dir,
    write_metadata_json,
)
from app.core.pipeline_steps.context import PipelineContext
from app.core.pipeline_steps.notes import (
    _download_note_images_for_download_step,
    _process_image_note,
)
from app.core.pipeline_steps.sources import (
    _download_resolves_url_title,
    _extract_audio_from_video,
    _looks_like_local_path,
    _subtitle_unavailable_message,
)
from app.core.pipeline_steps.state import (
    PipelineStep,
    _emit_timeline_event,
    _raise_if_cancelled,
    _update_flow_from_metadata,
    _update_flow_step,
    _update_step,
)
from app.core.pipeline_steps.subtitle_fast_path import _run_subtitle_fast_path
from app.core.workspace_lifecycle import run_in_thread
from app.models import MediaMetadata

logger = logging.getLogger(__name__)


async def prepare_download(ctx: PipelineContext) -> bool:
    import yt_dlp
    from app.core.queue import get_task_queue
    from app.services.analysis.source_context import source_context_to_analysis
    from app.services.archiving import archive_result
    from app.services.ingestion import download_media
    from app.services.ingestion.local import find_local_subtitle, parse_nfo
    from app.services.ingestion.ytdlp import (
        YoutubeNetworkError,
        download_subtitles,
        ytdlp_auth_opts,
        ytdlp_base_opts,
    )
    from app.services.ingestion.ytdlp import (
        fetch_metadata as fetch_ytdlp_metadata,
    )

    # ── Step 1: DOWNLOAD ───────────────────────────────────────────────────
    if PipelineStep.DOWNLOAD in ctx.done:
        log_event(
            logger,
            logging.INFO,
            "pipeline.step.skipped",
            step=PipelineStep.DOWNLOAD,
            reason="already_done",
        )
        ctx.restore_metadata()
        ctx.restore_audio_paths()
        # Restore has_subtitle + platform_subtitle from disk
        if ctx.task_dir:
            sub_dir = ctx.task_dir / "subtitles"
            if sub_dir.exists():
                for ext in ("*.srt", "*.ass", "*.vtt"):
                    srt_files = list(sub_dir.glob(ext))
                    if srt_files:
                        sub_file = srt_files[0]
                        ctx.platform_subtitle = {
                            "subtitle_path": str(sub_file),
                            "subtitle_lang": "zh",
                            "subtitle_format": sub_file.suffix.lstrip("."),
                        }
                        ctx.has_subtitle = True
                        break

        # Fast-path resume: LLM steps done, just need video + archive
        fast_path_steps = {PipelineStep.TRANSCRIBE, PipelineStep.ANALYZE, PipelineStep.POLISH}
        if fast_path_steps.issubset(ctx.done) and PipelineStep.ARCHIVE not in ctx.done:
            log_event(logger, logging.INFO, "pipeline.fast_path.redownload")
            ingest = await download_media(ctx.source, output_dir=ctx.task_dir)
            await _raise_if_cancelled(ctx.task.id)
            ctx.audio_path = ingest.get("file_path")
            if not ctx.metadata:
                ctx.metadata = MediaMetadata(**ingest.get("metadata", {"title": ctx.source}))
            if ingest.get("video_path"):
                ctx.metadata.file_path = ingest["video_path"]

            # Restore text outputs from disk
            ctx.restore_transcript()
            ctx.restore_analysis()
            ctx.restore_summary()
            ctx.restore_mindmap()

            # Archive
            from app.services.archiving import archive_result

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
            write_metadata_json(ctx.task_dir, ctx.metadata, status="completed")
            await _update_step(ctx.task, PipelineStep.ARCHIVE, completed=True)

            ctx.task.result = {
                "metadata": ctx.metadata.model_dump(mode="json"),
                "transcript_segments": len(ctx.recognition_segments),
                "archive": archive,
                "output_dir": str(ctx.task_dir),
                "analysis": ctx.analysis,
                "subtitle_source": "platform",
            }
            return True
    else:
        await _update_step(ctx.task, PipelineStep.DOWNLOAD)

        if ctx.source.startswith("upload://") or _looks_like_local_path(ctx.task.source):
            # Two sub-cases:
            #  1) upload:// — file already lives inside task_dir (browser upload)
            #  2) local path — file on disk, move it into task_dir
            is_browser_upload = ctx.source.startswith("upload://")

            if is_browser_upload:
                # File is already in task_dir — find it
                upload_name = ctx.source.removeprefix("upload://")
                if ctx.task_dir is None:
                    raise RuntimeError("upload:// source but task_dir is None")
                dest_source = ctx.task_dir / upload_name
                if not dest_source.exists():
                    raise FileNotFoundError(f"上传文件不存在: {dest_source}")
                source_path = dest_source  # for subtitle/nfo search (won't find any — that's fine)
            else:
                source_path = Path(ctx.source)
                if not source_path.exists():
                    raise FileNotFoundError(f"本地文件不存在: {ctx.source}")
                if not source_path.is_file():
                    raise ValueError(f"路径不是文件: {ctx.source}")
                title = source_path.stem
                if not ctx.task_dir:
                    ctx.task_dir = create_task_dir(ctx.task.id, title)
                dest_source = ctx.task_dir / source_path.name
                # If the source lives in the upload staging area, move it
                # (cheap on same volume) and drop the empty staging dir.
                # Otherwise copy so user's original file is preserved.
                is_staged = any(
                    root in source_path.resolve().parents
                    for root in get_workspace_paths().staging_roots
                )
                if is_staged:
                    shutil.move(str(source_path), str(dest_source))
                    try:
                        source_path.parent.rmdir()
                    except OSError:
                        pass
                else:
                    shutil.copy2(str(source_path), str(dest_source))

            from app.core.media_retention import record_media

            record_media(
                ctx.task_dir, dest_source, "source", playback=True, regenerate_from=source_path
            )
            title = dest_source.stem
            video_exts = {".mp4", ".mkv", ".avi", ".webm", ".mov"}
            audio_exts = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".opus", ".wma"}

            if dest_source.suffix.lower() in video_exts:
                ctx.audio_path = ctx.task_dir / f"{title}.wav"
                await run_in_thread(_extract_audio_from_video, dest_source, ctx.audio_path)
                record_media(ctx.task_dir, ctx.audio_path, "working", regenerate_from=dest_source)
                await _raise_if_cancelled(ctx.task.id)
                ctx.audio_path = str(ctx.audio_path)
                ctx.metadata = MediaMetadata(
                    title=title,
                    source_url=str(source_path),
                    media_type="video",
                    platform="local",
                    content_subtype="video",
                    file_path=str(dest_source),
                )

                # Search for local subtitle and NFO metadata
                # For browser uploads source_path == dest_source (no original dir to search)
                if not is_browser_upload and ctx.use_platform_subtitles:
                    ctx.platform_subtitle = find_local_subtitle(source_path)
                    if ctx.platform_subtitle:
                        log_event(
                            logger,
                            logging.INFO,
                            "subtitle.local.found",
                            path=ctx.platform_subtitle["subtitle_path"],
                        )

                if not is_browser_upload:
                    nfo_meta = parse_nfo(source_path)
                    if nfo_meta:
                        if nfo_meta.get("title"):
                            ctx.metadata.title = nfo_meta["title"]
                        if nfo_meta.get("description"):
                            ctx.metadata.description = nfo_meta["description"]
                        if nfo_meta.get("tags"):
                            ctx.metadata.tags = nfo_meta["tags"]
                        if nfo_meta.get("uploader"):
                            ctx.metadata.uploader = nfo_meta["uploader"]
                        if nfo_meta.get("upload_date"):
                            ctx.metadata.upload_date = nfo_meta["upload_date"]
                        if nfo_meta.get("source_url"):
                            ctx.metadata.source_url = nfo_meta["source_url"]
                            # Try to infer platform from NFO source_url
                            su = nfo_meta["source_url"]
                            if "bilibili.com" in su:
                                ctx.metadata.platform = "bilibili_video"
                            elif "youtube.com" in su or "youtu.be" in su:
                                ctx.metadata.platform = "youtube"

            elif dest_source.suffix.lower() in audio_exts:
                ctx.audio_path = str(dest_source)
                ctx.metadata = MediaMetadata(
                    title=title,
                    source_url=str(source_path),
                    media_type="audio",
                    platform="local",
                    content_subtype="audio",
                    file_path=str(dest_source),
                )
            else:
                raise ValueError(f"Unsupported file format: {dest_source.suffix}")

            ctx.has_subtitle = ctx.platform_subtitle is not None

            # Write metadata.json immediately after local file processing
            _sync_task_from_metadata(ctx.task, ctx.metadata)
            meta_path = write_metadata_json(ctx.task_dir, ctx.metadata, status="processing")
            await _emit_file_ready(ctx.task, "metadata.json", str(meta_path))

        else:
            # ── URL source: probe metadata + subtitle first ──
            # 1. Resolve title for task_dir naming
            if ctx.route_type in {"bilibili", "bilibili_video"}:
                bv_match = re.search(r"(BV[0-9A-Za-z]+)", ctx.source)
                title = bv_match.group(1) if bv_match else None
            elif ctx.route_type == "youtube":
                yt_match = re.search(r"(?:v=|youtu\.be/)([\w-]{11})", ctx.source)
                title = yt_match.group(1) if yt_match else None
                if not title:
                    ydl_opts = {"quiet": True, **ytdlp_base_opts(), **ytdlp_auth_opts()}
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(ctx.source, download=False)
                        title = info.get("title", "unknown") if info else "unknown"
            elif _download_resolves_url_title(ctx.route_type):
                # Title will be resolved during the actual download step; use task id as placeholder.
                title = None
            else:
                ydl_opts = {"quiet": True, **ytdlp_base_opts(), **ytdlp_auth_opts()}
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(ctx.source, download=False)
                    title = info.get("title", "unknown") if info else "unknown"

            if not ctx.task_dir:
                ctx.task_dir = create_task_dir(ctx.task.id, title or str(ctx.task.id))

            if (
                ctx.use_platform_subtitles
                and not ctx.force_asr
                and not _download_resolves_url_title(ctx.route_type)
            ):
                await _update_flow_step(ctx.task, "subtitle_probe", message="探测平台字幕")
                # Probe: fetch metadata + subtitle (lightweight, no video download)
                try:
                    probe_metadata = await fetch_ytdlp_metadata(ctx.source)
                except YoutubeNetworkError:
                    raise
                except Exception as e:
                    log_event(
                        logger,
                        logging.WARNING,
                        "metadata.probe.failed",
                        error=e,
                        fallback="full_pipeline",
                    )
                    probe_metadata = None

                probe_subtitle = None
                if probe_metadata:
                    try:
                        sub_dir = ctx.task_dir / "subtitles"
                        probe_subtitle = await download_subtitles(ctx.source, sub_dir)
                        if not probe_subtitle or not probe_subtitle.get("subtitle_path"):
                            if probe_metadata and probe_subtitle:
                                probe_metadata.extra["subtitle_engine"] = probe_subtitle.get(
                                    "subtitle_engine"
                                )
                                probe_metadata.extra["subtitle_diagnostics"] = (
                                    probe_subtitle.get("diagnostics") or []
                                )
                            probe_subtitle = None
                            if sub_dir.exists() and not any(sub_dir.iterdir()):
                                sub_dir.rmdir()
                    except Exception as e:
                        if isinstance(e, YoutubeNetworkError):
                            raise
                        log_event(logger, logging.WARNING, "subtitle.probe.failed", error=e)
                        probe_subtitle = None

                if probe_metadata and not probe_subtitle:
                    subtitle_unavailable_message = _subtitle_unavailable_message(probe_metadata)
                    await _emit_timeline_event(
                        ctx.task,
                        "subtitle.missing",
                        stage="subtitle",
                        step_id="subtitle_probe",
                        level="warning",
                        message=subtitle_unavailable_message,
                        data={
                            "diagnostics": getattr(probe_metadata, "extra", {}).get(
                                "subtitle_diagnostics", []
                            )
                        },
                    )
                    await _update_flow_step(
                        ctx.task,
                        "subtitle_probe",
                        completed=True,
                        level="warning",
                        message=subtitle_unavailable_message,
                    )

                if probe_metadata and probe_subtitle:
                    await _update_flow_step(
                        ctx.task, "subtitle_probe", completed=True, message="平台字幕可用"
                    )
                    # ── FAST PATH: subtitle + video download in parallel ──
                    log_event(
                        logger,
                        logging.INFO,
                        "pipeline.fast_path.started",
                        subtitle_path=probe_subtitle.get("subtitle_path"),
                    )
                    ctx.metadata = probe_metadata
                    ctx.source_flow = await _update_flow_from_metadata(
                        ctx.task,
                        ctx.source_flow,
                        ctx.metadata,
                        has_subtitle=True,
                        force_asr=ctx.force_asr,
                        current_step="subtitle_probe",
                    )

                    # Rename task_dir to real title
                    real_title = ctx.metadata.title
                    ctx.task_dir, old_dir = _rename_task_dir_to_title(ctx.task_dir, real_title)
                    if old_dir:
                        # Update all subtitle paths after rename: tracks[].path + back-compat subtitle_path
                        new_sub_dir = ctx.task_dir / "subtitles"
                        for tr in probe_subtitle.get("tracks") or []:
                            if tr.get("path"):
                                tr["path"] = str(new_sub_dir / Path(tr["path"]).name)
                        if probe_subtitle.get("subtitle_path"):
                            old_sub_path = Path(probe_subtitle["subtitle_path"])
                            probe_subtitle["subtitle_path"] = str(new_sub_dir / old_sub_path.name)
                        log_event(
                            logger,
                            logging.INFO,
                            "task_dir.renamed",
                            from_path=old_dir,
                            path=ctx.task_dir,
                        )

                    log_event(
                        logger,
                        logging.INFO,
                        "subtitle.downloaded",
                        path=probe_subtitle["subtitle_path"],
                        engine=probe_subtitle.get("subtitle_engine"),
                    )

                    # Write metadata.json
                    _sync_task_from_metadata(ctx.task, ctx.metadata)
                    meta_path = write_metadata_json(ctx.task_dir, ctx.metadata, status="processing")
                    await _emit_file_ready(ctx.task, "metadata.json", str(meta_path))

                    await _update_step(ctx.task, PipelineStep.DOWNLOAD, completed=True)

                    # Persist output_dir so resume can find task_dir
                    ctx.task.result = {"output_dir": str(ctx.task_dir)}
                    store = get_task_store()
                    store.update_status(ctx.task.id, ctx.task.status, result=ctx.task.result)

                    # Fork: Branch A (subtitle→LLM) + Branch B (video download)
                    async def _branch_video_download():
                        ingest = await download_media(ctx.source, output_dir=ctx.task_dir)
                        await _raise_if_cancelled(ctx.task.id)
                        ctx.audio_path = ingest.get("file_path")
                        # Update metadata with file paths from download
                        if ingest.get("video_path"):
                            ctx.metadata.file_path = ingest["video_path"]
                        write_metadata_json(ctx.task_dir, ctx.metadata, status="processing")

                    results = await asyncio.gather(
                        _run_subtitle_fast_path(
                            ctx.task, ctx.task_dir, probe_subtitle, ctx.metadata
                        ),
                        _branch_video_download(),
                        return_exceptions=True,
                    )
                    await _raise_if_cancelled(ctx.task.id)
                    text_result, video_result = results

                    # Text branch is the core output — if it fails, the task fails.
                    if isinstance(text_result, BaseException):
                        raise text_result
                    # Video branch is auxiliary: log but don't fail the task.
                    # The transcript/summary/mindmap are already produced.
                    if isinstance(video_result, BaseException):
                        log_event(
                            logger,
                            logging.WARNING,
                            "pipeline.fast_path.video_failed",
                            error_type=type(video_result).__name__,
                            error=video_result,
                        )
                        ctx.metadata.file_path = None

                    # Archive
                    await _raise_if_cancelled(ctx.task.id)
                    await _update_step(ctx.task, PipelineStep.ARCHIVE)
                    archive = await archive_result(
                        ctx.metadata,
                        polished_srt=text_result.get("polished", ""),
                        summary=text_result.get("summary", {}),
                        mindmap=text_result.get("mindmap", ""),
                        original_srt=text_result.get("srt", ""),
                        work_dir=ctx.task_dir,
                        task_id=str(ctx.task.id),
                        analysis=text_result.get("analysis", {}),
                    )
                    write_metadata_json(ctx.task_dir, ctx.metadata, status="completed")
                    await _update_step(ctx.task, PipelineStep.ARCHIVE, completed=True)

                    ctx.task.result = {
                        "metadata": ctx.metadata.model_dump(mode="json"),
                        "transcript_segments": len(text_result.get("recognition_segments", [])),
                        "archive": archive,
                        "output_dir": str(ctx.task_dir),
                        "analysis": text_result.get("analysis"),
                        "subtitle_source": "platform",
                    }
                    return True  # Done — skip the rest of run_pipeline

            # ── FULL PIPELINE: no subtitle or force_asr ──
            # (existing code path, unchanged)
            ingest = await download_media(ctx.source, output_dir=ctx.task_dir)
            await _raise_if_cancelled(ctx.task.id)
            ctx.audio_path = ingest.get("file_path")
            ctx.metadata = MediaMetadata(**ingest.get("metadata", {"title": ctx.source}))
            if ingest.get("video_path"):
                ctx.metadata.file_path = ingest["video_path"]

            # Rename task_dir from temp name (BV号/video ID) to real title
            real_title = ctx.metadata.title
            ctx.task_dir, old_dir = _rename_task_dir_to_title(ctx.task_dir, real_title)
            if old_dir:
                if ctx.audio_path:
                    ctx.audio_path = str(ctx.task_dir / Path(ctx.audio_path).name)
                if ctx.metadata.file_path:
                    ctx.metadata.file_path = str(ctx.task_dir / Path(ctx.metadata.file_path).name)
                _rewrite_ingest_paths_after_task_dir_move(
                    ingest, ctx.metadata, old_dir, ctx.task_dir
                )
                log_event(
                    logger, logging.INFO, "task_dir.renamed", from_path=old_dir, path=ctx.task_dir
                )

            # Notes take a different branch entirely — no GPU, no audio.
            if ctx.metadata.content_subtype in {"image_note", "text_note"}:
                ingest_info = ingest.get("info") or ingest
                ctx.source_flow = await _update_flow_from_metadata(
                    ctx.task,
                    ctx.source_flow,
                    ctx.metadata,
                    force_asr=ctx.force_asr,
                    current_step="download",
                )
                _sync_task_from_metadata(ctx.task, ctx.metadata)
                meta_path = write_metadata_json(ctx.task_dir, ctx.metadata, status="processing")
                await _emit_file_ready(ctx.task, "metadata.json", str(meta_path))

                existing_result = dict(ctx.task.result or {})
                existing_result["output_dir"] = str(ctx.task_dir)
                existing_result["_image_ingest_info"] = ingest_info
                ctx.task.result = existing_result
                get_task_store().update_status(ctx.task.id, ctx.task.status, result=ctx.task.result)

                if ctx.metadata.content_subtype == "image_note":
                    await _download_note_images_for_download_step(
                        ctx.task, ctx.metadata, ctx.task_dir, ingest_info
                    )
                    meta_path = write_metadata_json(ctx.task_dir, ctx.metadata, status="processing")
                    await _emit_file_ready(ctx.task, "metadata.json", str(meta_path))

                await _update_step(ctx.task, PipelineStep.DOWNLOAD, completed=True)
                await _raise_if_cancelled(ctx.task.id)
                if ctx.download_worker_call:
                    await get_task_queue().advance_to_gpu(ctx.task.id)
                    return True
                await _process_image_note(ctx.task, ctx.metadata, ctx.task_dir, ingest_info)
                return True

            # Try to download platform subtitles (for full pipeline, still useful)
            if ctx.use_platform_subtitles:
                await _update_flow_step(ctx.task, "subtitle_probe", message="探测平台字幕")
                try:
                    sub_dir = ctx.task_dir / "subtitles"
                    ctx.platform_subtitle = await download_subtitles(ctx.source, sub_dir)
                    if ctx.platform_subtitle.get("subtitle_path"):
                        log_event(
                            logger,
                            logging.INFO,
                            "subtitle.downloaded",
                            path=ctx.platform_subtitle["subtitle_path"],
                            engine=ctx.platform_subtitle.get("subtitle_engine"),
                        )
                    else:
                        ctx.metadata.extra["subtitle_engine"] = ctx.platform_subtitle.get(
                            "subtitle_engine"
                        )
                        ctx.metadata.extra["subtitle_diagnostics"] = (
                            ctx.platform_subtitle.get("diagnostics") or []
                        )
                        ctx.platform_subtitle = None
                        if sub_dir.exists() and not any(sub_dir.iterdir()):
                            sub_dir.rmdir()
                except Exception as e:
                    log_event(logger, logging.WARNING, "subtitle.download_failed", error=e)
                    ctx.platform_subtitle = None

            ctx.has_subtitle = ctx.platform_subtitle is not None
            if ctx.use_platform_subtitles:
                subtitle_unavailable_message = _subtitle_unavailable_message(ctx.metadata)
                await _update_flow_step(
                    ctx.task,
                    "subtitle_probe",
                    completed=True,
                    level="info" if ctx.has_subtitle else "warning",
                    message="平台字幕可用" if ctx.has_subtitle else subtitle_unavailable_message,
                )
            ctx.source_flow = await _update_flow_from_metadata(
                ctx.task,
                ctx.source_flow,
                ctx.metadata,
                has_subtitle=ctx.has_subtitle,
                force_asr=ctx.force_asr,
                current_step="download",
            )
            if ctx.use_platform_subtitles and not ctx.has_subtitle:
                await _emit_timeline_event(
                    ctx.task,
                    "subtitle.missing",
                    stage="subtitle",
                    step_id="subtitle_probe",
                    level="warning",
                    message=subtitle_unavailable_message,
                    data={"diagnostics": ctx.metadata.extra.get("subtitle_diagnostics", [])},
                )

            # Write metadata.json immediately after download
            _sync_task_from_metadata(ctx.task, ctx.metadata)
            meta_path = write_metadata_json(ctx.task_dir, ctx.metadata, status="processing")
            await _emit_file_ready(ctx.task, "metadata.json", str(meta_path))

        await _update_step(ctx.task, PipelineStep.DOWNLOAD, completed=True)
        await _raise_if_cancelled(ctx.task.id)
    # end if DOWNLOAD not in done

    # Sanity: we must have a task_dir by now
    if ctx.task_dir is None or ctx.metadata is None:
        raise RuntimeError("task_dir or metadata missing after DOWNLOAD step — cannot continue")

    # Note GPU-worker re-entry: DOWNLOAD is done, route directly to note branch.
    if ctx.metadata.content_subtype in {"image_note", "text_note"} and not ctx.download_worker_call:
        ctx.source_flow = await _update_flow_from_metadata(
            ctx.task,
            ctx.source_flow,
            ctx.metadata,
            force_asr=ctx.force_asr,
            current_step="download",
        )
        _sync_task_from_metadata(ctx.task, ctx.metadata)
        ingest_info = (ctx.task.result or {}).get("_image_ingest_info") or {
            "extra": ctx.metadata.extra or {}
        }
        await _process_image_note(ctx.task, ctx.metadata, ctx.task_dir, ingest_info)
        return True

    ctx.source_context = await _prepare_source_context(ctx.task, ctx.task_dir, ctx.metadata)
    from app.services.analysis.source_context import source_context_to_analysis

    ctx.analysis = source_context_to_analysis(ctx.source_context)

    # Hand off to GPU queue if we were called from a download worker.
    # The GPU worker will call process_task again; at that point DOWNLOAD is
    # in completed_steps so this block is skipped and we continue below.
    if ctx.download_worker_call:
        # Persist output_dir so the GPU worker can restore task_dir from DB
        # (task_dir may have been renamed after download, so we must save the
        # current — possibly renamed — path before handing off).
        ctx.task.result = {"output_dir": str(ctx.task_dir)}
        store = get_task_store()
        store.update_status(ctx.task.id, ctx.task.status, result=ctx.task.result)
        await get_task_queue().advance_to_gpu(ctx.task.id)
        return True

    return False
