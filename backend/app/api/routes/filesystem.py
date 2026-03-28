"""Filesystem browsing routes for file/folder selection."""

import os
import mimetypes
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

router = APIRouter(prefix="/filesystem", tags=["filesystem"])


class WriteFileRequest(BaseModel):
    path: str
    content: str


@router.get("/browse")
async def browse_directory(
    path: str = Query(".", description="Directory path to browse"),
    mode: Literal["file", "directory", "all"] = Query("all", description="Filter mode"),
):
    """
    Browse filesystem directory.

    Returns list of files and directories in the specified path.
    NOTE: This endpoint intentionally allows browsing outside data_root —
    it powers the file picker for importing local media. Access control
    is provided by the API auth layer (see main.py middleware).
    """
    try:
        dir_path = Path(path).expanduser().resolve()

        if not dir_path.exists():
            return {
                "success": False,
                "error": f"Path does not exist: {path}",
                "path": str(dir_path),
                "items": [],
            }

        if not dir_path.is_dir():
            # If it's a file, return parent directory
            dir_path = dir_path.parent

        items = []
        try:
            for entry in sorted(dir_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                try:
                    is_dir = entry.is_dir()

                    # Filter based on mode
                    if mode == "file" and is_dir:
                        continue
                    if mode == "directory" and not is_dir:
                        continue

                    # Skip hidden files on Unix
                    if entry.name.startswith('.') and os.name != 'nt':
                        continue

                    items.append({
                        "name": entry.name,
                        "path": str(entry),
                        "is_dir": is_dir,
                        "size": entry.stat().st_size if not is_dir else None,
                    })
                except (PermissionError, OSError):
                    continue
        except PermissionError:
            return {
                "success": False,
                "error": "Permission denied",
                "path": str(dir_path),
                "items": [],
            }

        # Add parent directory entry
        parent = dir_path.parent
        if parent != dir_path:  # Not at root
            items.insert(0, {
                "name": "..",
                "path": str(parent),
                "is_dir": True,
                "size": None,
            })

        return {
            "success": True,
            "path": str(dir_path),
            "items": items,
        }

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"browse_directory error: {e}")
        return {
            "success": False,
            "error": "Failed to browse directory",
            "path": path,
            "items": [],
        }


@router.get("/read")
async def read_file(
    path: str = Query(..., description="File path to read"),
):
    """Read a text file and return its content. Only allows files under data/."""
    try:
        file_path = Path(path).resolve()

        # Security: only allow reading files under the data root
        from app.core.settings import get_runtime_settings
        data_root = Path(get_runtime_settings().data_root).resolve()
        try:
            file_path.relative_to(data_root)
        except ValueError:
            return {"success": False, "error": "Access denied: path outside data directory"}

        if not file_path.exists():
            return {"success": False, "error": f"File not found: {path}"}
        if not file_path.is_file():
            return {"success": False, "error": f"Not a file: {path}"}

        content = file_path.read_text(encoding="utf-8")
        return {"success": True, "content": content, "path": str(file_path)}
    except UnicodeDecodeError:
        return {"success": False, "error": "File is not valid UTF-8 text"}
    except Exception as e:
        logging.getLogger(__name__).warning(f"read_file error: {e}")
        return {"success": False, "error": "Failed to read file"}


@router.post("/write")
async def write_file(req: WriteFileRequest):
    """Write text content to a file. Only allows files under data_root."""
    try:
        file_path = Path(req.path).resolve()

        from app.core.settings import get_runtime_settings
        data_root = Path(get_runtime_settings().data_root).resolve()
        try:
            file_path.relative_to(data_root)
        except ValueError:
            return {"success": False, "error": "Access denied: path outside data directory"}

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(req.content, encoding="utf-8")
        return {"success": True, "path": str(file_path)}
    except Exception as e:
        logging.getLogger(__name__).warning(f"write_file error: {e}")
        return {"success": False, "error": "Failed to write file"}


