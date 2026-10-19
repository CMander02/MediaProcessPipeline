"""Single preferred worker, with durable task ownership and offline fallback."""

from datetime import datetime
from pathlib import Path
import time
from uuid import uuid4

from app.core.database import get_task_store, task_write_transaction, _save_task_row
from app.core.settings import get_runtime_settings
from app.models import TaskStatus, TaskType

KEY = "_remote_execution"
ORIGIN = "_remote_origin"
_last_seen = 0.0


def source_file(task):
    path = Path(task.source).resolve()
    root = Path(get_runtime_settings().data_root).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None


def defer(task):
    settings = get_runtime_settings()
    if not settings.remote_execution_enabled or not settings.remote_worker_id:
        return False
    marker = task.options.get(KEY, {})
    if marker.get("attempt"):
        if task.status != TaskStatus.QUEUED:
            return True  # Never restore a remotely owned task into the server queue.
        marker = {}  # Explicit resume starts a new attempt and fences old uploads.
        task.options.pop(KEY, None)
        task.completed_steps = []  # Remote checkpoints are not server files.
        task.progress = 0.0
        task.current_step = "download"
        task.error = None
        get_task_store().save(task)
    if marker.get("fallback"):
        return False
    if (task.task_type != TaskType.PIPELINE or set(task.completed_steps) - {"download"}
            or task.options.get(ORIGIN) or task.options.get("speaker_repair")):
        return False
    if not task.source.lower().startswith(("http://", "https://")) and source_file(task) is None:
        return False
    task.options[KEY] = {"waiting_since": marker.get("waiting_since", time.time())}
    task.message = "等待执行电脑领取"
    get_task_store().save(task)
    return True


def poll(worker_id, state=None, capacity=False):
    global _last_seen
    settings = get_runtime_settings()
    if not settings.remote_execution_enabled or worker_id != settings.remote_worker_id:
        raise ValueError("Worker is not enabled on this server")
    _last_seen = time.time()
    store = get_task_store()
    with task_write_transaction() as conn:
        owned = []
        for task in store.list_by_statuses([TaskStatus.PROCESSING]):
            marker = task.options.get(KEY, {})
            if marker.get("worker_id") == worker_id and marker.get("attempt"):
                owned.append(task)
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
            marker["heartbeat"] = time.time()
            current.updated_at = datetime.now()
            current.progress = max(0.0, min(float(state.get("progress", 0)), 1.0))
            current.current_step = state.get("current_step")
            current.completed_steps = state.get("completed_steps", [])
            current.message = f"{settings.remote_worker_name or worker_id}：{state.get('message') or '处理中'}"
            if state.get("status") in {"failed", "cancelled", "paused"}:
                current.status = TaskStatus(state["status"])
                current.error = state.get("error")
            _save_task_row(conn, current)
            return {"command": str(current.status)}
        if owned:
            return {"task": owned[0].model_dump(mode="json")} if capacity else {}
        if capacity:
            for task in store.list_by_statuses([TaskStatus.QUEUED]):
                marker = task.options.get(KEY, {})
                if not marker or marker.get("fallback"):
                    continue
                task.options[KEY] = {**marker, "worker_id": worker_id,
                                     "attempt": str(uuid4()), "heartbeat": time.time()}
                task.status = TaskStatus.PROCESSING
                task.message = f"已交给 {settings.remote_worker_name or worker_id} 执行"
                task.updated_at = datetime.now()
                _save_task_row(conn, task)
                return {"task": task.model_dump(mode="json")}
    return {}


def fallbacks():
    settings = get_runtime_settings()
    if not settings.remote_execution_enabled:
        return []
    now = time.time()
    ready = []
    with task_write_transaction() as conn:
        for task in get_task_store().list_by_statuses([TaskStatus.QUEUED, TaskStatus.PROCESSING]):
            marker = task.options.get(KEY, {})
            if not marker or marker.get("fallback"):
                continue
            if marker.get("attempt"):
                if task.status == TaskStatus.PROCESSING and now - marker.get("heartbeat", now) > 120:
                    task.message = "执行电脑连接中断；等待重新连接，或暂停后重试"
                    _save_task_row(conn, task)
                continue  # A lost heartbeat does not authorize duplicate execution.
            if now - max(_last_seen, marker.get("waiting_since", now)) >= settings.remote_execution_wait_sec:
                task.options[KEY] = {**marker, "fallback": True}
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
