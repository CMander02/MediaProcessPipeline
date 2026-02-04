"""Task management routes with step-based progress tracking."""

import re
import shutil
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.core.config import get_settings
from app.models import Task, TaskCreate, TaskStatus, TaskType, MediaMetadata
from app.services.ingestion import download_media, scan_inbox
from app.services.preprocessing import separate_vocals
from app.services.recognition import transcribe_audio
from app.services.analysis import polish_text, summarize_text, generate_mindmap, analyze_content
from app.services.archiving import archive_result
from app.services.history import get_history_service
from app.api.routes.settings import get_runtime_settings

router = APIRouter(prefix="/tasks", tags=["tasks"])

# In-memory storage for active tasks (replace with DB in production)
_tasks: dict[UUID, Task] = {}


class PipelineStep(StrEnum):
    """Pipeline processing steps."""
    DOWNLOAD = "download"
    SEPARATE = "separate"
    TRANSCRIBE = "transcribe"
    ANALYZE = "analyze"
    POLISH = "polish"
    SUMMARIZE = "summarize"
    ARCHIVE = "archive"


# Step definitions with display names
PIPELINE_STEPS = [
    {"id": PipelineStep.DOWNLOAD, "name": "下载媒体", "name_en": "Downloading"},
    {"id": PipelineStep.SEPARATE, "name": "分离人声", "name_en": "Separating vocals"},
    {"id": PipelineStep.TRANSCRIBE, "name": "转录音频", "name_en": "Transcribing"},
    {"id": PipelineStep.ANALYZE, "name": "分析内容", "name_en": "Analyzing content"},
    {"id": PipelineStep.POLISH, "name": "润色字幕", "name_en": "Polishing transcript"},
    {"id": PipelineStep.SUMMARIZE, "name": "生成摘要", "name_en": "Generating summary"},
    {"id": PipelineStep.ARCHIVE, "name": "归档保存", "name_en": "Archiving"},
]


def _detect_source_type(source: str) -> str:
    """Detect the type of media source."""
    source_lower = source.lower()
    if "youtube.com" in source_lower or "youtu.be" in source_lower:
        return "youtube"
    elif "bilibili.com" in source_lower or "b23.tv" in source_lower:
        return "bilibili"
    elif source_lower.startswith(("http://", "https://")):
        return "url"
    elif any(source_lower.endswith(ext) for ext in [".mp4", ".mkv", ".avi", ".webm", ".mov"]):
        return "local_video"
    elif any(source_lower.endswith(ext) for ext in [".mp3", ".wav", ".flac", ".m4a", ".ogg"]):
        return "local_audio"
    else:
        return "unknown"


