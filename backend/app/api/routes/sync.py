"""Archive synchronization API for mobile clients and desktop daemons."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response

from app.core.archive_sync import (
    archive_file_manifest,
    get_archive_sync_service,
    publish_archive,
    safe_extract_zip,
    stream_upload_to_path,
)
from app.core.database import get_task_store
from app.core.settings import get_runtime_settings
from app.models import Task, TaskStatus

router = APIRouter(prefix="/sync", tags=["sync"])

_MAX_TASK_JSON_BYTES = 4 * 1024 * 1024


def _safe_archive_name(raw_name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(raw_name or "")).strip(" .")
    return (name[:120] or "remote-archive").strip(" .")


def _read_metadata(archive_root: Path) -> dict[str, Any]:
    path = archive_root / "metadata.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("metadata.json is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("metadata.json must contain an object")
    return payload


def _destination_for(data_root: Path, archive_name: str, task_id: str) -> Path:
    base_name = _safe_archive_name(archive_name)
    candidate = data_root / base_name
    suffix = 2
    while candidate.exists():
        try:
            metadata = _read_metadata(candidate)
        except ValueError:
            metadata = {}
        if str(metadata.get("task_id") or "") == task_id:
            return candidate
        candidate = data_root / f"{base_name} ({suffix})"
        suffix += 1
    return candidate


@router.get("/changes")
async def sync_changes(
    cursor: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    return get_archive_sync_service().changes(cursor, limit)


@router.get("/archives/{archive_id}/manifest")
async def sync_manifest(archive_id: str):
    manifest = get_archive_sync_service().manifest(archive_id)
    if manifest is None:
        raise HTTPException(404, "Archive not found")
    return manifest


@router.get("/archives/{archive_id}/files/{relative_path:path}")
async def sync_file(
    archive_id: str,
    relative_path: str,
    if_none_match: str | None = Header(default=None),
):
    resolved = get_archive_sync_service().resolve_declared_file(archive_id, relative_path)
    if resolved is None:
        raise HTTPException(404, "Synchronized file not found")
    path, entry = resolved
    etag = f'"{entry.sha256}"'
    if if_none_match and any(value.strip() == etag for value in if_none_match.split(",")):
        return Response(status_code=304, headers={"ETag": etag})
    return FileResponse(
        path,
        media_type=entry.mime,
        filename=path.name,
        headers={
            "ETag": etag,
            "Cache-Control": "private, no-cache",
            "X-Content-SHA256": entry.sha256,
        },
    )


@router.post("/rebuild")
async def rebuild_sync_index():
    return get_archive_sync_service().rebuild()


@router.post("/import")
async def import_completed_archive(
    task_json: str = Form(...),
    archive_name: str = Form(...),
    archive_sha256: str = Form(""),
    worker_id: str = Form(""),
    archive: UploadFile = File(...),
):
    """Import a completed local task and its portable archive idempotently."""
    if len(task_json.encode("utf-8")) > _MAX_TASK_JSON_BYTES:
        raise HTTPException(413, "Task metadata exceeds the 4 MB limit")
    try:
        raw_task = json.loads(task_json)
        task = Task.model_validate(raw_task)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(400, "Invalid task metadata") from exc
    if task.status != TaskStatus.COMPLETED:
        raise HTTPException(400, "Only completed tasks can be synchronized")

    expected_sha256 = archive_sha256.strip().casefold()
    if expected_sha256 and not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise HTTPException(400, "Invalid archive SHA-256")

    store = get_task_store()
    existing = store.get(task.id)
    existing_sync = (existing.result or {}).get("remote_sync") if existing else None
    if (
        expected_sha256
        and isinstance(existing_sync, dict)
        and existing_sync.get("archive_sha256") == expected_sha256
    ):
        await archive.close()
        return {
            "ok": True,
            "task_id": str(task.id),
            "already_synced": True,
            "sync": existing_sync,
        }

    data_root = Path(get_runtime_settings().data_root).resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    staging_dir = data_root / "_remote_sync" / f"{task.id}-{uuid4().hex}"
    zip_path = staging_dir / "archive.zip"
    extraction_dir = staging_dir / "extracted"
    staging_dir.mkdir(parents=True, exist_ok=False)
    try:
        upload_size, upload_sha256 = await stream_upload_to_path(archive, zip_path)
        if expected_sha256 and upload_sha256 != expected_sha256:
            raise HTTPException(400, "Archive SHA-256 mismatch")
        try:
            extracted_root = safe_extract_zip(zip_path, extraction_dir)
            metadata = _read_metadata(extracted_root)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        metadata["task_id"] = str(task.id)
        metadata.setdefault("archive_id", str(task.id))
        (extracted_root / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        destination = _destination_for(data_root, archive_name, str(task.id))
        publish_archive(extracted_root, destination, data_root)

        sync_info = {
            "worker_id": worker_id.strip(),
            "archive_sha256": upload_sha256,
            "archive_size": upload_size,
            "synced_at": datetime.now().astimezone().isoformat(),
        }
        result = dict(task.result or {})
        result["output_dir"] = str(destination)
        result["archive"] = {
            "output_dir": str(destination),
            "files": archive_file_manifest(destination),
        }
        result["remote_sync"] = sync_info
        task.result = result
        task.status = TaskStatus.COMPLETED
        task.progress = 1.0
        task.message = "处理完成，已从本地同步"
        task.error = None
        task.updated_at = datetime.now()
        task.completed_at = task.completed_at or datetime.now()
        store.save(task)
        store.add_event(
            task.id,
            "remote_archive_imported",
            stage="sync",
            message="本地归档已同步到服务器",
            data={"worker_id": worker_id.strip(), "archive_sha256": upload_sha256},
        )
        get_archive_sync_service().reconcile()
        return {
            "ok": True,
            "task_id": str(task.id),
            "already_synced": False,
            "sync": sync_info,
        }
    finally:
        await asyncio.to_thread(shutil.rmtree, staging_dir, True)
