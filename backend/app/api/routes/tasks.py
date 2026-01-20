"""Task management routes."""

import re
from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.core.config import get_settings
from app.models import Task, TaskCreate, TaskStatus, TaskType, MediaMetadata
from app.services.ingestion import download_media, scan_inbox
from app.services.preprocessing import separate_vocals
from app.services.recognition import transcribe_audio
from app.services.analysis import polish_text, summarize_text, generate_mindmap
from app.services.archiving import archive_result

router = APIRouter(prefix="/tasks", tags=["tasks"])

# In-memory storage (replace with DB in production)
_tasks: dict[UUID, Task] = {}


@router.post("", response_model=Task)
async def create_task(task_create: TaskCreate, background_tasks: BackgroundTasks):
    """Create a new processing task."""
    task = Task(
        task_type=task_create.task_type,
        source=task_create.source,
        options=task_create.options,
        webhook_url=task_create.webhook_url,
        status=TaskStatus.QUEUED,
    )
    _tasks[task.id] = task
    background_tasks.add_task(process_task, task.id)
    return task


@router.get("", response_model=list[Task])
async def list_tasks(status: TaskStatus | None = None, limit: int = 50):
    """List tasks with optional filtering."""
    tasks = list(_tasks.values())
    if status:
        tasks = [t for t in tasks if t.status == status]
    tasks.sort(key=lambda t: t.created_at, reverse=True)
    return tasks[:limit]


@router.get("/{task_id}", response_model=Task)
async def get_task(task_id: UUID):
    """Get task by ID."""
    if task_id not in _tasks:
        raise HTTPException(404, "Task not found")
    return _tasks[task_id]


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: UUID):
    """Cancel a pending task."""
    if task_id not in _tasks:
        raise HTTPException(404, "Task not found")
    task = _tasks[task_id]
    if task.status not in (TaskStatus.PENDING, TaskStatus.QUEUED):
        raise HTTPException(400, f"Cannot cancel task in status: {task.status}")
    task.status = TaskStatus.CANCELLED
    task.updated_at = datetime.now()
    return {"message": "Cancelled", "task_id": str(task_id)}


async def process_task(task_id: UUID):
    """Background task processor."""
    task = _tasks.get(task_id)
    if not task:
        return

    task.status = TaskStatus.PROCESSING
    task.updated_at = datetime.now()

    try:
        if task.task_type == TaskType.PIPELINE:
            await run_pipeline(task)
        elif task.task_type == TaskType.INGESTION:
            task.result = await download_media(task.source)
        elif task.task_type == TaskType.PREPROCESSING:
            task.result = await separate_vocals(task.source)
        elif task.task_type == TaskType.RECOGNITION:
            task.result = await transcribe_audio(task.source)
        elif task.task_type == TaskType.ANALYSIS:
            polished = await polish_text(task.source)
            summary = await summarize_text(task.source)
            mindmap = await generate_mindmap(task.source)
            task.result = {"polished": polished, "summary": summary, "mindmap": mindmap}

        task.status = TaskStatus.COMPLETED
        task.progress = 1.0
        task.completed_at = datetime.now()

    except Exception as e:
        task.status = TaskStatus.FAILED
        task.error = str(e)

    task.updated_at = datetime.now()


def _sanitize_filename(name: str) -> str:
    """Sanitize string for use as filename."""
    # Remove or replace invalid characters
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    # Remove leading/trailing spaces and dots
    name = name.strip(' .')
    # Limit length
    return name[:100] if len(name) > 100 else name


def _create_work_dir(task_id: UUID, title: str) -> Path:
    """Create a dedicated work directory for this media processing task."""
    settings = get_settings()
    # Format: processing/{task_id_short}_{sanitized_title}/
    dir_name = f"{str(task_id)[:8]}_{_sanitize_filename(title)}"
    work_dir = settings.data_processing.resolve() / dir_name
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


async def run_pipeline(task: Task):
    """Run full pipeline: ingest → preprocess → recognize → analyze → archive."""
    task.message = "Downloading..."
    task.progress = 0.1
    task.updated_at = datetime.now()

    ingest = await download_media(task.source)
    audio_path = ingest.get("file_path")
    metadata = MediaMetadata(**ingest.get("metadata", {"title": task.source}))

    # Create dedicated work directory for this media
    work_dir = _create_work_dir(task.id, metadata.title)

    task.message = "Separating vocals..."
    task.progress = 0.3
    task.updated_at = datetime.now()

    preprocess = await separate_vocals(audio_path, output_dir=work_dir)
    vocals_path = preprocess.get("vocals_path", audio_path)

    task.message = "Transcribing..."
    task.progress = 0.5
    task.updated_at = datetime.now()

    recognition = await transcribe_audio(vocals_path, output_dir=work_dir)
    transcript = " ".join(s["text"] for s in recognition.get("segments", []))
    srt = recognition.get("srt", "")

    task.message = "Analyzing..."
    task.progress = 0.7
    task.updated_at = datetime.now()

    polished = await polish_text(transcript)
    summary = await summarize_text(transcript)
    mindmap = await generate_mindmap(transcript)

    task.message = "Archiving..."
    task.progress = 0.9
    task.updated_at = datetime.now()

    archive = await archive_result(metadata, polished, summary, mindmap, srt, work_dir=work_dir)

    task.result = {
        "metadata": metadata.model_dump(mode="json"),
        "transcript_segments": len(recognition.get("segments", [])),
        "archive": archive,
        "work_dir": str(work_dir),
    }
