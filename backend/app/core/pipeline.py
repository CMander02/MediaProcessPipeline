"""Pipeline orchestration — extracted from api.routes.tasks.

This module owns the full processing pipeline (download → archive) and uses
TaskStore + EventBus for state management instead of in-memory dicts.
"""

import asyncio
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
    {"id": PipelineStep.ANALYZE, "name": "分析+摘要+脑图", "name_en": "Analyzing & summarizing"},
    {"id": PipelineStep.POLISH, "name": "润色字幕", "name_en": "Polishing transcript"},
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


def create_task_dir(task_id: UUID, title: str | None = None) -> Path:
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


def find_task_dir(task_id: UUID) -> Path | None:
    """Find an existing task directory by task_id prefix."""
    settings = get_runtime_settings()
    data_root = Path(settings.data_root).resolve()
    task_id_short = str(task_id)[:8]
    for d in data_root.iterdir():
        if d.is_dir() and d.name.startswith(task_id_short):
            return d
    return None


def write_metadata_json(task_dir: Path, metadata: MediaMetadata | dict, status: str = "processing") -> Path:
    """Write or update metadata.json in the task directory."""
    import json
    meta_path = task_dir / "metadata.json"
    if isinstance(metadata, MediaMetadata):
        data = metadata.model_dump(mode="json")
    else:
        data = dict(metadata)
    data["status"] = status
    meta_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta_path


async def _emit_file_ready(task: Task, filename: str, file_path: str) -> None:
    """Emit a file_ready SSE event when a file is written to disk."""
    bus = get_event_bus()
    await bus.publish(TaskEvent(task.id, "file_ready", {
        "file": filename,
        "path": file_path,
    }))


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


