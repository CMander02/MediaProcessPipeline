"""Direct pipeline operation routes."""

import asyncio
import hashlib
import ipaddress
import json
import logging
import re
import shutil
import subprocess
import urllib.request
from datetime import datetime
from email.utils import formatdate
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.network import httpx_client_kwargs, urllib_urlopen
from app.core.pipeline import pipeline_steps_schema
from app.core.settings import get_runtime_settings
from app.core.source_normalization import normalize_source_input
from app.services.archiving.thumbnails import (
    ArchiveImageFormatError,
    ArchiveImageTooLargeError,
    PillowUnavailableError,
    create_image_thumbnail,
    create_image_thumbnail_from_bytes,
    first_image_note_image,
    image_media_type,
    resize_archive_image_to_jpeg,
)

# Windows reserved device names (case-insensitive, with or without extension)
_WIN_RESERVED = re.compile(
    r'^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\..+)?$', re.IGNORECASE
)

logger = logging.getLogger(__name__)


async def _proxy_remote_mirror_mutation(
    archive_dir: Path,
    endpoint: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Apply archive mutations to the coordinator that owns a local mirror."""
    from app.core.database import get_task_store

    task = get_task_store().find_by_output_dir(archive_dir)
    result = task.result if task and isinstance(task.result, dict) else {}
    sync_info = (
        result.get("remote_sync")
        if isinstance(result.get("remote_sync"), dict)
        else {}
    )
    if not sync_info.get("mirror"):
        return None

    remote_path = str(sync_info.get("remote_output_dir") or "").strip()
    settings = get_runtime_settings()
    server_url = str(settings.remote_server_url or "").strip().rstrip("/")
    if not remote_path or not settings.remote_sync_enabled or not server_url:
        raise HTTPException(409, "Remote mirror coordinator is not configured")

    request_payload = {**payload, "path": remote_path}
    headers = {"X-Requested-With": "MPP-EXE-Remote-Mutation"}
    if settings.remote_api_token:
        headers["Authorization"] = f"Bearer {settings.remote_api_token}"
    url = f"{server_url}/api/pipeline/archives/{endpoint.lstrip('/')}"
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(360.0, connect=15.0),
            follow_redirects=False,
            **httpx_client_kwargs(url),
        ) as client:
            response = await client.post(url, json=request_payload, headers=headers)
    except httpx.RequestError as exc:
        raise HTTPException(502, "Cannot reach the remote archive coordinator") from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        raise HTTPException(
            response.status_code,
            str(detail or f"Remote archive mutation failed ({response.status_code})"),
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise HTTPException(502, "Remote archive coordinator returned invalid JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(502, "Remote archive coordinator returned invalid data")
    return body


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


class ArchivePolishRequest(BaseModel):
    path: str
    text: str | None = None
    source_filename: str | None = None


class XiaohongshuLoginRequest(BaseModel):
    timeout_sec: int = 180


_ALLOWED_MEDIA_EXTS = {
    ".mp4", ".mkv", ".avi", ".webm", ".mov", ".flv", ".wmv",
    ".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".wma",
}

_THUMBNAIL_MAX_BYTES = 12 * 1024 * 1024
_RAW_IMAGE_FALLBACK_MAX_BYTES = 1024 * 1024
_ARCHIVE_IMAGE_CACHE_SECONDS = 24 * 60 * 60
_ARCHIVE_INTERNAL_ROOTS = {
    "_staging",
    "_remote_sync",
    "_sync_downloads",
    "_remote_sync_client",
}
_ARCHIVE_MARKER_FILES = {
    "metadata.json",
    "source.md",
    "summary.md",
    "transcript.srt",
    "transcript_polished.srt",
}


def _read_archive_metadata(archive_dir: Path) -> dict[str, Any]:
    meta_path = archive_dir / "metadata.json"
    if not meta_path.exists():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _first_thumbnail_url(meta: dict[str, Any]) -> str | None:
    candidates: list[Any] = [meta.get("thumbnail")]
    extra = meta.get("extra")
    if isinstance(extra, dict):
        candidates.extend([
            extra.get("thumbnail"),
            extra.get("cover"),
            extra.get("cover_url"),
        ])
    for value in candidates:
        if isinstance(value, str):
            url = value.strip()
            if url.startswith("//"):
                url = f"https:{url}"
            if url.startswith(("http://", "https://")):
                return url
    return None


def _cache_remote_thumbnail(url: str, archive_dir: Path) -> Path | None:
    try:
        _validate_url(url)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Referer": f"{urlparse(url).scheme}://{urlparse(url).netloc}/",
            },
        )
        with urllib_urlopen(req, timeout=12) as response:
            data = response.read(_THUMBNAIL_MAX_BYTES + 1)
        if len(data) > _THUMBNAIL_MAX_BYTES:
            raise RuntimeError("thumbnail response too large")
        return create_image_thumbnail_from_bytes(data, archive_dir)
    except Exception as e:
        logger.debug("remote thumbnail fetch failed: %s", e)
        return None


def _can_return_raw_image(path: Path) -> bool:
    try:
        return path.stat().st_size <= _RAW_IMAGE_FALLBACK_MAX_BYTES
    except OSError:
        return False


def _resolve_archive_image(path: str) -> Path:
    """Resolve a supported image belonging to a flat data_root archive."""
    candidate = Path(path)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise HTTPException(404, "Archive image not found")

    data_root = Path(get_runtime_settings().data_root).resolve()
    try:
        relative = resolved.relative_to(data_root)
    except ValueError:
        raise HTTPException(403, "Cannot access paths outside data directory")

    if (
        len(relative.parts) < 2
        or relative.parts[0].startswith(".")
        or relative.parts[0] in _ARCHIVE_INTERNAL_ROOTS
    ):
        raise HTTPException(403, "Path does not belong to an archive")

    archive_dir = data_root / relative.parts[0]
    if not archive_dir.is_dir() or not any(
        (archive_dir / marker).is_file()
        for marker in _ARCHIVE_MARKER_FILES
    ):
        raise HTTPException(403, "Path does not belong to an archive")
    if not resolved.is_file():
        raise HTTPException(404, "Archive image not found")
    if resolved.suffix.lower() not in {
        ".avif",
        ".bmp",
        ".gif",
        ".jpeg",
        ".jpg",
        ".png",
        ".webp",
    }:
        raise HTTPException(415, "Unsupported archive image type")
    return resolved


def _archive_image_response_headers(
    source: Path,
    *,
    max_edge: int,
    quality: int,
) -> dict[str, str]:
    stat = source.stat()
    fingerprint = hashlib.sha256(
        f"{stat.st_mtime_ns}:{stat.st_size}:{max_edge}:{quality}".encode("ascii"),
    ).hexdigest()
    return {
        "Cache-Control": f"private, max-age={_ARCHIVE_IMAGE_CACHE_SECONDS}",
        "Content-Disposition": 'inline; filename="archive-image.jpg"',
        "ETag": f'"{fingerprint}"',
        "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
        "Vary": "Authorization",
        "X-Content-Type-Options": "nosniff",
    }


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    return any(
        candidate.strip() in {"*", etag}
        for candidate in if_none_match.split(",")
    )


def _generate_video_thumbnail(video_file: Path, archive_dir: Path) -> Path | None:
    thumb_path = archive_dir / "thumbnail.jpg"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.warning("ffmpeg thumbnail failed: ffmpeg executable not found")
        return None

    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-ss",
                "3",
                "-i",
                str(video_file),
                "-vframes",
                "1",
                "-vf",
                "scale=480:-2",
                "-q:v",
                "5",
                str(thumb_path),
            ],
            capture_output=True,
            timeout=15,
            check=False,
        )
        if result.returncode == 0 and thumb_path.is_file() and thumb_path.stat().st_size > 0:
            return thumb_path
        logger.warning(
            "ffmpeg thumbnail failed: returncode=%s stderr=%s",
            result.returncode,
            result.stderr.decode("utf-8", errors="replace").strip(),
        )
    except Exception as exc:
        logger.warning("ffmpeg thumbnail failed: %s", exc)

    try:
        thumb_path.unlink(missing_ok=True)
    except OSError:
        pass
    return None


@router.get("/steps")
async def get_pipeline_steps():
    """Return canonical pipeline step ids, names, and order."""
    return {"steps": pipeline_steps_schema()}


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


def _staging_root() -> Path:
    rt = get_runtime_settings()
    return Path(rt.data_root) / "_staging"


def _resolve_staging_dir(staging_id: str) -> Path:
    """Resolve a staging dir path and verify it stays inside the staging root."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", staging_id):
        raise HTTPException(400, "invalid staging_id")
    root = _staging_root().resolve()
    candidate = (root / staging_id).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(400, "invalid staging_id")
    return candidate


