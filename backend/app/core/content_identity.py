"""Stable content identity shared by library queries and task imports."""

from __future__ import annotations

from app.core.archive_index import source_key


def task_content_key(task) -> str:
    from app.core.task_import import _task_key

    metadata = (task.result or {}).get('metadata') or {}
    return (metadata.get('content_key') or task.options.get('content_key')
            or _task_key(task)
            or f"archive:{metadata.get('archive_id') or task.options.get('archive_id') or task.id}")


def archive_content_key(item: dict, task=None) -> str:
    from app.core.task_import import _archive_identity

    metadata = item.get('metadata') or {}
    stored = metadata.get('content_key')
    if isinstance(stored, str) and stored:
        return stored
    key, same_source = _archive_identity(task, metadata)
    if same_source and task:
        key = task.options.get('content_key') or key
    return key or f"archive:{metadata.get('archive_id') or item.get('archive_id') or (task.id if task else item.get('path'))}"


def new_content_key(source: str, archive_id: str) -> str:
    return source_key(source) or f'archive:{archive_id}'
