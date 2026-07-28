"""Coordinator routes for EXE workers and portable archive synchronization."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from app.core.archive_sync import (
    MAX_UPLOAD_BYTES,
    MAX_PORTABLE_RESULT_JSON_BYTES,
    archive_file_manifest,
    build_archive_zip,
    publish_archive,
    rewrite_archive_image_paths,
    rewrite_result_image_paths,
    safe_extract_zip,
    sanitize_portable_result,
    stream_upload_to_path,
)
from app.core.database import get_task_store
from app.core.events import TaskEvent, get_event_bus
from app.core.settings import get_runtime_settings
from app.models import TaskStatus

router = APIRouter(tags=["sync"])
_WORKER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class WorkerRegisterRequest(BaseModel):
    worker_id: str | None = Field(default=None, max_length=128)
    name: str = Field(default="MPP EXE", min_length=1, max_length=128)
    capabilities: dict[str, Any] = Field(default_factory=dict)


class WorkerHeartbeatRequest(BaseModel):
    capabilities: dict[str, Any] | None = None


class WorkerClaimRequest(BaseModel):
    lease_seconds: int = Field(default=900, ge=30, le=3600)


class LeaseRenewRequest(BaseModel):
    lease_token: str = Field(min_length=16, max_length=512)
    lease_seconds: int = Field(default=900, ge=30, le=3600)


class WorkerFailureRequest(BaseModel):
    lease_token: str = Field(min_length=16, max_length=512)
    error: str = Field(min_length=1, max_length=20_000)


def _validate_worker_id(worker_id: str) -> str:
    value = worker_id.strip()
    if not _WORKER_ID_PATTERN.fullmatch(value):
        raise HTTPException(status_code=400, detail="Invalid worker_id")
    return value


def _server_worker_record() -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": "server",
        "name": "Server",
        "executor": "server",
        "status": "online",
        "online": True,
        "registered_at": None,
        "last_seen_at": now,
        "capabilities": {"executor": "server"},
    }


def _worker_api_record(record: dict[str, Any]) -> dict[str, Any]:
    last_seen_raw = str(record.get("last_seen_at") or "")
    try:
        last_seen = datetime.fromisoformat(last_seen_raw)
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        online = (datetime.now(timezone.utc) - last_seen).total_seconds() <= 90
    except ValueError:
        online = False
    return {
        **record,
        "executor": "exe",
        "status": "online" if online else "offline",
        "online": online,
    }


@router.get("/workers")
async def list_workers():
    """List selectable processing targets, including the built-in server."""
    workers = [_server_worker_record()]
    workers.extend(_worker_api_record(record) for record in get_task_store().list_workers())
    return {"workers": workers}


@router.post("/workers/register")
async def register_worker(request: WorkerRegisterRequest):
    worker_id = _validate_worker_id(request.worker_id) if request.worker_id else None
    record = get_task_store().register_worker(
        worker_id=worker_id,
        name=request.name.strip(),
        capabilities=request.capabilities,
    )
    return {"worker": _worker_api_record(record)}


@router.post("/workers/{worker_id}/heartbeat")
async def heartbeat_worker(worker_id: str, request: WorkerHeartbeatRequest):
    worker_id = _validate_worker_id(worker_id)
    record = get_task_store().heartbeat_worker(
        worker_id,
        capabilities=request.capabilities,
    )
    if not record:
        raise HTTPException(status_code=404, detail="Worker is not registered")
    return {"worker": _worker_api_record(record)}


@router.post("/workers/{worker_id}/claim")
async def claim_task(worker_id: str, request: WorkerClaimRequest):
    worker_id = _validate_worker_id(worker_id)
    if not get_task_store().get_worker(worker_id):
        raise HTTPException(status_code=404, detail="Worker is not registered")
    claimed = get_task_store().claim_remote_task(
        worker_id,
        lease_seconds=request.lease_seconds,
    )
    if not claimed:
        return {"task": None}
    task = claimed["task"]
    try:
        input_path = _claimed_task_input_path(
            task.id,
            worker_id,
            claimed["lease_token"],
        )
    except HTTPException:
        input_path = None
    if input_path is not None:
        claimed["input"] = {
            "filename": input_path.name,
            "size": input_path.stat().st_size,
        }
    await get_event_bus().publish(
        TaskEvent(
            task.id,
            "claimed",
            {
                "executor": worker_id,
                "attempt": claimed["attempt"],
                "lease_expires_at": claimed["lease_expires_at"],
            },
        )
    )
    return claimed


@router.post("/workers/{worker_id}/tasks/{task_id}/lease/renew")
async def renew_lease(worker_id: str, task_id: UUID, request: LeaseRenewRequest):
    worker_id = _validate_worker_id(worker_id)
    renewed = get_task_store().renew_remote_lease(
        task_id,
        worker_id,
        request.lease_token,
        lease_seconds=request.lease_seconds,
    )
    if not renewed:
        raise HTTPException(
            status_code=409,
            detail="Lease is missing, expired, or owned by another worker",
        )
    return renewed


def _claimed_task_input_path(
    task_id: UUID,
    worker_id: str,
    lease_token: str,
) -> Path | None:
    """Resolve a lease-bound server upload without exposing arbitrary paths."""
    store = get_task_store()
    if not store.get_remote_lease(task_id, worker_id, lease_token):
        raise HTTPException(
            status_code=409,
            detail="Lease is missing, expired, or owned by another worker",
        )
    task = store.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if (
        task.status != TaskStatus.PROCESSING
        or task.requested_executor != "exe"
        or task.assigned_executor != worker_id
    ):
        raise HTTPException(status_code=409, detail="Task is not assigned to this EXE lease")

    source = str(task.source or "").strip()
    if source.startswith(("http://", "https://", "upload://")):
        return None

    data_root = Path(get_runtime_settings().data_root).resolve()
    staging_root = (data_root / "_staging").resolve()
    source_path = Path(source)
    try:
        resolved = source_path.resolve(strict=True)
        relative = resolved.relative_to(staging_root)
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=403,
            detail="Task input is outside the server staging directory",
        ) from exc
    if len(relative.parts) < 2 or source_path.is_symlink() or not resolved.is_file():
        raise HTTPException(status_code=403, detail="Task input is not a staged regular file")
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        raise HTTPException(status_code=404, detail="Task input is unavailable") from exc
    if size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Task input exceeds the 10 GB limit")
    return resolved


@router.get("/workers/{worker_id}/tasks/{task_id}/input")
async def download_claimed_task_input(
    worker_id: str,
    task_id: UUID,
    lease_token: str = Header(
        ...,
        alias="X-MPP-Lease-Token",
        min_length=16,
        max_length=512,
    ),
):
    """Download one server-staged input through its active EXE lease."""
    worker_id = _validate_worker_id(worker_id)
    input_path = _claimed_task_input_path(task_id, worker_id, lease_token)
    if input_path is None:
        raise HTTPException(status_code=404, detail="Task has no staged input")
    size = input_path.stat().st_size
    return FileResponse(
        input_path,
        media_type="application/octet-stream",
        filename=input_path.name,
        headers={
            "Cache-Control": "private, no-store",
            "X-MPP-Input-Size": str(size),
        },
    )


def _task_archive_destination(task_id: UUID) -> tuple[Path, Path]:
    store = get_task_store()
    task = store.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.requested_executor != "exe":
        raise HTTPException(status_code=409, detail="Task is not assigned to an EXE executor")

    data_root = Path(get_runtime_settings().data_root).resolve()
    result = task.result if isinstance(task.result, dict) else {}
    destination_raw = result.get("output_dir")
    destination = (
        Path(str(destination_raw)).resolve()
        if destination_raw
        else (data_root / f"remote-{task_id}").resolve()
    )
    try:
        destination.relative_to(data_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail="Task output directory is outside data_root",
        ) from exc
    if destination == data_root:
        raise HTTPException(status_code=409, detail="Task output directory is invalid")
    return data_root, destination


def _prepare_uploaded_metadata(archive_root: Path, task_id: UUID) -> dict[str, Any]:
    metadata_path = archive_root / "metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Uploaded metadata.json is invalid") from exc
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=400, detail="Uploaded metadata.json must be an object")
    metadata["status"] = "completed"
    metadata["task_id"] = str(task_id)
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return metadata


def _parse_portable_result_json(raw: str) -> dict[str, Any]:
    if len(raw.encode("utf-8")) > MAX_PORTABLE_RESULT_JSON_BYTES:
        raise HTTPException(status_code=413, detail="result_json exceeds the 4 MB limit")
    try:
        payload = json.loads(raw or "{}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="result_json is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="result_json must be an object")
    return sanitize_portable_result(payload)


@router.post("/workers/{worker_id}/tasks/{task_id}/archive")
async def upload_completed_archive(
    worker_id: str,
    task_id: UUID,
    lease_token: str = Form(...),
    result_json: str = Form("{}"),
    archive: UploadFile = File(...),
):
    """Upload, validate, atomically publish, and finalize one EXE result ZIP."""
    worker_id = _validate_worker_id(worker_id)
    store = get_task_store()
    upload_lease = store.begin_remote_upload(
        task_id,
        worker_id,
        lease_token,
        lease_seconds=3600,
    )
    if not upload_lease:
        raise HTTPException(
            status_code=409,
            detail="Lease is unavailable or another upload is already in progress",
        )
    if upload_lease["state"] == "completed":
        task = store.get(task_id)
        return {"task": task, "already_completed": True}

    staging_dir: Path | None = None
    finalized = False
    finalization_started = False
    release_on_exit = True

    try:
        portable_result = _parse_portable_result_json(result_json)
        data_root, destination = _task_archive_destination(task_id)
        staging_dir = data_root / "_remote_sync" / f"{task_id}-{uuid4().hex}"
        zip_path = staging_dir / "archive.zip"
        extraction_dir = staging_dir / "extracted"
        staging_dir.mkdir(parents=True, exist_ok=False)

        upload_size, upload_sha256 = await stream_upload_to_path(archive, zip_path)
        try:
            extracted_root = await asyncio.to_thread(safe_extract_zip, zip_path, extraction_dir)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Unsafe or invalid archive ZIP: {exc}",
            ) from exc

        metadata = _prepare_uploaded_metadata(extracted_root, task_id)
        if not store.begin_remote_finalization(
            task_id,
            worker_id,
            lease_token,
            lease_seconds=3600,
        ):
            raise HTTPException(
                status_code=409,
                detail="Task changed while the archive was uploading",
            )
        finalization_started = True

        await asyncio.to_thread(publish_archive, extracted_root, destination, data_root)
        await asyncio.to_thread(rewrite_archive_image_paths, destination)
        portable_result = await asyncio.to_thread(
            rewrite_result_image_paths,
            portable_result,
            destination,
        )
        try:
            metadata = json.loads(
                (destination / "metadata.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=500,
                detail="Published metadata.json could not be read",
            ) from exc
        files = await asyncio.to_thread(archive_file_manifest, destination)
        previous = store.get(task_id)
        previous_result = (
            dict(previous.result or {})
            if previous and isinstance(previous.result, dict)
            else {}
        )
        result = {
            **previous_result,
            **portable_result,
            "metadata": metadata,
            "output_dir": str(destination),
            "archive": {
                "output_dir": str(destination),
                "files": files,
            },
            "remote_sync": {
                "worker_id": worker_id,
                "archive_sha256": upload_sha256,
                "archive_size": upload_size,
            },
        }
        task, already_completed = store.complete_remote_task(
            task_id,
            worker_id,
            lease_token,
            result,
        )
        if not task:
            raise HTTPException(status_code=409, detail="Lease expired before result finalization")
        finalized = True

        await get_event_bus().publish(
            TaskEvent(
                task_id,
                "completed",
                {
                    "output_dir": str(destination),
                    "executor": worker_id,
                    "sync_revision": task.sync_revision,
                },
            )
        )
        return {"task": task, "already_completed": already_completed}
    except asyncio.CancelledError:
        # A thread-backed publish may still be finishing after the request is
        # cancelled. Keep its finalizing lease exclusive and let it expire
        # before another worker can retry.
        if finalization_started:
            release_on_exit = False
        raise
    finally:
        if not finalized and release_on_exit:
            store.release_remote_upload(task_id, worker_id, lease_token)
        if staging_dir is not None and (release_on_exit or finalized):
            shutil.rmtree(staging_dir, ignore_errors=True)


@router.post("/workers/{worker_id}/tasks/{task_id}/fail")
async def fail_claimed_task(worker_id: str, task_id: UUID, request: WorkerFailureRequest):
    worker_id = _validate_worker_id(worker_id)
    task, already_failed = get_task_store().fail_remote_task(
        task_id,
        worker_id,
        request.lease_token,
        request.error,
    )
    if not task:
        raise HTTPException(
            status_code=409,
            detail="Lease is missing, expired, or owned by another worker",
        )
    await get_event_bus().publish(
        TaskEvent(
            task_id,
            "failed",
            {
                "error": request.error,
                "executor": worker_id,
                "sync_revision": task.sync_revision,
            },
        )
    )
    return {"task": task, "already_failed": already_failed}


def _safe_download_name(task_title: str, task_id: UUID) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", task_title).strip(" .")
    return f"{(cleaned or str(task_id))[:100]}.zip"


@router.get("/sync/tasks/{task_id}/archive")
async def download_task_archive(
    task_id: UUID,
    include_media: bool = Query(False),
):
    """Download a portable archive package; media is opt-in."""
    task = get_task_store().get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != TaskStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Task is not completed")

    data_root = Path(get_runtime_settings().data_root).resolve()
    result = task.result if isinstance(task.result, dict) else {}
    output_dir_raw = result.get("output_dir")
    if not output_dir_raw:
        raise HTTPException(status_code=404, detail="Task has no archive output")
    output_dir = Path(str(output_dir_raw)).resolve()
    try:
        output_dir.relative_to(data_root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Task archive is outside data_root") from exc
    if not output_dir.is_dir():
        raise HTTPException(status_code=404, detail="Task archive directory not found")

    download_dir = data_root / "_sync_downloads" / uuid4().hex
    zip_path = download_dir / "archive.zip"
    download_dir.mkdir(parents=True, exist_ok=False)
    try:
        await asyncio.to_thread(
            build_archive_zip,
            output_dir,
            zip_path,
            include_media=include_media,
        )
    except (OSError, ValueError) as exc:
        shutil.rmtree(download_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"Cannot package task archive: {exc}") from exc

    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    filename = _safe_download_name(str(metadata.get("title") or ""), task_id)
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(shutil.rmtree, download_dir, ignore_errors=True),
    )