@router.post("", response_model=Task)
async def create_task(task_create: TaskCreate, background_tasks: BackgroundTasks):
    """Create a new processing task."""
    task = Task(
        task_type=task_create.task_type,
        source=task_create.source,
        options=task_create.options,
        webhook_url=task_create.webhook_url,
        status=TaskStatus.QUEUED,
        # Add step tracking
        current_step=None,
        steps=[s["id"] for s in PIPELINE_STEPS],
        completed_steps=[],
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

    # Record in history
    history = get_history_service()
    history.add_task(
        task_id=task.id,
        title=task.source,
        source=task.source,
        source_type=_detect_source_type(task.source),
        status="cancelled",
        created_at=task.created_at,
        completed_at=datetime.now(),
    )

    return {"message": "Cancelled", "task_id": str(task_id)}


def _sanitize_filename(name: str) -> str:
    """Sanitize string for use as filename."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = name.strip(' .')
    return name[:100] if len(name) > 100 else name


def _create_task_dir(task_id: UUID, title: str | None = None) -> Path:
    """Create a dedicated directory for this task under data/{task_id}/."""
    settings = get_runtime_settings()
    data_root = Path(settings.data_root).resolve()

    # Create directory: data/{task_id_short}_{title}/
    task_id_short = str(task_id)[:8]
    if title:
        dir_name = f"{task_id_short}_{_sanitize_filename(title)}"
    else:
        dir_name = task_id_short

    task_dir = data_root / dir_name
    task_dir.mkdir(parents=True, exist_ok=True)

    # Create source subdirectory for original files
    (task_dir / "source").mkdir(exist_ok=True)

    return task_dir


def _update_step(task: Task, step: PipelineStep, completed: bool = False) -> None:
    """Update task's current step and progress."""
    task.current_step = step
    task.message = next(
        (s["name"] for s in PIPELINE_STEPS if s["id"] == step),
        str(step)
    )
    if completed and step not in task.completed_steps:
        task.completed_steps.append(step)

    # Calculate progress based on completed steps
    total_steps = len(PIPELINE_STEPS)
    completed_count = len(task.completed_steps)
    task.progress = completed_count / total_steps

    task.updated_at = datetime.now()


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

        # Record in history
        history = get_history_service()
        metadata = task.result.get("metadata", {}) if task.result else {}
        history.add_task(
            task_id=task.id,
            title=metadata.get("title", task.source),
            source=task.source,
            source_type=_detect_source_type(task.source),
            status="completed",
            created_at=task.created_at,
            completed_at=task.completed_at,
            duration_seconds=metadata.get("duration_seconds"),
            output_dir=task.result.get("output_dir") if task.result else None,
            metadata=task.result.get("analysis", {}) if task.result else {},
        )

    except Exception as e:
        task.status = TaskStatus.FAILED
        task.error = str(e)

        # Record failure in history
        history = get_history_service()
        history.add_task(
            task_id=task.id,
            title=task.source,
            source=task.source,
            source_type=_detect_source_type(task.source),
            status="failed",
            created_at=task.created_at,
            completed_at=datetime.now(),
            error=str(e),
        )

    task.updated_at = datetime.now()


import logging

_logger = logging.getLogger(__name__)


def _clean_source_path(source: str) -> str:
    """Clean up source path by removing quotes and whitespace."""
    # Remove surrounding whitespace
    source = source.strip()
    # Remove surrounding quotes (both single and double)
    if (source.startswith('"') and source.endswith('"')) or \
       (source.startswith("'") and source.endswith("'")):
        source = source[1:-1]
    return source


def _looks_like_local_path(source: str) -> bool:
    """Check if source looks like a local file path (not a URL)."""
    source = _clean_source_path(source)

    # Definitely a URL
    if source.startswith(('http://', 'https://', 'ftp://', 'rtmp://')):
        return False

    # Windows absolute path (e.g., C:\...)
    if len(source) >= 2 and source[1] == ':':
        return True

    # Unix absolute path
    if source.startswith('/'):
        return True

    # Has file extension and no URL-like patterns
    if '.' in source and '://' not in source:
        ext = source.rsplit('.', 1)[-1].lower()
        media_exts = {'mp4', 'mkv', 'avi', 'webm', 'mov', 'mp3', 'wav', 'flac', 'm4a', 'ogg'}
        if ext in media_exts:
            return True

    return False


def _is_local_file(source: str) -> bool:
    """Check if source is a local file path that exists."""
    source = _clean_source_path(source)
    _logger.info(f"Checking if local file: {source}")

    if not _looks_like_local_path(source):
        _logger.info(f"Does not look like local path: {source}")
        return False

    path = Path(source)
    exists = path.exists() and path.is_file()
    _logger.info(f"Path exists check: {exists} for {path}")
    return exists


def _extract_audio_from_video(video_path: Path, output_path: Path) -> Path:
    """Extract audio from video file using ffmpeg."""
    import subprocess

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",  # No video
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(output_path)
    ]

    subprocess.run(cmd, check=True, capture_output=True)
    return output_path


