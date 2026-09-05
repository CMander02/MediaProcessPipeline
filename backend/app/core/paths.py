"""Managed data paths and directory ownership checks."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PureWindowsPath

from app.models.task import Task

SYSTEM_DIRECTORIES = frozenset(
    {
        "auth",
        "voiceprints",
        "logs",
        "uploads",
        "manual_task",
        "state",
        "tmp",
        "backups",
        "_staging",
        "_sync_downloads",
        "_remote_sync",
        "_remote_sync_client",
        "_deleting",
    }
)
ACTIVE_STATUSES = frozenset({"pending", "queued", "processing", "paused"})
CONFIG_FILE = Path(__file__).resolve().parents[3] / "config.json"
LAYOUT_FILE = ".mpp-layout.json"
MIGRATION_FILE = ".mpp-layout-migration.json"


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path
    version: int

    @property
    def state(self) -> Path:
        return self.root / "state" if self.version == 2 else self.root

    @property
    def tasks_db(self) -> Path:
        return self.state / "tasks.db"

    @property
    def kb_db(self) -> Path:
        return self.state / "kb.db"

    @property
    def voiceprints(self) -> Path:
        return self.state / "voiceprints"

    @property
    def auth(self) -> Path:
        return self.state / "auth"

    @property
    def archives(self) -> Path:
        return self.root / "archives" if self.version == 2 else self.root

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    def temporary(self, owner: str) -> Path:
        if owner not in {
            "staging",
            "sync_downloads",
            "remote_sync",
            "remote_sync_client",
            "deleting",
            "uploads",
            "manual_task",
            "pyannote",
            "download",
            "uvr",
        }:
            raise ValueError(f"Unknown temporary directory owner: {owner}")
        if self.version == 2:
            return self.root / "tmp" / owner
        if owner in {"uploads", "manual_task"}:
            return self.root / owner
        if owner == "pyannote":
            return self.root / ".cache" / "pyannote"
        if owner in {"download", "uvr"}:
            return self.root / "tmp" / owner
        return self.root / f"_{owner}"

    @property
    def staging_roots(self) -> tuple[Path, ...]:
        return tuple(dict.fromkeys((self.temporary("staging"), self.root / "_staging")))

    def ensure(self) -> None:
        from app.core.atomic_file import atomic_write_text

        self.root.mkdir(parents=True, exist_ok=True)
        self.state.mkdir(parents=True, exist_ok=True)
        self.archives.mkdir(parents=True, exist_ok=True)
        marker = self.root / LAYOUT_FILE
        if self.version == 2 and not marker.exists():
            atomic_write_text(marker, json.dumps({"version": self.version}) + "\n")


@lru_cache(maxsize=32)
def _workspace_paths(root: Path) -> WorkspacePaths:
    journal = root / MIGRATION_FILE
    if journal.exists():
        status = json.loads(journal.read_text(encoding="utf-8")).get("status")
        if status not in {"complete", "rolled_back"}:
            raise ValueError(
                "Storage migration is incomplete; resume or roll back with mpp storage migrate"
            )
    marker = root / LAYOUT_FILE
    if marker.exists():
        version = json.loads(marker.read_text(encoding="utf-8"))["version"]
        if version not in {1, 2}:
            raise ValueError(f"Unsupported storage layout: {version}")
        return WorkspacePaths(root, version)
    if (root / "tasks.db").exists() or (root / "kb.db").exists():
        return WorkspacePaths(root, 1)
    if (root / "state" / "tasks.db").exists():
        return WorkspacePaths(root, 2)
    if root.exists():
        if any(
            (root / name).exists() for name in ("auth", "voiceprints", "_staging", "history.json")
        ):
            return WorkspacePaths(root, 1)
        if any(path.is_dir() and (path / "metadata.json").exists() for path in root.iterdir()):
            return WorkspacePaths(root, 1)
    return WorkspacePaths(root, 2)


def get_workspace_paths(root: Path | str | None = None) -> WorkspacePaths:
    if root is None:
        from app.core.settings import get_runtime_settings

        root = get_runtime_settings().data_root
    return _workspace_paths(Path(root).expanduser().resolve())


def reset_workspace_paths() -> None:
    _workspace_paths.cache_clear()


def iter_archive_directories(root: Path | str):
    """Read both layouts, including a library that is part-way through migration."""
    root = Path(root).resolve()
    for parent in (root, root / "archives"):
        if not parent.is_dir():
            continue
        for candidate in parent.iterdir():
            try:
                target = archive_directory(candidate, root)
            except ValueError:
                continue
            if target.is_dir():
                yield target


_PATH_FIELDS = frozenset(
    {
        "source",
        "path",
        "output_dir",
        "file_path",
        "media_file",
        "audio_path",
        "video_path",
        "vocals_path",
        "staging_dir",
    }
)


def is_absolute_file_path(value: str) -> bool:
    return "://" not in value and (
        Path(value).is_absolute() or PureWindowsPath(value).is_absolute()
    )


def encode_workspace_paths(payload: dict, root: Path) -> tuple[dict, list[list]]:
    """Store owned absolute paths relative to the library, recording their locations."""
    fields: list[list] = []

    def visit(value, location, is_path=False):
        if isinstance(value, dict):
            return {
                key: visit(
                    item, [*location, key], key in _PATH_FIELDS or location[-1:] == ["files"]
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [visit(item, [*location, index], is_path) for index, item in enumerate(value)]
        if is_path and isinstance(value, str) and is_absolute_file_path(value):
            try:
                relative = Path(os.path.abspath(value)).relative_to(root)
            except (OSError, ValueError):
                return value
            fields.append(location)
            return relative.as_posix()
        return value

    return visit(payload, []), fields


def decode_workspace_paths(payload: dict, fields: list[list], root: Path) -> dict:
    """Resolve only fields explicitly marked relative; legacy relative text stays intact."""
    for location in fields:
        target = payload
        for key in location[:-1]:
            target = target[key]
        value = target[location[-1]]
        if not isinstance(value, str) or Path(value).is_absolute() or ".." in Path(value).parts:
            raise ValueError("Invalid stored workspace path")
        target[location[-1]] = str(root / value)
    return payload


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
