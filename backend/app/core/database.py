"""SQLite-backed task store.

Replaces in-memory _tasks dict and JSON-based HistoryService with a single
SQLite database at data/tasks.db.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.core.logging_setup import log_event
from app.models.task import PREFERRED_WORKER_OPTION, Task, TaskStatus, TaskType

logger = logging.getLogger(__name__)
_MAX_REMOTE_LEASE_ATTEMPTS = 3

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
    completed_steps TEXT NOT NULL DEFAULT '[]',
    flow            TEXT,
    origin_client   TEXT NOT NULL DEFAULT 'legacy',
    requested_executor TEXT NOT NULL DEFAULT 'server',
    assigned_executor TEXT,
    sync_revision   INTEGER NOT NULL DEFAULT 0
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

CREATE TABLE IF NOT EXISTS workers (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    capabilities  TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'online',
    registered_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workers_last_seen ON workers(last_seen_at DESC);

CREATE TABLE IF NOT EXISTS task_leases (
    task_id         TEXT PRIMARY KEY,
    worker_id       TEXT NOT NULL,
    token_hash      TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'active',
    attempt         INTEGER NOT NULL DEFAULT 1,
    claimed_at      TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
    FOREIGN KEY (worker_id) REFERENCES workers(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_leases_worker_state
    ON task_leases(worker_id, state, lease_expires_at);
"""

# Columns added after initial schema — applied idempotently via ALTER TABLE
_MIGRATIONS = [
    "ALTER TABLE tasks ADD COLUMN platform TEXT",
    "ALTER TABLE tasks ADD COLUMN uploader_id TEXT",
    "ALTER TABLE tasks ADD COLUMN content_subtype TEXT",
    "ALTER TABLE tasks ADD COLUMN flow TEXT",
    "ALTER TABLE tasks ADD COLUMN origin_client TEXT NOT NULL DEFAULT 'legacy'",
    "ALTER TABLE tasks ADD COLUMN requested_executor TEXT NOT NULL DEFAULT 'server'",
    "ALTER TABLE tasks ADD COLUMN assigned_executor TEXT",
    "ALTER TABLE tasks ADD COLUMN sync_revision INTEGER NOT NULL DEFAULT 0",
    "CREATE INDEX IF NOT EXISTS idx_tasks_platform ON tasks(platform)",
    (
        "CREATE INDEX IF NOT EXISTS idx_tasks_executor_status_created "
        "ON tasks(requested_executor, status, created_at)"
    ),
    "CREATE INDEX IF NOT EXISTS idx_tasks_assigned_executor ON tasks(assigned_executor)",
]


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
        "flow": json.dumps(task.flow, ensure_ascii=False) if task.flow else None,
        "platform": platform,
        "uploader_id": uploader_id,
        "content_subtype": content_subtype,
        "origin_client": task.origin_client,
        "requested_executor": task.requested_executor,
        "assigned_executor": task.assigned_executor,
        "sync_revision": task.sync_revision,
    }


def _row_to_task(row: sqlite3.Row) -> Task:
    """Convert a database row to a Task model."""
    keys = row.keys()
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
        flow=json.loads(row["flow"]) if "flow" in keys and row["flow"] else None,
        platform=row["platform"] if "platform" in keys else None,
        uploader_id=row["uploader_id"] if "uploader_id" in keys else None,
        content_subtype=row["content_subtype"] if "content_subtype" in keys else None,
        origin_client=(
            row["origin_client"]
            if "origin_client" in keys and row["origin_client"]
            else "legacy"
        ),
        requested_executor=(
            row["requested_executor"]
            if "requested_executor" in keys and row["requested_executor"] in {"server", "exe"}
            else "server"
        ),
        assigned_executor=row["assigned_executor"] if "assigned_executor" in keys else None,
        sync_revision=int(row["sync_revision"] or 0) if "sync_revision" in keys else 0,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _lease_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