@router.post("/stage")
async def stage_file(file: UploadFile = File(...)):
    """Save an uploaded media file into a staging directory without creating
    a task. The frontend later calls POST /api/tasks with the returned `path`
    when the user clicks "开始处理" — that's when options are captured.

    Returns: {staging_id, path, filename, title, size, media_type}
    """
    raw_name = file.filename or "uploaded_file"

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

    from uuid import uuid4
    staging_id = uuid4().hex
    staging_dir = _staging_root() / staging_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    dest_path = staging_dir / safe_name

    max_size = 10 * 1024 * 1024 * 1024  # 10 GB
    written = 0
    try:
        with open(dest_path, "wb") as f:
            while chunk := await file.read(8 * 1024 * 1024):
                written += len(chunk)
                if written > max_size:
                    raise HTTPException(413, "File too large (limit: 10 GB)")
                f.write(chunk)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    return {
        "staging_id": staging_id,
        "path": str(dest_path),
        "filename": safe_name,
        "title": title,
        "size": written,
        "media_type": media_type,
    }


@router.delete("/stage/{staging_id}")
async def delete_staged(staging_id: str):
    """Delete a staged file directory (called when user removes a queued file)."""
    staging_dir = _resolve_staging_dir(staging_id)
    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
    return {"deleted": True}


