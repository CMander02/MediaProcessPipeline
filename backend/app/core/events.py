"""In-process event bus for task progress → SSE streaming.

Events are published by the pipeline runner and consumed by SSE endpoints.
Each subscriber gets an asyncio.Queue; missed events before subscription are lost.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


class TaskEvent:
    """A single event in the task lifecycle."""

    __slots__ = ("task_id", "event_type", "data", "timestamp")

    def __init__(self, task_id: UUID | str, event_type: str, data: dict[str, Any] | None = None):
        self.task_id = str(task_id)
        self.event_type = event_type
        self.data = data or {}
        self.timestamp = datetime.now().isoformat()

    def to_sse(self) -> str:
        """Format as an SSE message."""
        payload = {
            "task_id": self.task_id,
            "type": self.event_type,
            "data": self.data,
            "timestamp": self.timestamp,
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class EventBus:
    """Simple in-process pub/sub for task events."""

    def __init__(self, log_buffer_size: int = 200):
        # Global subscribers (receive ALL events)
        self._global_subs: list[asyncio.Queue[TaskEvent]] = []
        # Per-task subscribers
        self._task_subs: dict[str, list[asyncio.Queue[TaskEvent]]] = {}
        self._lock = asyncio.Lock()
        # Ring buffer of recent events for UI log display
        self._log_buffer: list[TaskEvent] = []
        self._log_buffer_size = log_buffer_size

    def get_recent_log(self, n: int = 50) -> list[TaskEvent]:
        """Return the last N events from the log buffer (newest last)."""
        return self._log_buffer[-n:]

    async def publish(self, event: TaskEvent) -> None:
        """Publish an event to all matching subscribers."""
        self._persist_event(event)
        async with self._lock:
            # Append to log buffer
            self._log_buffer.append(event)
            if len(self._log_buffer) > self._log_buffer_size:
                self._log_buffer = self._log_buffer[-self._log_buffer_size:]
            # Deliver to global subscribers
            for q in self._global_subs:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass  # Drop if subscriber is too slow

            # Deliver to task-specific subscribers
            task_queues = self._task_subs.get(event.task_id, [])
            for q in task_queues:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    def _persist_event(self, event: TaskEvent) -> None:
        data = event.data or {}
        level = str(data.get("level") or _level_for_event(event.event_type)).lower()
        message_value = data.get("message") or data.get("error") or data.get("detail")
        try:
            from app.core.database import get_task_store

            get_task_store().add_event(
                event.task_id,
                event.event_type,
                stage=_as_optional_str(data.get("stage") or data.get("step")),
                step_id=_as_optional_str(data.get("step_id") or data.get("step")),
                level=level,
                message=str(message_value) if message_value is not None else None,
                data=data,
                timestamp=event.timestamp,
            )
        except Exception:
            logger.debug("task_event.persist_failed", exc_info=True)

    def subscribe_global(self, maxsize: int = 256) -> asyncio.Queue[TaskEvent]:
        """Subscribe to ALL task events. Returns a queue to read from."""
        q: asyncio.Queue[TaskEvent] = asyncio.Queue(maxsize=maxsize)
        self._global_subs.append(q)
        return q

    def subscribe_task(self, task_id: UUID | str, maxsize: int = 256) -> asyncio.Queue[TaskEvent]:
        """Subscribe to events for a specific task."""
        tid = str(task_id)
        q: asyncio.Queue[TaskEvent] = asyncio.Queue(maxsize=maxsize)
        if tid not in self._task_subs:
            self._task_subs[tid] = []
        self._task_subs[tid].append(q)
        return q

    async def unsubscribe_global(self, q: asyncio.Queue[TaskEvent]) -> None:
        """Remove a global subscriber."""
        async with self._lock:
            if q in self._global_subs:
                self._global_subs.remove(q)

    async def unsubscribe_task(self, task_id: UUID | str, q: asyncio.Queue[TaskEvent]) -> None:
        """Remove a task-specific subscriber."""
        tid = str(task_id)
        async with self._lock:
            subs = self._task_subs.get(tid, [])
            if q in subs:
                subs.remove(q)
            if not subs:
                self._task_subs.pop(tid, None)


# Singleton
_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get the global EventBus singleton."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def _level_for_event(event_type: str) -> str:
    if event_type in {"failed", "error"}:
        return "error"
    if event_type == "warning":
        return "warning"
    if event_type == "diagnostic":
        return "debug"
    return "info"


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