def _cleanup_source_copy(task_dir: Path, metadata: MediaMetadata) -> None:
    """Delete source/ copy for local files when the original still exists."""
    source_url = metadata.source_url or ""
    if not _looks_like_local_path(source_url):
        return  # Downloaded content — source/ is the only copy
    original = Path(source_url)
    if not original.exists():
        return  # Original is gone, keep the copy
    source_dir = task_dir / "source"
    if not source_dir.exists():
        return
    try:
        shutil.rmtree(source_dir)
        logger.info(f"Deleted source copy: {source_dir} (original at {original})")
    except Exception as e:
        logger.warning(f"Failed to delete source copy {source_dir}: {e}")


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
    from app.services.ingestion.ytdlp import download_subtitles
    from app.services.ingestion.local import find_local_subtitle, parse_nfo, find_original_file
    from app.services.preprocessing import separate_vocals
    from app.services.recognition import transcribe_audio
    from app.services.recognition.subtitle_processor import process_subtitles
    from app.services.analysis import polish_text, summarize_text, generate_mindmap, analyze_content
    from app.services.archiving import archive_result

    rt = get_runtime_settings()
    source = _clean_source_path(task.source)
    platform_subtitle = None  # {"subtitle_path", "subtitle_lang", "subtitle_format"}
    use_platform_subtitles = rt.prefer_platform_subtitles and not task.options.get("force_asr", False)

    # Resolve pre-created task dir (from create_task) or create one
    task_dir = None
    if task.result and task.result.get("output_dir"):
        candidate = Path(task.result["output_dir"])
        if candidate.exists():
            task_dir = candidate

    # Step 1: Download or copy local file
    await _update_step(task, PipelineStep.DOWNLOAD)

    if _looks_like_local_path(task.source):
        source_path = Path(source)
        if not source_path.exists():
            raise FileNotFoundError(f"本地文件不存在: {source}")
        if not source_path.is_file():
            raise ValueError(f"路径不是文件: {source}")

        title = source_path.stem
        if not task_dir:
            task_dir = create_task_dir(task.id, title)
        (task_dir / "source").mkdir(exist_ok=True)
        dest_source = task_dir / "source" / source_path.name
        shutil.copy2(source_path, dest_source)

        video_exts = {".mp4", ".mkv", ".avi", ".webm", ".mov"}
        audio_exts = {".mp3", ".wav", ".flac", ".m4a", ".ogg"}

        if source_path.suffix.lower() in video_exts:
            audio_path = task_dir / f"{title}.wav"
            await asyncio.to_thread(_extract_audio_from_video, dest_source, audio_path)
            audio_path = str(audio_path)
            metadata = MediaMetadata(
                title=title,
                source_url=str(source_path),
                media_type="video",
                file_path=str(dest_source),
            )

            # Search for local subtitle and NFO metadata
            # If file is in uploads dir, try to find the original location
            search_path = source_path
            if use_platform_subtitles:
                platform_subtitle = find_local_subtitle(search_path)
                if not platform_subtitle:
                    # File may have been uploaded — search for original location
                    original = find_original_file(search_path)
                    if original:
                        search_path = original
                        platform_subtitle = find_local_subtitle(search_path)
                if platform_subtitle:
                    logger.info(f"Found local subtitle: {platform_subtitle['subtitle_path']}")

            nfo_meta = parse_nfo(search_path)
            if nfo_meta:
                if nfo_meta.get("title"):
                    metadata.title = nfo_meta["title"]
                if nfo_meta.get("description"):
                    metadata.description = nfo_meta["description"]
                if nfo_meta.get("tags"):
                    metadata.tags = nfo_meta["tags"]
                if nfo_meta.get("uploader"):
                    metadata.uploader = nfo_meta["uploader"]
                if nfo_meta.get("upload_date"):
                    metadata.upload_date = nfo_meta["upload_date"]
                if nfo_meta.get("source_url"):
                    metadata.source_url = nfo_meta["source_url"]

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

        if not task_dir:
            task_dir = create_task_dir(task.id, title)
        source_dir = task_dir / "source"
        source_dir.mkdir(exist_ok=True)

        ingest = await download_media(source, output_dir=source_dir)
        audio_path = ingest.get("file_path")
        metadata = MediaMetadata(**ingest.get("metadata", {"title": source}))

        # Try to download platform subtitles
        if use_platform_subtitles:
            try:
                sub_dir = task_dir / "subtitles"
                platform_subtitle = await download_subtitles(source, sub_dir)
                if platform_subtitle.get("subtitle_path"):
                    logger.info(f"Downloaded platform subtitle: {platform_subtitle['subtitle_path']}")
                else:
                    platform_subtitle = None
            except Exception as e:
                logger.warning(f"Subtitle download failed: {e}")
                platform_subtitle = None

    has_subtitle = platform_subtitle is not None

    # Write metadata.json immediately after download
    meta_path = write_metadata_json(task_dir, metadata, status="processing")
    await _emit_file_ready(task, "metadata.json", str(meta_path))

    await _update_step(task, PipelineStep.DOWNLOAD, completed=True)

    # Step 2: Separate vocals (skip if using platform subtitles)
    await _update_step(task, PipelineStep.SEPARATE)
    skip_separation = task.options.get("skip_separation", False) or has_subtitle
    if skip_separation:
        vocals_path = audio_path
    else:
        preprocess = await separate_vocals(audio_path, output_dir=task_dir)
        vocals_path = preprocess.get("vocals_path", audio_path)
    await _update_step(task, PipelineStep.SEPARATE, completed=True)

    # Step 3: Transcribe
    await _update_step(task, PipelineStep.TRANSCRIBE)

    if has_subtitle:
        # Platform subtitle path: LLM processes subtitles for speaker ID + punctuation
        logger.info("Using platform subtitle path (skipping ASR)")
        sub_result = await process_subtitles(
            subtitle_path=platform_subtitle["subtitle_path"],
            subtitle_format=platform_subtitle["subtitle_format"],
            metadata=metadata,
        )
        transcript = " ".join(s["text"] for s in sub_result.get("segments", []))
        srt = sub_result.get("srt", "")
        polished = sub_result.get("polished_srt", "")
        polished_md = sub_result.get("polished_md", "")
        subtitle_source = "platform"
        recognition_segments = sub_result.get("segments", [])
    else:
        # ASR path: transcribe audio
        recognition = await transcribe_audio(vocals_path, output_dir=task_dir)
        transcript = " ".join(s["text"] for s in recognition.get("segments", []))
        srt = recognition.get("srt", "")
        polished = None  # Will be filled in POLISH step
        polished_md = None
        subtitle_source = "asr"
        recognition_segments = recognition.get("segments", [])

    # Write transcript.srt immediately (raw or platform-polished)
    if srt:
        srt_path = task_dir / "transcript.srt"
        srt_path.write_text(srt, encoding="utf-8")
        await _emit_file_ready(task, "transcript.srt", str(srt_path))
    # For platform subtitles, also write polished immediately
    if has_subtitle and polished:
        polished_srt_path = task_dir / "transcript_polished.srt"
        polished_srt_path.write_text(polished, encoding="utf-8")
        await _emit_file_ready(task, "transcript_polished.srt", str(polished_srt_path))
        if polished_md:
            polished_md_path = task_dir / "transcript_polished.md"
            polished_md_path.write_text(polished_md, encoding="utf-8")

    await _update_step(task, PipelineStep.TRANSCRIBE, completed=True)

    # Guard: skip LLM if transcript is empty or trivially short
    if not transcript or len(transcript.strip()) < 10:
        logger.warning(f"Transcript is empty or too short ({len(transcript)} chars), skipping LLM analysis")
        await _update_step(task, PipelineStep.ANALYZE, completed=True)
        await _update_step(task, PipelineStep.POLISH, completed=True)

        await _update_step(task, PipelineStep.ARCHIVE)
        empty_analysis = {"language": "unknown", "content_type": "unknown", "main_topics": [],
                          "keywords": [], "proper_nouns": [], "speakers_detected": 0, "tone": "unknown"}
        empty_summary = {"tldr": "未检测到有效语音内容", "key_facts": [], "action_items": [], "topics": []}
        archive = await archive_result(
            metadata,
            polished_srt="",
            summary=empty_summary,
            mindmap="",
            original_srt=srt,
            work_dir=task_dir,
            analysis=empty_analysis,
        )
        write_metadata_json(task_dir, metadata, status="completed")
        _cleanup_intermediate_files(task_dir, audio_path, vocals_path)
        _cleanup_source_copy(task_dir, metadata)
        await _update_step(task, PipelineStep.ARCHIVE, completed=True)

        task.result = {
            "metadata": metadata.model_dump(mode="json"),
            "transcript_segments": 0,
            "archive": archive,
            "output_dir": str(task_dir),
            "analysis": empty_analysis,
            "warning": "未检测到有效语音内容，跳过 LLM 分析",
        }
        return

    # Step 4: Analyze + Summarize + Mindmap (parallel)
    await _update_step(task, PipelineStep.ANALYZE)
    video_metadata = {
        "uploader": metadata.uploader,
        "description": metadata.description,
        "tags": metadata.tags,
        "chapters": [{"title": ch.title, "start_time": ch.start_time} for ch in metadata.chapters] if metadata.chapters else None,
    }
    analysis, summary, mindmap = await asyncio.gather(
        analyze_content(transcript, metadata.title, metadata=video_metadata),
        summarize_text(transcript),
        generate_mindmap(transcript),
    )
    # Write analysis + summary + mindmap immediately
    import json as _json
    if analysis:
        analysis_path = task_dir / "analysis.json"
        analysis_path.write_text(_json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
        await _emit_file_ready(task, "analysis.json", str(analysis_path))
    if summary:
        from app.services.archiving.archive import SUMMARY_TEMPLATE, get_archive_service
        _svc = get_archive_service()
        sum_path = task_dir / "summary.md"
        sum_content = SUMMARY_TEMPLATE.format(
            title=metadata.title,
            source_url=metadata.source_url or "",
            date=datetime.now().strftime("%Y-%m-%d"),
            tldr=summary.get("tldr", ""),
            key_facts=_svc._fmt_list(summary.get("key_facts", [])),
        )
        sum_path.write_text(sum_content, encoding="utf-8")
        await _emit_file_ready(task, "summary.md", str(sum_path))
    if mindmap:
        mm_path = task_dir / "mindmap.md"
        mm_path.write_text(mindmap, encoding="utf-8")
        await _emit_file_ready(task, "mindmap.md", str(mm_path))

    await _update_step(task, PipelineStep.ANALYZE, completed=True)

    # Step 5: Polish transcript
    await _update_step(task, PipelineStep.POLISH)
    if has_subtitle:
        # Platform subtitle path already produced polished output in TRANSCRIBE step
        logger.info("Skipping POLISH step (platform subtitle already polished)")
    else:
        # ASR path: polish with LLM
        polished = await polish_text(srt, context=analysis)
    # Write polished transcript immediately (ASR path)
    if not has_subtitle and polished:
        from app.services.analysis import srt_to_markdown
        polished_srt_path = task_dir / "transcript_polished.srt"
        polished_srt_path.write_text(polished, encoding="utf-8")
        await _emit_file_ready(task, "transcript_polished.srt", str(polished_srt_path))
        polished_md_content = srt_to_markdown(polished, metadata.title)
        polished_md_path = task_dir / "transcript_polished.md"
        polished_md_path.write_text(polished_md_content, encoding="utf-8")

    await _update_step(task, PipelineStep.POLISH, completed=True)

    # Step 6: Archive (finalize — writes any missing files, sets status to completed)
    await _update_step(task, PipelineStep.ARCHIVE)
    archive = await archive_result(
        metadata,
        polished_srt=polished or "",
        summary=summary,
        mindmap=mindmap,
        original_srt=srt,
        work_dir=task_dir,
        analysis=analysis,
    )

    # Update metadata status to completed
    meta_path = write_metadata_json(task_dir, metadata, status="completed")
    await _emit_file_ready(task, "metadata.json", str(meta_path))

    _cleanup_intermediate_files(task_dir, audio_path, vocals_path)

    # Delete source/ copy for local files (original still exists)
    _cleanup_source_copy(task_dir, metadata)

    await _update_step(task, PipelineStep.ARCHIVE, completed=True)

    task.result = {
        "metadata": metadata.model_dump(mode="json"),
        "transcript_segments": len(recognition_segments),
        "archive": archive,
        "output_dir": str(task_dir),
        "analysis": analysis,
        "subtitle_source": subtitle_source,
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

        # Update metadata.json status to failed
        output_dir = task.result.get("output_dir") if task.result else None
        if output_dir:
            meta_path = Path(output_dir) / "metadata.json"
            if meta_path.exists():
                try:
                    import json as _json
                    meta = _json.loads(meta_path.read_text(encoding="utf-8"))
                    meta["status"] = "failed"
                    meta_path.write_text(_json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    pass

        store.update_status(
            task_id,
            TaskStatus.FAILED,
            error=str(e),
            completed_at=datetime.now(),
        )
        await bus.publish(TaskEvent(task_id, "failed", {"error": str(e)}))