async def run_pipeline(task: Task):
    """Run full pipeline: ingest → preprocess → recognize → analyze → archive."""

    # Clean up source path (remove quotes)
    source = _clean_source_path(task.source)

    # Step 1: Download or copy local file
    _update_step(task, PipelineStep.DOWNLOAD)

    # Check if it looks like a local path
    if _looks_like_local_path(task.source):
        source_path = Path(source)
        if not source_path.exists():
            raise FileNotFoundError(f"本地文件不存在: {source}")
        if not source_path.is_file():
            raise ValueError(f"路径不是文件: {source}")
        # Handle local file - skip download
        title = source_path.stem  # Use filename as title

        # Create task directory first
        task_dir = _create_task_dir(task.id, title)

        # Copy source file to task directory
        dest_source = task_dir / "source" / source_path.name
        shutil.copy2(source_path, dest_source)

        # Determine if we need to extract audio
        video_exts = {".mp4", ".mkv", ".avi", ".webm", ".mov"}
        audio_exts = {".mp3", ".wav", ".flac", ".m4a", ".ogg"}

        if source_path.suffix.lower() in video_exts:
            # Extract audio from video
            audio_path = task_dir / f"{title}.wav"
            _extract_audio_from_video(dest_source, audio_path)
            audio_path = str(audio_path)
            metadata = MediaMetadata(
                title=title,
                source_url=str(source_path),
                media_type="video",
                file_path=str(dest_source),
            )
        elif source_path.suffix.lower() in audio_exts:
            # Use audio directly
            audio_path = str(dest_source)
            metadata = MediaMetadata(
                title=title,
                source_url=str(source_path),
                media_type="audio",
                file_path=str(dest_source),
            )
        else:
            raise ValueError(f"Unsupported file format: {source_path.suffix}")

    else:
        # Handle URL - download
        # First, fetch metadata to get title for task directory
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(source, download=False)
            title = info.get("title", "unknown") if info else "unknown"

        # Create task directory with title BEFORE downloading
        task_dir = _create_task_dir(task.id, title)
        source_dir = task_dir / "source"

        # Download directly to task source directory
        ingest = await download_media(source, output_dir=source_dir)
        audio_path = ingest.get("file_path")
        metadata = MediaMetadata(**ingest.get("metadata", {"title": source}))

    _update_step(task, PipelineStep.DOWNLOAD, completed=True)

    # Step 2: Separate vocals
    _update_step(task, PipelineStep.SEPARATE)
    skip_separation = task.options.get("skip_separation", False)
    if skip_separation:
        vocals_path = audio_path
    else:
        preprocess = await separate_vocals(audio_path, output_dir=task_dir)
        vocals_path = preprocess.get("vocals_path", audio_path)
    _update_step(task, PipelineStep.SEPARATE, completed=True)

    # Step 3: Transcribe
    _update_step(task, PipelineStep.TRANSCRIBE)
    recognition = await transcribe_audio(vocals_path, output_dir=task_dir)
    transcript = " ".join(s["text"] for s in recognition.get("segments", []))
    srt = recognition.get("srt", "")
    _update_step(task, PipelineStep.TRANSCRIBE, completed=True)

    # Step 4: Analyze content (first LLM pass - extract metadata)
    # Pass video metadata (description, tags, chapters) to improve analysis
    _update_step(task, PipelineStep.ANALYZE)
    video_metadata = {
        "uploader": metadata.uploader,
        "description": metadata.description,
        "tags": metadata.tags,
        "chapters": [{"title": ch.title, "start_time": ch.start_time} for ch in metadata.chapters] if metadata.chapters else None,
    }
    analysis = await analyze_content(transcript, metadata.title, metadata=video_metadata)
    _update_step(task, PipelineStep.ANALYZE, completed=True)

    # Step 5: Polish transcript (second LLM pass - sliding window)
    _update_step(task, PipelineStep.POLISH)
    polished = await polish_text(srt, context=analysis)
    _update_step(task, PipelineStep.POLISH, completed=True)

    # Step 6: Generate summary and mindmap
    _update_step(task, PipelineStep.SUMMARIZE)
    summary = await summarize_text(transcript)
    mindmap = await generate_mindmap(transcript)
    _update_step(task, PipelineStep.SUMMARIZE, completed=True)

    # Step 7: Archive
    _update_step(task, PipelineStep.ARCHIVE)
    archive = await archive_result(
        metadata,
        polished_srt=polished,
        summary=summary,
        mindmap=mindmap,
        original_srt=srt,
        work_dir=task_dir,
        analysis=analysis
    )
    _update_step(task, PipelineStep.ARCHIVE, completed=True)

    task.result = {
        "metadata": metadata.model_dump(mode="json"),
        "transcript_segments": len(recognition.get("segments", [])),
        "archive": archive,
        "output_dir": str(task_dir),
        "analysis": analysis,
    }


# History endpoints
@router.get("/history/stats")
async def get_history_stats():
    """Get history statistics."""
    history = get_history_service()
    return history.get_stats()


@router.get("/history")
async def get_history(status: str | None = None, limit: int = 50, offset: int = 0):
    """Get task history."""
    history = get_history_service()
    return {
        "stats": history.get_stats(),
        "tasks": history.list_tasks(status=status, limit=limit, offset=offset),
    }


@router.delete("/history/{task_id}")
async def delete_history_entry(task_id: str):
    """Delete a history entry."""
    history = get_history_service()
    if history.delete_task(task_id):
        return {"message": "Deleted", "task_id": task_id}
    raise HTTPException(404, "History entry not found")
