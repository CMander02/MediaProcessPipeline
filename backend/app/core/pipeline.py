"""Public pipeline entry points and queue orchestration.

Source preparation -> transcription -> post-processing -> archive.
Stage state is carried by PipelineContext; artifact writes use ArtifactStore.
"""

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from uuid import UUID

from app.core.database import get_task_store
from app.core.events import TaskEvent, get_event_bus
from app.core.logging_setup import log_event
from app.core.settings import get_runtime_settings
from app.models import Task, TaskStatus, TaskType

logger = logging.getLogger(__name__)

from app.core.pipeline_steps.artifacts import _emit_file_ready as _emit_file_ready
from app.core.pipeline_steps.artifacts import _prepare_source_context as _prepare_source_context
from app.core.pipeline_steps.artifacts import _rename_task_dir_to_title as _rename_task_dir_to_title
from app.core.pipeline_steps.artifacts import (
    _rewrite_ingest_paths_after_task_dir_move as _rewrite_ingest_paths_after_task_dir_move,
)
from app.core.pipeline_steps.artifacts import (
    _rewrite_path_after_dir_move as _rewrite_path_after_dir_move,
)
from app.core.pipeline_steps.artifacts import _sanitize_filename as _sanitize_filename
from app.core.pipeline_steps.artifacts import _schedule_kb_index as _schedule_kb_index
from app.core.pipeline_steps.artifacts import _sync_task_from_metadata as _sync_task_from_metadata
from app.core.pipeline_steps.artifacts import _unique_child_dir as _unique_child_dir
from app.core.pipeline_steps.artifacts import _write_detail_file as _write_detail_file
from app.core.pipeline_steps.artifacts import _write_mindmap_files as _write_mindmap_files
from app.core.pipeline_steps.artifacts import _write_speaker_map as _write_speaker_map
from app.core.pipeline_steps.artifacts import _write_summary_files as _write_summary_files
from app.core.pipeline_steps.artifacts import _write_text_artifact as _write_text_artifact
from app.core.pipeline_steps.artifacts import create_task_dir as create_task_dir
from app.core.pipeline_steps.artifacts import update_metadata_status as update_metadata_status
from app.core.pipeline_steps.artifacts import write_metadata_json as write_metadata_json
from app.core.pipeline_steps.context import create_context
from app.core.pipeline_steps.download import prepare_download
from app.core.pipeline_steps.notes import _append_task_warning as _append_task_warning
from app.core.pipeline_steps.notes import _cache_note_cover_thumbnail as _cache_note_cover_thumbnail
from app.core.pipeline_steps.notes import (
    _download_note_images_for_download_step as _download_note_images_for_download_step,
)
from app.core.pipeline_steps.notes import (
    _downloaded_note_image_paths as _downloaded_note_image_paths,
)
from app.core.pipeline_steps.notes import _downloader_accepts_cancel as _downloader_accepts_cancel
from app.core.pipeline_steps.notes import _existing_note_image_paths as _existing_note_image_paths
from app.core.pipeline_steps.notes import _fallback_note_analysis as _fallback_note_analysis
from app.core.pipeline_steps.notes import _fallback_note_mindmap as _fallback_note_mindmap
from app.core.pipeline_steps.notes import _fallback_note_summary as _fallback_note_summary
from app.core.pipeline_steps.notes import _image_note_index as _image_note_index
from app.core.pipeline_steps.notes import _note_expected_image_count as _note_expected_image_count
from app.core.pipeline_steps.notes import (
    _note_image_download_diagnostics as _note_image_download_diagnostics,
)
from app.core.pipeline_steps.notes import (
    _note_image_download_failure_message as _note_image_download_failure_message,
)
from app.core.pipeline_steps.notes import _note_image_downloader as _note_image_downloader
from app.core.pipeline_steps.notes import (
    _note_should_fail_on_missing_images as _note_should_fail_on_missing_images,
)
from app.core.pipeline_steps.notes import _note_text_excerpt as _note_text_excerpt
from app.core.pipeline_steps.notes import _process_image_note as _process_image_note
from app.core.pipeline_steps.notes import (
    _restored_note_image_descriptions as _restored_note_image_descriptions,
)
from app.core.pipeline_steps.notes import _run_note_image_downloader as _run_note_image_downloader
from app.core.pipeline_steps.notes import _safe_pipeline_error as _safe_pipeline_error
from app.core.pipeline_steps.notes import (
    _write_note_fallback_outputs as _write_note_fallback_outputs,
)
from app.core.pipeline_steps.postprocess import postprocess
from app.core.pipeline_steps.sources import _canonical_image_url as _canonical_image_url
from app.core.pipeline_steps.sources import _clean_source_path as _clean_source_path
from app.core.pipeline_steps.sources import _detect_source_type as _detect_source_type
from app.core.pipeline_steps.sources import (
    _download_resolves_url_title as _download_resolves_url_title,
)
from app.core.pipeline_steps.sources import _extract_audio_from_video as _extract_audio_from_video
from app.core.pipeline_steps.sources import (
    _localize_note_markdown_image_refs as _localize_note_markdown_image_refs,
)
from app.core.pipeline_steps.sources import _looks_like_local_path as _looks_like_local_path
from app.core.pipeline_steps.sources import _platform_prefer_subtitles as _platform_prefer_subtitles
from app.core.pipeline_steps.sources import (
    _subtitle_unavailable_message as _subtitle_unavailable_message,
)
from app.core.pipeline_steps.state import PIPELINE_STEPS as PIPELINE_STEPS
from app.core.pipeline_steps.state import PipelineStep as PipelineStep
from app.core.pipeline_steps.state import _emit_timeline_event as _emit_timeline_event
from app.core.pipeline_steps.state import (
    _flow_step_for_pipeline_step as _flow_step_for_pipeline_step,
)
from app.core.pipeline_steps.state import _flow_step_ids as _flow_step_ids
from app.core.pipeline_steps.state import _raise_if_cancelled as _raise_if_cancelled
from app.core.pipeline_steps.state import (
    _select_asr_provider_for_fallback as _select_asr_provider_for_fallback,
)
from app.core.pipeline_steps.state import _set_task_flow as _set_task_flow
from app.core.pipeline_steps.state import _task_download_cancelled as _task_download_cancelled
from app.core.pipeline_steps.state import _update_flow_from_metadata as _update_flow_from_metadata
from app.core.pipeline_steps.state import _update_flow_step as _update_flow_step
from app.core.pipeline_steps.state import _update_step as _update_step
from app.core.pipeline_steps.state import _update_step_progress as _update_step_progress
from app.core.pipeline_steps.state import pipeline_steps_schema as pipeline_steps_schema
from app.core.pipeline_steps.subtitle_fast_path import (
    _run_subtitle_fast_path as _run_subtitle_fast_path,
)
from app.core.pipeline_steps.transcript import (
    _extract_internal_asr_error as _extract_internal_asr_error,
)
from app.core.pipeline_steps.transcript import (
    _is_transcript_too_short_for_uvr_fallback as _is_transcript_too_short_for_uvr_fallback,
)
from app.core.pipeline_steps.transcript import _plain_text_from_srt as _plain_text_from_srt
from app.core.pipeline_steps.transcript import _render_recognition_srt as _render_recognition_srt
from app.core.pipeline_steps.transcript import _require_audio_file as _require_audio_file
from app.core.pipeline_steps.transcript import (
    _save_all_tracks_as_transcripts as _save_all_tracks_as_transcripts,
)
from app.core.pipeline_steps.transcript import _select_polish_track as _select_polish_track
from app.core.pipeline_steps.transcript import _user_language_hint as _user_language_hint
from app.core.pipeline_steps.transcription import (
    _release_uvr_gpu_resources as _release_uvr_gpu_resources,
)
from app.core.pipeline_steps.transcription import _run_voiceprint_step as _run_voiceprint_step
from app.core.pipeline_steps.transcription import transcribe


