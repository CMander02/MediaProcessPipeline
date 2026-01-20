"""Direct pipeline operation routes."""

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Any

from app.services.ingestion import download_media, scan_inbox
from app.services.preprocessing import separate_vocals
from app.services.recognition import transcribe_audio
from app.services.analysis import polish_text, summarize_text, generate_mindmap
from app.services.archiving import list_archives
from app.services.cleanup import cleanup_failed_task, cleanup_orphaned_files, get_disk_usage
from app.api.routes.settings import get_runtime_settings

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