def sweep_stale_staging(max_age_hours: float = 24.0) -> int:
    """Remove staging directories older than max_age_hours. Called at daemon startup."""
    import time
    root = _staging_root()
    if not root.exists():
        return 0
    protected_dirs: set[Path] = set()
    try:
        from app.core.database import get_task_store

        active_tasks = get_task_store().list_by_statuses(
            ["pending", "queued", "processing", "paused"]
        )
        resolved_root = root.resolve()
        for task in active_tasks:
            source = str(task.source or "")
            if source.startswith(("http://", "https://", "upload://")):
                continue
            try:
                relative = Path(source).resolve().relative_to(resolved_root)
            except (OSError, ValueError):
                continue
            if relative.parts:
                protected_dirs.add((resolved_root / relative.parts[0]).resolve())
    except Exception:
        logger.warning("Unable to resolve active staged inputs during cleanup", exc_info=True)
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for entry in root.iterdir():
        try:
            if entry.resolve() in protected_dirs:
                continue
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


@router.get("/probe")
async def probe_url(url: str):
    """Extract metadata from a URL without downloading (for hotword suggestions)."""
    url = normalize_source_input(url)
    _validate_url(url)
    import asyncio

    def _probe(url: str) -> dict[str, Any]:
        from app.services.ingestion.ytdlp import get_ytdlp_service
        try:
            info = get_ytdlp_service().fetch_metadata(url)
            if not info:
                return {}
            return {
                "title": info.get("title"),
                "description": info.get("description"),
                "tags": info.get("tags") or [],
                "uploader": info.get("uploader") or info.get("channel"),
                "duration": info.get("duration"),
            }
        except Exception as e:
            logger.warning(f"Probe failed for {url}: {e}")
            return {}

    result = await asyncio.to_thread(_probe, url)
    if not result:
        raise HTTPException(status_code=404, detail="无法获取视频信息")
    return result


