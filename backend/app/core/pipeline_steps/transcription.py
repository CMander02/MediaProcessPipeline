"""Transcription responsibilities for the media pipeline."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from pathlib import Path
from typing import Any

from app.core.logging_setup import log_event
from app.core.pipeline_steps.artifacts import _write_speaker_map, _write_text_artifact
from app.core.pipeline_steps.context import PipelineContext
from app.core.pipeline_steps.state import (
    PipelineStep,
    _emit_timeline_event,
    _raise_if_cancelled,
    _select_asr_provider_for_fallback,
    _update_flow_from_metadata,
    _update_step,
    _update_step_progress,
)
from app.core.pipeline_steps.transcript import (
    _extract_internal_asr_error,
    _is_transcript_too_short_for_uvr_fallback,
    _render_recognition_srt,
    _require_audio_file,
    _save_all_tracks_as_transcripts,
    _select_polish_track,
)
from app.core.workspace_lifecycle import run_in_thread
from app.models import Task

logger = logging.getLogger(__name__)


def _release_uvr_gpu_resources() -> None:
    """Unload UVR before ASR/local LLM steps that need the same GPU memory."""
    import gc

    try:
        from app.services.preprocessing.uvr import release_uvr_service

        release_uvr_service()
        log_event(logger, logging.INFO, "gpu.uvr.release")
    except Exception as e:
        log_event(logger, logging.WARNING, "gpu.uvr.release_failed", error=e)

    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            log_event(logger, logging.INFO, "gpu.cuda_cache.cleared")
    except Exception as e:
        log_event(logger, logging.WARNING, "gpu.cuda_cache.clear_failed", error=e)

    try:
        gc.collect()
        log_event(logger, logging.INFO, "runtime.gc.collected")
    except Exception:
        pass


async def _run_voiceprint_step(
    task: Task,
    recognition_segments: list,
    task_dir: Path,
    source_context: dict[str, Any] | None = None,
) -> list:
    """Extract speaker embeddings, match against library, rewrite segment speakers.

    Gracefully no-ops when:
      - voiceprint disabled in settings
      - no diarization was run (no speaker labels present)
      - ASR service didn't cache a diarize_df (e.g. platform subtitle path)
    """
    from app.core.settings import get_runtime_settings

    rt = get_runtime_settings()
    if not getattr(rt, "enable_voiceprint", True):
        return recognition_segments
    if not recognition_segments:
        return recognition_segments

    # Only run if diarization produced speaker labels
    has_speakers = any(s.get("speaker") for s in recognition_segments)
    if not has_speakers:
        log_event(logger, logging.INFO, "voiceprint.skipped", reason="no_speaker_labels")
        return recognition_segments

    from app.services.recognition import get_diarization_service

    service = get_diarization_service()
    pipeline_obj = service.get_pyannote_pipeline()
    if pipeline_obj is None:
        log_event(logger, logging.INFO, "voiceprint.skipped", reason="pyannote_not_loaded")
        return recognition_segments

    diarize_df, audio_path = service.get_last_diarization()
    if diarize_df is None or audio_path is None:
        log_event(logger, logging.INFO, "voiceprint.skipped", reason="no_cached_diarization")
        return recognition_segments

    from app.services.voiceprint import get_voiceprint_store
    from app.services.voiceprint.extractor import extract_voiceprints
    from app.services.voiceprint.matcher import apply_to_segments, resolve_speakers

    store = get_voiceprint_store()
    clips_dir = store.clips_dir

    try:
        voiceprints = extract_voiceprints(
            audio_path=audio_path,
            diarize_df=diarize_df,
            pyannote_pipeline=pipeline_obj,
            clips_dir=clips_dir,
            sample_id_prefix=f"{task.id}_",
        )
    except Exception as exc:
        log_event(logger, logging.WARNING, "voiceprint.extraction_failed", error=exc)
        return recognition_segments
    if not voiceprints:
        log_event(logger, logging.INFO, "voiceprint.skipped", reason="no_voiceprints")
        return recognition_segments

    try:
        resolutions = resolve_speakers(
            task_id=str(task.id),
            voiceprints=voiceprints,
            store=store,
            match_threshold=float(getattr(rt, "voiceprint_match_threshold", 0.75)),
            suggest_threshold=float(getattr(rt, "voiceprint_suggest_threshold", 0.60)),
        )
    except Exception as exc:
        log_event(logger, logging.WARNING, "voiceprint.resolution_failed", error=exc)
        return recognition_segments
    await _write_speaker_map(
        task,
        task_dir,
        recognition_segments,
        source_context,
        resolutions=resolutions,
    )
    recognition_segments = apply_to_segments(recognition_segments, resolutions)
    log_event(logger, logging.INFO, "voiceprint.resolved", speakers=len(resolutions))
    return recognition_segments


async def transcribe(ctx: PipelineContext) -> None:
    from app.core.queue import get_task_queue
    from app.services.preprocessing import separate_vocals
    from app.services.recognition import transcribe_audio
    from app.services.recognition.subtitle_processor import process_subtitles

    # ── Steps 2+3: SEPARATE + TRANSCRIBE — GPU-bound, serialised by semaphore ──
    gpu_sem = get_task_queue().gpu_semaphore

    if PipelineStep.SEPARATE in ctx.done and PipelineStep.TRANSCRIBE in ctx.done:
        log_event(
            logger,
            logging.INFO,
            "pipeline.step.skipped",
            step="separate,transcribe",
            reason="already_done",
        )
        ctx.restore_transcript()
        ctx.restore_audio_paths()
    else:
        async with gpu_sem:
            log_event(logger, logging.INFO, "gpu.semaphore.acquired")

            # Step 2: Separate vocals
            if PipelineStep.SEPARATE in ctx.done:
                log_event(
                    logger,
                    logging.INFO,
                    "pipeline.step.skipped",
                    step=PipelineStep.SEPARATE,
                    reason="already_done",
                )
                ctx.restore_audio_paths()
            else:
                await _update_step(ctx.task, PipelineStep.SEPARATE)
                skip_separation = (
                    ctx.task.options.get("skip_separation", False)
                    or ctx.task.options.get("api_flow", False)
                    or ctx.has_subtitle
                    or not ctx.source_flow.requires_uvr
                )
                if skip_separation:
                    ctx.vocals_path = ctx.audio_path
                    if not ctx.has_subtitle:
                        ctx.uvr_fallback_reason = "uvr.skipped"
                        await _emit_timeline_event(
                            ctx.task,
                            ctx.uvr_fallback_reason,
                            stage="uvr",
                            step_id="separate",
                            level="warning",
                            message="已跳过 UVR，人声分离不参与本次转录",
                            data={
                                "skip_separation": bool(
                                    ctx.task.options.get("skip_separation", False)
                                ),
                                "api_flow": bool(ctx.task.options.get("api_flow", False)),
                                "requires_uvr": ctx.source_flow.requires_uvr,
                            },
                        )
                else:
                    source_audio = _require_audio_file(ctx.audio_path, stage="UVR separation")
                    try:
                        try:
                            loop = asyncio.get_running_loop()
                            progress_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
                            progress_active = threading.Event()
                            progress_active.set()

                            def _on_uvr_progress(update: dict[str, Any]) -> None:
                                if progress_active.is_set():
                                    loop.call_soon_threadsafe(
                                        progress_queue.put_nowait, dict(update)
                                    )

                            async def _consume_uvr_progress() -> None:
                                while True:
                                    update = await progress_queue.get()
                                    if update is None:
                                        return
                                    try:
                                        await _update_step_progress(
                                            ctx.task,
                                            PipelineStep.SEPARATE,
                                            float(update.get("progress") or 0.0),
                                            str(update.get("message") or "分离人声"),
                                            details={
                                                key: value
                                                for key, value in update.items()
                                                if key not in {"progress", "message"}
                                            },
                                        )
                                    except Exception as progress_error:
                                        log_event(
                                            logger,
                                            logging.WARNING,
                                            "uvr.progress.update_failed",
                                            error=progress_error,
                                        )

                            progress_consumer = asyncio.create_task(_consume_uvr_progress())
                            try:
                                preprocess = await separate_vocals(
                                    source_audio,
                                    output_dir=ctx.task_dir,
                                    progress_callback=_on_uvr_progress,
                                )
                                # Thread-safe queue callbacks are scheduled before the worker
                                # completes; yield once so the consumer sees the final update.
                                await asyncio.sleep(0)
                            finally:
                                progress_active.clear()
                                await progress_queue.put(None)
                                await progress_consumer
                        except Exception as e:
                            log_event(
                                logger,
                                logging.WARNING,
                                "uvr.separation.failed_fallback",
                                error=e,
                                fallback="original_audio",
                            )
                            ctx.metadata.extra["uvr_error"] = str(e)
                            ctx.metadata.extra["uvr_fallback"] = "original_audio"
                            ctx.vocals_path = source_audio
                            ctx.uvr_fallback_reason = "uvr.failed"
                            await _emit_timeline_event(
                                ctx.task,
                                ctx.uvr_fallback_reason,
                                stage="uvr",
                                step_id="separate",
                                level="warning",
                                message="UVR 处理失败，转录将使用原始音频",
                                data={"error": str(e)},
                            )
                        else:
                            ctx.vocals_path = preprocess.get("vocals_path") or source_audio
                            ctx.vocals_path = _require_audio_file(
                                ctx.vocals_path,
                                stage="UVR separation output",
                            )
                            if preprocess.get("model_used") == "mock" or Path(
                                ctx.vocals_path
                            ) == Path(source_audio):
                                ctx.uvr_fallback_reason = "uvr.unavailable"
                                await _emit_timeline_event(
                                    ctx.task,
                                    ctx.uvr_fallback_reason,
                                    stage="uvr",
                                    step_id="separate",
                                    level="warning",
                                    message="UVR 当前不可用，转录将使用原始音频",
                                    data={"model_used": preprocess.get("model_used")},
                                )
                    finally:
                        await run_in_thread(_release_uvr_gpu_resources)
                    await _raise_if_cancelled(ctx.task.id)
                await _update_step(ctx.task, PipelineStep.SEPARATE, completed=True)

            # Step 3: Transcribe
            if PipelineStep.TRANSCRIBE in ctx.done:
                log_event(
                    logger,
                    logging.INFO,
                    "pipeline.step.skipped",
                    step=PipelineStep.TRANSCRIBE,
                    reason="already_done",
                )
                ctx.restore_transcript()
            else:
                await _update_step(ctx.task, PipelineStep.TRANSCRIBE)
                if ctx.has_subtitle:
                    log_event(logger, logging.INFO, "asr.skipped", reason="platform_subtitle")
                    pst_tracks = ctx.platform_subtitle.get("tracks") or []
                    if not pst_tracks and ctx.platform_subtitle.get("subtitle_path"):
                        pst_tracks = [
                            {
                                "path": ctx.platform_subtitle["subtitle_path"],
                                "lang": ctx.platform_subtitle.get("subtitle_lang") or "unknown",
                                "format": ctx.platform_subtitle.get("subtitle_format") or "srt",
                                "type": "cc",
                            }
                        ]
                    tracks_manifest = _save_all_tracks_as_transcripts(pst_tracks, ctx.task_dir)
                    selected_track, detected_lang = await _select_polish_track(pst_tracks)
                    for entry in tracks_manifest:
                        if entry["lang"] == (selected_track.get("lang") or "unknown"):
                            entry["polished"] = True
                    ctx.metadata.extra["subtitle_tracks"] = tracks_manifest
                    ctx.metadata.extra["detected_language"] = detected_lang
                    ctx.metadata.extra["subtitle_engine"] = ctx.platform_subtitle.get(
                        "subtitle_engine"
                    )
                    ctx.metadata.extra["subtitle_diagnostics"] = (
                        ctx.platform_subtitle.get("diagnostics") or []
                    )

                    sub_result = await process_subtitles(
                        subtitle_path=selected_track["path"],
                        subtitle_format=selected_track.get("format") or "srt",
                        metadata=ctx.metadata,
                        source_context=ctx.source_context,
                    )
                    ctx.transcript = " ".join(s["text"] for s in sub_result.get("segments", []))
                    ctx.srt = sub_result.get("srt", "")
                    ctx.polished = sub_result.get("polished_srt", "")
                    ctx.polished_md = sub_result.get("polished_md", "")
                    ctx.subtitle_source = "platform"
                    ctx.recognition_segments = sub_result.get("segments", [])
                else:
                    from app.services.analysis.source_context import (
                        merge_hotwords,
                        speaker_constraints,
                    )

                    num_speakers, min_speakers, max_speakers = speaker_constraints(
                        ctx.source_context,
                        ctx.task.options,
                    )
                    asr_hotwords = merge_hotwords(
                        ctx.task.options.get("hotwords"),
                        ctx.source_context,
                    )
                    asr_provider = ctx.task.options.get("asr_provider")
                    if ctx.task.options.get("api_flow", False) and not asr_provider:
                        asr_provider = "siliconflow"
                    asr_selection_reason = "task_option" if asr_provider else "settings"
                    if (
                        ctx.source_type == "url"
                        and ctx.uvr_fallback_reason
                        and ctx.source_flow.flow_id in {"url_media_asr", "url_platform_video_asr"}
                    ):
                        selected_provider, asr_selection_reason, selected_api = (
                            (
                                str(asr_provider),
                                asr_selection_reason,
                                str(asr_provider) == "siliconflow",
                            )
                            if asr_provider
                            else _select_asr_provider_for_fallback(ctx.task)
                        )
                        asr_provider = selected_provider
                        if selected_api:
                            ctx.source_flow = await _update_flow_from_metadata(
                                ctx.task,
                                ctx.source_flow,
                                ctx.metadata,
                                force_asr=ctx.force_asr,
                                api_fallback=True,
                                current_step="transcribe",
                                preferred_asr_provider=asr_provider,
                            )
                            await _emit_timeline_event(
                                ctx.task,
                                "asr.api_fallback.selected",
                                stage="asr",
                                step_id="transcribe",
                                level="info",
                                message="已选择 API ASR fallback",
                                data={
                                    "provider": asr_provider,
                                    "reason": asr_selection_reason,
                                    "uvr_reason": ctx.uvr_fallback_reason,
                                },
                            )
                        else:
                            await _emit_timeline_event(
                                ctx.task,
                                "diagnostic",
                                stage="asr",
                                step_id="transcribe",
                                level="info",
                                message="继续使用当前默认 ASR",
                                data={
                                    "provider": asr_provider,
                                    "reason": asr_selection_reason,
                                    "uvr_reason": ctx.uvr_fallback_reason,
                                },
                            )
                    asr_audio_path = _require_audio_file(ctx.vocals_path, stage="ASR transcription")
                    await _emit_timeline_event(
                        ctx.task,
                        "asr.started",
                        stage="asr",
                        step_id="transcribe",
                        level="info",
                        message="开始 ASR 转录",
                        data={
                            "provider": asr_provider or "settings",
                            "selection_reason": asr_selection_reason,
                        },
                    )

                    async def _transcribe_selected_audio(
                        selected_audio_path: str,
                    ) -> dict[str, Any]:
                        loop = asyncio.get_running_loop()
                        progress_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
                        progress_active = threading.Event()
                        progress_active.set()

                        def _on_asr_progress(update: dict[str, Any]) -> None:
                            if progress_active.is_set():
                                loop.call_soon_threadsafe(
                                    progress_queue.put_nowait,
                                    dict(update),
                                )

                        async def _consume_asr_progress() -> None:
                            while True:
                                update = await progress_queue.get()
                                if update is None:
                                    return
                                try:
                                    await _update_step_progress(
                                        ctx.task,
                                        PipelineStep.TRANSCRIBE,
                                        float(update.get("progress") or 0.0),
                                        str(update.get("message") or "转录音频"),
                                        details={
                                            key: value
                                            for key, value in update.items()
                                            if key not in {"progress", "message"}
                                        },
                                    )
                                except Exception as progress_error:
                                    log_event(
                                        logger,
                                        logging.WARNING,
                                        "asr.progress.update_failed",
                                        error=progress_error,
                                    )

                        progress_consumer = asyncio.create_task(_consume_asr_progress())
                        try:
                            result = await transcribe_audio(
                                selected_audio_path,
                                output_dir=ctx.task_dir,
                                num_speakers=num_speakers,
                                min_speakers=min_speakers,
                                max_speakers=max_speakers,
                                provider=asr_provider,
                                model=ctx.task.options.get("asr_model"),
                                diarize=not ctx.task.options.get("disable_diarization", False),
                                chunk_strategy=ctx.task.options.get("asr_chunk_strategy"),
                                timestamp_mode=ctx.task.options.get("asr_timestamp_mode"),
                                hotwords=asr_hotwords,
                                audio_processing_flow=ctx.task.options.get("audio_processing_flow"),
                                diarization_audio_path=ctx.audio_path,
                                progress_callback=_on_asr_progress,
                            )
                            await asyncio.sleep(0)
                            return result
                        finally:
                            progress_active.clear()
                            await progress_queue.put(None)
                            await progress_consumer

                    try:
                        recognition = await _transcribe_selected_audio(asr_audio_path)
                    except Exception as e:
                        await _emit_timeline_event(
                            ctx.task,
                            "asr.failed",
                            stage="asr",
                            step_id="transcribe",
                            level="error",
                            message="ASR 转录失败",
                            data={"provider": asr_provider or "settings", "error": str(e)},
                        )
                        raise
                    await _raise_if_cancelled(ctx.task.id)
                    ctx.transcript = " ".join(s["text"] for s in recognition.get("segments", []))
                    if Path(asr_audio_path) != Path(
                        ctx.audio_path
                    ) and _is_transcript_too_short_for_uvr_fallback(ctx.transcript):
                        original_asr_audio = _require_audio_file(
                            ctx.audio_path,
                            stage="ASR fallback original audio",
                        )
                        ctx.metadata.extra["uvr_fallback"] = "asr_too_short_original_audio"
                        ctx.metadata.extra["uvr_transcript_chars"] = len(
                            re.sub(r"\s+", "", ctx.transcript or "")
                        )
                        ctx.uvr_fallback_reason = "uvr.asr_too_short"
                        await _emit_timeline_event(
                            ctx.task,
                            ctx.uvr_fallback_reason,
                            stage="asr",
                            step_id="transcribe",
                            level="warning",
                            message="UVR 后转写文本过短，改用原始音频重新 ASR",
                            data={
                                "provider": asr_provider or "settings",
                                "segments": len(recognition.get("segments", [])),
                                "transcript_chars": ctx.metadata.extra["uvr_transcript_chars"],
                                "fallback_audio": original_asr_audio,
                            },
                        )
                        recognition = await _transcribe_selected_audio(original_asr_audio)
                        await _raise_if_cancelled(ctx.task.id)
                        ctx.transcript = " ".join(
                            s["text"] for s in recognition.get("segments", [])
                        )
                    ctx.srt = recognition.get("srt", "")
                    ctx.polished = None
                    ctx.polished_md = None
                    ctx.subtitle_source = "asr"
                    ctx.recognition_segments = recognition.get("segments", [])
                    ctx.metadata.extra["asr_quality_diagnostics"] = recognition.get(
                        "quality_diagnostics",
                        [],
                    )
                    await _write_speaker_map(
                        ctx.task,
                        ctx.task_dir,
                        ctx.recognition_segments,
                        ctx.source_context,
                    )
                    ctx.recognition_segments = await _run_voiceprint_step(
                        ctx.task,
                        ctx.recognition_segments,
                        ctx.task_dir,
                        ctx.source_context,
                    )
                    ctx.srt = _render_recognition_srt(ctx.recognition_segments)
                    ctx.metadata.extra["audio_processing"] = {
                        "flow": recognition.get("audio_processing_flow", "asr"),
                        "provider": recognition.get("provider", asr_provider or "settings"),
                        "model": recognition.get("model"),
                        "runtime_provider": recognition.get("runtime_provider"),
                        "runtime_version": recognition.get("runtime_version"),
                        "timestamp_source": recognition.get("timestamp_source"),
                        "asr_metadata": recognition.get("asr_metadata", {}),
                        "diarization": recognition.get("diarization", "none"),
                    }
                    resolved_speakers = sorted(
                        {
                            str(segment.get("speaker") or "").strip()
                            for segment in ctx.recognition_segments
                            if str(segment.get("speaker") or "").strip()
                        }
                    )
                    ctx.metadata.extra["speakers"] = resolved_speakers
                    ctx.metadata.extra["speaker_count"] = len(resolved_speakers)
                    await _emit_timeline_event(
                        ctx.task,
                        "asr.completed",
                        stage="asr",
                        step_id="transcribe",
                        level="info",
                        message="ASR 转录完成",
                        data={
                            "provider": recognition.get("provider", asr_provider or "settings"),
                            "segments": len(ctx.recognition_segments),
                            "language": recognition.get("language"),
                        },
                    )

                    asr_error = _extract_internal_asr_error(ctx.recognition_segments)
                    if asr_error:
                        raise RuntimeError(
                            f"ASR backend produced an internal error placeholder: {asr_error}"
                        )

                    # Detect transcript language (non-fatal, populates metadata for UI)
                    if ctx.srt:
                        try:
                            from app.services.analysis.language_detect import (
                                detect_transcript_language,
                            )

                            detected_lang = await detect_transcript_language(srt=ctx.srt)
                            ctx.metadata.extra["detected_language"] = detected_lang
                            ctx.metadata.extra["subtitle_tracks"] = [
                                {
                                    "lang": detected_lang if detected_lang != "unknown" else "asr",
                                    "type": "asr",
                                    "filename": "transcript.srt",
                                    "polished": True,  # polish step will populate
                                }
                            ]
                        except Exception as e:
                            log_event(logger, logging.WARNING, "asr.lang_detect.failed", error=e)

                # Write transcript.srt immediately
                if ctx.srt:
                    await _write_text_artifact(ctx.task, ctx.task_dir, "transcript.srt", ctx.srt)
                if ctx.has_subtitle and ctx.polished:
                    await _write_text_artifact(
                        ctx.task, ctx.task_dir, "transcript_polished.srt", ctx.polished
                    )
                    if ctx.polished_md:
                        await _write_text_artifact(
                            ctx.task, ctx.task_dir, "transcript_polished.md", ctx.polished_md
                        )

                await _update_step(ctx.task, PipelineStep.TRANSCRIBE, completed=True)
                await _raise_if_cancelled(ctx.task.id)
            # end if TRANSCRIBE not in done

        # end async with gpu_sem

    # end if SEPARATE+TRANSCRIBE not both done
