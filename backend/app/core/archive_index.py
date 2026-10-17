"""Queryable fields for the rebuildable archive index."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cmp_to_key
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


def register_title_collation(conn):
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
    return collate


MEDIA_CONDITIONS = {
    "all": "1",
    "video": "has_video=1",
    "audio": "has_video=0 AND has_image=0 AND has_audio=1",
    "image": "(has_image=1 OR content_subtype IN ('image_note','text_note'))",
}
ORDERING = {
    "processed_desc": "last_processed.processing_at DESC",
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
    value = value.strip()
    if re.fullmatch(r"BV[0-9A-Za-z]{10}", value):
        return f"bilibili:{value}:p1"
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        # Local runs are archived in fresh folders; their paths can't be matched reliably.
        return None
    host = parsed.netloc.lower()
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    query = parse_qs(parsed.query)
    bv = re.search(r"(?:^|/)(BV[0-9A-Za-z]{10})(?:/|$)", parsed.path)
    bvid = bv.group(1) if bv else (query.get("bvid") or [""])[0]
    bvid = bvid if re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid) else None
    if (host == "bilibili.com" or host.endswith(".bilibili.com")) and bvid:
        part = (query.get("p") or query.get("page") or ["1"])[0]
        try:
            page = max(1, int(part))
        except ValueError:
            page = 1
        return f"bilibili:{bvid}:p{page}"
    if host == "youtu.be" and parsed.path.strip('/'):
        return f"youtube:{parsed.path.strip('/').split('/')[0]}"
    if host == "youtube.com" or host.endswith(".youtube.com"):
        video_id = (query.get("v") or [None])[0]
        path_video = re.match(r"^/(?:shorts|live|embed)/([^/]+)", parsed.path)
        if not video_id and path_video:
            video_id = path_video.group(1)
        if video_id:
            return f"youtube:{video_id}"
    # Unknown platforms may identify different media solely through query values.
    return f"{host}{parsed.path.rstrip('/')}" + (f"?{parsed.query}" if parsed.query else "")


@dataclass
class LibraryGroups:
    """Repeated attempts at the same source, collapsed to one representative each."""

    hidden: set[str] = field(default_factory=set)  # non-representative attempts
    attempts: dict[str, int] = field(default_factory=dict)  # representative id -> attempt count
    members: set[str] = field(default_factory=set)  # every archive in a group of 2+
    by_key: dict[str, list[dict]] = field(default_factory=dict)
    content_keys: dict[str, str] = field(default_factory=dict)
    processed_at: dict[str, str] = field(default_factory=dict)


def library_groups(conn) -> LibraryGroups:
    from app.core.content_identity import archive_content_key
    from app.core.database import _row_to_task
    from app.core.library import processing_time

    tasks = {str(row['id']): _row_to_task(row) for row in conn.execute('SELECT * FROM tasks')}
    rows = conn.execute(
        f"SELECT archive_id, processing, created_at, {_META_STATUS} AS meta_status, "
        "snapshot, task_id, json_extract(snapshot, '$.metadata.source_url') AS source_url "
        "FROM archive_sync_index WHERE deleted=0"
    ).fetchall()
    groups = LibraryGroups()
    for row in rows:
        item = json.loads(row['snapshot'])
        item['archive_id'] = row['archive_id']
        task = tasks.get(str(item.get('task_id') or row['task_id'] or ''))
        key = archive_content_key(item, task)
        groups.content_keys[row['archive_id']] = key
        groups.processed_at[row['archive_id']] = processing_time(item, task)
        if key:
            groups.by_key.setdefault(key, []).append({
                "archive_id": row["archive_id"],
                "bucket": status_bucket(row["processing"], row["meta_status"]),
                "created_at": row["created_at"] or 0,
            })
    for members in groups.by_key.values():
        latest = max((groups.processed_at[item['archive_id']] for item in members), key=timestamp)
        for item in members:
            groups.processed_at[item['archive_id']] = latest
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


def _conditions(groups: LibraryGroups, *, search, media, source, status, duplicates, scope_ids=None):
    """WHERE clause for the library; values within a filter are OR-ed, filters are AND-ed."""
    where = ["deleted=0"]
    values: list = []
    if scope_ids is not None:
        where.append('archive_id IN (SELECT value FROM json_each(?))')
        values.append(_ids_param(scope_ids))
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


def _facets(conn, groups: LibraryGroups, *, search, media, source, status, duplicates, scope_ids=None) -> dict:
    """Counts for each filter option, each computed as if its own filter were cleared."""
    filters = {"search": search, "media": media, "source": source, "status": status, "duplicates": duplicates, 'scope_ids': scope_ids}

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
        groups, search="", media=[], source=[], status=[], duplicates=False, scope_ids=scope_ids))
    return {
        "status": status_counts,
        "source": source_counts,
        "media": media_counts,
        "total": library_total,
    }


def query_library(
    conn, *, page=1, page_size=28, search="", media="all", source="all",
    sort="processed_desc", status="all", duplicates=False, folder_id=None, unclassified=False, offset=None,
    include_folders=False,
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
    from app.core.library import folder_directory, require_folder, revision

    if folder_id and unclassified:
        raise ValueError('folder_id 与 unclassified 不能同时使用')
    if include_folders and (folder_id or not unclassified):
        raise ValueError('include_folders 只能用于未分类根目录')
    if folder_id:
        require_folder(conn, folder_id)
    folder_map = {row['content_key']: row['folder_id'] for row in conn.execute('SELECT content_key,folder_id FROM library_folder_members')}
    if folder_id or unclassified:
        filters['scope_ids'] = {archive_id for archive_id, key in groups.content_keys.items()
                                if (folder_map.get(key) == folder_id if folder_id else not folder_map.get(key))}
    clause, values = _conditions(groups, **filters)
    archive_total = _count(conn, clause, values)
    all_folders = folder_directory(conn) if include_folders else []
    folders = [folder for folder in all_folders if not search.strip() or search.lower() in folder['name'].lower()]
    total = archive_total + len(folders)
    page = max(1, min(page, max(1, (total + page_size - 1) // page_size))) if offset is None else max(1, page)
    # Runs of the same source sit next to each other when listing duplicates.
    order = (
        "title COLLATE ARCHIVE_TITLE ASC, created_at DESC"
        if filters["duplicates"] else ORDERING[sort] if include_folders or sort == 'processed_desc'
        else f"processing DESC, {ORDERING[sort]}"
    )
    start = (page - 1) * page_size if offset is None else max(0, offset)
    processed_times = {key: timestamp(value) for key, value in groups.processed_at.items()}
    join = ' LEFT JOIN processed_times last_processed ON last_processed.key=archive_id' if sort == 'processed_desc' and not filters['duplicates'] else ''
    prefix = 'WITH processed_times AS MATERIALIZED (SELECT key,CAST(value AS REAL) processing_at FROM json_each(?)) ' if join else ''
    query_values = (json.dumps(processed_times), *values) if join else tuple(values)
    rows = conn.execute(
        prefix + "SELECT archive_id, snapshot, title, created_at, published_at FROM archive_sync_index" + join + " WHERE "
        + clause
        + f" ORDER BY {order}, archive_id ASC LIMIT ? OFFSET ?",
        (*query_values, start + page_size if include_folders else page_size, 0 if include_folders else start),
    ).fetchall()
    entries = None
    if include_folders:
        compare_title = register_title_collation(conn)
        combined = [dict(row, kind='archive', id=row['archive_id'], processed=processed_times[row['archive_id']]) for row in rows]
        combined.extend({'kind': 'folder', 'id': folder['id'], 'title': folder['name'],
                         'created_at': timestamp(folder['created_at']),
                         'published_at': timestamp(folder['last_processed_at']),
                         'processed': timestamp(folder['last_processed_at'])} for folder in folders)

        def compare(left, right):
            if sort == 'title_asc' or filters['duplicates']:
                result = compare_title(left['title'], right['title'])
            else:
                field = {'processed_desc': 'processed', 'published_desc': 'published_at'}.get(sort, 'created_at')
                result = (left[field] > right[field]) - (left[field] < right[field])
                if sort != 'created_asc':
                    result = -result
            if not result and filters['duplicates']:
                result = (right['created_at'] > left['created_at']) - (right['created_at'] < left['created_at'])
            return result or (left['id'] > right['id']) - (left['id'] < right['id'])

        chosen = sorted(combined, key=cmp_to_key(compare))[start:start + page_size]
        entries = [{'kind': item['kind'], 'id': item['id']} for item in chosen]
        chosen_archives = {item['id'] for item in chosen if item['kind'] == 'archive'}
        chosen_folders = {item['id'] for item in chosen if item['kind'] == 'folder'}
        rows = [row for row in rows if row['archive_id'] in chosen_archives]
        folders = [folder for folder in folders if folder['id'] in chosen_folders]
    facets = _facets(conn, groups, **filters)
    if include_folders:
        facets['total'] += len(all_folders)
    return {
        "rows": rows,
        "total": total,
        "page": page,
        "attempts": groups.attempts,
        "duplicates": filters["duplicates"],
        "facets": facets,
        'content_keys': groups.content_keys,
        'folder_map': folder_map,
        'library_revision': revision(conn),
        'processed_at': groups.processed_at,
        'archive_total': archive_total,
        'folders': folders,
        'entries': entries,
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