@router.get("/bilibili/collection")
async def inspect_bilibili_collection_url(url: str):
    """Return selectable entries when a Bilibili URL is multi-part or a season."""
    url = normalize_source_input(url)
    if not url.startswith(("http://", "https://")):
        bvid_match = re.search(r"\bBV[0-9A-Za-z]{10}\b", url)
        if bvid_match:
            url = f"https://www.bilibili.com/video/{bvid_match.group(0)}"
    _validate_url(url)
    from app.services.ingestion.platform.bilibili.collection import inspect_bilibili_collection

    try:
        return await asyncio.to_thread(inspect_bilibili_collection, url)
    except Exception as e:
        logger.warning("Bilibili collection inspection failed for %s: %s", url, e)
        raise HTTPException(status_code=502, detail="无法读取哔哩哔哩合集信息") from e


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


@router.post("/archives/polish")
async def polish_archive(req: ArchivePolishRequest):
    """Run an additional polish pass and persist it as a portable archive artifact."""
    archive_dir = Path(req.path)
    if not archive_dir.is_dir():
        raise HTTPException(404, "Archive directory not found")

    data_root = Path(get_runtime_settings().data_root).resolve()
    try:
        archive_dir = archive_dir.resolve()
        archive_dir.relative_to(data_root)
    except ValueError:
        raise HTTPException(403, "Cannot modify paths outside data directory")

    source_name = (req.source_filename or "").strip()
    if source_name and (
        Path(source_name).name != source_name
        or "/" in source_name
        or "\\" in source_name
    ):
        raise HTTPException(400, "Invalid source filename")

    proxied = await _proxy_remote_mirror_mutation(
        archive_dir,
        "polish",
        {
            "text": req.text,
            "source_filename": source_name or None,
        },
    )
    if proxied is not None:
        return proxied

    text = req.text
    if text is None:
        candidates = [
            source_name,
            "transcript_polished.md",
            "transcript_raw.md",
            "transcript.srt",
            "content.md",
            "source.md",
        ]
        for filename in candidates:
            if not filename:
                continue
            candidate = archive_dir / filename
            if candidate.is_file():
                text = candidate.read_text(encoding="utf-8")
                source_name = filename
                break

    content = (text or "").strip()
    if not content:
        raise HTTPException(400, "No text is available for additional polishing")
    if len(content) > 2_000_000:
        raise HTTPException(413, "Text is too large for additional polishing")

    metadata = _read_archive_metadata(archive_dir)
    from app.services.analysis import polish_text

    polished = await polish_text(
        content,
        context={
            "title": str(metadata.get("title") or ""),
            "additional_pass": True,
        },
    )
    output_name = "transcript_extra_polished.md"
    output_path = archive_dir / output_name
    temp_path = archive_dir / f".{output_name}.tmp"
    temp_path.write_text(polished, encoding="utf-8")
    temp_path.replace(output_path)

    metadata["extra_polish_file"] = output_name
    metadata["extra_polish_source"] = source_name or "request"
    metadata["extra_polished_at"] = datetime.now().isoformat()
    metadata_path = archive_dir / "metadata.json"
    metadata_temp = archive_dir / ".metadata.json.extra-polish.tmp"
    metadata_temp.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    metadata_temp.replace(metadata_path)

    from app.core.database import get_task_store

    store = get_task_store()
    task = store.find_by_output_dir(archive_dir)
    updated_task = None
    if task:
        updated_task = store.touch_sync_revision(
            task.id,
            result_updates={
                "extra_polish_file": output_name,
                "extra_polished_at": metadata["extra_polished_at"],
            },
        )

    return {
        "polished": polished,
        "filename": output_name,
        "path": str(output_path),
        "sync_revision": updated_task.sync_revision if updated_task else None,
    }


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
async def archives(lite: bool = False):
    """List archived content (all, sorted by mtime desc)."""
    from app.services.archiving import list_archives
    return {"archives": await list_archives(lite=lite)}


