"""Keyword search over transcripts, for the global Ctrl+K search."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.core.workspace_lifecycle import run_in_thread

router = APIRouter(prefix="/search", tags=["search"])


@router.get("/transcripts")
async def search_transcripts(
    q: str = Query(..., max_length=200),
    limit: int = Query(20, ge=1, le=50),
) -> dict[str, Any]:
    from app.services.transcript_search import get_transcript_search_service

    return await run_in_thread(get_transcript_search_service().search, q, limit=limit)
