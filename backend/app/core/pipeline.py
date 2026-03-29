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
    """Create a dedicated directory for this task under data/{title}/."""
    settings = get_runtime_settings()
    data_root = Path(settings.data_root).resolve()

    if title:
        dir_name = _sanitize_filename(title)
    else:
        dir_name = str(task_id)[:8]

    task_dir = data_root / dir_name
    # Handle duplicate names by appending (2), (3), etc.
    if task_dir.exists():
        counter = 2
        while True:
            candidate = data_root / f"{dir_name} ({counter})"
            if not candidate.exists():
                task_dir = candidate
                break
            counter += 1

    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


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
    # Resolve to absolute paths so filenames starting with '-' can't be
    # misinterpreted as ffmpeg options.
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path.resolve()),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(output_path.resolve()),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    return output_path


def _cleanup_vocals(task_dir: Path, audio_path: str | None, vocals_path: str | None) -> None:
    """Clean up UVR vocals and ASR segment files after transcription.

    Called right after TRANSCRIBE completes — these large WAVs are no longer
    needed once ASR is done.
    """
    cleaned_files = []
    cleaned_size = 0

    # Delete UVR vocals output (only if it's a separate file from the source audio)
    if vocals_path and vocals_path != audio_path:
        vocals_file = Path(vocals_path)
        if vocals_file.exists():
            size = vocals_file.stat().st_size
            vocals_file.unlink()
            cleaned_files.append(vocals_file.name)
            cleaned_size += size

    # Delete ASR segment files
    for segment_file in task_dir.glob("segment_*.wav"):
        size = segment_file.stat().st_size
        segment_file.unlink()
        cleaned_files.append(segment_file.name)
        cleaned_size += size

    if cleaned_files:
        size_mb = cleaned_size / (1024 * 1024)
        logger.info(f"Cleaned up vocals/segments ({size_mb:.1f} MB): {cleaned_files}")


def _cleanup_extracted_audio(task_dir: Path, audio_path: str | None, media_type: str | None) -> None:
    """Clean up the extracted WAV from video in the final archive step.

    Only deletes the ffmpeg-extracted WAV for video files. Audio-only files
    keep their source since it IS the original media.
    """
    if media_type != "video" or not audio_path:
        return
    audio_file = Path(audio_path)
    if audio_file.exists() and audio_file.suffix.lower() == ".wav":
        size = audio_file.stat().st_size
        audio_file.unlink()
        logger.info(f"Cleaned up extracted audio ({size / (1024*1024):.1f} MB): {audio_file.name}")




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

