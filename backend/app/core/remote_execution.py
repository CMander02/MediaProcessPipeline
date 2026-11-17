import json
import logging
import platform
import socket
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import uuid4

from app.core.database import _get_conn, get_task_store, task_write_transaction, _save_task_row
from app.core.paths import managed_staged_file, task_uses_directory
from app.core.settings import get_runtime_settings
from app.models import Task, TaskStatus, TaskType

logger = logging.getLogger(__name__)

KEY = "_remote_execution"
ORIGIN = "_remote_origin"
_TEXT_RESULT_FILES = (
    "transcript.srt", "transcript_polished.srt", "transcript_polished.md",
    "summary.md", "mindmap.md", "detail.md", "source.md",
)
_server_started_at = 0.0
ONLINE_WINDOW_SEC = 45
OfflinePolicy = Literal["fallback_server", "wait", "fallback_any_online"]


@dataclass(frozen=True)
class RoutingIntent:
    target_worker_id: str | None
    offline_policy: OfflinePolicy
    waiting_since: float


def route_target(intent: RoutingIntent, *, now: float, last_seen: float,
                 server_started_at: float, wait_sec: float) -> str | None:
    """Return the eligible node ID, any remote node, or the local server."""
    if intent.target_worker_id == "local":
        return "local"
    deadline = max(intent.waiting_since, last_seen, server_started_at) + wait_sec
    if intent.offline_policy == "wait" or now < deadline:
        return intent.target_worker_id
    return "local" if intent.offline_policy == "fallback_server" else None


def server_started():
    global _server_started_at
    _server_started_at = time.time()


def worker_identity(settings) -> str:
    return settings.remote_worker_id.strip() or f"desktop-{socket.gethostname() or 'mpp'}"[:128]


@lru_cache(maxsize=1)
def host_capabilities() -> dict:
    from app.core.compute_device import detect_compute_capabilities
    return detect_compute_capabilities().as_dict()


def worker_metadata(settings) -> dict:
    return {
        "worker_name": settings.remote_worker_name or socket.gethostname(),
        "platform": {"Darwin": "macos", "Windows": "windows"}.get(platform.system(), "linux"),
        "compute_capabilities": host_capabilities(),
    }


def _compute_devices(capabilities: dict) -> list[str]:
    return ["cpu", *(name for name in ("cuda", "mps", "metal") if capabilities.get(f"{name}_available"))]


def _nodes(conn) -> list[dict]:
    return [{
        **dict(row), "compute_devices": json.loads(row["compute_devices"]),
        "capabilities": json.loads(row["capabilities"]), "busy": bool(row["busy"]),
    } for row in conn.execute("SELECT * FROM execution_nodes ORDER BY display_name, node_id")]


def list_nodes() -> dict:
    settings = get_runtime_settings()
    now = time.time()
    metadata = worker_metadata(settings)
    from app.core.queue import get_task_queue
    queue = get_task_queue()
    local = {
        "node_id": "local", "display_name": "服务器本机", "platform": metadata["platform"],
        "compute_devices": _compute_devices(metadata["compute_capabilities"]),
        "capabilities": metadata["compute_capabilities"], "busy": bool(queue.active_task_ids or queue.pending_count),
        "last_heartbeat_at": now, "updated_at": now, "online": True,
    }
    return {
        "enabled": settings.remote_execution_enabled,
        "preferred_worker_id": settings.remote_preferred_worker_id or settings.remote_worker_id,
        "offline_policy": settings.remote_execution_offline_policy,
        "nodes": [local, *({**node, "online": now - node["last_heartbeat_at"] <= ONLINE_WINDOW_SEC}
                           for node in _nodes(_get_conn()))],
    }


def _routing_intent(task, settings, now) -> RoutingIntent:
    marker = task.options.get(KEY, {})
    return RoutingIntent(
        target_worker_id=marker.get("target_worker_id") or settings.remote_preferred_worker_id
                         or settings.remote_worker_id or None,
        offline_policy=settings.remote_execution_offline_policy,
        waiting_since=marker.get("waiting_since", now),
    )