def _ensure_browser_playable(file_path: Path) -> tuple[Path, str]:
    """If the file is an m4a/ogg/etc. that Chrome may choke on, return a
    transcoded mp3 copy (cached next to the original).  Otherwise return the
    file as-is.
    """
    needs_transcode = file_path.suffix.lower() in {".m4a", ".ogg", ".opus", ".wma", ".aac"}
    if not needs_transcode:
        ct, _ = mimetypes.guess_type(str(file_path))
        return file_path, ct or "application/octet-stream"

    mp3_path = file_path.with_suffix(".browser.mp3")
    if mp3_path.exists() and mp3_path.stat().st_size > 0:
        return mp3_path, "audio/mpeg"

    # Transcode once, cache the result
    import subprocess, logging
    logger = logging.getLogger(__name__)
    logger.info(f"Transcoding for browser playback: {file_path.name} → .browser.mp3")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(file_path),
             "-c:a", "libmp3lame", "-q:a", "2",
             str(mp3_path)],
            capture_output=True, check=True, timeout=300,
        )
    except Exception as e:
        logger.warning(f"Transcode failed: {e}")
        ct, _ = mimetypes.guess_type(str(file_path))
        return file_path, ct or "application/octet-stream"

    return mp3_path, "audio/mpeg"


@router.get("/media")
async def serve_media(
    path: str = Query(..., description="Media file path to serve"),
):
    """Serve a media file with correct Content-Type and Range support.

    Security: only allows files under data_root.
    """
    from app.core.settings import get_runtime_settings

    file_path = Path(path).resolve()
    data_root = Path(get_runtime_settings().data_root).resolve()

    try:
        file_path.relative_to(data_root)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied: path outside data directory")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    serve_path, content_type = _ensure_browser_playable(file_path)

    return FileResponse(
        path=str(serve_path),
        media_type=content_type,
        filename=file_path.stem + serve_path.suffix,
    )



MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".webm", ".mov", ".flv", ".wmv",
    ".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".wma",
}


@router.get("/scan-folder")
async def scan_folder(
    path: str = Query(..., description="Root folder path to scan"),
    recursive: bool = Query(True, description="Scan subdirectories"),
):
    """List all media files in a directory (optionally recursive).

    NOTE: Intentionally allows scanning outside data_root — used for
    batch-importing from user-chosen folders. Protected by API auth layer.
    """
    try:
        folder = Path(path).expanduser().resolve()
        if not folder.exists() or not folder.is_dir():
            return {"success": False, "error": "Directory not found", "files": []}

        files = []
        iterator = folder.rglob("*") if recursive else folder.iterdir()
        for entry in iterator:
            try:
                if entry.is_file() and entry.suffix.lower() in MEDIA_EXTENSIONS:
                    files.append({
                        "path": str(entry),
                        "name": entry.name,
                        "size": entry.stat().st_size,
                    })
            except (PermissionError, OSError):
                continue

        files.sort(key=lambda f: f["name"].lower())
        return {"success": True, "path": str(folder), "files": files, "count": len(files)}
    except Exception as e:
        logging.getLogger(__name__).warning(f"scan_folder error: {e}")
        return {"success": False, "error": "Failed to scan folder", "files": []}


@router.get("/drives")
async def list_drives():
    """
    List available drives (Windows) or mount points (Unix).
    """
    drives = []

    if os.name == 'nt':
        # Windows: list drive letters
        import string
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if Path(drive).exists():
                drives.append({
                    "name": f"{letter}:",
                    "path": drive,
                    "is_dir": True,
                })
    else:
        # Unix: common mount points
        common_paths = ["/", "/home", "/mnt", "/media"]
        for p in common_paths:
            if Path(p).exists():
                drives.append({
                    "name": p,
                    "path": p,
                    "is_dir": True,
                })

    return {
        "success": True,
        "drives": drives,
    }
