"""Direct pipeline operation routes."""

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Any

# Windows reserved device names (case-insensitive, with or without extension)
_WIN_RESERVED = re.compile(
    r'^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\..+)?$', re.IGNORECASE
)

import ipaddress
from urllib.parse import urlparse

from app.core.settings import get_runtime_settings
from app.core.database import get_task_store
from app.core.pipeline import (
    PIPELINE_STEPS, PipelineStep, _sanitize_filename,
    create_task_dir, write_metadata_json,
)
from app.core.queue import get_task_queue
from app.models import Task, TaskCreate as TaskCreateModel, TaskStatus

logger = logging.getLogger(__name__)


def _validate_url(url: str) -> None:
    """Validate URL to prevent SSRF — reject file://, internal IPs, localhost."""
    parsed = urlparse(url)

    # Only allow http(s)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, f"Unsupported URL scheme: {parsed.scheme!r} — only http/https allowed")

    hostname = parsed.hostname or ""

    # Reject localhost variants
    if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        raise HTTPException(400, "URL pointing to localhost is not allowed")

    # Reject private/reserved IP ranges
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            raise HTTPException(400, "URL pointing to private/reserved IP is not allowed")
    except ValueError:
        pass  # hostname is a domain name, not an IP — that's fine

    # Reject cloud metadata endpoints
    if hostname in ("169.254.169.254", "metadata.google.internal"):
        raise HTTPException(400, "URL pointing to cloud metadata endpoint is not allowed")

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


class DownloadRequest(BaseModel):
    url: str


class TranscribeRequest(BaseModel):
    audio_path: str
    language: str | None = None


class AnalyzeRequest(BaseModel):
    text: str


_ALLOWED_MEDIA_EXTS = {
    ".mp4", ".mkv", ".avi", ".webm", ".mov", ".flv", ".wmv",
    ".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".wma",
}


def _sanitize_upload_name(raw_name: str) -> str:
    """Sanitize an uploaded filename for safe filesystem use."""
    # Remove directory components
    safe = raw_name.replace("/", "_").replace("\\", "_")
    # Remove characters illegal on Windows
    safe = re.sub(r'[<>:"|?*\x00-\x1f]', '_', safe)
    # Strip leading/trailing dots and spaces
    safe = safe.strip('. ')
    # Prefix Windows reserved device names
    stem = safe.split('.')[0] if '.' in safe else safe
    if _WIN_RESERVED.match(stem):
        safe = f"_{safe}"
    return safe or "uploaded_file"


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    options: str = Form("{}"),
):
    """Upload a local media file, create task directory & task atomically.

    Saves the file directly into data/{title}/ — no intermediate uploads/ dir.
    Returns the created Task object so the frontend needs only one request.
    """
    raw_name = file.filename or "uploaded_file"

    # Validate extension
    ext = Path(raw_name).suffix.lower()
    if ext not in _ALLOWED_MEDIA_EXTS:
        raise HTTPException(
            400,
            f"不支持的文件格式: {ext or '(无扩展名)'}。"
            f"支持的格式: {', '.join(sorted(_ALLOWED_MEDIA_EXTS))}",
        )

    safe_name = _sanitize_upload_name(raw_name)
    title = Path(safe_name).stem
    video_exts = {".mp4", ".mkv", ".avi", ".webm", ".mov", ".flv", ".wmv"}
    media_type = "video" if ext in video_exts else "audio"

    # Parse options JSON from form field
    import json as _json
    try:
        task_options = _json.loads(options) if options else {}
    except (ValueError, TypeError):
        task_options = {}

    # Create task + directory
    from uuid import uuid4
    task = Task(
        task_type="pipeline",
        source=f"upload://{safe_name}",
        options=task_options,
        status=TaskStatus.QUEUED,
        current_step=PipelineStep.DOWNLOAD,
        message="等待处理...",
        steps=[s["id"] for s in PIPELINE_STEPS],
        completed_steps=[],
    )

    task_dir = create_task_dir(task.id, title)
    dest_path = task_dir / safe_name

    # Stream file to task_dir with size limit (10 GB)
    max_size = 10 * 1024 * 1024 * 1024
    written = 0
    try:
        with open(dest_path, "wb") as f:
            while chunk := await file.read(8 * 1024 * 1024):
                written += len(chunk)
                if written > max_size:
                    raise HTTPException(413, "File too large (limit: 10 GB)")
                f.write(chunk)
    except Exception:
        # Clean up task_dir on failure
        shutil.rmtree(task_dir, ignore_errors=True)
        raise

    # Write initial metadata
    write_metadata_json(task_dir, {
        "title": title,
        "source_url": f"upload://{safe_name}",
        "media_type": media_type,
    }, status="queued")

    task.result = {"output_dir": str(task_dir)}

    store = get_task_store()
    store.save(task)

    queue = get_task_queue()
    await queue.submit(task.id)

    return task


@router.get("/probe")
async def probe_url(url: str):
    """Extract metadata from a URL without downloading (for hotword suggestions)."""
    _validate_url(url)
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
    _validate_url(req.url)
    from app.services.ingestion import download_media
    return await download_media(req.url)


@router.post("/scan")
async def scan():
    """Scan inbox for new files."""
    from app.services.ingestion import scan_inbox
    files = await scan_inbox()
    return {"new_files": files, "count": len(files)}


