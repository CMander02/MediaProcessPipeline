"""Direct pipeline operation routes."""

import json
import logging
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Any

from app.services.ingestion import download_media, scan_inbox
from app.services.preprocessing import separate_vocals
from app.services.recognition import transcribe_audio
from app.services.analysis import polish_text, summarize_text, generate_mindmap
from app.services.archiving import list_archives
from app.services.cleanup import cleanup_failed_task, cleanup_orphaned_files, get_disk_usage
from app.core.settings import get_runtime_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


class DownloadRequest(BaseModel):
    url: str


class TranscribeRequest(BaseModel):
    audio_path: str
    language: str | None = None


class AnalyzeRequest(BaseModel):
    text: str


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a local media file for processing."""
    rt = get_runtime_settings()
    upload_dir = Path(rt.data_root).resolve() / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize filename
    safe_name = file.filename.replace("/", "_").replace("\\", "_") if file.filename else "uploaded_file"
    dest_path = upload_dir / safe_name

    # Handle duplicate filenames
    counter = 1
    original_stem = dest_path.stem
    while dest_path.exists():
        dest_path = upload_dir / f"{original_stem}_{counter}{dest_path.suffix}"
        counter += 1

    # Save uploaded file
    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    return {"file_path": str(dest_path), "filename": safe_name}


@router.get("/probe")
async def probe_url(url: str):
    """Extract metadata from a URL without downloading (for hotword suggestions)."""
    import asyncio

    def _probe(url: str) -> dict[str, Any]:
        import yt_dlp
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True}) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return {}
                return {
                    "title": info.get("title"),
                    "description": info.get("description"),
                    "tags": info.get("tags") or [],
                    "uploader": info.get("uploader"),
                    "duration": info.get("duration"),
                }
        except Exception as e:
            logger.warning(f"Probe failed for {url}: {e}")
            return {}

    result = await asyncio.to_thread(_probe, url)
    if not result:
        raise HTTPException(status_code=404, detail="无法获取视频信息")
    return result


@router.post("/download")
async def download(req: DownloadRequest):
    """Download media from URL."""
    return await download_media(req.url)


@router.post("/scan")
async def scan():
    """Scan inbox for new files."""
    files = await scan_inbox()
    return {"new_files": files, "count": len(files)}


@router.post("/separate")
async def separate(audio_path: str):
    """Separate vocals from audio."""
    return await separate_vocals(audio_path)


@router.post("/transcribe")
async def transcribe(req: TranscribeRequest):
    """Transcribe audio file."""
    return await transcribe_audio(req.audio_path, req.language)


@router.post("/polish")
async def polish(req: AnalyzeRequest):
    """Polish transcript text."""
    return {"polished": await polish_text(req.text)}


@router.post("/summarize")
async def summarize(req: AnalyzeRequest):
    """Generate summary."""
    return await summarize_text(req.text)


@router.post("/mindmap")
async def mindmap(req: AnalyzeRequest):
    """Generate mindmap."""
    return {"markdown": await generate_mindmap(req.text)}


@router.get("/archives")
async def archives(limit: int = 50):
    """List archived content."""
    return {"archives": await list_archives(limit)}


class ArchiveDeleteRequest(BaseModel):
    path: str


@router.delete("/archives")
async def delete_archive(req: ArchiveDeleteRequest):
    """Delete an archive directory and its associated task record."""
    archive_dir = Path(req.path)
    if not archive_dir.is_dir():
        raise HTTPException(404, "Archive directory not found")

    # Safety: only allow deleting within data_root
    rt = get_runtime_settings()
    data_root = Path(rt.data_root).resolve()
    try:
        archive_dir.resolve().relative_to(data_root)
    except ValueError:
        raise HTTPException(403, "Cannot delete paths outside data directory")

    # Try to find and delete the associated task record by matching output_dir
    task_deleted = False
    from uuid import UUID
    from app.core.database import get_task_store, _get_conn
    store = get_task_store()
    conn = _get_conn()
    archive_dir_str = str(archive_dir.resolve())
    # Search for task whose result JSON contains this output_dir path
    rows = conn.execute("SELECT id, result FROM tasks WHERE result IS NOT NULL").fetchall()
    for row in rows:
        try:
            result = json.loads(row["result"]) if isinstance(row["result"], str) else row["result"]
            if result and str(Path(result.get("output_dir", "")).resolve()) == archive_dir_str:
                store.delete(UUID(row["id"]))
                task_deleted = True
                break
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    # Also try to delete the uploaded source file
    source_deleted = False
    metadata_file = archive_dir / "metadata.json"
    if metadata_file.exists():
        try:
            meta = json.loads(metadata_file.read_text(encoding="utf-8"))
            source_url = meta.get("source_url", "")
            uploads_dir = data_root / "uploads"
            source_path = Path(source_url)
            if source_path.exists() and uploads_dir.resolve() in source_path.resolve().parents:
                source_path.unlink()
                source_deleted = True
        except Exception:
            pass

    # Delete the archive directory (with Windows file-lock retry)
    import time

    def _onerror_retry(func, path, exc_info):
        """Retry handler for shutil.rmtree on Windows PermissionError."""
        import stat
        if isinstance(exc_info[1], PermissionError):
            os.chmod(path, stat.S_IWRITE)
            try:
                func(path)
            except Exception:
                pass  # Will be caught by outer retry
        else:
            raise exc_info[1]

    import os
    last_err = None
    for attempt in range(3):
        try:
            shutil.rmtree(archive_dir, onerror=_onerror_retry)
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(0.5)
    else:
        # If folder still exists but is now empty, that's ok
        if archive_dir.exists() and any(archive_dir.iterdir()):
            raise HTTPException(500, f"Failed to delete archive: {last_err}")
        # Empty dir or gone — try final rmdir
        try:
            archive_dir.rmdir()
        except Exception:
            pass

    logger.info(f"Deleted archive: {archive_dir} (task={task_deleted}, source={source_deleted})")

    return {
        "message": "Deleted",
        "path": str(archive_dir),
        "task_deleted": task_deleted,
        "source_deleted": source_deleted,
    }


@router.get("/archives/thumbnail")
async def archive_thumbnail(path: str):
    """Get or generate a thumbnail for an archive directory.

    Checks for existing thumbnail.jpg/cover.jpg/cover.png, otherwise
    extracts a frame at 3s from the source video via ffmpeg.
    """
    archive_dir = Path(path)
    if not archive_dir.is_dir():
        raise HTTPException(404, "Archive directory not found")

    # Check for existing thumbnail / cover
    for candidate in ["thumbnail.jpg", "cover.jpg", "cover.png"]:
        thumb = archive_dir / candidate
        if thumb.exists():
            media_type = "image/png" if candidate.endswith(".png") else "image/jpeg"
            return FileResponse(thumb, media_type=media_type)

    # Try to generate from video in source/
    video_exts = {".mp4", ".mkv", ".avi", ".webm", ".mov"}
    source_dir = archive_dir / "source"
    video_file = None
    if source_dir.exists():
        for f in source_dir.iterdir():
            if f.suffix.lower() in video_exts:
                video_file = f
                break

    # Fallback: source/ deleted, try original source_url from metadata
    if not video_file:
        meta_path = archive_dir / "metadata.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                source_url = meta.get("source_url", "")
                if source_url:
                    original = Path(source_url)
                    if original.exists() and original.suffix.lower() in video_exts:
                        video_file = original
            except Exception:
                pass

    if not video_file:
        raise HTTPException(404, "No thumbnail or video found")

    # Generate thumbnail via ffmpeg
    thumb_path = archive_dir / "thumbnail.jpg"
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", "3", "-i", str(video_file),
                "-vframes", "1", "-vf", "scale=480:-2",
                "-q:v", "5", str(thumb_path),
            ],
            capture_output=True,
            timeout=15,
        )
    except Exception as e:
        logger.warning(f"ffmpeg thumbnail failed: {e}")
        raise HTTPException(500, "Thumbnail generation failed")

    if thumb_path.exists():
        return FileResponse(thumb_path, media_type="image/jpeg")

    raise HTTPException(500, "Thumbnail generation produced no output")


@router.post("/cleanup/{task_id}")
async def cleanup_task(task_id: str):
    """Clean up files from a specific task."""
    return await cleanup_failed_task(task_id)


@router.post("/cleanup")
async def cleanup_all(max_age_hours: int = 24):
    """Clean up orphaned temporary files."""
    return await cleanup_orphaned_files(max_age_hours)


@router.get("/disk-usage")
async def disk_usage():
    """Get disk usage statistics for data directory."""
    return await get_disk_usage()