class TaskStore:
    """SQLite-backed task persistence."""

    def save(self, task: Task) -> None:
        """Insert a task or update it while preserving related rows."""
        self._upsert(task, exact_sync_revision=False)

    def save_remote_mirror(self, task: Task) -> None:
        """Upsert a coordinator mirror using its canonical sync revision."""
        self._upsert(task, exact_sync_revision=True)

    def _upsert(self, task: Task, *, exact_sync_revision: bool) -> None:
        conn = _get_conn()
        row = _task_to_row(task)
        cols = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row.keys())
        updates = [
            f"{key} = excluded.{key}"
            for key in row.keys()
            if key not in {"id", "sync_revision"}
        ]
        if exact_sync_revision:
            updates.append("sync_revision = excluded.sync_revision")
        else:
            updates.append("sync_revision = tasks.sync_revision + 1")
        with _db_lock:
            conn.execute(
                f"""
                INSERT INTO tasks ({cols}) VALUES ({placeholders})
                ON CONFLICT(id) DO UPDATE SET {", ".join(updates)}
                """,
                row,
            )
            conn.commit()

    def get(self, task_id: UUID) -> Task | None:
        """Get a single task by ID."""
        conn = _get_conn()
        cur = conn.execute("SELECT * FROM tasks WHERE id = ?", (str(task_id),))
        row = cur.fetchone()
        return _row_to_task(row) if row else None

    def find_by_output_dir(self, output_dir: Path | str) -> Task | None:
        """Resolve a task whose result points at one local archive directory."""
        target = str(Path(output_dir).resolve())
        for task in self.list(limit=10_000):
            result = task.result if isinstance(task.result, dict) else {}
            archive = result.get("archive")
            candidates = [
                result.get("output_dir"),
                archive.get("output_dir") if isinstance(archive, dict) else None,
            ]
            for candidate in candidates:
                if not candidate:
                    continue
                try:
                    if str(Path(str(candidate)).resolve()) == target:
                        return task
                except OSError:
                    continue
        return None

    def touch_sync_revision(
        self,
        task_id: UUID,
        *,
        result_updates: dict[str, Any] | None = None,
    ) -> Task | None:
        """Atomically bump a task revision after an archive-side mutation."""
        conn = _get_conn()
        now = datetime.now().isoformat()
        with _db_lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT result FROM tasks WHERE id = ?",
                    (str(task_id),),
                ).fetchone()
                if not row:
                    conn.rollback()
                    return None
                try:
                    result = json.loads(row["result"]) if row["result"] else {}
                except (TypeError, ValueError):
                    result = {}
                if not isinstance(result, dict):
                    result = {}
                if result_updates:
                    result.update(result_updates)
                conn.execute(
                    """
                    UPDATE tasks
                    SET result = ?,
                        sync_revision = sync_revision + 1,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(result, ensure_ascii=False),
                        now,
                        str(task_id),
                    ),
                )
                updated = conn.execute(
                    "SELECT * FROM tasks WHERE id = ?",
                    (str(task_id),),
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return _row_to_task(updated) if updated else None

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

    def register_worker(
        self,
        *,
        worker_id: str | None,
        name: str,
        capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Register or refresh one EXE worker.

        A caller may persist ``worker_id`` locally and pass it again on the next
        startup. Supplying no id creates a stable opaque id for the caller to
        save after registration.
        """
        conn = _get_conn()
        resolved_id = worker_id or f"exe-{uuid4().hex}"
        now = _utc_now().isoformat()
        payload = json.dumps(capabilities or {}, ensure_ascii=False)
        with _db_lock:
            conn.execute(
                """
                INSERT INTO workers
                    (id, name, capabilities, status, registered_at, last_seen_at)
                VALUES (?, ?, ?, 'online', ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    capabilities = excluded.capabilities,
                    status = 'online',
                    last_seen_at = excluded.last_seen_at
                """,
                (resolved_id, name, payload, now, now),
            )
            conn.commit()
        return self.get_worker(resolved_id) or {}

    def get_worker(self, worker_id: str) -> dict[str, Any] | None:
        conn = _get_conn()
        row = conn.execute(
            """
            SELECT id, name, capabilities, status, registered_at, last_seen_at
            FROM workers
            WHERE id = ?
            """,
            (worker_id,),
        ).fetchone()
        if not row:
            return None
        record = dict(row)
        try:
            record["capabilities"] = json.loads(record["capabilities"] or "{}")
        except json.JSONDecodeError:
            record["capabilities"] = {}
        return record

    def list_workers(self) -> list[dict[str, Any]]:
        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT id, name, capabilities, status, registered_at, last_seen_at
            FROM workers
            ORDER BY last_seen_at DESC
            """
        ).fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            try:
                record["capabilities"] = json.loads(record["capabilities"] or "{}")
            except json.JSONDecodeError:
                record["capabilities"] = {}
            records.append(record)
        return records

    def heartbeat_worker(
        self,
        worker_id: str,
        *,
        capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        conn = _get_conn()
        now = _utc_now().isoformat()
        with _db_lock:
            if capabilities is None:
                cur = conn.execute(
                    """
                    UPDATE workers
                    SET status = 'online', last_seen_at = ?
                    WHERE id = ?
                    """,
                    (now, worker_id),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE workers
                    SET status = 'online', last_seen_at = ?, capabilities = ?
                    WHERE id = ?
                    """,
                    (now, json.dumps(capabilities, ensure_ascii=False), worker_id),
                )
            conn.commit()
        if cur.rowcount == 0:
            return None
        return self.get_worker(worker_id)

    def _expire_remote_leases_locked(self, conn: sqlite3.Connection, now: datetime) -> int:
        rows = conn.execute(
            """
            SELECT l.task_id, l.attempt, t.options
            FROM task_leases AS l
            JOIN tasks AS t ON t.id = l.task_id
            WHERE l.state IN ('active', 'uploading', 'finalizing')
              AND l.lease_expires_at <= ?
            """,
            (now.isoformat(),),
        ).fetchall()
        if not rows:
            return 0
        task_ids = [str(row["task_id"]) for row in rows]
        placeholders = ", ".join("?" for _ in task_ids)
        conn.execute(
            f"""
            UPDATE task_leases
            SET state = 'expired', updated_at = ?
            WHERE task_id IN ({placeholders})
              AND state IN ('active', 'uploading', 'finalizing')
            """,
            [now.isoformat(), *task_ids],
        )
        for row in rows:
            if int(row["attempt"] or 0) >= _MAX_REMOTE_LEASE_ATTEMPTS:
                conn.execute(
                    """
                    UPDATE task_leases
                    SET state = 'failed', updated_at = ?
                    WHERE task_id = ? AND state = 'expired'
                    """,
                    (now.isoformat(), str(row["task_id"])),
                )
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'failed',
                        message = 'EXE 任务多次中断，已停止自动重试',
                        error = 'EXE worker lease expired repeatedly',
                        completed_at = ?,
                        updated_at = ?,
                        sync_revision = sync_revision + 1
                    WHERE id = ?
                      AND requested_executor = 'exe'
                      AND status = 'processing'
                    """,
                    (
                        now.isoformat(),
                        now.isoformat(),
                        str(row["task_id"]),
                    ),
                )
                continue
            try:
                options = json.loads(str(row["options"] or "{}"))
            except (TypeError, ValueError):
                options = {}
            preferred_worker = (
                str(options.get(PREFERRED_WORKER_OPTION) or "").strip()
                if isinstance(options, dict)
                else ""
            )
            conn.execute(
                """
                UPDATE tasks
                SET status = 'queued',
                    assigned_executor = ?,
                    message = '等待 EXE 处理...',
                    updated_at = ?,
                    sync_revision = sync_revision + 1
                WHERE id = ?
                  AND requested_executor = 'exe'
                  AND status = 'processing'
                """,
                (preferred_worker or None, now.isoformat(), str(row["task_id"])),
            )
        return len(task_ids)

    def claim_remote_task(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 900,
    ) -> dict[str, Any] | None:
        """Atomically lease the oldest queued ``requested_executor=exe`` task."""
        conn = _get_conn()
        now = _utc_now()
        expires_at = now + timedelta(seconds=lease_seconds)
        token = secrets.token_urlsafe(32)
        token_hash = _lease_token_hash(token)

        with _db_lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                worker = conn.execute(
                    "SELECT id FROM workers WHERE id = ?",
                    (worker_id,),
                ).fetchone()
                if not worker:
                    conn.rollback()
                    return None

                self._expire_remote_leases_locked(conn, now)
                row = conn.execute(
                    """
                    SELECT t.*
                    FROM tasks AS t
                    LEFT JOIN task_leases AS l ON l.task_id = t.id
                    WHERE t.requested_executor = 'exe'
                      AND t.status = 'queued'
                      AND (t.assigned_executor IS NULL OR t.assigned_executor = ?)
                      AND (
                          l.task_id IS NULL
                          OR l.state NOT IN ('active', 'uploading', 'finalizing')
                      )
                    ORDER BY t.created_at ASC
                    LIMIT 1
                    """,
                    (worker_id,),
                ).fetchone()
                if not row:
                    conn.commit()
                    return None

                task_id = str(row["id"])
                previous = conn.execute(
                    "SELECT attempt FROM task_leases WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                attempt = int(previous["attempt"] or 0) + 1 if previous else 1
                updated = conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'processing',
                        assigned_executor = ?,
                        message = 'EXE 正在处理...',
                        updated_at = ?,
                        sync_revision = sync_revision + 1
                    WHERE id = ?
                      AND status = 'queued'
                      AND requested_executor = 'exe'
                      AND (assigned_executor IS NULL OR assigned_executor = ?)
                    """,
                    (worker_id, now.isoformat(), task_id, worker_id),
                )
                if updated.rowcount != 1:
                    conn.rollback()
                    return None
                conn.execute(
                    """
                    INSERT INTO task_leases
                        (task_id, worker_id, token_hash, state, attempt,
                         claimed_at, lease_expires_at, updated_at)
                    VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        worker_id = excluded.worker_id,
                        token_hash = excluded.token_hash,
                        state = 'active',
                        attempt = excluded.attempt,
                        claimed_at = excluded.claimed_at,
                        lease_expires_at = excluded.lease_expires_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        task_id,
                        worker_id,
                        token_hash,
                        attempt,
                        now.isoformat(),
                        expires_at.isoformat(),
                        now.isoformat(),
                    ),
                )
                conn.execute(
                    """
                    UPDATE workers
                    SET status = 'online', last_seen_at = ?
                    WHERE id = ?
                    """,
                    (now.isoformat(), worker_id),
                )
                claimed_row = conn.execute(
                    "SELECT * FROM tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        if not claimed_row:
            return None
        return {
            "task": _row_to_task(claimed_row),
            "lease_token": token,
            "lease_expires_at": expires_at.isoformat(),
            "attempt": attempt,
        }

    def get_remote_lease(
        self,
        task_id: UUID | str,
        worker_id: str,
        lease_token: str,
        *,
        allow_completed: bool = False,
    ) -> dict[str, Any] | None:
        """Validate lease ownership and return its state."""
        conn = _get_conn()
        row = conn.execute(
            """
            SELECT task_id, worker_id, token_hash, state, attempt,
                   claimed_at, lease_expires_at, updated_at
            FROM task_leases
            WHERE task_id = ? AND worker_id = ?
            """,
            (str(task_id), worker_id),
        ).fetchone()
        if not row or not secrets.compare_digest(
            str(row["token_hash"]),
            _lease_token_hash(lease_token),
        ):
            return None
        record = dict(row)
        if record["state"] == "completed" and allow_completed:
            return record
        if record["state"] != "active":
            return None
        if _parse_timestamp(str(record["lease_expires_at"])) <= _utc_now():
            return None
        return record

    def renew_remote_lease(
        self,
        task_id: UUID | str,
        worker_id: str,
        lease_token: str,
        *,
        lease_seconds: int = 900,
    ) -> dict[str, Any] | None:
        conn = _get_conn()
        now = _utc_now()
        expires_at = now + timedelta(seconds=lease_seconds)
        with _db_lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    """
                    SELECT l.token_hash, l.state, l.lease_expires_at,
                           t.status AS task_status,
                           t.requested_executor,
                           t.assigned_executor
                    FROM task_leases AS l
                    JOIN tasks AS t ON t.id = l.task_id
                    WHERE l.task_id = ? AND l.worker_id = ?
                    """,
                    (str(task_id), worker_id),
                ).fetchone()
                valid = (
                    row
                    and row["state"] in {"active", "uploading", "finalizing"}
                    and secrets.compare_digest(
                        str(row["token_hash"]),
                        _lease_token_hash(lease_token),
                    )
                    and _parse_timestamp(str(row["lease_expires_at"])) > now
                    and row["task_status"] == TaskStatus.PROCESSING.value
                    and row["requested_executor"] == "exe"
                    and row["assigned_executor"] == worker_id
                )
                if not valid:
                    conn.rollback()
                    return None
                conn.execute(
                    """
                    UPDATE task_leases
                    SET lease_expires_at = ?, updated_at = ?
                    WHERE task_id = ?
                      AND worker_id = ?
                      AND state IN ('active', 'uploading', 'finalizing')
                    """,
                    (expires_at.isoformat(), now.isoformat(), str(task_id), worker_id),
                )
                conn.execute(
                    """
                    UPDATE workers
                    SET status = 'online', last_seen_at = ?
                    WHERE id = ?
                    """,
                    (now.isoformat(), worker_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            "task_id": str(task_id),
            "worker_id": worker_id,
            "lease_expires_at": expires_at.isoformat(),
        }

    def begin_remote_upload(
        self,
        task_id: UUID | str,
        worker_id: str,
        lease_token: str,
        *,
        lease_seconds: int = 3600,
    ) -> dict[str, Any] | None:
        """Exclusively move one active lease into the archive-upload phase."""
        conn = _get_conn()
        now = _utc_now()
        expires_at = now + timedelta(seconds=lease_seconds)
        with _db_lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._expire_remote_leases_locked(conn, now)
                row = conn.execute(
                    """
                    SELECT l.task_id, l.worker_id, l.token_hash, l.state, l.attempt,
                           l.claimed_at, l.lease_expires_at, l.updated_at,
                           t.status AS task_status,
                           t.requested_executor,
                           t.assigned_executor
                    FROM task_leases AS l
                    JOIN tasks AS t ON t.id = l.task_id
                    WHERE l.task_id = ? AND l.worker_id = ?
                    """,
                    (str(task_id), worker_id),
                ).fetchone()
                if (
                    not row
                    or not secrets.compare_digest(
                        str(row["token_hash"]),
                        _lease_token_hash(lease_token),
                    )
                ):
                    conn.commit()
                    return None
                if row["state"] == "completed":
                    conn.commit()
                    return dict(row)
                valid = (
                    row["state"] == "active"
                    and _parse_timestamp(str(row["lease_expires_at"])) > now
                    and row["task_status"] == TaskStatus.PROCESSING.value
                    and row["requested_executor"] == "exe"
                    and row["assigned_executor"] == worker_id
                )
                if not valid:
                    conn.commit()
                    return None
                updated = conn.execute(
                    """
                    UPDATE task_leases
                    SET state = 'uploading',
                        lease_expires_at = ?,
                        updated_at = ?
                    WHERE task_id = ?
                      AND worker_id = ?
                      AND token_hash = ?
                      AND state = 'active'
                    """,
                    (
                        expires_at.isoformat(),
                        now.isoformat(),
                        str(task_id),
                        worker_id,
                        _lease_token_hash(lease_token),
                    ),
                )
                if updated.rowcount != 1:
                    conn.rollback()
                    return None
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            "task_id": str(task_id),
            "worker_id": worker_id,
            "state": "uploading",
            "lease_expires_at": expires_at.isoformat(),
        }

    def begin_remote_finalization(
        self,
        task_id: UUID | str,
        worker_id: str,
        lease_token: str,
        *,
        lease_seconds: int = 3600,
    ) -> bool:
        """CAS an uploading lease into the non-cancellable publish phase."""
        conn = _get_conn()
        now = _utc_now()
        expires_at = now + timedelta(seconds=lease_seconds)
        with _db_lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._expire_remote_leases_locked(conn, now)
                updated = conn.execute(
                    """
                    UPDATE task_leases
                    SET state = 'finalizing',
                        lease_expires_at = ?,
                        updated_at = ?
                    WHERE task_id = ?
                      AND worker_id = ?
                      AND token_hash = ?
                      AND state = 'uploading'
                      AND lease_expires_at > ?
                      AND EXISTS (
                          SELECT 1
                          FROM tasks
                          WHERE tasks.id = task_leases.task_id
                            AND tasks.status = 'processing'
                            AND tasks.requested_executor = 'exe'
                            AND tasks.assigned_executor = ?
                      )
                    """,
                    (
                        expires_at.isoformat(),
                        now.isoformat(),
                        str(task_id),
                        worker_id,
                        _lease_token_hash(lease_token),
                        now.isoformat(),
                        worker_id,
                    ),
                )
                if updated.rowcount != 1:
                    conn.commit()
                    return False
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return True

    def release_remote_upload(
        self,
        task_id: UUID | str,
        worker_id: str,
        lease_token: str,
    ) -> bool:
        """Release an interrupted upload/finalization so the lease can retry."""
        conn = _get_conn()
        now = _utc_now()
        with _db_lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._expire_remote_leases_locked(conn, now)
                updated = conn.execute(
                    """
                    UPDATE task_leases
                    SET state = 'active', updated_at = ?
                    WHERE task_id = ?
                      AND worker_id = ?
                      AND token_hash = ?
                      AND state IN ('uploading', 'finalizing')
                      AND lease_expires_at > ?
                      AND EXISTS (
                          SELECT 1
                          FROM tasks
                          WHERE tasks.id = task_leases.task_id
                            AND tasks.status = 'processing'
                            AND tasks.requested_executor = 'exe'
                            AND tasks.assigned_executor = ?
                      )
                    """,
                    (
                        now.isoformat(),
                        str(task_id),
                        worker_id,
                        _lease_token_hash(lease_token),
                        now.isoformat(),
                        worker_id,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return updated.rowcount == 1

    def transition_task_control(
        self,
        task_id: UUID,
        status: TaskStatus,
        *,
        flow: dict[str, Any] | None = None,
    ) -> Task | None:
        """Atomically cancel/pause a task and terminate its mutable EXE lease."""
        if status == TaskStatus.CANCELLED:
            allowed = {
                TaskStatus.PENDING.value,
                TaskStatus.QUEUED.value,
                TaskStatus.PROCESSING.value,
                TaskStatus.PAUSED.value,
            }
            message = "已取消"
            lease_state = "cancelled"
        elif status == TaskStatus.PAUSED:
            allowed = {
                TaskStatus.PENDING.value,
                TaskStatus.QUEUED.value,
                TaskStatus.PROCESSING.value,
            }
            message = "已暂停"
            lease_state = "paused"
        else:
            raise ValueError("Task control transition only supports cancelled or paused")

        conn = _get_conn()
        now = _utc_now()
        with _db_lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._expire_remote_leases_locked(conn, now)
                current = conn.execute(
                    """
                    SELECT status, requested_executor
                    FROM tasks
                    WHERE id = ?
                    """,
                    (str(task_id),),
                ).fetchone()
                if not current or current["status"] not in allowed:
                    conn.commit()
                    return None

                if current["requested_executor"] == "exe":
                    lease = conn.execute(
                        "SELECT state FROM task_leases WHERE task_id = ?",
                        (str(task_id),),
                    ).fetchone()
                    if lease and lease["state"] == "finalizing":
                        conn.commit()
                        return None
                    conn.execute(
                        """
                        UPDATE task_leases
                        SET state = ?, updated_at = ?
                        WHERE task_id = ?
                          AND state IN ('active', 'uploading')
                        """,
                        (lease_state, now.isoformat(), str(task_id)),
                    )

                values: list[Any] = [
                    status.value,
                    message,
                    now.isoformat(),
                    now.isoformat() if status == TaskStatus.CANCELLED else None,
                ]
                flow_assignment = ""
                if status == TaskStatus.PAUSED:
                    flow_assignment = ", flow = ?"
                    values.append(json.dumps(flow, ensure_ascii=False) if flow else None)
                values.append(str(task_id))
                placeholders = ", ".join("?" for _ in allowed)
                values.extend(sorted(allowed))
                updated = conn.execute(
                    f"""
                    UPDATE tasks
                    SET status = ?,
                        message = ?,
                        updated_at = ?,
                        completed_at = ?,
                        sync_revision = sync_revision + 1
                        {flow_assignment}
                    WHERE id = ?
                      AND status IN ({placeholders})
                    """,
                    values,
                )
                if updated.rowcount != 1:
                    conn.rollback()
                    return None
                row = conn.execute(
                    "SELECT * FROM tasks WHERE id = ?",
                    (str(task_id),),
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return _row_to_task(row) if row else None

    def complete_remote_task(
        self,
        task_id: UUID | str,
        worker_id: str,
        lease_token: str,
        result: dict[str, Any],
    ) -> tuple[Task | None, bool]:
        """Finalize a leased task. Returns ``(task, already_completed)``."""
        conn = _get_conn()
        now = _utc_now()
        with _db_lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                lease = conn.execute(
                    """
                    SELECT token_hash, state, lease_expires_at
                    FROM task_leases
                    WHERE task_id = ? AND worker_id = ?
                    """,
                    (str(task_id), worker_id),
                ).fetchone()
                if (
                    not lease
                    or not secrets.compare_digest(
                        str(lease["token_hash"]),
                        _lease_token_hash(lease_token),
                    )
                ):
                    conn.rollback()
                    return None, False
                if lease["state"] == "completed":
                    row = conn.execute(
                        "SELECT * FROM tasks WHERE id = ?",
                        (str(task_id),),
                    ).fetchone()
                    conn.commit()
                    return (_row_to_task(row) if row else None), True
                if (
                    lease["state"] != "finalizing"
                    or _parse_timestamp(str(lease["lease_expires_at"])) <= now
                ):
                    conn.rollback()
                    return None, False

                updated = conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'completed',
                        assigned_executor = ?,
                        progress = 1.0,
                        message = '处理完成',
                        result = ?,
                        error = NULL,
                        completed_at = ?,
                        updated_at = ?,
                        sync_revision = sync_revision + 1
                    WHERE id = ?
                      AND requested_executor = 'exe'
                      AND status = 'processing'
                    """,
                    (
                        worker_id,
                        json.dumps(result, ensure_ascii=False),
                        now.isoformat(),
                        now.isoformat(),
                        str(task_id),
                    ),
                )
                if updated.rowcount != 1:
                    conn.rollback()
                    return None, False
                conn.execute(
                    """
                    UPDATE task_leases
                    SET state = 'completed', updated_at = ?
                    WHERE task_id = ? AND worker_id = ?
                    """,
                    (now.isoformat(), str(task_id), worker_id),
                )
                row = conn.execute(
                    "SELECT * FROM tasks WHERE id = ?",
                    (str(task_id),),
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return (_row_to_task(row) if row else None), False

    def fail_remote_task(
        self,
        task_id: UUID | str,
        worker_id: str,
        lease_token: str,
        error: str,
    ) -> tuple[Task | None, bool]:
        """Mark a leased task failed. Repeated calls with the same lease are idempotent."""
        conn = _get_conn()
        now = _utc_now()
        with _db_lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                lease = conn.execute(
                    """
                    SELECT token_hash, state, lease_expires_at
                    FROM task_leases
                    WHERE task_id = ? AND worker_id = ?
                    """,
                    (str(task_id), worker_id),
                ).fetchone()
                if (
                    not lease
                    or not secrets.compare_digest(
                        str(lease["token_hash"]),
                        _lease_token_hash(lease_token),
                    )
                ):
                    conn.rollback()
                    return None, False
                if lease["state"] == "failed":
                    row = conn.execute(
                        "SELECT * FROM tasks WHERE id = ?",
                        (str(task_id),),
                    ).fetchone()
                    conn.commit()
                    return (_row_to_task(row) if row else None), True
                if (
                    lease["state"] != "active"
                    or _parse_timestamp(str(lease["lease_expires_at"])) <= now
                ):
                    conn.rollback()
                    return None, False

                updated = conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'failed',
                        assigned_executor = ?,
                        message = 'EXE 处理失败',
                        error = ?,
                        completed_at = ?,
                        updated_at = ?,
                        sync_revision = sync_revision + 1
                    WHERE id = ?
                      AND requested_executor = 'exe'
                      AND status = 'processing'
                    """,
                    (worker_id, error, now.isoformat(), now.isoformat(), str(task_id)),
                )
                if updated.rowcount != 1:
                    conn.rollback()
                    return None, False
                conn.execute(
                    """
                    UPDATE task_leases
                    SET state = 'failed', updated_at = ?
                    WHERE task_id = ? AND worker_id = ?
                    """,
                    (now.isoformat(), str(task_id), worker_id),
                )
                row = conn.execute(
                    "SELECT * FROM tasks WHERE id = ?",
                    (str(task_id),),
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return (_row_to_task(row) if row else None), False

    def update_status(
        self,
        task_id: UUID,
        status: TaskStatus,
        **kwargs: Any,
    ) -> None:
        """Update task status and optional fields."""
        conn = _get_conn()
        has_explicit_revision = "sync_revision" in kwargs
        sets = ["status = ?", "updated_at = ?"]
        if not has_explicit_revision:
            sets.append("sync_revision = sync_revision + 1")
        vals: list[Any] = [status, datetime.now().isoformat()]

        for key, value in kwargs.items():
            if key in (
                "progress",
                "message",
                "error",
                "current_step",
                "uploader_id",
                "platform",
                "content_subtype",
                "origin_client",
                "requested_executor",
                "assigned_executor",
                "sync_revision",
            ):
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
            elif key == "flow":
                sets.append("flow = ?")
                vals.append(json.dumps(value, ensure_ascii=False) if value else None)

        vals.append(str(task_id))
        with _db_lock:
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
        with _db_lock:
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

    def get_artifact_by_output_dir(
        self,
        output_dir: Path | str,
        filename: str,
    ) -> dict[str, Any] | None:
        """Resolve a task by its result output_dir/archive.output_dir and return an artifact."""
        target = str(Path(output_dir).resolve())
        for task in self.list(limit=10000):
            result = task.result or {}
            candidates = [
                result.get("output_dir"),
                (
                    (result.get("archive") or {}).get("output_dir")
                    if isinstance(result.get("archive"), dict)
                    else None
                ),
            ]
            if any(
                candidate and str(Path(candidate).resolve()) == target
                for candidate in candidates
            ):
                return self.get_artifact(task.id, filename)
        return None

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
    global _db_path
    close_db()
    _db_path = Path(data_root).resolve() / "tasks.db" if data_root else None