@router.post("/separate")
async def separate(audio_path: str):
    """Separate vocals from audio."""
    from app.services.preprocessing import separate_vocals
    return await separate_vocals(audio_path)


@router.post("/transcribe")
async def transcribe(req: TranscribeRequest):
    """Transcribe audio file."""
    from app.services.recognition import transcribe_audio
    return await transcribe_audio(req.audio_path, req.language)


@router.post("/polish")
async def polish(req: AnalyzeRequest):
    """Polish transcript text."""
    from app.services.analysis import polish_text
    return {"polished": await polish_text(req.text)}


@router.post("/summarize")
async def summarize(req: AnalyzeRequest):
    """Generate summary."""
    from app.services.analysis import summarize_text
    return await summarize_text(req.text)


@router.post("/mindmap")
async def mindmap(req: AnalyzeRequest):
    """Generate mindmap."""
    from app.services.analysis import generate_mindmap
    return {"markdown": await generate_mindmap(req.text)}


@router.get("/archives")
async def archives():
    """List archived content (all, sorted by mtime desc)."""
    from app.services.archiving import list_archives
    return {"archives": await list_archives()}


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

    logger.info(f"Deleted archive: {archive_dir} (task={task_deleted})")

    return {
        "message": "Deleted",
        "path": str(archive_dir),
        "task_deleted": task_deleted,
    }


class ArchiveRenameRequest(BaseModel):
    path: str
    title: str


@router.post("/archives/rename")
async def rename_archive(req: ArchiveRenameRequest):
    """Update the title in an archive's metadata.json."""
    archive_dir = Path(req.path)
    if not archive_dir.is_dir():
        raise HTTPException(404, "Archive directory not found")

    rt = get_runtime_settings()
    data_root = Path(rt.data_root).resolve()
    try:
        archive_dir.resolve().relative_to(data_root)
    except ValueError:
        raise HTTPException(403, "Cannot modify paths outside data directory")

    new_title = req.title.strip()
    if not new_title:
        raise HTTPException(400, "Title cannot be empty")

    meta_path = archive_dir / "metadata.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    meta["title"] = new_title
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"success": True, "title": new_title}


@router.get("/archives/thumbnail")
async def archive_thumbnail(path: str):
    """Get or generate a thumbnail for an archive directory.

    Checks for existing thumbnail.jpg/cover.jpg/cover.png, otherwise
    extracts a frame at 3s from the source video via ffmpeg.
    """
    archive_dir = Path(path)
    if not archive_dir.is_dir():
        raise HTTPException(404, "Archive directory not found")

    # Security: only allow paths under data_root
    rt = get_runtime_settings()
    data_root = Path(rt.data_root).resolve()
    try:
        archive_dir.resolve().relative_to(data_root)
    except ValueError:
        raise HTTPException(403, "Cannot access paths outside data directory")

    # Check for existing thumbnail / cover
    for candidate in ["thumbnail.jpg", "cover.jpg", "cover.png"]:
        thumb = archive_dir / candidate
        if thumb.exists():
            media_type = "image/png" if candidate.endswith(".png") else "image/jpeg"
            return FileResponse(thumb, media_type=media_type)

    # Try to find video in archive directory
    video_exts = {".mp4", ".mkv", ".avi", ".webm", ".mov"}
    video_file = None
    for f in archive_dir.iterdir():
        if f.is_file() and f.suffix.lower() in video_exts:
            video_file = f
            break

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
    from app.services.cleanup import cleanup_failed_task
    return await cleanup_failed_task(task_id)


@router.post("/cleanup")
async def cleanup_all(max_age_hours: int = 24):
    """Clean up orphaned temporary files."""
    if max_age_hours < 1:
        raise HTTPException(400, "max_age_hours must be at least 1")
    from app.services.cleanup import cleanup_orphaned_files
    return await cleanup_orphaned_files(max_age_hours)


@router.get("/disk-usage")
async def disk_usage():
    """Get disk usage statistics for data directory."""
    from app.services.cleanup import get_disk_usage
    return await get_disk_usage()


@router.get("/bilibili/status")
async def bilibili_login_status():
    """Check BBDown login status by reading BBDown.data cookie expiry."""
    bbdown_data = Path(__file__).resolve().parent.parent.parent.parent / "tools" / "bbdown" / "BBDown.data"
    if not bbdown_data.exists():
        return {"logged_in": False, "message": "BBDown.data 不存在"}

    try:
        text = bbdown_data.read_text(encoding="utf-8")
        # Parse Expires from cookie string
        import re
        m = re.search(r'Expires=(\d+)', text)
        if not m:
            return {"logged_in": False, "message": "无法解析 cookie"}

        from datetime import datetime, timezone
        expires = int(m.group(1))
        expires_dt = datetime.fromtimestamp(expires, tz=timezone.utc)
        now = datetime.now(tz=timezone.utc)

        if now >= expires_dt:
            return {"logged_in": False, "expires": expires_dt.isoformat(), "message": "Cookie 已过期"}

        days_left = (expires_dt - now).days
        # Extract DedeUserID
        uid_m = re.search(r'DedeUserID=(\d+)', text)
        uid = uid_m.group(1) if uid_m else "unknown"

        return {
            "logged_in": True,
            "uid": uid,
            "expires": expires_dt.isoformat(),
            "days_left": days_left,
        }
    except Exception as e:
        return {"logged_in": False, "message": str(e)}
