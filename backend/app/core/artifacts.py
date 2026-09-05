"""File-first text artifacts with repairable SQLite copies."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from app.core.atomic_file import atomic_write_text
from app.core.database import get_task_store
from app.core.paths import managed_child
from app.core.workspace_lifecycle import uses_workspace

TEXT_SUFFIXES = frozenset({".json", ".md", ".srt", ".txt", ".vtt"})
_write_lock = threading.RLock()


def artifact_content_type(filename: str) -> str:
    return {
        ".json": "application/json",
        ".md": "text/markdown",
        ".srt": "application/x-subrip",
        ".vtt": "text/vtt",
    }.get(Path(filename).suffix.lower(), "text/plain")


class ArtifactMirrorError(OSError):
    def __init__(self, path: Path, cause: Exception):
        self.path = path
        super().__init__(f"File saved; SQLite copy needs repair: {path}: {cause}")


class ArtifactStore:
    def __init__(self, task_store=None):
        self._task_store = task_store

    @property
    def tasks(self):
        return self._task_store if self._task_store is not None else get_task_store()

    @staticmethod
    def _path(output_dir: Path | str, filename: str) -> Path:
        if Path(filename).is_absolute():
            raise ValueError("Artifact filename must be relative")
        target = managed_child(Path(output_dir) / filename, output_dir)
        if target.suffix.lower() not in TEXT_SUFFIXES:
            raise ValueError(f"Unsupported text artifact: {filename}")
        return target

    @staticmethod
    def _validate(path: Path, content: str) -> None:
        if path.suffix.lower() == ".json":
            try:
                json.loads(content.removeprefix("\ufeff"))
            except ValueError as exc:
                raise ValueError(f"Invalid JSON artifact: {path}") from exc

    @staticmethod
    def _changed(output_dir: Path | str) -> None:
        from app.core.archive_sync import get_archive_sync_service

        get_archive_sync_service().mark_changed(output_dir)

    @uses_workspace
    def write(self, task_id, output_dir: Path | str, filename: str, content: str) -> Path:
        target = self._path(output_dir, filename)
        self._validate(target, content)
        with _write_lock:
            atomic_write_text(target, content)
            self._changed(output_dir)
            if task_id is not None:
                try:
                    self.tasks.save_artifact(
                        task_id, filename, content, artifact_content_type(filename)
                    )
                except Exception as exc:
                    raise ArtifactMirrorError(target, exc) from exc
        return target

    @uses_workspace
    def read(self, task_id, output_dir: Path | str, filename: str) -> dict:
        target = self._path(output_dir, filename)
        if target.exists():
            content = target.read_text(encoding="utf-8")
            self._validate(target, content)
            return {"content": content, "source": "file", "path": str(target)}
        artifact = self.tasks.get_artifact(task_id, filename) if task_id is not None else None
        if artifact is None:
            raise FileNotFoundError(str(target))
        self._validate(target, artifact["content"])
        return {"content": artifact["content"], "source": "sqlite", "path": str(target)}

    @uses_workspace
    def inspect(self, task_id, output_dir: Path | str) -> list[dict]:
        """Compare plain text directly; no file fingerprints or automatic overwrites."""
        directory = Path(output_dir)
        mirrors = {item["filename"]: item for item in self.tasks.list_artifacts(task_id)}
        names = set(mirrors)
        if directory.is_dir():
            names.update(
                path.relative_to(directory).as_posix()
                for path in directory.rglob("*")
                if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES
            )
        results = []
        for filename in sorted(names):
            try:
                target = self._path(directory, filename)
                if not target.exists():
                    state = "file_missing"
                else:
                    content = target.read_text(encoding="utf-8")
                    self._validate(target, content)
                    mirror = mirrors.get(filename)
                    state = (
                        "mirror_missing"
                        if mirror is None
                        else ("synced" if mirror["content"] == content else "mirror_stale")
                    )
                results.append({"filename": filename, "state": state})
            except (OSError, ValueError, UnicodeError) as exc:
                results.append({"filename": filename, "state": "invalid", "error": str(exc)})
        return results

    @uses_workspace
    def repair(self, task_id, output_dir: Path | str, filename: str | None = None) -> list[dict]:
        """Rebuild SQLite copies from existing, valid files."""
        with _write_lock:
            report = self.inspect(task_id, output_dir)
            if filename is not None:
                report = [item for item in report if item["filename"] == filename]
            for item in report:
                if item["state"] not in {"mirror_missing", "mirror_stale"}:
                    continue
                target = self._path(output_dir, item["filename"])
                content = target.read_text(encoding="utf-8")
                self._validate(target, content)
                self.tasks.save_artifact(
                    task_id, item["filename"], content, artifact_content_type(item["filename"])
                )
                item["state"] = "synced"
            return report

    @uses_workspace
    def restore_file(self, task_id, output_dir: Path | str, filename: str) -> Path:
        """Explicitly restore a missing file from SQLite; keep existing files intact."""
        with _write_lock:
            target = self._path(output_dir, filename)
            if target.exists():
                raise FileExistsError(f"Artifact already exists: {target}")
            artifact = self.tasks.get_artifact(task_id, filename)
            if artifact is None:
                raise FileNotFoundError(f"SQLite copy not found: {filename}")
            return self.write(task_id, output_dir, filename, artifact["content"])


_store = ArtifactStore()


def get_artifact_store() -> ArtifactStore:
    return _store