async def run_pipeline(task: Task, _download_worker_call: bool = False) -> None:
    """Run full pipeline: ingest → preprocess → recognize → analyze → archive.

    Supports:
    - Checkpoint resume: skips steps already in task.completed_steps and
      reconstructs needed variables from files already written to disk.
    - Two-stage execution: when _download_worker_call=True, runs only the
      DOWNLOAD step then calls advance_to_gpu() and returns. The GPU worker
      calls this again with _download_worker_call=False to run the rest.
    - GPU semaphore: UVR + ASR are protected by gpu_semaphore so concurrent
      workers never fight over VRAM.
    """
    from app.services.ingestion import download_media
    from app.services.ingestion.ytdlp import download_subtitles
    from app.services.ingestion.local import find_local_subtitle, parse_nfo
    from app.services.preprocessing import separate_vocals
    from app.services.recognition import transcribe_audio
    from app.services.recognition.subtitle_processor import process_subtitles
    from app.services.analysis import polish_text, summarize_text, generate_mindmap, analyze_content
    from app.services.archiving import archive_result
    from app.core.queue import get_task_queue

    rt = get_runtime_settings()
    source = _clean_source_path(task.source)
    platform_subtitle = None
    use_platform_subtitles = rt.prefer_platform_subtitles and not task.options.get("force_asr", False)

    # Resolve pre-created task dir
    task_dir = None
    if task.result and task.result.get("output_dir"):
        candidate = Path(task.result["output_dir"])
        if candidate.exists():
            task_dir = candidate

    done = set(task.completed_steps or [])
    logger.info(f"Task {task.id}: starting pipeline, already done: {done}")

    # Variables that later steps depend on — populated either by running the
    # step or by reading back files written in a previous run.
    audio_path: str | None = None
    vocals_path: str | None = None
    metadata: "MediaMetadata | None" = None
    has_subtitle: bool = False
    srt: str = ""
    transcript: str = ""
    polished: str | None = None
    polished_md: str | None = None
    subtitle_source: str = "asr"
    recognition_segments: list = []
    analysis: dict = {}
    summary: dict = {}
    mindmap: str = ""

    # ── Checkpoint restore helpers ─────────────────────────────────────────
    def _restore_metadata() -> bool:
        """Read metadata.json back from disk into `metadata`. Returns True on success."""
        nonlocal metadata
        if task_dir is None:
            return False
        meta_path = task_dir / "metadata.json"
        if not meta_path.exists():
            return False
        try:
            import json as _json
            raw = _json.loads(meta_path.read_text(encoding="utf-8"))
            metadata = MediaMetadata(
                title=raw.get("title", task_dir.name),
                source_url=raw.get("source_url", ""),
                media_type=raw.get("media_type", "audio"),
                file_path=raw.get("file_path"),
                uploader=raw.get("uploader"),
                description=raw.get("description"),
                duration=raw.get("duration"),
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to restore metadata: {e}")
            return False

    def _restore_audio_paths() -> bool:
        """Find audio/vocals files on disk. Returns True if usable paths found."""
        nonlocal audio_path, vocals_path
        if task_dir is None:
            return False
        # Vocals (post-UVR)
        for candidate in task_dir.glob("vocals*.wav"):
            vocals_path = str(candidate)
            audio_path = vocals_path
            return True
        # Raw extracted audio
        for candidate in task_dir.glob("*.wav"):
            audio_path = str(candidate)
            vocals_path = audio_path
            return True
        # Original audio (mp3/m4a/etc.)
        for f in task_dir.iterdir():
            if f.is_file() and f.suffix.lower() in {".mp3", ".flac", ".m4a", ".ogg"}:
                audio_path = str(f)
                vocals_path = audio_path
                return True
        return False

    def _restore_transcript() -> bool:
        """Read transcript SRT back from disk. Returns True if found."""
        nonlocal srt, transcript, polished, polished_md, subtitle_source
        if task_dir is None:
            return False
        polished_path = task_dir / "transcript_polished.srt"
        raw_path = task_dir / "transcript.srt"
        if polished_path.exists():
            polished = polished_path.read_text(encoding="utf-8")
            subtitle_source = "asr"
        if raw_path.exists():
            srt = raw_path.read_text(encoding="utf-8")
            transcript = " ".join(
                line.strip() for line in srt.splitlines()
                if line.strip() and not line.strip().isdigit()
                and "-->" not in line
            )
            return True
        return bool(polished)

    def _restore_analysis() -> bool:
        nonlocal analysis
        if task_dir is None:
            return False
        path = task_dir / "analysis.json"
        if path.exists():
            try:
                import json as _j
                analysis = _j.loads(path.read_text(encoding="utf-8"))
                return True
            except Exception:
                pass
        return False

    def _restore_summary() -> bool:
        nonlocal summary
        if task_dir is None:
            return False
        # summary is stored as formatted markdown; we can't fully restore the dict
        # but returning True lets us skip re-running summarize_text
        return (task_dir / "summary.md").exists()

    def _restore_mindmap() -> bool:
        nonlocal mindmap
        if task_dir is None:
            return False
        path = task_dir / "mindmap.md"
        if path.exists():
            mindmap = path.read_text(encoding="utf-8")
            return True
        return False

    # ── Step 1: DOWNLOAD ───────────────────────────────────────────────────
    if PipelineStep.DOWNLOAD in done:
        logger.info(f"Task {task.id}: skipping DOWNLOAD (already done), restoring from disk")
        _restore_metadata()
        _restore_audio_paths()
        # Restore has_subtitle flag from disk (check for subtitle files)
        if task_dir:
            sub_dir = task_dir / "subtitles"
            if sub_dir.exists() and any(sub_dir.iterdir()):
                has_subtitle = True
            elif any(task_dir.glob("transcript_polished.srt")):
                # Platform subtitle path writes polished immediately
                has_subtitle = True
    else:
        await _update_step(task, PipelineStep.DOWNLOAD)

        if source.startswith("upload://") or _looks_like_local_path(task.source):
            # Two sub-cases:
            #  1) upload:// — file already lives inside task_dir (browser upload)
            #  2) local path — file on disk, move it into task_dir
            is_browser_upload = source.startswith("upload://")

            if is_browser_upload:
                # File is already in task_dir — find it
                upload_name = source.removeprefix("upload://")
                if task_dir is None:
                    raise RuntimeError("upload:// source but task_dir is None")
                dest_source = task_dir / upload_name
                if not dest_source.exists():
                    raise FileNotFoundError(f"上传文件不存在: {dest_source}")
                source_path = dest_source  # for subtitle/nfo search (won't find any — that's fine)
            else:
                source_path = Path(source)
                if not source_path.exists():
                    raise FileNotFoundError(f"本地文件不存在: {source}")
                if not source_path.is_file():
                    raise ValueError(f"路径不是文件: {source}")
                title = source_path.stem
                if not task_dir:
                    task_dir = create_task_dir(task.id, title)
                dest_source = task_dir / source_path.name
                # Move instead of copy — same partition is instant rename
                shutil.move(str(source_path), str(dest_source))

            title = dest_source.stem
            video_exts = {".mp4", ".mkv", ".avi", ".webm", ".mov"}
            audio_exts = {".mp3", ".wav", ".flac", ".m4a", ".ogg"}

            if dest_source.suffix.lower() in video_exts:
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
                # For browser uploads source_path == dest_source (no original dir to search)
                if not is_browser_upload and use_platform_subtitles:
                    platform_subtitle = find_local_subtitle(source_path)
                    if platform_subtitle:
                        logger.info(f"Found local subtitle: {platform_subtitle['subtitle_path']}")

                if not is_browser_upload:
                    nfo_meta = parse_nfo(source_path)
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

            elif dest_source.suffix.lower() in audio_exts:
                audio_path = str(dest_source)
                metadata = MediaMetadata(
                    title=title,
                    source_url=str(source_path),
                    media_type="audio",
                    file_path=str(dest_source),
                )
            else:
                raise ValueError(f"Unsupported file format: {dest_source.suffix}")

            has_subtitle = platform_subtitle is not None

            # Write metadata.json immediately after local file processing
            meta_path = write_metadata_json(task_dir, metadata, status="processing")
            await _emit_file_ready(task, "metadata.json", str(meta_path))

        else:
            # For Bilibili URLs, skip yt-dlp probe (403 without cookie);
            # BBDown inside download_media will handle title extraction.
            source_type = _detect_source_type(source)
            if source_type != "bilibili":
                import yt_dlp
                with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
                    info = ydl.extract_info(source, download=False)
                    title = info.get("title", "unknown") if info else "unknown"
            else:
                title = None

            if not task_dir:
                task_dir = create_task_dir(task.id, title or "download")

            ingest = await download_media(source, output_dir=task_dir)
            audio_path = ingest.get("file_path")
            metadata = MediaMetadata(**ingest.get("metadata", {"title": source}))
            # Store video path for frontend playback
            if ingest.get("video_path"):
                metadata.file_path = ingest["video_path"]

            # Rename task_dir to real title if it was a placeholder (e.g. Bilibili)
            real_title = metadata.title
            if real_title and task_dir.name != _sanitize_filename(real_title):
                new_dir = task_dir.parent / _sanitize_filename(real_title)
                if not new_dir.exists():
                    task_dir.rename(new_dir)
                    task_dir = new_dir
                    # Update file paths to reflect new directory
                    if audio_path:
                        audio_path = str(new_dir / Path(audio_path).name)
                    if metadata.file_path:
                        metadata.file_path = str(new_dir / Path(metadata.file_path).name)
                    logger.info(f"Renamed task dir to: {new_dir}")

            # Try to download platform subtitles
            if use_platform_subtitles:
                try:
                    sub_dir = task_dir / "subtitles"
                    platform_subtitle = await download_subtitles(source, sub_dir)
                    if platform_subtitle.get("subtitle_path"):
                        logger.info(f"Downloaded platform subtitle: {platform_subtitle['subtitle_path']}")
                    else:
                        platform_subtitle = None
                        # Clean up empty subtitles directory
                        if sub_dir.exists() and not any(sub_dir.iterdir()):
                            sub_dir.rmdir()
                except Exception as e:
                    logger.warning(f"Subtitle download failed: {e}")
                    platform_subtitle = None

            has_subtitle = platform_subtitle is not None

            # Write metadata.json immediately after download
            meta_path = write_metadata_json(task_dir, metadata, status="processing")
            await _emit_file_ready(task, "metadata.json", str(meta_path))

        await _update_step(task, PipelineStep.DOWNLOAD, completed=True)
    # end if DOWNLOAD not in done

    # Sanity: we must have a task_dir by now
    if task_dir is None or metadata is None:
        raise RuntimeError("task_dir or metadata missing after DOWNLOAD step — cannot continue")

    # Hand off to GPU queue if we were called from a download worker.
    # The GPU worker will call process_task again; at that point DOWNLOAD is
    # in completed_steps so this block is skipped and we continue below.
    if _download_worker_call:
        await get_task_queue().advance_to_gpu(task.id)
        return

    # ── Steps 2+3: SEPARATE + TRANSCRIBE — GPU-bound, serialised by semaphore ──
    gpu_sem = get_task_queue().gpu_semaphore

    if PipelineStep.SEPARATE in done and PipelineStep.TRANSCRIBE in done:
        logger.info(f"Task {task.id}: skipping SEPARATE+TRANSCRIBE (already done), restoring transcript")
        _restore_transcript()
        _restore_audio_paths()
    else:
        async with gpu_sem:
            logger.info(f"Task {task.id}: acquired GPU semaphore")

            # Step 2: Separate vocals
            if PipelineStep.SEPARATE in done:
                logger.info(f"Task {task.id}: skipping SEPARATE, restoring audio paths")
                _restore_audio_paths()
            else:
                await _update_step(task, PipelineStep.SEPARATE)
                skip_separation = task.options.get("skip_separation", False) or has_subtitle
                if skip_separation:
                    vocals_path = audio_path
                else:
                    preprocess = await separate_vocals(audio_path, output_dir=task_dir)
                    vocals_path = preprocess.get("vocals_path", audio_path)
                await _update_step(task, PipelineStep.SEPARATE, completed=True)

            # Step 3: Transcribe
            if PipelineStep.TRANSCRIBE in done:
                logger.info(f"Task {task.id}: skipping TRANSCRIBE, restoring transcript")
                _restore_transcript()
            else:
                await _update_step(task, PipelineStep.TRANSCRIBE)
                if has_subtitle:
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
                    num_speakers = task.options.get("num_speakers")
                    recognition = await transcribe_audio(vocals_path, output_dir=task_dir, num_speakers=num_speakers)
                    transcript = " ".join(s["text"] for s in recognition.get("segments", []))
                    srt = recognition.get("srt", "")
                    polished = None
                    polished_md = None
                    subtitle_source = "asr"
                    recognition_segments = recognition.get("segments", [])

                # Write transcript.srt immediately
                if srt:
                    srt_path = task_dir / "transcript.srt"
                    srt_path.write_text(srt, encoding="utf-8")
                    await _emit_file_ready(task, "transcript.srt", str(srt_path))
                if has_subtitle and polished:
                    polished_srt_path = task_dir / "transcript_polished.srt"
                    polished_srt_path.write_text(polished, encoding="utf-8")
                    await _emit_file_ready(task, "transcript_polished.srt", str(polished_srt_path))
                    if polished_md:
                        polished_md_path = task_dir / "transcript_polished.md"
                        polished_md_path.write_text(polished_md, encoding="utf-8")

                await _update_step(task, PipelineStep.TRANSCRIBE, completed=True)
            # end if TRANSCRIBE not in done
        # end async with gpu_sem

    # Clean up UVR vocals and segment files immediately after ASR is done —
    # these large WAVs are no longer needed and can free significant disk space.
    _cleanup_vocals(task_dir, audio_path, vocals_path)

    # end if SEPARATE+TRANSCRIBE not both done

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
        _cleanup_extracted_audio(task_dir, audio_path, metadata.media_type if metadata else None)
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

    # ── Step 4: Analyze + Summarize + Mindmap (parallel, CPU/network) ────────
    if PipelineStep.ANALYZE in done:
        logger.info(f"Task {task.id}: skipping ANALYZE, restoring from disk")
        _restore_analysis()
        _restore_summary()
        _restore_mindmap()
    else:
        await _update_step(task, PipelineStep.ANALYZE)
        video_metadata = {
            "uploader": metadata.uploader,
            "description": metadata.description,
            "tags": metadata.tags,
            "chapters": [{"title": ch.title, "start_time": ch.start_time} for ch in metadata.chapters] if metadata.chapters else None,
        }
        # Build mindmap metadata with title, chapters, description for map-reduce
        mindmap_metadata = {
            "title": metadata.title,
            "uploader": metadata.uploader,
            "description": metadata.description,
            "chapters": [{"title": ch.title, "start_time": ch.start_time} for ch in metadata.chapters] if metadata.chapters else None,
        }
        analysis, summary, mindmap = await asyncio.gather(
            analyze_content(transcript, metadata.title, metadata=video_metadata),
            summarize_text(transcript),
            generate_mindmap(srt or transcript, metadata=mindmap_metadata),
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
    # end if ANALYZE not in done

    # ── Step 5: Polish transcript (CPU/network) ────────────────────────────
    if PipelineStep.POLISH in done:
        logger.info(f"Task {task.id}: skipping POLISH, restoring from disk")
        _restore_transcript()  # picks up polished if present
    else:
        await _update_step(task, PipelineStep.POLISH)
        if has_subtitle:
            logger.info("Skipping POLISH step (platform subtitle already polished)")
        else:
            hotwords = task.options.get("hotwords")
            if hotwords and analysis:
                existing = analysis.get("proper_nouns", []) or []
                analysis["proper_nouns"] = list(set(existing + hotwords))
            polished = await polish_text(srt, context=analysis)
        if not has_subtitle and polished:
            from app.services.analysis import srt_to_markdown
            polished_srt_path = task_dir / "transcript_polished.srt"
            polished_srt_path.write_text(polished, encoding="utf-8")
            await _emit_file_ready(task, "transcript_polished.srt", str(polished_srt_path))
            polished_md_content = srt_to_markdown(polished, metadata.title)
            polished_md_path = task_dir / "transcript_polished.md"
            polished_md_path.write_text(polished_md_content, encoding="utf-8")
        await _update_step(task, PipelineStep.POLISH, completed=True)
    # end if POLISH not in done

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

    _cleanup_extracted_audio(task_dir, audio_path, metadata.media_type if metadata else None)

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

async def process_task(task_id: UUID, _download_worker_call: bool = False) -> None:
    """Process a single task — called by both download workers and GPU worker.

    download worker  → process_task(id, _download_worker_call=True)
                         runs DOWNLOAD, then advance_to_gpu(), returns
    GPU worker       → process_task(id, _download_worker_call=False)
                         DOWNLOAD already in completed_steps, skips it,
                         runs SEPARATE → TRANSCRIBE → ANALYZE → POLISH → ARCHIVE
    """
    from app.services.ingestion import download_media
    from app.services.preprocessing import separate_vocals
    from app.services.recognition import transcribe_audio
    from app.services.analysis import polish_text, summarize_text, generate_mindmap

    store = get_task_store()
    bus = get_event_bus()

    task = store.get(task_id)
    if not task:
        return

    # Only set PROCESSING status on first entry (download worker call).
    # On GPU worker re-entry the task is already PROCESSING.
    if task.status != TaskStatus.PROCESSING:
        store.update_status(task_id, TaskStatus.PROCESSING)
        await bus.publish(TaskEvent(task_id, "processing"))

    # Re-read from DB to get latest completed_steps
    task = store.get(task_id)

    try:
        if task.task_type == TaskType.PIPELINE:
            await run_pipeline(task, _download_worker_call=_download_worker_call)
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

        # If this was the download-worker call, run_pipeline already returned
        # early after advance_to_gpu() — don't mark COMPLETED yet.
        if _download_worker_call and task.task_type == TaskType.PIPELINE:
            return

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