def _eligible_target(task, settings, nodes, now):
    intent = _routing_intent(task, settings, now)
    last_seen = max((node["last_heartbeat_at"] for node in nodes
                     if intent.target_worker_id is None or intent.target_worker_id == node["node_id"]), default=0.0)
    return route_target(intent, now=now, last_seen=last_seen,
                        server_started_at=_server_started_at, wait_sec=settings.remote_execution_wait_sec)


def source_file(task):
    path = Path(task.source).resolve()
    root = Path(get_runtime_settings().data_root).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None


def _has_archived_text(files: dict[str, str]) -> bool:
    for name in _TEXT_RESULT_FILES:
        if name not in files:
            continue
        with suppress(FileNotFoundError):
            with Path(files[name]).open(encoding="utf-8-sig") as text:
                if text.read(1024).strip():
                    return True
    return False


def cleanup_completed_upload(task: Task | None, data_root: Path) -> None:
    from app.core.archive_sync import is_archive_media_file

    if (
        task is None
        or task.status != TaskStatus.COMPLETED
        or task.options.get("keep_server_media") is True
    ):
        return
    path = managed_staged_file(task.source, data_root)
    if path is None:
        return
    archive = (task.result or {}).get("archive")
    files = archive.get("files") if isinstance(archive, dict) else None
    if not isinstance(files, dict) or any(is_archive_media_file(name) for name in files):
        return
    try:
        if not _has_archived_text(files):
            return
        if any(
            other.id != task.id and task_uses_directory(other, path.parent)
            for other in get_task_store().list(limit=-1)
        ):
            return
        path.unlink(missing_ok=True)
        with suppress(OSError):
            path.parent.rmdir()
    except (OSError, ValueError, RuntimeError):
        logger.warning("Could not reclaim staged upload for task %s", task.id, exc_info=True)


def defer(task):
    settings = get_runtime_settings()
    marker = task.options.get(KEY, {})
    if marker.get("attempt"):
        if task.status != TaskStatus.QUEUED:
            return True  # Never restore a remotely owned task into the server queue.
        marker = {key: value for key, value in marker.items() if key == "target_worker_id"}
        task.options[KEY] = marker
        task.completed_steps = []  # Remote checkpoints are not server files.
        task.progress = 0.0
        task.current_step = "download"
        task.error = None
        get_task_store().save(task)
    if not settings.remote_execution_enabled:
        return False
    if marker.get("fallback") or _routing_intent(task, settings, time.time()).target_worker_id == "local":
        return False
    if (task.task_type != TaskType.PIPELINE or set(task.completed_steps) - {"download"}
            or task.options.get(ORIGIN) or task.options.get("speaker_repair")):
        return False
    if not task.source.lower().startswith(("http://", "https://")) and source_file(task) is None:
        return False
    task.options[KEY] = {**marker, "waiting_since": marker.get("waiting_since", time.time())}
    task.message = "等待执行电脑领取"
    get_task_store().save(task)
    return True


