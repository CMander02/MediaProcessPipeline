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


def _published_at(value: Any) -> int | None:
    timestamp = _int_or_none(value)
    return timestamp if timestamp is not None and timestamp > 0 else None


def _multipart_items(data: dict[str, Any], bvid: str) -> list[dict[str, Any]]:
    pages = data.get("pages") or []
    cover = _cover_url(data.get("pic"))
    items: list[dict[str, Any]] = []
    for fallback_index, page in enumerate(pages, start=1):
        page_number = _int_or_none(page.get("page"))
        if page_number is None or page_number < 1:
            page_number = fallback_index
        items.append({
            "id": f"{bvid}:p{page_number}",
            "content_key": f"bilibili:{bvid}:p{page_number}",
            "position": fallback_index,
            "published_at": _published_at(data.get("pubdate")),
            "bvid": bvid,
            "page": page_number,
            "title": str(page.get("part") or f"P{page_number}").strip(),
            "duration": _int_or_none(page.get("duration")),
            "cover": _cover_url(page.get("first_frame")) or cover,
            "url": _bilibili_canonical_video_url(bvid, page_number),
        })
    return items


def _ugc_season_items(
    season: dict[str, Any], current_bvid: str, current_page: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    position = 0
    for section in season.get("sections") or []:
        section_title = str(section.get("title") or "").strip() or None
        for episode in section.get("episodes") or []:
            position += 1
            arc = episode.get("arc") or {}
            bvid = str(episode.get("bvid") or arc.get("bvid") or "").strip()
            if not bvid or bvid in seen:
                continue
            seen.add(bvid)
            page_number = current_page if bvid == current_bvid else 1
            items.append({
                "id": bvid,
                "content_key": f"bilibili:{bvid}:p{page_number}",
                "position": position,
                "published_at": (
                    _published_at(arc.get("pubdate"))
                    or _published_at(episode.get("pubdate"))
                ),
                "bvid": bvid,
                "page": page_number,
                "title": str(
                    episode.get("title") or arc.get("title") or bvid
                ).strip(),
                "duration": _int_or_none(arc.get("duration") or episode.get("duration")),
                "cover": _cover_url(arc.get("pic") or episode.get("cover")),
                "section": section_title,
                "url": _bilibili_canonical_video_url(bvid, page_number),
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
    owner_data = data.get("owner") or {}
    # Folder cards show the uploader's avatar for collections created from this result.
    owner = {"name": str(owner_data.get("name") or ""), "face": _cover_url(owner_data.get("face")) or "",
             "mid": _int_or_none(owner_data.get("mid"))}
    current_page = _extract_bilibili_page_number(normalized_url)
    pages = data.get("pages") or []
    season = data.get('ugc_season') or {}
    season_items = _ugc_season_items(season, bvid, current_page)
    season_total = max(_int_or_none(season.get('ep_count')) or 0, len(season_items))
    related_collections = []
    if season_total > 1 and season.get('id'):
        related_collections.append({'is_collection': True, 'collection_type': 'ugc_season',
            'collection_key': f"bilibili:ugc:{season['id']}", 'title': str(season.get('title') or title),
            'items': season_items, 'total': season_total, 'complete': season_total == len(season_items),
            'owner': owner})
    if len(pages) > 1:
        items = _multipart_items(data, bvid)
        return {
            "is_bilibili": True,
            "is_collection": True,
            "collection_type": "multipart",
            "collection_key": f"bilibili:{bvid}",
            "entry_kind": "video",
            "total": len(items),
            "complete": True,
            "title": title,
            "current_item_id": f"{bvid}:p{current_page}",
            "items": items,
            'related_collections': related_collections,
            "owner": owner,
        }

    season = data.get("ugc_season") or {}
    season_items = _ugc_season_items(season, bvid, current_page)
    total = max(_int_or_none(season.get("ep_count")) or 0, len(season_items))
    if total > 1:
        return {
            "is_bilibili": True,
            "is_collection": True,
            "collection_type": "ugc_season",
            "collection_key": f"bilibili:ugc:{season['id']}" if season.get("id") else None,
            "entry_kind": "video",
            "total": total,
            "complete": total == len(season_items),
            "title": str(season.get("title") or title).strip(),
            "current_item_id": bvid,
            "items": season_items,
            "owner": owner,
        }

    return {
        "is_bilibili": True,
        "is_collection": False,
        "collection_key": None,
        "entry_kind": "video",
        "total": 0,
        "complete": True,
        "title": title,
        "current_item_id": bvid,
        "items": [],
    }
