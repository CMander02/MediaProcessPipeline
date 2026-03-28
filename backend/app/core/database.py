"""SQLite-backed task store.

Replaces in-memory _tasks dict and JSON-based HistoryService with a single
SQLite database at data/tasks.db.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.models.task import Task, TaskStatus, TaskType

logger = logging.getLogger(__name__)

# DB path - resolved at init time from settings
_db_path: Path | None = None
_connection: sqlite3.Connection | None = None
_db_lock = threading.Lock()  # Serialize all DB writes

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    task_type       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    source          TEXT NOT NULL,
    options         TEXT NOT NULL DEFAULT '{}',
    progress        REAL NOT NULL DEFAULT 0.0,
    message         TEXT,
    result          TEXT,
    error           TEXT,
    webhook_url     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    completed_at    TEXT,
    current_step    TEXT,
    steps           TEXT NOT NULL DEFAULT '[]',
    completed_steps TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);
"""


def _get_db_path() -> Path:
    """Get the database path, resolving from settings if needed."""
    global _db_path
    if _db_path is None:
        from app.core.settings import get_runtime_settings
        rt = get_runtime_settings()
        _db_path = Path(rt.data_root).resolve() / "tasks.db"
    return _db_path


def _get_conn() -> sqlite3.Connection:
    """Get or create the database connection (singleton)."""
    global _connection
    if _connection is None:
        db_path = _get_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _connection = sqlite3.connect(str(db_path), check_same_thread=False)
        _connection.row_factory = sqlite3.Row
        # WAL mode for concurrent reads
        _connection.execute("PRAGMA journal_mode=WAL")
        _connection.execute("PRAGMA foreign_keys=ON")
        _connection.executescript(SCHEMA)
        logger.info(f"SQLite task store opened at {db_path}")
    return _connection


def _task_to_row(task: Task) -> dict:
    """Convert a Task model to a dict of column values."""
    return {
        "id": str(task.id),
        "task_type": task.task_type,
        "status": task.status,
        "source": task.source,
        "options": json.dumps(task.options, ensure_ascii=False),
        "progress": task.progress,
        "message": task.message,
        "result": json.dumps(task.result, ensure_ascii=False) if task.result else None,
        "error": task.error,
        "webhook_url": task.webhook_url,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "current_step": task.current_step,
        "steps": json.dumps(task.steps, ensure_ascii=False),
        "completed_steps": json.dumps(task.completed_steps, ensure_ascii=False),
    }


def _row_to_task(row: sqlite3.Row) -> Task:
    """Convert a database row to a Task model."""
    return Task(
        id=UUID(row["id"]),
        task_type=TaskType(row["task_type"]),
        status=TaskStatus(row["status"]),
        source=row["source"],
        options=json.loads(row["options"]),
        progress=row["progress"],
        message=row["message"],
        result=json.loads(row["result"]) if row["result"] else None,
        error=row["error"],
        webhook_url=row["webhook_url"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        current_step=row["current_step"],
        steps=json.loads(row["steps"]),
        completed_steps=json.loads(row["completed_steps"]),
    )


class TaskStore:
    """SQLite-backed task persistence."""

    def save(self, task: Task) -> None:
        """Insert or replace a task."""
        conn = _get_conn()
        row = _task_to_row(task)
        cols = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row.keys())
        with _db_lock:
            conn.execute(
                f"INSERT OR REPLACE INTO tasks ({cols}) VALUES ({placeholders})",
                row,
            )
            conn.commit()

    def get(self, task_id: UUID) -> Task | None:
        """Get a single task by ID."""
        conn = _get_conn()
        cur = conn.execute("SELECT * FROM tasks WHERE id = ?", (str(task_id),))
        row = cur.fetchone()
        return _row_to_task(row) if row else None

    def list(
        self,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Task]:
        """List tasks with optional status filter, newest first."""
        conn = _get_conn()
        if status:
            cur = conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        return [_row_to_task(r) for r in cur.fetchall()]

    def list_by_statuses(self, statuses: list[str]) -> list[Task]:
        """List tasks matching any of the given statuses, oldest first (for queue restore)."""
        conn = _get_conn()
        placeholders = ", ".join("?" for _ in statuses)
        cur = conn.execute(
            f"SELECT * FROM tasks WHERE status IN ({placeholders}) ORDER BY created_at ASC",
            statuses,
        )
        return [_row_to_task(r) for r in cur.fetchall()]

    def update_status(
        self,
        task_id: UUID,
        status: TaskStatus,
        **kwargs: Any,
    ) -> None:
        """Update task status and optional fields."""
        conn = _get_conn()
        sets = ["status = ?", "updated_at = ?"]
        vals: list[Any] = [status, datetime.now().isoformat()]

        for key, value in kwargs.items():
            if key in ("progress", "message", "error", "current_step"):
                sets.append(f"{key} = ?")
                vals.append(value)
            elif key == "result":
                sets.append("result = ?")
                vals.append(json.dumps(value, ensure_ascii=False) if value else None)
            elif key == "completed_at":
                sets.append("completed_at = ?")
                vals.append(value.isoformat() if value else None)
            elif key == "completed_steps":
                sets.append("completed_steps = ?")
                vals.append(json.dumps(value, ensure_ascii=False))

        vals.append(str(task_id))
        with _db_lock:
            conn.execute(
                f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?",
                vals,
            )
            conn.commit()

    def delete(self, task_id: UUID) -> bool:
        """Delete a task."""
        conn = _get_conn()
        with _db_lock:
            cur = conn.execute("DELETE FROM tasks WHERE id = ?", (str(task_id),))
            conn.commit()
        return cur.rowcount > 0

    def count(self, status: str | None = None) -> int:
        """Count tasks, optionally filtered by status."""
        conn = _get_conn()
        if status:
            cur = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = ?", (status,))
        else:
            cur = conn.execute("SELECT COUNT(*) FROM tasks")
        return cur.fetchone()[0]

    def stats(self) -> dict[str, int]:
        """Return a status → count mapping."""
        conn = _get_conn()
        cur = conn.execute("SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status")
        result = {row["status"]: row["cnt"] for row in cur.fetchall()}
        result["total"] = sum(result.values())
        return result


# Singleton
_store: TaskStore | None = None


def get_task_store() -> TaskStore:
    """Get the global TaskStore singleton."""
    global _store
    if _store is None:
        _store = TaskStore()
    return _store


def init_db(data_root: Path | None = None) -> None:
    """Initialize the database (call during app startup)."""
    global _db_path
    if data_root:
        _db_path = Path(data_root).resolve() / "tasks.db"
    # Force connection creation + schema init
    _get_conn()
    logger.info("Task database initialized")


def close_db() -> None:
    """Close the database connection (call during app shutdown)."""
    global _connection
    if _connection:
        _connection.close()
        _connection = None
        logger.info("Task database closed")
