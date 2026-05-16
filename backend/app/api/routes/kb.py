"""Knowledge base API routes."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/kb", tags=["kb"])


@router.get("/search")
async def search_kb(
    q: str = Query(..., description="Search query text"),
    top_k: int = Query(10, ge=1, le=50),
    platform: str | None = Query(None),
    uploader_id: str | None = Query(None),
) -> dict[str, Any]:
    """Search the knowledge base using semantic similarity.

    Returns ranked chunks with task metadata, timestamps, and similarity scores.
    """
    from app.core.settings import get_runtime_settings
    rt = get_runtime_settings()
    if not rt.kb_enabled:
        raise HTTPException(503, "Knowledge base is disabled (kb_enabled=false)")
    if not rt.kb_embedding_api_base:
        raise HTTPException(503, "Embedding API not configured (kb_embedding_api_base is empty)")

    from app.services.kb.embedding import get_embedding_service
    from app.services.kb.store import get_kb_store
    from app.core.database import get_task_store

    emb_service = get_embedding_service()
    try:
        query_vec = await asyncio.to_thread(emb_service.embed_one, q)
    except Exception as e:
        raise HTTPException(500, f"Embedding failed: {e}")

    filters = {}
    if platform:
        filters["platform"] = platform
    if uploader_id:
        filters["uploader_id"] = uploader_id

    try:
        results = await asyncio.to_thread(get_kb_store().search, query_vec, top_k, filters or None)
    except Exception as e:
        raise HTTPException(500, f"Search failed: {e}")

    # Enrich with task metadata
    task_store = get_task_store()
    enriched = []
    for r in results:
        task_meta: dict = {}
        try:
            import uuid
            task = task_store.get(uuid.UUID(r["task_id"]))
            if task:
                task_meta = {
                    "title": task.result.get("metadata", {}).get("title") if task.result else None,
                    "platform": task.platform,
                    "uploader_id": task.uploader_id,
                    "content_subtype": task.content_subtype,
                }
        except Exception:
            pass
        enriched.append({**r, "task_metadata": task_meta})

    return {"results": enriched, "total": len(enriched), "query": q}


@router.post("/reindex")
async def reindex_kb() -> dict[str, Any]:
    """Reindex all completed tasks into the knowledge base."""
    from app.core.settings import get_runtime_settings
    rt = get_runtime_settings()
    if not rt.kb_enabled:
        raise HTTPException(503, "Knowledge base is disabled")
    if not rt.kb_embedding_api_base:
        raise HTTPException(503, "Embedding API not configured")

    from app.services.kb.indexer import reindex_all
    try:
        count = await asyncio.to_thread(reindex_all)
    except Exception as e:
        raise HTTPException(500, f"Reindex failed: {e}")

    return {"ok": True, "tasks_indexed": count}


@router.get("/stats")
async def kb_stats() -> dict[str, Any]:
    """Return knowledge base statistics."""
    from app.services.kb.store import get_kb_store
    try:
        count = get_kb_store().chunk_count()
    except Exception:
        count = 0
    return {"chunk_count": count}
