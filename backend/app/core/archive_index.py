"""Queryable fields for the rebuildable archive index."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone


def source_filter(metadata: dict) -> str:
    aliases = {
        "xiaohongshu": "xiaohongshu",
        "xhs": "xiaohongshu",
        "bilibili": "bilibili",
        "bilibili_opus": "bilibili",
        "bilibili_video": "bilibili",
        "bili": "bilibili",
        "youtube": "youtube",
        "yt": "youtube",
        "twitter": "x",
        "x": "x",
        "x_twitter": "x",
        "webpage": "webpage",
        "web": "webpage",
        "generic_webpage": "webpage",
        "url": "webpage",
        "zhihu": "zhihu",
        "xiaoyuzhou": "xiaoyuzhou",
        "apple": "apple_podcast",
        "apple_podcast": "apple_podcast",
        "local": "local",
        "local_file": "local",
        "local_video": "local",
        "local_audio": "local",
    }
    extra = metadata.get("extra") or {}
    candidates = (
        metadata.get("platform"),
        extra.get("platform") if isinstance(extra, dict) else None,
        metadata.get("source_type"),
        metadata.get("media_type"),
        metadata.get("content_subtype"),
    )
    return next(
        (
            aliases[value.strip().lower()]
            for value in candidates
            if isinstance(value, str) and value.strip().lower() in aliases
        ),
        "other",
    )


def timestamp(value) -> float:
    if not isinstance(value, str) or len(value) < 10:
        return 0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if len(value) == 10:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return 0


def index_fields(item: dict) -> dict:
    metadata = item.get("metadata") or {}
    return {
        "task_id": item.get("task_id"),
        "title": item.get("title") or "",
        "title_search": str(item.get("title") or "").lower(),
        "created_at": timestamp(item.get("created_at")),
        "published_at": timestamp(metadata.get("upload_date")),
        "platform": source_filter(metadata),
        "content_subtype": metadata.get("content_subtype") or "",
        "has_video": int(bool(item.get("has_video"))),
        "has_audio": int(bool(item.get("has_audio"))),
        "has_image": int(bool(item.get("has_image"))),
        "processing": int(bool(item.get("processing"))),
    }


def backfill_query_fields(conn) -> None:
    rows = conn.execute(
        "SELECT archive_id, snapshot FROM archive_sync_index WHERE title IS NULL"
    ).fetchall()
    for archive_id, snapshot in rows:
        fields = index_fields(json.loads(snapshot))
        conn.execute(
            "UPDATE archive_sync_index SET "
            + ",".join(f"{key}=?" for key in fields)
            + " WHERE archive_id=?",
            (*fields.values(), archive_id),
        )
    conn.commit()


def register_title_collation(conn) -> None:
    # Use the operating system's Chinese collation on the Windows desktop.
    if os.name == "nt":
        import ctypes

        compare = ctypes.windll.kernel32.CompareStringEx
        compare.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_ulong,
            ctypes.c_wchar_p,
            ctypes.c_int,
            ctypes.c_wchar_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_longlong,
        ]
        compare.restype = ctypes.c_int

        def collate(left, right):
            result = compare("zh-CN", 0, left, -1, right, -1, None, None, 0)
            return result - 2 if result else (left > right) - (left < right)
    else:
        import locale

        def collate(left, right):
            return locale.strcoll(left, right)

    conn.create_collation("ARCHIVE_TITLE", collate)


def query_page(
    conn, *, page=1, page_size=28, search="", media="all", source="all", sort="created_desc"
):
    media_conditions = {
        "all": "1",
        "video": "has_video=1",
        "audio": "has_video=0 AND has_image=0 AND has_audio=1",
        "image": "(has_image=1 OR content_subtype IN ('image_note','text_note'))",
    }
    ordering = {
        "created_desc": "created_at DESC",
        "created_asc": "created_at ASC",
        "published_desc": "published_at DESC",
        "title_asc": "title COLLATE ARCHIVE_TITLE ASC",
    }
    if media not in media_conditions or sort not in ordering:
        raise ValueError("Unknown archive filter or ordering")
    where = ["deleted=0", media_conditions[media]]
    values = []
    if search.strip():
        where.append("instr(title_search, ?) > 0")
        values.append(search.lower())
    if source != "all":
        where.append("platform=?")
        values.append(source)
    clause = " AND ".join(where)
    total = conn.execute(
        "SELECT COUNT(*) FROM archive_sync_index WHERE " + clause, values
    ).fetchone()[0]
    page = max(1, min(page, max(1, (total + page_size - 1) // page_size)))
    rows = conn.execute(
        "SELECT snapshot FROM archive_sync_index WHERE "
        + clause
        + f" ORDER BY processing DESC, {ordering[sort]}, archive_id ASC LIMIT ? OFFSET ?",
        (*values, page_size, (page - 1) * page_size),
    ).fetchall()
    return rows, total, page
