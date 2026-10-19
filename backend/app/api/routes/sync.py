"""Archive synchronization API for mobile clients and desktop daemons."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from app.core.paths import get_workspace_paths

from app.core.workspace_lifecycle import run_in_thread

from app.core.archive_sync import (
    archive_file_manifest,
    build_archive_zip,
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
_PORTABLE_RESULT_FIELDS = {
    "metadata",
    "image_descriptions",
    "image_download_diagnostics",
    "analysis",
    "warnings",
    "warning",
    "content_subtype",
    "subtitle_source",
    "transcript_segments",
}


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
    # Reuse the existing task archive across a layout upgrade.
    existing = get_task_store().get(UUID(task_id))
    if existing and (existing.result or {}).get("output_dir"):
        path = Path(existing.result["output_dir"])
        if path.is_dir() and data_root in path.resolve().parents:
            return path
    archive_root = get_workspace_paths(data_root).archives
    base_name = _safe_archive_name(archive_name)
    candidate = archive_root / base_name
    suffix = 2
    while candidate.exists():
        try:
            metadata = _read_metadata(candidate)
        except ValueError:
            metadata = {}
        if str(metadata.get("task_id") or "") == task_id:
            return candidate
        candidate = archive_root / f"{base_name} ({suffix})"
        suffix += 1
    return candidate


def _task_archive_dir(task: Task, data_root: Path) -> Path:
    result = task.result if isinstance(task.result, dict) else {}
    raw_path = result.get("output_dir")
    if not raw_path and isinstance(result.get("archive"), dict):
        raw_path = result["archive"].get("output_dir")
    if not raw_path:
        raise HTTPException(404, "Task has no archive directory")
    try:
        archive_dir = Path(str(raw_path)).resolve()
        archive_dir.relative_to(data_root)
    except (OSError, ValueError) as exc:
        raise HTTPException(403, "Task archive is outside data_root") from exc
    if not archive_dir.is_dir() or not (archive_dir / "metadata.json").is_file():
        raise HTTPException(404, "Task archive is unavailable")
    return archive_dir


def _portable_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    return {
        key: result[key]
        for key in _PORTABLE_RESULT_FIELDS
        if key in result
    }


@router.get("/changes")
async def sync_changes(
    cursor: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    x_mpp_workspace_id: str | None = Header(default=None),
):
    _check_workspace(x_mpp_workspace_id)
    return await run_in_thread(get_archive_sync_service().changes, cursor, limit)


def _check_workspace(expected: str | None) -> None:
    if expected is not None and unquote(expected) != str(Path(get_runtime_settings().data_root).resolve()):
        raise HTTPException(409, "Workspace changed; restart synchronization")


@router.get("/status")
async def sync_status():
    return await run_in_thread(get_archive_sync_service().index_status)


@router.get('/library')
async def sync_library():
    from app.core.library import get_library_service
    return await run_in_thread(get_library_service().sync_snapshot)


@router.get("/archives/{archive_id}/manifest")
async def sync_manifest(archive_id: str, x_mpp_workspace_id: str | None = Header(default=None)):
    _check_workspace(x_mpp_workspace_id)
    manifest = await run_in_thread(get_archive_sync_service().manifest, archive_id)
    if manifest is None:
        raise HTTPException(404, "Archive not found")
    return manifest


@router.get("/archives/{archive_id}/files/{relative_path:path}")
async def sync_file(
    archive_id: str,
    relative_path: str,
    if_none_match: str | None = Header(default=None),
    x_mpp_workspace_id: str | None = Header(default=None),
):
    _check_workspace(x_mpp_workspace_id)
    resolved = await run_in_thread(
        get_archive_sync_service().resolve_declared_file,
        archive_id,
        relative_path,
    )
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
            "X-MPP-Workspace-ID": quote(str(Path(get_runtime_settings().data_root).resolve()), safe=""),
        },
    )


@router.post("/rebuild")
async def rebuild_sync_index():
    return await run_in_thread(get_archive_sync_service().rebuild)


@router.get("/tasks/{task_id}/archive")
async def export_completed_archive(
    task_id: UUID,
    include_media: bool = Query(False),
):
    """Export one completed task as a portable ZIP for another MPP endpoint."""
    task = get_task_store().get(task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    if task.status != TaskStatus.COMPLETED:
        raise HTTPException(409, "Only completed tasks can be transferred")
    data_root = Path(get_runtime_settings().data_root).resolve()
    archive_dir = _task_archive_dir(task, data_root)
    staging_dir = get_workspace_paths(data_root).temporary("sync_downloads") / f"{task_id}-{uuid4().hex}"
    zip_path = staging_dir / "archive.zip"
    staging_dir.mkdir(parents=True, exist_ok=False)
    try:
        await run_in_thread(
            build_archive_zip,
            archive_dir,
            zip_path,
            include_media=include_media,
        )
    except Exception:
        await run_in_thread(shutil.rmtree, staging_dir, True)
        raise
    encoded_name = quote(archive_dir.name, safe="")
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"{archive_dir.name}.zip",
        headers={
            "Cache-Control": "no-store",
            "X-MPP-Archive-Name": encoded_name,
            "X-MPP-Include-Media": str(include_media).lower(),
        },
        background=BackgroundTask(shutil.rmtree, staging_dir, True),
    )


@router.post("/import")
async def import_completed_archive(
    task_json: str = Form(...),
    archive_name: str = Form(...),
    archive_sha256: str = Form(""),
    worker_id: str = Form(""),
    execution_id: str = Form(""),
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
    _require_importable(task.id, existing, worker_id.strip(), execution_id.strip())

    data_root = Path(get_runtime_settings().data_root).resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    staging_dir = get_workspace_paths(data_root).temporary("remote_sync") / f"{task.id}-{uuid4().hex}"
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

        # No await inside the commit: queue commands on this event loop cannot
        # read the old task and then enqueue it halfway through replacement.
        outcome = _commit_import(task, extracted_root, metadata, data_root, archive_name,
                                 upload_sha256, upload_size, worker_id.strip(), execution_id.strip())
        if execution_id:
            from app.core.events import TaskEvent, get_event_bus
            await get_event_bus().publish(TaskEvent(task.id, "completed", {"progress": 1.0}))
        return outcome
    finally:
        await archive.close()
        await run_in_thread(shutil.rmtree, staging_dir, True)


def _require_importable(task_id: UUID, existing: Task | None, worker_id="", execution_id="") -> None:
    from app.core.queue import get_task_queue

    queue = get_task_queue()
    from app.core.remote_execution import require_owner
    if require_owner(existing, worker_id, execution_id):
        return
    if (existing and existing.status in {
        TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.PROCESSING, TaskStatus.PAUSED,
    }) or task_id in queue.active_task_ids or task_id in queue._running_tasks:
        raise HTTPException(409, "Task is active; stop it before importing its archive")


def _commit_import(task, extracted_root, metadata, data_root, archive_name,
                   upload_sha256, upload_size, worker_id, execution_id=""):
    from app.core.artifacts import _write_lock, get_artifact_store
    from app.core.database import _save_task_row, task_write_transaction
    from app.services.archiving.archive import get_archive_service

    artifacts = get_artifact_store()
    service = get_archive_sync_service()
    store = get_task_store()
    publication = None
    # Lock ordering matches indexing then artifact writes then database writes.
    with service._reconcile_lock, _write_lock:
        try:
            with task_write_transaction() as conn:
                existing = store.get(task.id)
                _require_importable(task.id, existing, worker_id, execution_id)
                from app.core.content_identity import archive_content_key, task_content_key
                deleting = {row[0] for row in conn.execute("SELECT content_key FROM library_deletions")}
                task_keys = {task_content_key(existing or task)}
                if deleting:
                    for row in conn.execute("SELECT archive_id,snapshot FROM archive_sync_index WHERE task_id=? AND deleted=0",
                                            (str(task.id),)):
                        item = {**json.loads(row["snapshot"]), "archive_id": row["archive_id"]}
                        task_keys.add(archive_content_key(item, existing or task))
                if task_keys & deleting:
                    raise HTTPException(409, "Task content is being deleted")
                existing_sync = (existing.result or {}).get("remote_sync") if existing else None
                if isinstance(existing_sync, dict) and existing_sync.get("archive_sha256") == upload_sha256:
                    output_dir = (existing.result or {}).get("output_dir")
                    if output_dir and Path(output_dir).is_dir():
                        repairs = artifacts.import_mirrors(conn, task.id, Path(output_dir))
                        return {"ok": True, "task_id": str(task.id), "already_synced": True,
                                "artifacts": repairs, "sync": existing_sync}

                destination = _destination_for(data_root, archive_name, str(task.id))
                archive_id = str(task.id)
                if destination.exists():
                    try:
                        archive_id = str(UUID(str(_read_metadata(destination).get("archive_id"))))
                    except ValueError:
                        pass
                row = conn.execute("SELECT archive_path FROM archive_sync_index WHERE archive_id=? AND deleted=0",
                                   (archive_id,)).fetchone()
                if row and (data_root / row["archive_path"]).resolve() != destination.resolve():
                    archive_id = str(uuid4())
                metadata.update(task_id=str(task.id), archive_id=archive_id, status="completed")
                artifacts.write(None, extracted_root, "metadata.json",
                                json.dumps(metadata, ensure_ascii=False, indent=2), notify_index=False)
                sync_info = {"worker_id": worker_id, "archive_sha256": upload_sha256,
                             "archive_size": upload_size, "import_id": str(uuid4()),
                             "synced_at": datetime.now().astimezone().isoformat()}
                publication = publish_archive(extracted_root, destination, data_root,
                                              task_id=task.id, import_id=sync_info["import_id"])
                task.result = {**_portable_result(task.result), "output_dir": str(destination),
                               "archive": {"output_dir": str(destination),
                                           "files": archive_file_manifest(destination)},
                               "remote_sync": sync_info}
                task.status, task.progress, task.error = TaskStatus.COMPLETED, 1.0, None
                task.message = "处理完成，已从本地同步"
                task.updated_at = datetime.now()
                task.completed_at = task.completed_at or datetime.now()
                if existing:
                    task.created_at = existing.created_at
                    if execution_id:
                        task.options = existing.options
                        task.source = existing.source
                        task.webhook_url = existing.webhook_url
                _save_task_row(conn, task)
                repairs = artifacts.import_mirrors(conn, task.id, destination)
                conn.execute(
                    "INSERT INTO task_events (task_id,event_type,stage,message,data,timestamp) "
                    "VALUES (?, 'remote_archive_imported', 'sync', ?, ?, ?)",
                    (str(task.id), "本地归档已同步到服务器", json.dumps({"worker_id": worker_id,
                     "archive_sha256": upload_sha256}), task.updated_at.isoformat()),
                )
                archive_service = get_archive_service()
                item = archive_service._archive_item(destination, {
                    archive_service._path_key(destination): {
                        "id": str(task.id), "created_at": task.created_at.isoformat(),
                        "status": task.status, "error": None,
                    },
                }, lite=True)
                if item is None:
                    raise ValueError("Imported archive cannot be indexed")
                service._upsert_if_changed(archive_id, destination, service._file_state(destination),
                                           item, transaction=conn)
        except BaseException:
            if publication is not None:
                current = store.get(task.id)
                committed = ((current.result or {}).get("remote_sync") or {}) if current else {}
                if committed.get("import_id") == sync_info["import_id"]:
                    # An interruption immediately after SQLite commit must not
                    # restore old files over the now committed task and mirrors.
                    publication.finish()
                else:
                    publication.rollback()
            raise
        publication.finish()
    return {"ok": True, "task_id": str(task.id), "already_synced": False,
            "artifacts": repairs, "sync": sync_info}