@router.get("/archives/detail")
async def archive_detail(path: str):
    """Return one archive with full metadata and analysis."""
    archive_dir = Path(path)
    if not archive_dir.is_dir():
        raise HTTPException(404, "Archive directory not found")

    rt = get_runtime_settings()
    data_root = Path(rt.data_root).resolve()
    try:
        archive_dir.resolve().relative_to(data_root)
    except ValueError:
        raise HTTPException(403, "Cannot access paths outside data directory")

    from app.services.archiving import get_archive

    archive = await get_archive(str(archive_dir), lite=False)
    if not archive:
        raise HTTPException(404, "Archive not found")
    return {"archive": archive}


class ArchiveDeleteRequest(BaseModel):
    path: str


@router.delete("/archives")
async def delete_archive(req: ArchiveDeleteRequest):
    """Delete an archive directory and its associated task record."""
    archive_dir = Path(req.path)
    if not archive_dir.is_dir():
        raise HTTPException(404, "Archive directory not found")

    # Only direct, registered archive directories may be removed. Internal
    # staging/synchronization trees and data_root itself are never archives.
    rt = get_runtime_settings()
    data_root = Path(rt.data_root).resolve()
    resolved_archive = archive_dir.resolve()
    try:
        relative_archive = resolved_archive.relative_to(data_root)
    except ValueError:
        raise HTTPException(403, "Cannot delete paths outside data directory")
    internal_roots = {
        "_staging",
        "_remote_sync",
        "_sync_downloads",
        "_remote_sync_client",
    }
    if (
        resolved_archive == data_root
        or len(relative_archive.parts) != 1
        or relative_archive.parts[0] in internal_roots
        or relative_archive.parts[0].startswith(".")
        or archive_dir.is_symlink()
    ):
        raise HTTPException(403, "Path is not a deletable archive directory")

    from app.core.database import get_task_store

    store = get_task_store()
    task = store.find_by_output_dir(resolved_archive)
    if task is None:
        raise HTTPException(404, "Archive is not associated with a registered task")
    if str(task.status) not in {"completed", "failed", "cancelled"}:
        raise HTTPException(409, "Archive directory is used by an active task")

    # Remove files first so a filesystem error leaves the task record intact.
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
            shutil.rmtree(resolved_archive, onerror=_onerror_retry)
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(0.5)
    else:
        # If folder still exists but is now empty, that's ok
        if resolved_archive.exists() and any(resolved_archive.iterdir()):
            raise HTTPException(500, f"Failed to delete archive: {last_err}")
        # Empty dir or gone — try final rmdir
        try:
            resolved_archive.rmdir()
        except Exception:
            pass

    if resolved_archive.exists():
        raise HTTPException(500, "Failed to delete archive directory")
    task_deleted = store.delete(task.id)
    if not task_deleted:
        raise HTTPException(500, "Archive removed but task record could not be deleted")

    logger.info(f"Deleted archive: {resolved_archive} (task={task_deleted})")

    return {
        "message": "Deleted",
        "path": str(resolved_archive),
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

    proxied = await _proxy_remote_mirror_mutation(
        archive_dir.resolve(),
        "rename",
        {"title": new_title},
    )
    if proxied is not None:
        return proxied

    meta_path = archive_dir / "metadata.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    meta["title"] = new_title
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    from app.core.database import get_task_store

    store = get_task_store()
    task = store.find_by_output_dir(archive_dir)
    updated_task = None
    if task:
        result_metadata = (
            dict(task.result.get("metadata") or {})
            if isinstance(task.result, dict)
            and isinstance(task.result.get("metadata"), dict)
            else {}
        )
        result_metadata["title"] = new_title
        updated_task = store.touch_sync_revision(
            task.id,
            result_updates={"metadata": result_metadata},
        )

    return {
        "success": True,
        "title": new_title,
        "sync_revision": updated_task.sync_revision if updated_task else None,
    }


@router.get("/archives/image")
async def archive_image(
    request: Request,
    path: str,
    max_edge: int = Query(default=1280, ge=256, le=4096),
    quality: int = Query(default=84, ge=55, le=90),
):
    """Return a bounded JPEG rendition of one image stored in an archive."""
    source = _resolve_archive_image(path)
    try:
        headers = _archive_image_response_headers(
            source,
            max_edge=max_edge,
            quality=quality,
        )
    except OSError:
        raise HTTPException(404, "Archive image not found")

    if _etag_matches(request.headers.get("if-none-match"), headers["ETag"]):
        return Response(status_code=304, headers=headers)

    try:
        payload = await asyncio.to_thread(
            resize_archive_image_to_jpeg,
            source,
            max_edge=max_edge,
            quality=quality,
        )
    except ArchiveImageTooLargeError as exc:
        raise HTTPException(413, str(exc)) from exc
    except ArchiveImageFormatError as exc:
        raise HTTPException(415, str(exc)) from exc
    except PillowUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc

    return Response(
        content=payload,
        media_type="image/jpeg",
        headers=headers,
    )


@router.get("/archives/thumbnail")
async def archive_thumbnail(path: str):
    """Get or generate a thumbnail for an archive directory.

    Uses a cached low-resolution thumbnail when available. Otherwise it tries
    local cover art, image-note content, platform cover URL, then video frame.
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

    cached = archive_dir / "thumbnail.jpg"
    if cached.exists():
        return FileResponse(cached, media_type="image/jpeg")

    for candidate in ["cover.jpg", "cover.png", "cover.webp"]:
        cover = archive_dir / candidate
        if cover.exists():
            thumb = await asyncio.to_thread(create_image_thumbnail, cover, archive_dir)
            if thumb:
                return FileResponse(thumb, media_type="image/jpeg")
            if _can_return_raw_image(cover):
                return FileResponse(cover, media_type=image_media_type(cover))
            break

    first_image = first_image_note_image(archive_dir)
    if first_image:
        thumb = await asyncio.to_thread(create_image_thumbnail, first_image, archive_dir)
        if thumb:
            return FileResponse(thumb, media_type="image/jpeg")
        if _can_return_raw_image(first_image):
            return FileResponse(first_image, media_type=image_media_type(first_image))

    meta = _read_archive_metadata(archive_dir)
    remote_thumb = _first_thumbnail_url(meta)
    if remote_thumb:
        thumb = await asyncio.to_thread(_cache_remote_thumbnail, remote_thumb, archive_dir)
        if thumb:
            return FileResponse(thumb, media_type="image/jpeg")

    # Try to find video in archive directory
    video_exts = {".mp4", ".mkv", ".avi", ".webm", ".mov"}
    video_file = None
    for f in archive_dir.iterdir():
        if f.is_file() and f.suffix.lower() in video_exts:
            video_file = f
            break

    if not video_file:
        source_url = meta.get("source_url", "")
        if source_url:
            try:
                original = Path(source_url)
                if original.exists() and original.suffix.lower() in video_exts:
                    video_file = original
            except Exception:
                pass

    if not video_file:
        raise HTTPException(404, "No thumbnail or video found")

    thumb = await asyncio.to_thread(_generate_video_thumbnail, video_file, archive_dir)
    if thumb:
        return FileResponse(thumb, media_type="image/jpeg")
    raise HTTPException(500, "Thumbnail generation failed")


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
    """Check Bilibili login status using auth.py (settings or BBDown.data fallback)."""
    try:
        from app.services.ingestion.platform.bilibili.auth import is_logged_in, get_cookie
        from datetime import datetime, timezone

        cookie = get_cookie()
        if not cookie:
            return {"logged_in": False, "message": "未配置 Bilibili cookie（settings 或 BBDown.data）"}

        # Parse Expires and DedeUserID from cookie string
        expires_m = re.search(r'Expires=(\d+)', cookie)
        uid_m = re.search(r'DedeUserID=(\d+)', cookie)

        expires_dt = None
        days_left = None
        if expires_m:
            expires = int(expires_m.group(1))
            expires_dt = datetime.fromtimestamp(expires, tz=timezone.utc)
            now = datetime.now(tz=timezone.utc)
            if now >= expires_dt:
                return {"logged_in": False, "expires": expires_dt.isoformat(), "message": "Cookie 已过期"}
            days_left = (expires_dt - now).days

        # Expiry metadata is advisory; the nav API is the source of truth for session validity.
        logged = is_logged_in()
        uid = uid_m.group(1) if uid_m else "unknown"
        if logged:
            return {
                "logged_in": True,
                "uid": uid,
                "expires": expires_dt.isoformat() if expires_dt else None,
                "days_left": days_left,
            }
        return {
            "logged_in": False,
            "expires": expires_dt.isoformat() if expires_dt else None,
            "message": "Cookie 无效或未登录",
        }

    except Exception as e:
        return {"logged_in": False, "message": str(e)}


@router.get("/xiaohongshu/auth/status")
async def xiaohongshu_auth_status():
    """Return Xiaohongshu Cookie/storage-state auth status."""
    from app.services.ingestion.platform.xiaohongshu.api import auth_state_status

    status = auth_state_status()
    status["auth_status"] = (
        "cookie_configured"
        if status.get("configured_cookie")
        else "storage_state_ready"
        if status.get("storage_state_exists") and status.get("cookie_count")
        else "not_configured"
    )
    return status


@router.post("/xiaohongshu/auth/login")
async def xiaohongshu_auth_login(request: XiaohongshuLoginRequest):
    """Open a browser for Xiaohongshu login and save Playwright storage_state."""
    from app.services.ingestion.platform.xiaohongshu.api import interactive_login

    try:
        return await asyncio.to_thread(interactive_login, request.timeout_sec)
    except Exception as e:
        raise HTTPException(500, str(e)) from e


@router.get("/twitter/auth/status")
async def twitter_auth_status():
    """Return the saved X browser-session status used for X Articles."""
    from app.services.ingestion.platform.twitter.api import auth_state_status

    return auth_state_status()


@router.post("/twitter/auth/login")
async def twitter_auth_login(request: XiaohongshuLoginRequest):
    """Open X in a browser and save the authenticated Playwright session."""
    from app.services.ingestion.platform.twitter.api import interactive_login

    try:
        return await asyncio.to_thread(interactive_login, request.timeout_sec)
    except Exception as e:
        raise HTTPException(500, str(e)) from e


@router.get("/platforms")
async def get_platform_configs():
    """Get per-platform download strategy configs + auth status."""
    from app.services.ingestion.platform.bilibili.auth import is_logged_in as bili_logged_in
    from app.core.settings import get_runtime_settings
    import json
    rt = get_runtime_settings()
    try:
        stored = json.loads(rt.platform_configs or "{}")
    except Exception:
        stored = {}

    bilibili_cfg = stored.get("bilibili", {})
    youtube_cfg = stored.get("youtube", {})
    xiaohongshu_cfg = stored.get("xiaohongshu", {})
    if not isinstance(xiaohongshu_cfg, dict):
        xiaohongshu_cfg = {}

    try:
        bili_status = bili_logged_in()
    except Exception:
        bili_status = False
    try:
        from app.services.ingestion.platform.xiaohongshu.api import auth_state_status

        xhs_auth = auth_state_status()
    except Exception:
        xhs_auth = {"configured_cookie": False, "storage_state_exists": False, "cookie_count": 0}
    xhs_configured = bool(
        xhs_auth.get("configured_cookie")
        or (xhs_auth.get("storage_state_exists") and xhs_auth.get("cookie_count"))
    )

    return {
        "platforms": [
            {
                "id": "bilibili",
                "name": "哔哩哔哩",
                "status": "active",
                "auth_status": "logged_in" if bili_status else "not_logged_in",
                "preferred_quality": bilibili_cfg.get("preferred_quality", rt.bilibili_preferred_quality),
                "prefer_subtitle": bilibili_cfg.get("prefer_subtitle", rt.prefer_platform_subtitles),
                "subtitle_engine": bilibili_cfg.get("subtitle_engine", rt.bilibili_subtitle_engine),
                "subtitle_languages": bilibili_cfg.get("subtitle_languages", rt.subtitle_languages),
                "subtitle_strict_validation": bilibili_cfg.get(
                    "subtitle_strict_validation",
                    rt.bilibili_subtitle_strict_validation,
                ),
                "subtitle_min_coverage": bilibili_cfg.get(
                    "subtitle_min_coverage",
                    rt.bilibili_subtitle_min_coverage,
                ),
                "subtitle_allow_legacy_fallback": bilibili_cfg.get(
                    "subtitle_allow_legacy_fallback",
                    rt.bilibili_subtitle_allow_legacy_fallback,
                ),
            },
            {
                "id": "youtube",
                "name": "YouTube",
                "status": "active",
                "auth_status": "configured" if (rt.youtube_cookies_file or rt.youtube_cookies_browser) else "not_configured",
                "preferred_quality": youtube_cfg.get("preferred_quality", rt.youtube_preferred_quality),
                "prefer_subtitle": youtube_cfg.get("prefer_subtitle", True),
            },
            {
                "id": "xiaoyuzhou",
                "name": "小宇宙",
                "status": "active",
                "auth_status": "not_applicable",
                "preferred_quality": None,
                "prefer_subtitle": False,
            },
            {
                "id": "apple_podcast",
                "name": "Apple Podcasts",
                "status": "active",
                "auth_status": "not_applicable",
                "preferred_quality": None,
                "prefer_subtitle": False,
            },
            {
                "id": "xiaohongshu",
                "name": "小红书",
                "status": "active",
                "auth_status": "configured" if xhs_configured else "optional",
                "preferred_quality": None,
                "prefer_subtitle": False,
                "storage_state_path": xhs_auth.get("storage_state_path"),
                "storage_state_exists": xhs_auth.get("storage_state_exists"),
                "login_cookie": xhs_auth.get("login_cookie"),
                "image_strategy_order": xiaohongshu_cfg.get(
                    "image_strategy_order",
                    ["raw_url", "cdn_fallback", "browser_request", "browser_interactive"],
                ),
                "fail_on_missing_images": xiaohongshu_cfg.get("fail_on_missing_images", True),
            },
            {
                "id": "zhihu",
                "name": "知乎",
                "status": "active",
                "auth_status": "not_applicable",
                "preferred_quality": None,
                "prefer_subtitle": False,
            },
        ]
    }


@router.put("/platforms/{platform_id}")
async def update_platform_config(platform_id: str, config: dict):
    """Update per-platform download strategy."""
    from app.core.settings import patch_runtime_settings
    rt = get_runtime_settings()
    try:
        stored = json.loads(rt.platform_configs or "{}")
    except Exception:
        stored = {}
    existing = stored.get(platform_id, {})
    stored[platform_id] = {**existing, **config}

    updates: dict = {"platform_configs": json.dumps(stored)}
    if platform_id == "bilibili" and "preferred_quality" in config:
        updates["bilibili_preferred_quality"] = config["preferred_quality"]
    if platform_id == "bilibili" and "subtitle_engine" in config:
        updates["bilibili_subtitle_engine"] = config["subtitle_engine"]
    if platform_id == "bilibili" and "subtitle_languages" in config:
        updates["subtitle_languages"] = config["subtitle_languages"]
    if platform_id == "bilibili" and "subtitle_strict_validation" in config:
        updates["bilibili_subtitle_strict_validation"] = config["subtitle_strict_validation"]
    if platform_id == "bilibili" and "subtitle_min_coverage" in config:
        updates["bilibili_subtitle_min_coverage"] = config["subtitle_min_coverage"]
    if platform_id == "bilibili" and "subtitle_allow_legacy_fallback" in config:
        updates["bilibili_subtitle_allow_legacy_fallback"] = config["subtitle_allow_legacy_fallback"]
    if platform_id == "youtube" and "preferred_quality" in config:
        updates["youtube_preferred_quality"] = config["preferred_quality"]
    patch_runtime_settings(updates)
    return {"ok": True}
