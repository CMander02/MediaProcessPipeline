"""Queryable fields for the rebuildable archive index."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit


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


MEDIA_CONDITIONS = {
    "all": "1",
    "video": "has_video=1",
    "audio": "has_video=0 AND has_image=0 AND has_audio=1",
    "image": "(has_image=1 OR content_subtype IN ('image_note','text_note'))",
}
ORDERING = {
    "created_desc": "created_at DESC",
    "created_asc": "created_at ASC",
    "published_desc": "published_at DESC",
    "title_asc": "title COLLATE ARCHIVE_TITLE ASC",
}
STATUSES = ("processing", "paused", "failed", "completed")
SOURCES = (
    "xiaohongshu", "bilibili", "youtube", "x", "webpage", "zhihu",
    "xiaoyuzhou", "apple_podcast", "local", "other",
)

# Library status of an indexed archive, derived from the task status copied into metadata.json.
# Cancelled runs are grouped with failed ones: both left an unfinished archive behind.
_META_STATUS = "COALESCE(json_extract(snapshot, '$.metadata.status'), '')"
STATUS_BUCKET_SQL = (
    f"CASE WHEN {_META_STATUS}='paused' THEN 'paused' "
    "WHEN processing=1 THEN 'processing' "
    f"WHEN {_META_STATUS} IN ('failed','cancelled') THEN 'failed' "
    "ELSE 'completed' END"
)
_BUCKET_RANK = {"processing": 4, "completed": 3, "paused": 2, "failed": 1}


def status_bucket(processing, meta_status) -> str:
    if meta_status == "paused":
        return "paused"
    if processing:
        return "processing"
    if meta_status in ("failed", "cancelled"):
        return "failed"
    return "completed"


def source_key(value) -> str | None:
    """Identity of what an archive was made from, so repeated attempts can be grouped."""
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        # Local runs are archived in fresh folders; their paths can't be matched reliably.
        return None
    host = parsed.netloc.lower()
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    query = parse_qs(parsed.query)
    bv = re.search(r"\bBV[0-9A-Za-z]{10}\b", value)
    if bv:
        part = (query.get("p") or ["1"])[0]
        return f"bilibili:{bv.group(0)}:p{part if part.isdigit() else '1'}"
    if host == "youtu.be":
        return f"youtube:{parsed.path.strip('/')}"
    if host.endswith("youtube.com") and query.get("v"):
        return f"youtube:{query['v'][0]}"
    return f"{host}{parsed.path.rstrip('/')}"


@dataclass
class LibraryGroups:
    """Repeated attempts at the same source, collapsed to one representative each."""

    hidden: set[str] = field(default_factory=set)  # non-representative attempts
    attempts: dict[str, int] = field(default_factory=dict)  # representative id -> attempt count
    members: set[str] = field(default_factory=set)  # every archive in a group of 2+
    by_key: dict[str, list[dict]] = field(default_factory=dict)


def library_groups(conn) -> LibraryGroups:
    rows = conn.execute(
        f"SELECT archive_id, processing, created_at, {_META_STATUS} AS meta_status, "
        "json_extract(snapshot, '$.metadata.source_url') AS source_url "
        "FROM archive_sync_index WHERE deleted=0"
    ).fetchall()
    groups = LibraryGroups()
    for row in rows:
        key = source_key(row["source_url"])
        if key:
            groups.by_key.setdefault(key, []).append({
                "archive_id": row["archive_id"],
                "bucket": status_bucket(row["processing"], row["meta_status"]),
                "created_at": row["created_at"] or 0,
            })
    for members in groups.by_key.values():
        if len(members) < 2:
            continue
        best = max(members, key=lambda item: (_BUCKET_RANK[item["bucket"]], item["created_at"]))
        groups.attempts[best["archive_id"]] = len(members)
        for item in members:
            groups.members.add(item["archive_id"])
            if item["archive_id"] != best["archive_id"]:
                groups.hidden.add(item["archive_id"])
    return groups


def _ids_param(ids) -> str:
    return json.dumps(sorted(ids))


def filter_values(value, allowed, name: str) -> list[str]:
    """Normalise one filter: "all", "", a comma list or a list of values."""
    if value is None:
        return []
    items = value.split(",") if isinstance(value, str) else list(value)
    chosen = sorted({str(item).strip() for item in items} - {"", "all"})
    unknown = [item for item in chosen if item not in allowed]
    if unknown:
        raise ValueError(f"Unknown {name} filter: {', '.join(unknown)}")
    return chosen


def _in_clause(column: str, values: list[str]) -> str:
    return f"{column} IN ({','.join('?' * len(values))})"


def _conditions(groups: LibraryGroups, *, search, media, source, status, duplicates):
    """WHERE clause for the library; values within a filter are OR-ed, filters are AND-ed."""
    where = ["deleted=0"]
    values: list = []
    if media:
        where.append("(" + " OR ".join(MEDIA_CONDITIONS[item] for item in media) + ")")
    if search.strip():
        where.append("instr(title_search, ?) > 0")
        values.append(search.lower())
    if source:
        where.append(_in_clause("platform", source))
        values.extend(source)
    if duplicates:
        # Every run of sources processed more than once, so they can be compared and cleaned up.
        where.append("archive_id IN (SELECT value FROM json_each(?))")
        values.append(_ids_param(groups.members))
    elif groups.hidden:
        where.append("archive_id NOT IN (SELECT value FROM json_each(?))")
        values.append(_ids_param(groups.hidden))
    if status:
        where.append(_in_clause(f"({STATUS_BUCKET_SQL})", status))
        values.extend(status)
    return " AND ".join(where), values


def _count(conn, clause: str, values: list) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM archive_sync_index WHERE {clause}", values).fetchone()[0]


def _facets(conn, groups: LibraryGroups, *, search, media, source, status, duplicates) -> dict:
    """Counts for each filter option, each computed as if its own filter were cleared."""
    filters = {"search": search, "media": media, "source": source, "status": status, "duplicates": duplicates}

    clause, values = _conditions(groups, **{**filters, "status": []})
    status_counts = {name: 0 for name in ("all", *STATUSES, "duplicates")}
    for bucket, count in conn.execute(
        f"SELECT {STATUS_BUCKET_SQL} AS bucket, COUNT(*) FROM archive_sync_index "
        f"WHERE {clause} GROUP BY bucket", values
    ).fetchall():
        status_counts[bucket] = count
        status_counts["all"] += count
    # How many cards the duplicates switch would show with the other filters kept.
    status_counts["duplicates"] = _count(conn, *_conditions(groups, **{**filters, "duplicates": True}))

    clause, values = _conditions(groups, **{**filters, "source": []})
    source_counts = dict(conn.execute(
        f"SELECT platform, COUNT(*) FROM archive_sync_index WHERE {clause} GROUP BY platform", values
    ).fetchall())

    clause, values = _conditions(groups, **{**filters, "media": []})
    sums = ", ".join(
        f"SUM(CASE WHEN {condition} THEN 1 ELSE 0 END)" for condition in MEDIA_CONDITIONS.values()
    )
    counts = conn.execute(f"SELECT {sums} FROM archive_sync_index WHERE {clause}", values).fetchone()
    media_counts = {name: int(count or 0) for name, count in zip(MEDIA_CONDITIONS, counts)}

    library_total = _count(conn, *_conditions(
        groups, search="", media=[], source=[], status=[], duplicates=False))
    return {
        "status": status_counts,
        "source": source_counts,
        "media": media_counts,
        "total": library_total,
    }


def query_library(
    conn, *, page=1, page_size=28, search="", media="all", source="all",
    sort="created_desc", status="all", duplicates=False,
) -> dict:
    """One page of the library. media, source and status take one value, a comma list or a list."""
    if sort not in ORDERING:
        raise ValueError("Unknown archive ordering")
    media = filter_values(media, [name for name in MEDIA_CONDITIONS if name != "all"], "media")
    source = filter_values(source, SOURCES, "source")
    status = filter_values(status, (*STATUSES, "duplicates"), "status")
    if "duplicates" in status:  # older clients sent the duplicates view as a status
        status = [item for item in status if item != "duplicates"]
        duplicates = True
    filters = {"search": search, "media": media, "source": source, "status": status,
               "duplicates": bool(duplicates)}
    groups = library_groups(conn)
    clause, values = _conditions(groups, **filters)
    total = _count(conn, clause, values)
    page = max(1, min(page, max(1, (total + page_size - 1) // page_size)))
    # Runs of the same source sit next to each other when listing duplicates.
    order = (
        "title COLLATE ARCHIVE_TITLE ASC, created_at DESC"
        if filters["duplicates"] else f"processing DESC, {ORDERING[sort]}"
    )
    rows = conn.execute(
        "SELECT archive_id, snapshot FROM archive_sync_index WHERE "
        + clause
        + f" ORDER BY {order}, archive_id ASC LIMIT ? OFFSET ?",
        (*values, page_size, (page - 1) * page_size),
    ).fetchall()
    return {
        "rows": rows,
        "total": total,
        "page": page,
        "attempts": groups.attempts,
        "duplicates": filters["duplicates"],
        "facets": _facets(conn, groups, **filters),
    }


def query_page(
    conn, *, page=1, page_size=28, search="", media="all", source="all", sort="created_desc"
):
    result = query_library(
        conn, page=page, page_size=page_size, search=search, media=media, source=source, sort=sort
    )
    return result["rows"], result["total"], result["page"]


def duplicate_cleanup_ids(conn) -> list[str]:
    """Failed or cancelled attempts whose source also has a completed archive."""
    groups = library_groups(conn)
    ids: list[str] = []
    for members in groups.by_key.values():
        if len(members) > 1 and any(item["bucket"] == "completed" for item in members):
            ids.extend(item["archive_id"] for item in members if item["bucket"] == "failed")
    return sorted(ids)


def archives_for_sources(conn, sources) -> dict[str, list[str]]:
    """Map each submitted link to the archive ids already made from it."""
    wanted = {source: source_key(source) for source in sources}
    groups = library_groups(conn)
    return {
        source: [item["archive_id"] for item in groups.by_key.get(key, [])]
        for source, key in wanted.items()
        if key and groups.by_key.get(key)
    }