def poll(worker_id, state=None, capacity=False, *, worker_name="", platform="", compute_capabilities=None):
    settings = get_runtime_settings()
    if (not settings.remote_execution_enabled or worker_id == "local"
            or settings.remote_worker_id and worker_id != settings.remote_worker_id):
        raise ValueError("Worker is not enabled on this server")
    now = time.time()
    store = get_task_store()
    with task_write_transaction() as conn:
        owned = [task for task in store.list_by_statuses([TaskStatus.PROCESSING])
                 if task.options.get(KEY, {}).get("worker_id") == worker_id
                 and task.options.get(KEY, {}).get("attempt")]
        previous = conn.execute("SELECT * FROM execution_nodes WHERE node_id=?", (worker_id,)).fetchone()
        capabilities = compute_capabilities if compute_capabilities is not None else (
            json.loads(previous["capabilities"]) if previous else {})
        display_name = worker_name or (previous["display_name"] if previous else settings.remote_worker_name or worker_id)
        node_platform = platform or (previous["platform"] if previous else "unknown")
        conn.execute("""INSERT INTO execution_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET display_name=excluded.display_name,
            platform=excluded.platform, compute_devices=excluded.compute_devices,
            capabilities=excluded.capabilities, busy=excluded.busy,
            last_heartbeat_at=excluded.last_heartbeat_at, updated_at=excluded.updated_at""",
            (worker_id, display_name, node_platform, json.dumps(_compute_devices(capabilities)),
             json.dumps(capabilities), int(bool(owned) or not capacity), now, now))
        if state:
            from uuid import UUID
            current = store.get(UUID(state["id"]))
            if current is None:
                return {"command": "cancel"}
            marker = current.options.get(KEY, {})
            if marker.get("attempt") != state.get("attempt") or marker.get("worker_id") != worker_id:
                return {"command": "cancel"}
            if current.status != TaskStatus.PROCESSING:
                return {"command": str(current.status)}
            marker["heartbeat"] = now
            current.updated_at = datetime.now()
            current.progress = max(0.0, min(float(state.get("progress", 0)), 1.0))
            current.current_step = state.get("current_step")
            current.completed_steps = state.get("completed_steps", [])
            current.message = f"{display_name}：{state.get('message') or '处理中'}"
            if state.get("status") in {"failed", "cancelled", "paused"}:
                current.status = TaskStatus(state["status"])
                current.error = state.get("error")
            _save_task_row(conn, current)
            return {"command": str(current.status)}
        if owned:
            return {"task": owned[0].model_dump(mode="json")} if capacity else {}
        if capacity:
            nodes = _nodes(conn)
            for task in store.list_by_statuses([TaskStatus.QUEUED]):
                marker = task.options.get(KEY, {})
                if "waiting_since" not in marker or marker.get("fallback") or marker.get("attempt"):
                    continue
                if _eligible_target(task, settings, nodes, now) not in {None, worker_id}:
                    continue
                task.options[KEY] = {**marker, "worker_id": worker_id,
                                     "attempt": str(uuid4()), "heartbeat": now}
                task.status = TaskStatus.PROCESSING
                task.message = f"已交给 {display_name} 执行"
                task.updated_at = datetime.now()
                _save_task_row(conn, task)
                conn.execute("UPDATE execution_nodes SET busy=1 WHERE node_id=?", (worker_id,))
                return {"task": task.model_dump(mode="json")}
    return {}


def fallbacks():
    settings = get_runtime_settings()
    if not settings.remote_execution_enabled:
        return []
    now = time.time()
    ready = []
    with task_write_transaction() as conn:
        nodes = _nodes(conn)
        for task in get_task_store().list_by_statuses([TaskStatus.QUEUED, TaskStatus.PROCESSING]):
            marker = task.options.get(KEY, {})
            if "waiting_since" not in marker or marker.get("fallback"):
                continue
            if marker.get("attempt"):
                if task.status == TaskStatus.PROCESSING and now - marker.get("heartbeat", now) > 120:
                    task.message = "执行电脑连接中断；等待重新连接，或暂停后重试"
                    _save_task_row(conn, task)
                continue  # A lost heartbeat does not authorize duplicate execution.
            if _eligible_target(task, settings, nodes, now) == "local":
                task.options[KEY] = {**marker, "fallback": True}
                task.message = "执行电脑离线，已转到云端处理"
                _save_task_row(conn, task)
                ready.append(task.id)
    return ready


def require_owner(existing, worker_id, attempt):
    """Fence result commits, including a late upload after cancel/retry."""
    from fastapi import HTTPException
    marker = existing.options.get(KEY, {}) if existing else {}
    if not attempt:
        if marker.get("attempt"):
            raise HTTPException(409, "Remote execution attempt is required")
        return False
    if (not marker or marker.get("attempt") != attempt or marker.get("worker_id") != worker_id
            or existing.status not in {TaskStatus.PROCESSING, TaskStatus.COMPLETED}):
        raise HTTPException(409, "Remote execution ownership changed")
    return True
