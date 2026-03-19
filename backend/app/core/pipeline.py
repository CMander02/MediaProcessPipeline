"""Pipeline orchestration — extracted from api.routes.tasks.

This module owns the full processing pipeline (download → archive) and uses
TaskStore + EventBus for state management instead of in-memory dicts.
"""

import logging
import re
import shutil
import subprocess
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.database import get_task_store
from app.core.events import TaskEvent, get_event_bus
from app.core.settings import get_runtime_settings
from app.models import MediaMetadata, Task, TaskStatus, TaskType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline step definitions
# ---------------------------------------------------------------------------

class PipelineStep(StrEnum):
    """Pipeline processing steps."""
    DOWNLOAD = "download"
    SEPARATE = "separate"
    TRANSCRIBE = "transcribe"
    ANALYZE = "analyze"
    POLISH = "polish"
    SUMMARIZE = "summarize"
    ARCHIVE = "archive"


PIPELINE_STEPS = [
    {"id": PipelineStep.DOWNLOAD, "name": "下载媒体", "name_en": "Downloading"},
    {"id": PipelineStep.SEPARATE, "name": "分离人声", "name_en": "Separating vocals"},
    {"id": PipelineStep.TRANSCRIBE, "name": "转录音频", "name_en": "Transcribing"},
    {"id": PipelineStep.ANALYZE, "name": "分析内容", "name_en": "Analyzing content"},
    {"id": PipelineStep.POLISH, "name": "润色字幕", "name_en": "Polishing transcript"},
    {"id": PipelineStep.SUMMARIZE, "name": "生成摘要", "name_en": "Generating summary"},
    {"id": PipelineStep.ARCHIVE, "name": "归档保存", "name_en": "Archiving"},
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _sanitize_filename(name: str) -> str:
    """Sanitize string for use as filename."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = name.strip(' .')
    return name[:100] if len(name) > 100 else name


def _create_task_dir(task_id: UUID, title: str | None = None) -> Path:
    """Create a dedicated directory for this task under data/{task_id}/."""
    settings = get_runtime_settings()
    data_root = Path(settings.data_root).resolve()

    task_id_short = str(task_id)[:8]
    if title:
        dir_name = f"{task_id_short}_{_sanitize_filename(title)}"
    else:
        dir_name = task_id_short

    task_dir = data_root / dir_name
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "source").mkdir(exist_ok=True)
    return task_dir


def _clean_source_path(source: str) -> str:
    """Clean up source path by removing quotes and whitespace."""
    source = source.strip()
    if (source.startswith('"') and source.endswith('"')) or \
       (source.startswith("'") and source.endswith("'")):
        source = source[1:-1]
    return source


def _looks_like_local_path(source: str) -> bool:
    """Check if source looks like a local file path (not a URL)."""
    source = _clean_source_path(source)
    if source.startswith(('http://', 'https://', 'ftp://', 'rtmp://')):
        return False
    if len(source) >= 2 and source[1] == ':':
        return True
    if source.startswith('/'):
        return True
    if '.' in source and '://' not in source:
        ext = source.rsplit('.', 1)[-1].lower()
        media_exts = {'mp4', 'mkv', 'avi', 'webm', 'mov', 'mp3', 'wav', 'flac', 'm4a', 'ogg'}
        if ext in media_exts:
            return True
    return False


def _extract_audio_from_video(video_path: Path, output_path: Path) -> Path:
    """Extract audio from video file using ffmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path


def _cleanup_intermediate_files(task_dir: Path, audio_path: str, vocals_path: str) -> None:
    """Clean up intermediate audio files to save disk space."""
    cleaned_files = []
    cleaned_size = 0

    if vocals_path and vocals_path != audio_path:
        vocals_file = Path(vocals_path)
        if vocals_file.exists():
            size = vocals_file.stat().st_size
            vocals_file.unlink()
            cleaned_files.append(vocals_file.name)
            cleaned_size += size

    for segment_file in task_dir.glob("segment_*.wav"):
        size = segment_file.stat().st_size
        segment_file.unlink()
        cleaned_files.append(segment_file.name)
        cleaned_size += size

    for wav_file in task_dir.glob("*.wav"):
        if "source" in str(wav_file.parent):
            continue
        if "_Vocals_" in wav_file.name or "(Vocals)" in wav_file.name:
            continue
        if wav_file.name.startswith("segment_"):
            continue
        size = wav_file.stat().st_size
        wav_file.unlink()
        cleaned_files.append(wav_file.name)
        cleaned_size += size

    if cleaned_files:
        size_mb = cleaned_size / (1024 * 1024)
        logger.info(f"Cleaned up {len(cleaned_files)} intermediate files ({size_mb:.1f} MB): {cleaned_files}")


# ---------------------------------------------------------------------------
# Step update — writes to TaskStore + publishes events
# ---------------------------------------------------------------------------

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

    total_steps = len(PIPELINE_STEPS)
    completed_count = len(task.completed_steps)
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
    await bus.publish(TaskEvent(task.id, "step", {
        "step": step,
        "completed": completed,
        "progress": task.progress,
        "message": task.message,
    }))


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

async def run_pipeline(task: Task) -> None:
    """Run full pipeline: ingest → preprocess → recognize → analyze → archive."""
    from app.services.ingestion import download_media
    from app.services.preprocessing import separate_vocals
    from app.services.recognition import transcribe_audio
    from app.services.analysis import polish_text, summarize_text, generate_mindmap, analyze_content
    from app.services.archiving import archive_result

    source = _clean_source_path(task.source)

    # Step 1: Download or copy local file
    await _update_step(task, PipelineStep.DOWNLOAD)

    if _looks_like_local_path(task.source):
        source_path = Path(source)
        if not source_path.exists():
            raise FileNotFoundError(f"本地文件不存在: {source}")
        if not source_path.is_file():
            raise ValueError(f"路径不是文件: {source}")

        title = source_path.stem
        task_dir = _create_task_dir(task.id, title)
        dest_source = task_dir / "source" / source_path.name
        shutil.copy2(source_path, dest_source)

        video_exts = {".mp4", ".mkv", ".avi", ".webm", ".mov"}
        audio_exts = {".mp3", ".wav", ".flac", ".m4a", ".ogg"}

        if source_path.suffix.lower() in video_exts:
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
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(source, download=False)
            title = info.get("title", "unknown") if info else "unknown"

        task_dir = _create_task_dir(task.id, title)
        source_dir = task_dir / "source"

        ingest = await download_media(source, output_dir=source_dir)
        audio_path = ingest.get("file_path")
        metadata = MediaMetadata(**ingest.get("metadata", {"title": source}))

    await _update_step(task, PipelineStep.DOWNLOAD, completed=True)

    # Step 2: Separate vocals
    await _update_step(task, PipelineStep.SEPARATE)
    skip_separation = task.options.get("skip_separation", False)
    if skip_separation:
        vocals_path = audio_path
    else:
        preprocess = await separate_vocals(audio_path, output_dir=task_dir)
        vocals_path = preprocess.get("vocals_path", audio_path)
    await _update_step(task, PipelineStep.SEPARATE, completed=True)

    # Step 3: Transcribe
    await _update_step(task, PipelineStep.TRANSCRIBE)
    recognition = await transcribe_audio(vocals_path, output_dir=task_dir)
    transcript = " ".join(s["text"] for s in recognition.get("segments", []))
    srt = recognition.get("srt", "")
    await _update_step(task, PipelineStep.TRANSCRIBE, completed=True)

    # Step 4: Analyze content
    await _update_step(task, PipelineStep.ANALYZE)
    video_metadata = {
        "uploader": metadata.uploader,
        "description": metadata.description,
        "tags": metadata.tags,
        "chapters": [{"title": ch.title, "start_time": ch.start_time} for ch in metadata.chapters] if metadata.chapters else None,
    }
    analysis = await analyze_content(transcript, metadata.title, metadata=video_metadata)
    await _update_step(task, PipelineStep.ANALYZE, completed=True)

    # Step 5: Polish transcript
    await _update_step(task, PipelineStep.POLISH)
    polished = await polish_text(srt, context=analysis)
    await _update_step(task, PipelineStep.POLISH, completed=True)

    # Step 6: Generate summary and mindmap
    await _update_step(task, PipelineStep.SUMMARIZE)
    summary = await summarize_text(transcript)
    mindmap = await generate_mindmap(transcript)
    await _update_step(task, PipelineStep.SUMMARIZE, completed=True)

    # Step 7: Archive
    await _update_step(task, PipelineStep.ARCHIVE)
    archive = await archive_result(
        metadata,
        polished_srt=polished,
        summary=summary,
        mindmap=mindmap,
        original_srt=srt,
        work_dir=task_dir,
        analysis=analysis,
    )

    _cleanup_intermediate_files(task_dir, audio_path, vocals_path)
    await _update_step(task, PipelineStep.ARCHIVE, completed=True)

    task.result = {
        "metadata": metadata.model_dump(mode="json"),
        "transcript_segments": len(recognition.get("segments", [])),
        "archive": archive,
        "output_dir": str(task_dir),
        "analysis": analysis,
    }


# ---------------------------------------------------------------------------
# Task processor — called by queue worker
# ---------------------------------------------------------------------------

async def process_task(task_id: UUID) -> None:
    """Process a single task (called by TaskQueue worker)."""
    from app.services.ingestion import download_media
    from app.services.preprocessing import separate_vocals
    from app.services.recognition import transcribe_audio
    from app.services.analysis import polish_text, summarize_text, generate_mindmap

    store = get_task_store()
    bus = get_event_bus()

    task = store.get(task_id)
    if not task:
        return

    task.status = TaskStatus.PROCESSING
    task.updated_at = datetime.now()
    store.update_status(task_id, TaskStatus.PROCESSING)
    await bus.publish(TaskEvent(task_id, "processing"))

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

        store.update_status(
            task_id,
            TaskStatus.COMPLETED,
            progress=1.0,
            result=task.result,
            completed_at=task.completed_at,
        )
        await bus.publish(TaskEvent(task_id, "completed", {
            "output_dir": task.result.get("output_dir") if task.result else None,
        }))

    except Exception as e:
        logger.exception(f"Task {task_id} failed")
        task.status = TaskStatus.FAILED
        task.error = str(e)

        store.update_status(
            task_id,
            TaskStatus.FAILED,
            error=str(e),
            completed_at=datetime.now(),
        )
        await bus.publish(TaskEvent(task_id, "failed", {"error": str(e)}))
