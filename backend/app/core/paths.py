"""Managed data paths and directory ownership checks."""

from __future__ import annotations

import stat
from pathlib import Path

from app.models.task import Task

SYSTEM_DIRECTORIES = frozenset({
    "auth", "voiceprints", "logs", "uploads", "manual_task", "state", "tmp", "backups",
    "_staging", "_sync_downloads", "_remote_sync", "_remote_sync_client", "_deleting",
})
ACTIVE_STATUSES = frozenset({"pending", "queued", "processing", "paused"})


def is_directory_link(path: Path) -> bool:
    """Recognize symlinks and Windows junctions without following their targets."""
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def managed_child(path: str | Path, root: str | Path) -> Path:
    """Resolve a strict descendant, rejecting links in the supplied path."""
    root = Path(root).resolve()
    supplied = Path(path).absolute()
    resolved = supplied.resolve()
    relative = resolved.relative_to(root)
    if not relative.parts:
        raise ValueError("The data root cannot be removed")
    # Check the lexical path as well: resolving alone loses in-root symlinks.
    lexical = supplied.relative_to(root)
    current = root
    for part in lexical.parts:
        if part in (".", ".."):
            raise ValueError("Use a direct managed path")
        current = current / part
        if current.exists() or current.is_symlink():
            if is_directory_link(current):
                raise ValueError("Linked directories are not managed deletion targets")
    return resolved


def archive_directory(path: str | Path, root: str | Path) -> Path:
    """Accept a flat archive or an archive in the dedicated archives directory."""
    root = Path(root).resolve()
    target = managed_child(path, root)
    parts = target.relative_to(root).parts
    if parts[0].casefold() in SYSTEM_DIRECTORIES or parts[0].startswith("."):
        raise ValueError("System directories cannot be removed as archives")
    expected_depth = 2 if parts[0].casefold() == "archives" else 1
    if len(parts) != expected_depth:
        raise ValueError("Expected an archive directory")
    return target


def task_output_paths(task: Task) -> list[Path]:
    result = task.result or {}
    archive = result.get("archive")
    values = [result.get("output_dir")]
    if isinstance(archive, dict):
        values.append(archive.get("output_dir"))
    return [Path(value).resolve() for value in values if isinstance(value, str) and value]


def task_uses_directory(task: Task, directory: Path) -> bool:
    paths = task_output_paths(task)
    if task.source and "://" not in task.source:
        paths.append(Path(task.source).resolve())
    return any(path == directory or directory in path.parents for path in paths)
