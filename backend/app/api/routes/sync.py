"""Archive synchronization API for the Capacitor mobile client."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse, Response

from app.core.archive_sync import get_archive_sync_service

router = APIRouter(prefix="/sync", tags=["sync"])


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
