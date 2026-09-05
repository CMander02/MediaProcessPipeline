"""SQLite-backed task store.

Replaces in-memory _tasks dict and JSON-based HistoryService with a single
SQLite database resolved through app.core.paths for the selected library.
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

from app.core.logging_setup import log_event
from app.models.task import Task, TaskStatus, TaskType

logger = logging.getLogger(__name__)

# DB path - resolved at init time from settings
_db_path: Path | None = None
_db_root: Path | None = None
_connection: sqlite3.Connection | None = None
_db_lock = threading.Lock()  # Serialize all DB writes

SCHEMA = """
CREATE TABLE IF NOT EXISTS archive_deletions (
    original_path TEXT PRIMARY KEY,
    staging_name TEXT NOT NULL,
    task_ids TEXT NOT NULL,
    archive_ids TEXT NOT NULL
);

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
    completed_steps TEXT NOT NULL DEFAULT '[]',
    flow            TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);

CREATE TABLE IF NOT EXISTS task_artifacts (
    task_id      TEXT NOT NULL,
    filename     TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'text/plain',
    content      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (task_id, filename),
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_artifacts_task_id ON task_artifacts(task_id);

CREATE TABLE IF NOT EXISTS task_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    event_type TEXT NOT NULL,
    stage      TEXT,
    step_id    TEXT,
    level      TEXT NOT NULL DEFAULT 'info',
    message    TEXT,
    data       TEXT NOT NULL DEFAULT '{}',
    timestamp  TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_events_task_id_id ON task_events(task_id, id);

CREATE TABLE IF NOT EXISTS archive_sync_meta (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    current_revision INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO archive_sync_meta (id, current_revision) VALUES (1, 0);

CREATE TABLE IF NOT EXISTS archive_sync_index (
    archive_id   TEXT PRIMARY KEY,
    archive_path TEXT NOT NULL,
    revision     INTEGER NOT NULL,
    fingerprint  TEXT NOT NULL,
    snapshot     TEXT NOT NULL,
    deleted      INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_archive_sync_path ON archive_sync_index(archive_path);
CREATE INDEX IF NOT EXISTS idx_archive_sync_active ON archive_sync_index(deleted, revision);

CREATE TABLE IF NOT EXISTS archive_sync_changes (
    revision   INTEGER PRIMARY KEY,
    archive_id TEXT NOT NULL,
    operation  TEXT NOT NULL CHECK (operation IN ('upsert', 'delete')),
    snapshot   TEXT,
    changed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_archive_sync_changes_archive ON archive_sync_changes(archive_id, revision);
"""

# Columns added after initial schema — applied idempotently via ALTER TABLE
_MIGRATIONS = [
    "ALTER TABLE tasks ADD COLUMN path_fields TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE tasks ADD COLUMN external_source INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE tasks ADD COLUMN platform TEXT",
    "ALTER TABLE tasks ADD COLUMN uploader_id TEXT",
    "ALTER TABLE tasks ADD COLUMN content_subtype TEXT",
    "ALTER TABLE tasks ADD COLUMN flow TEXT",
    "CREATE INDEX IF NOT EXISTS idx_tasks_platform ON tasks(platform)",
]


def _database_root() -> Path:
    if _db_root is not None:
        return _db_root
    from app.core.settings import get_runtime_settings
    return Path(get_runtime_settings().data_root).expanduser().resolve()


def _get_db_path() -> Path:
    global _db_path
    if _db_path is None:
        from app.core.paths import get_workspace_paths
        paths = get_workspace_paths(_database_root())
        paths.ensure()
        _db_path = paths.tasks_db
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
        _apply_migrations(_connection)
        log_event(logger, logging.INFO, "database.opened", path=db_path)
    return _connection


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply schema migrations idempotently (ALTER TABLE ignores duplicate-column errors)."""
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
            conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower() and "already exists" not in str(e).lower():
                raise


def _task_to_row(task: Task) -> dict:
    """Convert a Task model to a dict of column values."""
    # Derive denormalized metadata columns from result JSON if not already set on task
    platform = task.platform
    uploader_id = task.uploader_id
    content_subtype = task.content_subtype
    if task.result and isinstance(task.result.get("metadata"), dict):
        meta = task.result["metadata"]
        if platform is None:
            platform = meta.get("platform") or (meta.get("extra") or {}).get("platform")
        if uploader_id is None:
            uploader_id = meta.get("uploader_id")
        if content_subtype is None:
            content_subtype = meta.get("content_subtype")

    from app.core.paths import encode_workspace_paths, is_absolute_file_path
    root = _database_root()
    payload, path_fields = encode_workspace_paths({
        "source": task.source, "options": task.options, "result": task.result, "flow": task.flow,
    }, root)
    external_source = is_absolute_file_path(task.source) and ["source"] not in path_fields

    return {
        "id": str(task.id),
        "task_type": task.task_type,
        "status": task.status,
        "source": payload["source"],
        "path_fields": json.dumps(path_fields),
        "external_source": int(external_source),
        "options": json.dumps(payload["options"], ensure_ascii=False),
        "progress": task.progress,
        "message": task.message,
        "result": json.dumps(payload["result"], ensure_ascii=False) if task.result else None,
        "error": task.error,
        "webhook_url": task.webhook_url,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "current_step": task.current_step,
        "steps": json.dumps(task.steps, ensure_ascii=False),
        "completed_steps": json.dumps(task.completed_steps, ensure_ascii=False),
        "flow": json.dumps(payload["flow"], ensure_ascii=False) if task.flow else None,
        "platform": platform,
        "uploader_id": uploader_id,
        "content_subtype": content_subtype,
    }


def _row_to_task(row: sqlite3.Row) -> Task:
    """Convert a database row to a Task model."""
    keys = row.keys()
    from app.core.paths import decode_workspace_paths
    payload = {"source": row["source"], "options": json.loads(row["options"]),
               "result": json.loads(row["result"]) if row["result"] else None,
               "flow": json.loads(row["flow"]) if "flow" in keys and row["flow"] else None}
    fields = json.loads(row["path_fields"]) if "path_fields" in keys else []
    payload = decode_workspace_paths(payload, fields, _database_root())
    return Task(
        id=UUID(row["id"]),
        task_type=TaskType(row["task_type"]),
        status=TaskStatus(row["status"]),
        source=payload["source"],
        external_source=bool(row["external_source"]) if "external_source" in keys else False,
        options=payload["options"],
        progress=row["progress"],
        message=row["message"],
        result=payload["result"],
        error=row["error"],
        webhook_url=row["webhook_url"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        current_step=row["current_step"],
        steps=json.loads(row["steps"]),
        completed_steps=json.loads(row["completed_steps"]),
        flow=payload["flow"],
        platform=row["platform"] if "platform" in keys else None,
        uploader_id=row["uploader_id"] if "uploader_id" in keys else None,
        content_subtype=row["content_subtype"] if "content_subtype" in keys else None,
    )


class TaskStore:
    """SQLite-backed task persistence."""

    def save(self, task: Task) -> None:
        """Insert or update a task while preserving its identity and child records."""
        conn = _get_conn()
        row = _task_to_row(task)
        cols = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row.keys())
        updates = ", ".join(
            f"{column} = excluded.{column}"
            for column in row if column not in {"id", "created_at"}
        )
        with _db_lock, conn:
            conn.execute(
                f"INSERT INTO tasks ({cols}) VALUES ({placeholders}) "
                f"ON CONFLICT(id) DO UPDATE SET {updates}",
                row,
            )

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
        statuses: list[str] | None = None,
    ) -> list[Task]:
        """List tasks with optional status filter, newest first."""
        conn = _get_conn()
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            cur = conn.execute(
                f"SELECT * FROM tasks WHERE status IN ({placeholders}) "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*statuses, limit, offset),
            )
        elif status:
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

        changed_path_fields = {}
        for key, value in kwargs.items():
            if key in ("progress", "message", "error", "current_step", "uploader_id", "platform", "content_subtype"):
                sets.append(f"{key} = ?")
                vals.append(value)
            elif key == "result":
                sets.append("result = ?")
                from app.core.paths import encode_workspace_paths
                encoded, fields = encode_workspace_paths({key: value}, _database_root())
                changed_path_fields[key] = fields
                vals.append(json.dumps(encoded[key], ensure_ascii=False) if value else None)
            elif key == "completed_at":
                sets.append("completed_at = ?")
                vals.append(value.isoformat() if value else None)
            elif key == "completed_steps":
                sets.append("completed_steps = ?")
                vals.append(json.dumps(value, ensure_ascii=False))
            elif key == "flow":
                sets.append("flow = ?")
                from app.core.paths import encode_workspace_paths
                encoded, fields = encode_workspace_paths({key: value}, _database_root())
                changed_path_fields[key] = fields
                vals.append(json.dumps(encoded[key], ensure_ascii=False) if value else None)

        vals.append(str(task_id))
        with _db_lock, conn:
            if changed_path_fields:
                row = conn.execute("SELECT path_fields FROM tasks WHERE id = ?", (str(task_id),)).fetchone()
                previous = json.loads(row[0]) if row else []
                fields = [field for field in previous if field[0] not in changed_path_fields]
                fields.extend(field for changed in changed_path_fields.values() for field in changed)
                sets.append("path_fields = ?")
                vals.insert(-1, json.dumps(fields))
            conn.execute(
                f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?",
                vals,
            )
            conn.commit()

    def add_event(
        self,
        task_id: UUID | str,
        event_type: str,
        *,
        stage: str | None = None,
        step_id: str | None = None,
        level: str = "info",
        message: str | None = None,
        data: dict[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> None:
        """Persist one task timeline event."""
        conn = _get_conn()
        now = timestamp or datetime.now().isoformat()
        payload = data or {}
        with _db_lock:
            conn.execute(
                """
                INSERT INTO task_events
                    (task_id, event_type, stage, step_id, level, message, data, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(task_id),
                    event_type,
                    stage,
                    step_id,
                    level,
                    message,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                ),
            )
            conn.commit()

    def list_events(self, task_id: UUID | str, limit: int = 1000) -> list[dict[str, Any]]:
        """Return persisted timeline events for a task in chronological order."""
        conn = _get_conn()
        cur = conn.execute(
            """
            SELECT id, task_id, event_type, stage, step_id, level, message, data, timestamp
            FROM task_events
            WHERE task_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (str(task_id), limit),
        )
        events: list[dict[str, Any]] = []
        for row in cur.fetchall():
            event = dict(row)
            try:
                event["data"] = json.loads(event["data"]) if event["data"] else {}
            except json.JSONDecodeError:
                event["data"] = {}
            events.append(event)
        return events

    def save_artifact(
        self,
        task_id: UUID | str,
        filename: str,
        content: str,
        content_type: str = "text/plain",
    ) -> None:
        """Persist a generated text artifact in SQLite.

        This is intentionally text-only for now: transcripts, markdown exports,
        JSON navigation trees, and analysis/summary payloads. Large media stays
        on disk.
        """
        conn = _get_conn()
        now = datetime.now().isoformat()
        with _db_lock, conn:
            conn.execute(
                """
                INSERT INTO task_artifacts
                    (task_id, filename, content_type, content, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id, filename) DO UPDATE SET
                    content_type = excluded.content_type,
                    content = excluded.content,
                    updated_at = excluded.updated_at
                """,
                (str(task_id), filename, content_type, content, now, now),
            )
            conn.commit()

    def get_artifact(self, task_id: UUID | str, filename: str) -> dict[str, Any] | None:
        """Return one SQLite-backed artifact for a task."""
        conn = _get_conn()
        cur = conn.execute(
            """
            SELECT task_id, filename, content_type, content, created_at, updated_at
            FROM task_artifacts
            WHERE task_id = ? AND filename = ?
            """,
            (str(task_id), filename),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def list_artifacts(self, task_id: UUID | str) -> list[dict[str, Any]]:
        return [dict(row) for row in _get_conn().execute(
            "SELECT * FROM task_artifacts WHERE task_id = ? ORDER BY filename", (str(task_id),)
        ).fetchall()]

    def find_task_by_output_dir(self, output_dir: Path | str) -> Task | None:
        """Resolve stored output paths directly without loading thousands of task models."""
        import os
        target = Path(output_dir).resolve().as_posix().rstrip("/")
        try:
            relative = Path(output_dir).resolve().relative_to(_database_root()).as_posix()
        except ValueError:
            relative = target
        collation = " COLLATE NOCASE" if os.name == "nt" else ""
        row = _get_conn().execute(
            "SELECT * FROM tasks WHERE "
            "rtrim(replace(json_extract(result, '$.output_dir'), char(92), '/'), '/')" + collation + " IN (?, ?)" +
            " OR rtrim(replace(json_extract(result, '$.archive.output_dir'), char(92), '/'), '/')" + collation + " IN (?, ?)" +
            " ORDER BY updated_at DESC LIMIT 1", (target, relative, target, relative)
        ).fetchone()
        return _row_to_task(row) if row else None

    def get_artifact_by_output_dir(self, output_dir: Path | str, filename: str) -> dict[str, Any] | None:
        task = self.find_task_by_output_dir(output_dir)
        return self.get_artifact(task.id, filename) if task else None

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
    if data_root and Path(data_root).resolve() != _database_root():
        reset_db_path(data_root)
    # Force connection creation + schema init
    _get_conn()
    log_event(logger, logging.INFO, "database.initialized", path=_get_db_path())


def close_db() -> None:
    """Close the database connection (call during app shutdown)."""
    global _connection
    if _connection:
        _connection.close()
        _connection = None
        log_event(logger, logging.INFO, "database.closed")


def reset_db_path(data_root: Path | None = None) -> None:
    """Close the current connection and make the next access resolve a fresh DB path."""
    global _db_path, _db_root
    from app.core.paths import reset_workspace_paths
    close_db()
    _db_root = Path(data_root).resolve() if data_root else None
    _db_path = None
    reset_workspace_paths()
