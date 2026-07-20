"""Bilibili multi-part and UGC-season selection metadata."""

from __future__ import annotations

from typing import Any

from app.services.ingestion.ytdlp import (
    _bilibili_canonical_video_url,
    _extract_bilibili_bvid,
    _extract_bilibili_page_number,
    normalize_bilibili_source_url,
)

from .api import view


def _cover_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    url = value.strip()
    return f"https:{url}" if url.startswith("//") else url


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _multipart_items(data: dict[str, Any], bvid: str) -> list[dict[str, Any]]:
    pages = data.get("pages") or []
    cover = _cover_url(data.get("pic"))
    items: list[dict[str, Any]] = []
    for fallback_index, page in enumerate(pages, start=1):
        page_number = _int_or_none(page.get("page")) or fallback_index
        items.append({
            "id": f"{bvid}:p{page_number}",
            "bvid": bvid,
            "page": page_number,
            "title": str(page.get("part") or f"P{page_number}").strip(),
            "duration": _int_or_none(page.get("duration")),
            "cover": _cover_url(page.get("first_frame")) or cover,
            "url": _bilibili_canonical_video_url(bvid, page_number),
        })
    return items


def _ugc_season_items(season: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for section in season.get("sections") or []:
        section_title = str(section.get("title") or "").strip() or None
        for episode in section.get("episodes") or []:
            arc = episode.get("arc") or {}
            bvid = str(episode.get("bvid") or arc.get("bvid") or "").strip()
            if not bvid or bvid in seen:
                continue
            seen.add(bvid)
            items.append({
                "id": bvid,
                "bvid": bvid,
                "page": 1,
                "title": str(
                    episode.get("title") or arc.get("title") or bvid
                ).strip(),
                "duration": _int_or_none(arc.get("duration") or episode.get("duration")),
                "cover": _cover_url(arc.get("pic") or episode.get("cover")),
                "section": section_title,
                "url": _bilibili_canonical_video_url(bvid),
            })
    return items


def inspect_bilibili_collection(url: str) -> dict[str, Any]:
    """Return a selectable collection for Bilibili multi-part/season URLs."""
    normalized_url = normalize_bilibili_source_url(url)
    bvid = _extract_bilibili_bvid(normalized_url)
    if not bvid:
        return {"is_bilibili": False, "is_collection": False, "items": []}

    data = view(bvid)
    title = str(data.get("title") or bvid).strip()
    pages = data.get("pages") or []
    if len(pages) > 1:
        return {
            "is_bilibili": True,
            "is_collection": True,
            "collection_type": "multipart",
            "title": title,
            "current_item_id": f"{bvid}:p{_extract_bilibili_page_number(normalized_url)}",
            "items": _multipart_items(data, bvid),
        }

    season = data.get("ugc_season") or {}
    season_items = _ugc_season_items(season)
    if len(season_items) > 1:
        return {
            "is_bilibili": True,
            "is_collection": True,
            "collection_type": "ugc_season",
            "title": str(season.get("title") or title).strip(),
            "current_item_id": bvid,
            "items": season_items,
        }

    return {
        "is_bilibili": True,
        "is_collection": False,
        "title": title,
        "current_item_id": bvid,
        "items": [],
    }
