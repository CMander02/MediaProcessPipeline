"""Direct pipeline operation routes."""

import asyncio
import json
import logging
import re
import shutil
import subprocess
import urllib.request
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
from app.core.pipeline import pipeline_steps_schema
from app.core.source_normalization import normalize_source_input
from app.core.network import urllib_urlopen
from app.services.archiving.thumbnails import (
    create_image_thumbnail,
    create_image_thumbnail_from_bytes,
    first_image_note_image,
    image_media_type,
)

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


class XiaohongshuLoginRequest(BaseModel):
    timeout_sec: int = 180


_ALLOWED_MEDIA_EXTS = {
    ".mp4", ".mkv", ".avi", ".webm", ".mov", ".flv", ".wmv",
    ".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".wma",
}

_THUMBNAIL_MAX_BYTES = 12 * 1024 * 1024
_RAW_IMAGE_FALLBACK_MAX_BYTES = 1024 * 1024


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
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for entry in root.iterdir():
        try:
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
    rows = conn.execute("SELECT id, status, result FROM tasks WHERE result IS NOT NULL").fetchall()
    for row in rows:
        try:
            result = json.loads(row["result"]) if isinstance(row["result"], str) else row["result"]
            if result and str(Path(result.get("output_dir", "")).resolve()) == archive_dir_str:
                if str(row["status"]) in {"queued", "processing"}:
                    raise HTTPException(409, "Archive directory is used by an active task")
                store.delete(UUID(row["id"]))
                task_deleted = True
                break
        except HTTPException:
            raise
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
            thumb = create_image_thumbnail(cover, archive_dir)
            if thumb:
                return FileResponse(thumb, media_type="image/jpeg")
            if _can_return_raw_image(cover):
                return FileResponse(cover, media_type=image_media_type(cover))
            break

    first_image = first_image_note_image(archive_dir)
    if first_image:
        thumb = create_image_thumbnail(first_image, archive_dir)
        if thumb:
            return FileResponse(thumb, media_type="image/jpeg")
        if _can_return_raw_image(first_image):
            return FileResponse(first_image, media_type=image_media_type(first_image))

    meta = _read_archive_metadata(archive_dir)
    remote_thumb = _first_thumbnail_url(meta)
    if remote_thumb:
        thumb = _cache_remote_thumbnail(remote_thumb, archive_dir)
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
