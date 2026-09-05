"""Coordinate workspace changes with operations using its files and databases."""

from __future__ import annotations

import asyncio
import functools
import inspect
import threading
from contextlib import contextmanager
from pathlib import Path

_lock = threading.Lock()
_active = 0
_switching = False


class WorkspaceBusyError(ValueError):
    pass


@contextmanager
def workspace_activity():
    global _active
    with _lock:
        if _switching:
            raise WorkspaceBusyError("Workspace change is in progress")
        _active += 1
    try:
        yield
    finally:
        with _lock:
            _active -= 1


def uses_workspace(function):
    if inspect.iscoroutinefunction(function):

        @functools.wraps(function)
        async def asynchronous(*args, **kwargs):
            with workspace_activity():
                return await function(*args, **kwargs)

        return asynchronous

    @functools.wraps(function)
    def synchronous(*args, **kwargs):
        with workspace_activity():
            return function(*args, **kwargs)

    return synchronous


async def run_in_thread(function, /, *args, **kwargs):
    """Drain a worker on cancellation before releasing its workspace resources."""
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()
        raise


@contextmanager
def workspace_change():
    global _switching
    with _lock:
        if _switching or _active:
            raise WorkspaceBusyError(
                "Cannot change data_root while workspace operations are running"
            )
        _switching = True
    try:
        from app.core.database import get_task_store
        from app.core.queue import get_task_queue
        from app.models import TaskStatus

        if get_task_queue().active_task_ids or get_task_store().list_by_statuses(
            [
                TaskStatus.PENDING,
                TaskStatus.QUEUED,
                TaskStatus.PROCESSING,
                TaskStatus.PAUSED,
            ]
        ):
            raise WorkspaceBusyError("Cannot change data_root while active tasks exist")
        yield
    finally:
        with _lock:
            _switching = False


def reset_workspace_stores(root: Path) -> None:
    from app.core.database import init_db, reset_db_path
    from app.services.kb.store import reset_kb_store
    from app.services.voiceprint.store import reset_voiceprint_store

    reset_kb_store()
    reset_voiceprint_store()
    reset_db_path(root)
    init_db(root)


class WorkspaceActivityMiddleware:
    """Keep file streaming and threaded API work attached to their workspace."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or not path.startswith("/api/")
            or path == "/api/settings"
            or path.endswith("/events")
        ):
            return await self.app(scope, receive, send)
        with workspace_activity():
            await self.app(scope, receive, send)
