"""State responsibilities for the media pipeline."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.core.database import get_task_store
from app.core.events import TaskEvent, get_event_bus
from app.core.logging_setup import log_event
from app.core.settings import get_runtime_settings
from app.core.source_resolver import SourceFlow, flow_from_metadata
from app.models import MediaMetadata, Task, TaskStatus

logger = logging.getLogger(__name__)


class PipelineStep(StrEnum):
    """Pipeline processing steps."""

    DOWNLOAD = "download"
    SEPARATE = "separate"
    TRANSCRIBE = "transcribe"
    VOICEPRINT = "voiceprint"
    ANALYZE = "analyze"
    POLISH = "polish"
    ARCHIVE = "archive"


PIPELINE_STEPS = [
    {"id": PipelineStep.DOWNLOAD, "name": "下载媒体", "name_en": "Downloading"},
    {"id": PipelineStep.SEPARATE, "name": "分离人声", "name_en": "Separating vocals"},
    {"id": PipelineStep.TRANSCRIBE, "name": "转录音频", "name_en": "Transcribing"},
    {"id": PipelineStep.POLISH, "name": "润色字幕", "name_en": "Polishing transcript"},
    {"id": PipelineStep.ANALYZE, "name": "分析+摘要+脑图", "name_en": "Analyzing & summarizing"},
    {"id": PipelineStep.ARCHIVE, "name": "归档保存", "name_en": "Archiving"},
]


def pipeline_steps_schema() -> list[dict[str, str]]:
    """Return the public pipeline step schema in execution order."""
    return [
        {"id": str(s["id"]), "name": s["name"], "name_en": s["name_en"]} for s in PIPELINE_STEPS
    ]


async def _raise_if_cancelled(task_id: UUID) -> None:
    """Honor cancellation/pause requests between blocking pipeline phases."""
    task = get_task_store().get(task_id)
    if task and task.status in {TaskStatus.CANCELLED, TaskStatus.PAUSED}:
        raise asyncio.CancelledError()


def _task_download_cancelled(task_id: UUID) -> bool:
    """Return True when a blocking downloader should stop promptly."""
    task = get_task_store().get(task_id)
    return task is None or task.status in {TaskStatus.CANCELLED, TaskStatus.PAUSED}


def _flow_step_ids(task: Task) -> list[str]:
    flow = task.flow or {}
    return [
        str(step.get("id"))
        for step in flow.get("steps", [])
        if isinstance(step, dict) and step.get("id")
    ]


def _flow_step_for_pipeline_step(task: Task, step: PipelineStep) -> str:
    step_id = str(step)
    flow_step_ids = _flow_step_ids(task)
    if (
        step == PipelineStep.SEPARATE
        and step_id not in flow_step_ids
        and "transcribe" in flow_step_ids
    ):
        return "transcribe"
    return step_id


async def _update_step_progress(
    task: Task,
    step: PipelineStep,
    step_progress: float,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> None:
    """Persist and publish fractional progress within a long-running pipeline step."""
    normalized = max(0.0, min(float(step_progress), 0.999))
    task.current_step = step
    task.message = message

    public_step_ids = {pipeline_step["id"] for pipeline_step in PIPELINE_STEPS}
    total_steps = len(public_step_ids)
    completed_count = len(public_step_ids.intersection(task.completed_steps))
    task.progress = (completed_count + normalized) / total_steps if total_steps else 0.0
    task.updated_at = datetime.now()

    log_event(
        logger,
        logging.INFO,
        "pipeline.step.progress",
        step=str(step),
        step_progress=round(normalized, 4),
        progress=round(task.progress, 4),
        message=task.message,
        **(details or {}),
    )

    get_task_store().update_status(
        task.id,
        task.status,
        progress=task.progress,
        message=task.message,
        current_step=task.current_step,
        completed_steps=task.completed_steps,
    )

    payload = {
        "step": step,
        "stage": str(step),
        "step_id": str(step),
        "completed": False,
        "progress": task.progress,
        "step_progress": normalized,
        "message": task.message,
    }
    if details:
        payload.update(details)
    await get_event_bus().publish(TaskEvent(task.id, "step", payload))

    await _update_flow_step(
        task,
        _flow_step_for_pipeline_step(task, step),
        completed=False,
        message=task.message,
        step_progress=normalized,
    )


async def _set_task_flow(
    task: Task,
    source_flow: SourceFlow,
    *,
    status: str = "processing",
    current_step: str | None = None,
) -> None:
    previous = task.flow or {}
    previous_done = (
        previous.get("completed_steps") if isinstance(previous.get("completed_steps"), list) else []
    )
    snapshot = source_flow.snapshot(
        status=status, current_step=current_step or previous.get("current_step")
    )
    snapshot["completed_steps"] = [
        step for step in previous_done if step in {s["id"] for s in snapshot["steps"]}
    ]
    task.flow = snapshot
    task.platform = source_flow.platform
    task.content_subtype = source_flow.content_subtype
    get_task_store().update_status(
        task.id,
        task.status,
        flow=task.flow,
        platform=task.platform,
        content_subtype=task.content_subtype,
    )

    if previous.get("id") != source_flow.flow_id:
        await get_event_bus().publish(
            TaskEvent(
                task.id,
                "flow_selected",
                {
                    "stage": "resolve",
                    "step_id": snapshot.get("current_step"),
                    "level": "info",
                    "message": source_flow.label,
                    "flow": snapshot,
                    "platform": source_flow.platform,
                    "content_subtype": source_flow.content_subtype,
                },
            )
        )


async def _update_flow_step(
    task: Task,
    step_id: str,
    *,
    completed: bool = False,
    status: str | None = None,
    message: str | None = None,
    level: str = "info",
    step_progress: float | None = None,
) -> None:
    if not task.flow:
        return

    flow = dict(task.flow)
    step_ids = _flow_step_ids(task)
    if step_id not in step_ids:
        return

    completed_steps = flow.get("completed_steps")
    if not isinstance(completed_steps, list):
        completed_steps = []
    if completed and step_id not in completed_steps:
        completed_steps = [*completed_steps, step_id]

    index = step_ids.index(step_id)
    total = len(step_ids)
    flow["current_step"] = step_id
    flow["current_step_index"] = index
    flow["current_step_label"] = next(
        (step.get("label") for step in flow.get("steps", []) if step.get("id") == step_id),
        step_id,
    )
    flow["completed_steps"] = completed_steps
    flow["total_steps"] = total
    normalized_step_progress = None
    if not completed and step_progress is not None:
        normalized_step_progress = max(0.0, min(float(step_progress), 0.999))
        flow["step_progress"] = normalized_step_progress
        flow["step_progress_step"] = step_id
    elif completed or flow.get("step_progress_step") != step_id:
        flow.pop("step_progress", None)
        flow.pop("step_progress_step", None)
    else:
        normalized_step_progress = max(
            0.0,
            min(float(flow.get("step_progress") or 0.0), 0.999),
        )

    completed_count = len({step for step in completed_steps if step in step_ids})
    flow["progress"] = (
        (completed_count + (normalized_step_progress or 0.0)) / total if total else 0.0
    )
    flow["status"] = status or flow.get("status") or "processing"
    task.flow = flow
    get_task_store().update_status(task.id, task.status, flow=task.flow)

    await get_event_bus().publish(
        TaskEvent(
            task.id,
            "flow_step",
            {
                "stage": step_id,
                "step_id": step_id,
                "completed": completed,
                "level": level,
                "message": message or flow["current_step_label"],
                "flow": flow,
                "step_progress": normalized_step_progress,
            },
        )
    )


async def _emit_timeline_event(
    task: Task,
    event_type: str,
    *,
    stage: str,
    step_id: str | None = None,
    level: str = "info",
    message: str,
    data: dict[str, Any] | None = None,
) -> None:
    payload = {
        "stage": stage,
        "step_id": step_id or stage,
        "level": level,
        "message": message,
    }
    if data:
        payload.update(data)
    await get_event_bus().publish(TaskEvent(task.id, event_type, payload))


async def _update_flow_from_metadata(
    task: Task,
    source_flow: SourceFlow,
    metadata: MediaMetadata,
    *,
    has_subtitle: bool = False,
    force_asr: bool = False,
    api_fallback: bool = False,
    current_step: str | None = None,
    preferred_asr_provider: str | None = None,
) -> SourceFlow:
    resolved = flow_from_metadata(
        source_flow,
        metadata,
        has_subtitle=has_subtitle,
        force_asr=force_asr,
        api_fallback=api_fallback,
        preferred_asr_provider=preferred_asr_provider,
    )
    await _set_task_flow(task, resolved, status="processing", current_step=current_step)
    return resolved


def _select_asr_provider_for_fallback(task: Task) -> tuple[str | None, str, bool]:
    """Choose the ASR provider when URL media enters API fallback."""
    from app.core.model_router import resolve_asr_binding

    rt = get_runtime_settings()
    explicit = str(task.options.get("asr_provider") or "").strip()
    if explicit:
        return explicit, "task_option", explicit == "siliconflow"

    if rt.audio_processing_flow == "moss":
        return "moss_cpp", "audio_processing_flow", False

    try:
        runtime_binding = resolve_asr_binding(rt)
        if runtime_binding.provider == "siliconflow" and runtime_binding.configured:
            return "siliconflow", "runtime_api_provider", True
    except Exception as exc:
        log_event(logger, logging.DEBUG, "asr.runtime_provider.resolve_failed", error=exc)

    try:
        siliconflow_binding = resolve_asr_binding(rt, task_options={"asr_provider": "siliconflow"})
        if siliconflow_binding.configured:
            return "siliconflow", "siliconflow_configured", True
    except Exception as exc:
        log_event(logger, logging.DEBUG, "asr.siliconflow_provider.resolve_failed", error=exc)

    default_provider = str(getattr(rt, "asr_provider", "") or "").strip() or None
    return default_provider, "default_asr_provider", False


async def _update_step(
    task: Task,
    step: PipelineStep,
    completed: bool = False,
) -> None:
    """Update task step progress, persist to DB, and publish event."""
    task.current_step = step
    task.message = next(
        (s["name"] for s in PIPELINE_STEPS if s["id"] == step),
        str(step),
    )
    if completed and step not in task.completed_steps:
        task.completed_steps.append(step)

    public_step_ids = {step["id"] for step in PIPELINE_STEPS}
    total_steps = len(public_step_ids)
    completed_count = len(public_step_ids.intersection(task.completed_steps))
    task.progress = completed_count / total_steps
    task.updated_at = datetime.now()

    # Persist to SQLite
    store = get_task_store()
    store.update_status(
        task.id,
        task.status,
        progress=task.progress,
        message=task.message,
        current_step=task.current_step,
        completed_steps=task.completed_steps,
    )

    # Publish SSE event
    bus = get_event_bus()
    await bus.publish(
        TaskEvent(
            task.id,
            "step",
            {
                "step": step,
                "completed": completed,
                "progress": task.progress,
                "message": task.message,
            },
        )
    )
    flow_step = _flow_step_for_pipeline_step(task, step)
    await _update_flow_step(
        task,
        flow_step,
        completed=completed and flow_step == str(step),
        message=task.message,
    )
