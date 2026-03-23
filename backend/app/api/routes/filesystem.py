"""Filesystem browsing routes for file/folder selection."""

import os
import mimetypes
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/filesystem", tags=["filesystem"])


@router.get("/browse")
async def browse_directory(
    path: str = Query(".", description="Directory path to browse"),
    mode: Literal["file", "directory", "all"] = Query("all", description="Filter mode"),
):
    """
    Browse filesystem directory.

    Returns list of files and directories in the specified path.
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
        return {
            "success": False,
            "error": str(e),
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
        if not str(file_path).startswith(str(data_root)):
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
        return {"success": False, "error": str(e)}


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

    if not str(file_path).startswith(str(data_root)):
        raise HTTPException(status_code=403, detail="Access denied: path outside data directory")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    content_type, _ = mimetypes.guess_type(str(file_path))
    if content_type is None:
        content_type = "application/octet-stream"

    return FileResponse(
        path=str(file_path),
        media_type=content_type,
        filename=file_path.name,
    )


@router.get("/source-media")
async def serve_source_media(
    archive_path: str = Query(..., description="Archive directory path"),
):
    """Serve the original source media file referenced by an archive's metadata.

    Used when source/ copy has been deleted and the original is outside data_root.
    Security: archive_path must be under data_root, and we only serve the source_url from its metadata.
    """
    import json
    from app.core.settings import get_runtime_settings

    data_root = Path(get_runtime_settings().data_root).resolve()
    archive_dir = Path(archive_path).resolve()

    if not str(archive_dir).startswith(str(data_root)):
        raise HTTPException(status_code=403, detail="Access denied: path outside data directory")

    meta_path = archive_dir / "metadata.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="metadata.json not found")

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to read metadata.json")

    source_url = meta.get("source_url", "")
    if not source_url:
        raise HTTPException(status_code=404, detail="No source_url in metadata")

    file_path = Path(source_url).resolve()
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"Source file not found: {source_url}")

    content_type, _ = mimetypes.guess_type(str(file_path))
    if content_type is None:
        content_type = "application/octet-stream"

    return FileResponse(
        path=str(file_path),
        media_type=content_type,
        filename=file_path.name,
    )


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
