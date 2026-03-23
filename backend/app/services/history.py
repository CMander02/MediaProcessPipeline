"""History service for persistent task tracking."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID
from threading import Lock

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TaskHistoryEntry(BaseModel):
    """Single task history entry."""
    id: str
    title: str
    source: str
    source_type: str = "unknown"  # youtube, bilibili, local_video, local_audio
    status: str  # completed, failed, cancelled
    created_at: str
    completed_at: str | None = None
    duration_seconds: float | None = None
    output_dir: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class HistoryStats(BaseModel):
    """Aggregated statistics."""
    total: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0


class HistoryData(BaseModel):
    """Complete history data structure."""
    version: str = "1.0"
    stats: HistoryStats = Field(default_factory=HistoryStats)
    tasks: list[TaskHistoryEntry] = Field(default_factory=list)


class HistoryService:
    """Service for managing persistent task history."""

    def __init__(self, data_root: Path):
        self._data_root = Path(data_root).resolve()
        self._history_file = self._data_root / "history.json"
        self._lock = Lock()
        self._data: HistoryData = self._load()

    def _load(self) -> HistoryData:
        """Load history from file."""
        if not self._history_file.exists():
            logger.info(f"No history file found, creating new at {self._history_file}")
            return HistoryData()

        try:
            with open(self._history_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return HistoryData.model_validate(raw)
        except Exception as e:
            logger.error(f"Failed to load history: {e}")
            # Backup corrupted file
            backup = self._history_file.with_suffix(".json.bak")
            if self._history_file.exists():
                self._history_file.rename(backup)
            return HistoryData()

    def _save(self) -> None:
        """Save history to file."""
        self._data_root.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._history_file, "w", encoding="utf-8") as f:
                json.dump(self._data.model_dump(), f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save history: {e}")

    def _update_stats(self) -> None:
        """Recalculate stats from task list."""
        self._data.stats = HistoryStats(
            total=len(self._data.tasks),
            completed=len([t for t in self._data.tasks if t.status == "completed"]),
            failed=len([t for t in self._data.tasks if t.status == "failed"]),
            cancelled=len([t for t in self._data.tasks if t.status == "cancelled"]),
        )

    def add_task(
        self,
        task_id: UUID | str,
        title: str,
        source: str,
        source_type: str = "unknown",
        status: str = "completed",
        created_at: datetime | None = None,
        completed_at: datetime | None = None,
        duration_seconds: float | None = None,
        output_dir: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskHistoryEntry:
        """Add a new task to history."""
        entry = TaskHistoryEntry(
            id=str(task_id),
            title=title,
            source=source,
            source_type=source_type,
            status=status,
            created_at=(created_at or datetime.now()).isoformat(),
            completed_at=completed_at.isoformat() if completed_at else None,
            duration_seconds=duration_seconds,
            output_dir=output_dir,
            error=error,
            metadata=metadata or {},
        )

        with self._lock:
            # Check if task already exists (update instead of add)
            existing_idx = next(
                (i for i, t in enumerate(self._data.tasks) if t.id == str(task_id)),
                None
            )
            if existing_idx is not None:
                self._data.tasks[existing_idx] = entry
            else:
                # Insert at beginning (newest first)
                self._data.tasks.insert(0, entry)

            self._update_stats()
            self._save()

        return entry

    def update_task(
        self,
        task_id: UUID | str,
        **updates: Any,
    ) -> TaskHistoryEntry | None:
        """Update an existing task entry."""
        with self._lock:
            for i, task in enumerate(self._data.tasks):
                if task.id == str(task_id):
                    data = task.model_dump()
                    data.update(updates)
                    self._data.tasks[i] = TaskHistoryEntry.model_validate(data)
                    self._update_stats()
                    self._save()
                    return self._data.tasks[i]
        return None

    def get_task(self, task_id: UUID | str) -> TaskHistoryEntry | None:
        """Get a single task by ID."""
        for task in self._data.tasks:
            if task.id == str(task_id):
                return task
        return None

    def list_tasks(
        self,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TaskHistoryEntry]:
        """List tasks with optional filtering."""
        tasks = self._data.tasks
        if status:
            tasks = [t for t in tasks if t.status == status]
        return tasks[offset:offset + limit]

    def get_stats(self) -> HistoryStats:
        """Get current statistics."""
        return self._data.stats

    def get_all(self) -> HistoryData:
        """Get full history data."""
        return self._data

    def delete_task(self, task_id: UUID | str) -> bool:
        """Delete a task from history."""
        with self._lock:
            original_len = len(self._data.tasks)
            self._data.tasks = [t for t in self._data.tasks if t.id != str(task_id)]
            if len(self._data.tasks) < original_len:
                self._update_stats()
                self._save()
                return True
        return False

    def clear_all(self) -> None:
        """Clear all history."""
        with self._lock:
            self._data = HistoryData()
            self._save()


# Global service instance
_service: HistoryService | None = None


def get_history_service(data_root: Path | None = None) -> HistoryService:
    """Get or create the global history service instance."""
    global _service
    if _service is None:
        if data_root is None:
            from app.core.settings import get_runtime_settings
            data_root = Path(get_runtime_settings().data_root).resolve()
        _service = HistoryService(data_root)
    return _service


def init_history_service(data_root: Path) -> HistoryService:
    """Initialize the history service with a specific data root."""
    global _service
    _service = HistoryService(data_root)
    return _service
