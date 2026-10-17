"""Safe, per-source imports alongside the legacy unconditional task endpoints."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from app.core.archive_index import source_key
from app.core.database import (
    _db_lock,
    _get_conn,
    _row_to_task,
    _save_task_row,
    get_task_store,
    task_write_transaction,
)
from app.core.source_normalization import normalize_source_input
from app.models.task import Task, TaskImportItem, TaskType

_ACTIVE = {"pending", "queued", "processing"}
_PRIORITY = {
    "completed": 4,
    "pending": 3,
    "queued": 3,
    "processing": 3,
    "paused": 2,
    "failed": 1,
    "cancelled": 1,
}


def normalize_import_source(source: str) -> str:
    """Resolve Bilibili aliases before entering a database transaction."""
    from app.services.ingestion.platform import source_urls

    source = normalize_source_input(source)
    if not source:
        raise ValueError("来源不能为空")
    if source_urls._is_bilibili_short_url(source):
        source = source_urls.normalize_bilibili_source_url(source)
        if source_urls._is_bilibili_short_url(source):
            raise ValueError("B站短链接解析失败，请刷新或使用完整视频链接")
    if source_urls._is_bilibili_video_url(source):
        # The existing BV resolver accepts av paths, while some shares use ?aid=.
        parsed = urlsplit(source)
        query = parse_qs(parsed.query)
        path_id = re.search(r"(?:^|/)(BV[0-9A-Za-z]{10}|av\d+)(?:/|$)", parsed.path)
        resolver_source = (
            f"https://www.bilibili.com/video/{path_id.group(1)}" if path_id else source
        )
        bvid = source_urls._extract_bilibili_bvid(resolver_source)
        if not bvid and not path_id and query.get("aid") and str(query["aid"][0]).isdigit():
            bvid = source_urls._extract_bilibili_bvid(
                f"https://www.bilibili.com/video/av{query['aid'][0]}",
            )
        if not bvid:
            raise ValueError("无法确认B站视频身份，请刷新后重试")
        return source_urls._bilibili_canonical_video_url(
            bvid,
            source_urls._extract_bilibili_page_number(source),
        )
    return source


def _key(source) -> str | None:
    try:
        return source_key(source)
    except ValueError:
        return None


def _task_match(task: Task) -> dict:
    result = task.result or {}
    metadata = result.get("metadata") or {}
    path = result.get("output_dir") or (result.get("archive") or {}).get("output_dir") or ""
    return {
        "path": path,
        "title": metadata.get("title")
        or result.get("title")
        or (Path(path).name if path else task.source),
        "task_id": str(task.id),
        "created_at": task.created_at.isoformat(),
        "status": str(task.status),
        "processing": str(task.status) in _ACTIVE,
    }


def _task_key(task: Task) -> str | None:
    key = _key(task.source)
    metadata = (task.result or {}).get("metadata") or {}
    metadata_key = _metadata_key(metadata)
    selected_page = _explicit_page(task.source)
    if key and key.startswith("bilibili:"):
        if (
            selected_page
            or not metadata_key
            or key.rsplit(":p", 1)[0] != metadata_key.rsplit(":p", 1)[0]
        ):
            return key
        return metadata_key
    if metadata_key and metadata_key.startswith("bilibili:") and selected_page:
        return _key(
            f"https://www.bilibili.com/video/{metadata_key.split(':')[1]}?p={selected_page}"
        )
    return metadata_key or key


def _explicit_page(source) -> str | None:
    try:
        query = parse_qs(urlsplit(str(source or "")).query)
    except ValueError:
        return None
    return (query.get("p") or query.get("page") or [None])[0]


def _metadata_key(metadata: dict) -> str | None:
    key = _key(metadata.get("source_url"))
    extra = metadata.get("extra") or {}
    bvid = key.split(":")[1] if key and key.startswith("bilibili:") else extra.get("bvid")
    if isinstance(bvid, str) and re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid):
        page = _explicit_page(metadata.get("source_url")) or extra.get("page_number") or 1
        return _key(f"https://www.bilibili.com/video/{bvid}?p={page}")
    return key


def _archive_identity(task: Task | None, metadata: dict) -> tuple[str | None, bool]:
    """Retain archive identity without binding it to an unrelated processing task."""
    archived_key = _metadata_key(metadata)
    task_key = _task_key(task) if task else None
    if not archived_key or not task_key or archived_key == task_key:
        return archived_key or task_key, True
    if task_key == _key(metadata.get("source_url")):
        # Stored BV metadata resolves the exact old av/short URL without I/O.
        return archived_key, True
    # An explicit task selection wins over metadata for the same BV. Without
    # that selection, the metadata's explicit P wins over the default P1.
    if (
        task_key.startswith("bilibili:")
        and archived_key.startswith("bilibili:")
        and task_key.rsplit(":p", 1)[0] == archived_key.rsplit(":p", 1)[0]
    ):
        if _explicit_page(task.source):
            return task_key, True
        return archived_key, True
    return archived_key, False


def _matches(
    conn, keys: set[str], task_type: TaskType = TaskType.PIPELINE
) -> dict[str, list[dict]]:
    """Read tasks and archive snapshots without network calls or index writes."""
    from app.core.archive_sync import ArchiveSyncService
    from app.core.database import _database_root

    found: dict[str, list[dict]] = {key: [] for key in keys}
    tasks = {str(row["id"]): _row_to_task(row) for row in conn.execute("SELECT * FROM tasks")}
    archives = []
    deleted_task_ids: set[str] = set()
    live_task_ids: set[str] = set()
    conflicting_task_ids: set[str] = set()
    linked_keys: dict[str, str] = {}
    for row in conn.execute(
        "SELECT archive_id, task_id, snapshot, deleted FROM archive_sync_index",
    ).fetchall():
        item = ArchiveSyncService.decode_snapshot(json.loads(row["snapshot"]), _database_root())
        task_id = str(item.get("task_id") or row["task_id"] or "")
        if row["deleted"]:
            deleted_task_ids.add(task_id)
            continue
        live_task_ids.add(task_id)
        task = tasks.get(task_id)
        # A download-only result is not a completed pipeline result. A missing
        # owner is different: standalone historical archives remain reusable.
        if task and task.task_type != task_type:
            continue
        metadata = item.get("metadata") or {}
        key, same_source = _archive_identity(task, metadata)
        from app.core.content_identity import archive_content_key
        key = archive_content_key({**item, 'archive_id': row['archive_id']}, task)
        if task and not same_source:
            conflicting_task_ids.add(task_id)
            task = None
        elif task and key:
            linked_keys[task_id] = key
        archives.append((row["archive_id"], item, task, key))

    by_task: dict[str, dict] = {}
    for task_id, task in tasks.items():
        if task.task_type != task_type:
            continue
        if str(task.status) not in _ACTIVE:
            if task_id in deleted_task_ids - live_task_ids:
                continue
            if task_id in conflicting_task_ids and task_id not in linked_keys:
                continue
        from app.core.content_identity import task_content_key
        key = linked_keys.get(task_id) or task_content_key(task)
        if key in keys:
            candidate = {"task": task, "match": _task_match(task)}
            found[key].append(candidate)
            by_task[task_id] = candidate

    for archive_id, item, task, key in archives:
        metadata = item.get("metadata") or {}
        if key not in keys:
            continue
        task_id = str(task.id) if task else None
        if task_id in by_task:
            by_task[task_id]["match"].update(
                archive_id=archive_id,
                path=item["path"],
                title=item.get("title") or by_task[task_id]["match"]["title"],
            )
            continue
        match = (
            _task_match(task)
            if task
            else {
                "path": item["path"],
                "title": item.get("title") or metadata.get("title") or "",
                "task_id": None,
                "created_at": item.get("created_at"),
                "status": metadata.get("status")
                or ("processing" if item.get("processing") else "completed"),
                "processing": bool(item.get("processing")),
            }
        )
        match["archive_id"] = archive_id
        found[key].append({"task": task, "match": match})

    for candidates in found.values():
        candidates.sort(
            key=lambda candidate: (
                _PRIORITY.get(candidate["match"]["status"], 0),
                str(candidate["match"].get("created_at") or ""),
            ),
            reverse=True,
        )
    return found


def detect_import_collection(source: str) -> str | None:
    """Inspect only unknown BVs when the library has a registered collection folder."""
    from app.core.library import get_library_service
    from app.services.ingestion.platform.bilibili.collection import inspect_bilibili_collection

    key = source_key(source)
    library = get_library_service()
    if (not key or not key.startswith('bilibili:') or not library.has_collection_folders()
            or library.knows_collection_content(key)):
        return None
    try:
        library.register_collection(inspect_bilibili_collection(source))
    except Exception as exc:
        return str(exc)
    return None


def lookup_import_sources(sources: list[str]) -> dict:
    """Legacy lookup shape, with current tasks and explicit normalization errors."""
    from app.core.archive_sync import get_archive_sync_service

    normalized: dict[str, str | None] = {}
    errors: dict[str, str] = {}
    for source in sources:
        try:
            normalized[source] = source_key(normalize_import_source(source))
        except Exception as exc:
            errors[source] = str(exc)
    get_archive_sync_service().flush_changes()
    with _db_lock:
        found = _matches(_get_conn(), {key for key in normalized.values() if key})
    matches = {
        source: [candidate["match"] for candidate in found[key]]
        for source, key in normalized.items()
        if key and found.get(key)
    }
    return {"matches": matches, **({"errors": errors} if errors else {})}


def import_task(task: Task, initialize: Callable[[Task], None], *, folder_id=None, explicit_folder=False, move_existing=False) -> TaskImportItem:
    """Check and save one queued task atomically; the caller notifies the queue."""
    from app.core.content_identity import task_content_key
    from app.core.library import assign_import
    key = task_content_key(task)
    created = False
    try:
        with task_write_transaction() as conn:
            if conn.execute('SELECT 1 FROM library_deletions WHERE content_key=?', (key,)).fetchone():
                raise ValueError('content_deleting')
            if explicit_folder and folder_id is not None:
                from app.core.library import require_folder
                require_folder(conn, folder_id)
            candidates = _matches(conn, {key}, task.task_type).get(key, []) if key else []
            if candidates:
                candidate = candidates[0]
                status = candidate["match"]["status"]
                reusable = status == "completed" or status in _ACTIVE
                return TaskImportItem(
                    source=task.source,
                    content_key=key,
                    outcome="reused" if reusable else "skipped",
                    task=candidate["task"],
                    match=candidate["match"],
                    reason=None if reusable else "needs_action",
                    **assign_import(conn, key, folder_id, explicit=explicit_folder, move_existing=move_existing),
                )
            initialize(task)
            _save_task_row(conn, task)
            classification = assign_import(conn, key, folder_id, explicit=explicit_folder, move_existing=move_existing)
        created = True
    finally:
        # Only these newly initialized files belong to an unsuccessful insert.
        if not created and task.result and task.result.get("output_dir"):
            directory = Path(task.result["output_dir"])
            (directory / "metadata.json").unlink(missing_ok=True)
            directory.rmdir()
    get_task_store()._archive_changed(task)
    return TaskImportItem(source=task.source, content_key=key, outcome="created", task=task, **classification)