async def run_pipeline(task: Task, _download_worker_call: bool = False) -> None:
    """Run a queue stage, restoring persisted outputs at each worker handoff."""
    context = await create_context(task, get_runtime_settings(), _download_worker_call)
    if await prepare_download(context):
        return
    await transcribe(context)
    await postprocess(context)


async def process_task(task_id: UUID, _download_worker_call: bool = False) -> None:
    """Process a single task — called by both download workers and GPU worker.

    download worker  → process_task(id, _download_worker_call=True)
                         runs DOWNLOAD, then advance_to_gpu(), returns
    GPU worker       → process_task(id, _download_worker_call=False)
                         DOWNLOAD already in completed_steps, skips it,
                         runs SEPARATE → TRANSCRIBE → POLISH → ANALYZE → ARCHIVE
    """
    from app.services.analysis import generate_mindmap, polish_text, summarize_text
    from app.services.ingestion import download_media
    from app.services.preprocessing import separate_vocals
    from app.services.recognition import transcribe_audio

    store = get_task_store()
    bus = get_event_bus()

    task = store.get(task_id)
    if not task:
        return

    # Only set PROCESSING status on first entry (download worker call).
    # On GPU worker re-entry the task is already PROCESSING.
    if task.status != TaskStatus.PROCESSING:
        store.update_status(task_id, TaskStatus.PROCESSING, error=None)
        await bus.publish(TaskEvent(task_id, "processing"))

    # Re-read from DB to get latest completed_steps
    task = store.get(task_id)
    started_at = time.perf_counter()
    log_event(
        logger,
        logging.INFO,
        "task.started",
        task_type=task.task_type,
        download_worker_call=_download_worker_call,
    )

    try:
        if task.task_type == TaskType.PIPELINE:
            await run_pipeline(task, _download_worker_call=_download_worker_call)
        elif task.task_type == TaskType.INGESTION:
            task.result = await download_media(task.source)
        elif task.task_type == TaskType.PREPROCESSING:
            task.result = await separate_vocals(task.source)
        elif task.task_type == TaskType.RECOGNITION:
            task.result = await transcribe_audio(
                task.source,
                provider=task.options.get("asr_provider"),
                model=task.options.get("asr_model"),
                diarize=not task.options.get("disable_diarization", False),
                chunk_strategy=task.options.get("asr_chunk_strategy"),
                timestamp_mode=task.options.get("asr_timestamp_mode"),
                hotwords=task.options.get("hotwords"),
            )
        elif task.task_type == TaskType.ANALYSIS:
            polished = await polish_text(task.source)
            summary = await summarize_text(task.source)
            mindmap = await generate_mindmap(task.source)
            task.result = {"polished": polished, "summary": summary, "mindmap": mindmap}

        # If this was the download-worker call, run_pipeline normally returned
        # early after advance_to_gpu() — don't mark COMPLETED yet. The subtitle
        # fast path is the exception: it can finish ARCHIVE inside the download
        # worker, so it must fall through to the normal completion write below.
        if _download_worker_call and task.task_type == TaskType.PIPELINE:
            if PipelineStep.ARCHIVE not in (task.completed_steps or []):
                return

        task.status = TaskStatus.COMPLETED
        task.progress = 1.0
        task.completed_at = datetime.now()
        if task.flow:
            flow = dict(task.flow)
            flow["status"] = "completed"
            flow["progress"] = 1.0
            flow["completed_steps"] = [
                step.get("id") for step in flow.get("steps", []) if isinstance(step, dict)
            ]
            task.flow = flow

        store.update_status(
            task_id,
            TaskStatus.COMPLETED,
            progress=1.0,
            result=task.result,
            completed_at=task.completed_at,
            error=None,
            flow=task.flow,
            platform=task.platform,
            uploader_id=task.uploader_id,
            content_subtype=task.content_subtype,
        )
        await bus.publish(
            TaskEvent(
                task_id,
                "completed",
                {
                    "output_dir": task.result.get("output_dir") if task.result else None,
                },
            )
        )
        log_event(
            logger,
            logging.INFO,
            "task.completed",
            task_type=task.task_type,
            duration_ms=round((time.perf_counter() - started_at) * 1000),
            output_dir=task.result.get("output_dir") if task.result else None,
        )

    except asyncio.CancelledError:
        log_event(
            logger,
            logging.INFO,
            "task.paused"
            if (store.get(task_id) or task).status == TaskStatus.PAUSED
            else "task.cancelled",
            duration_ms=round((time.perf_counter() - started_at) * 1000),
        )
        current = store.get(task_id) or task
        output_dir = current.result.get("output_dir") if current.result else None
        paused = current.status == TaskStatus.PAUSED
        final_status = TaskStatus.PAUSED if paused else TaskStatus.CANCELLED
        status_text = "paused" if paused else "cancelled"
        update_metadata_status(Path(output_dir) if output_dir else None, status_text)
        flow = current.flow or task.flow
        if flow:
            flow = dict(flow)
            flow["status"] = status_text
        store.update_status(
            task_id,
            final_status,
            completed_at=None if paused else datetime.now(),
            message="已暂停" if paused else "已取消",
            flow=flow,
        )
        await bus.publish(TaskEvent(task_id, status_text, {"status": status_text}))
        raise

    except Exception as e:
        log_event(
            logger,
            logging.ERROR,
            "task.failed",
            task_type=task.task_type,
            duration_ms=round((time.perf_counter() - started_at) * 1000),
            error=e,
            exc_info=True,
        )
        task.status = TaskStatus.FAILED
        task.error = str(e)

        # Update metadata.json status to failed
        current = store.get(task_id) or task
        output_dir = current.result.get("output_dir") if current.result else None
        update_metadata_status(Path(output_dir) if output_dir else None, "failed")
        flow = current.flow or task.flow
        if flow:
            flow = dict(flow)
            flow["status"] = "failed"

        store.update_status(
            task_id,
            TaskStatus.FAILED,
            error=str(e),
            completed_at=datetime.now(),
            flow=flow,
        )
        await bus.publish(
            TaskEvent(task_id, "failed", {"error": str(e), "stage": task.current_step})
        )

    finally:
        # Offload local GGUF model after each task to free VRAM.
        # No-op when using API providers.
        if not _download_worker_call:
            from app.services.analysis.llm import offload_local_llm

            offload_local_llm()
