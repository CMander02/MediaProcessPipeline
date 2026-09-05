"""Recoverable archive removal across files, task records and search stores."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from uuid import UUID, uuid4

from app.core.paths import get_workspace_paths

from app.core.database import _db_lock, _get_conn, get_task_store
from app.core.paths import (
    ACTIVE_STATUSES,
    archive_directory,
    managed_child,
    task_output_paths,
    task_uses_directory,
)
from app.core.settings import get_runtime_settings
from app.core.workspace_lifecycle import uses_workspace

logger = logging.getLogger(__name__)


class ArchiveBusyError(ValueError):
    """An active task still owns the archive."""


class ArchiveLifecycle:
    @uses_workspace
    def delete(self, path: str | Path) -> dict:
        from app.core.archive_sync import get_archive_sync_service

        root = Path(get_runtime_settings().data_root).resolve()
        target = archive_directory(path, root)
        sync = get_archive_sync_service()
        # Reconciliation must not publish a directory while it is being staged.
        with sync._reconcile_lock:
            entry = self._prepare(target)
            return self._finish(entry, root, sync)

    def _prepare(self, target: Path) -> dict:
        conn = _get_conn()
        pending = conn.execute(
            "SELECT * FROM archive_deletions WHERE original_path = ?", (str(target),)
        ).fetchone()
        if pending:
            return dict(pending)
        if not target.is_dir():
            raise FileNotFoundError("Archive directory not found")
        tasks = get_task_store().list(limit=-1)
        if any(t.status in ACTIVE_STATUSES and task_uses_directory(t, target) for t in tasks):
            raise ArchiveBusyError("Archive directory is used by an active task")
        task_ids = [str(t.id) for t in tasks if target in task_output_paths(t)]
        try:
            metadata = json.loads((target / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        if not task_ids and not any(metadata.get(k) for k in ("title", "task_id", "archive_id")):
            raise ValueError("Directory is not a recognized archive")
        # Metadata may retain an ID after the history entry has been removed.
        if metadata.get("task_id"):
            try:
                metadata_id = UUID(str(metadata["task_id"]))
                if get_task_store().get(metadata_id) is None:
                    task_ids.append(str(metadata_id))
            except ValueError:
                pass
        archive_ids = [
            r[0]
            for r in conn.execute(
                "SELECT archive_id FROM archive_sync_index WHERE archive_path IN (?, ?) AND deleted = 0",
                (str(target), target.relative_to(get_workspace_paths().root).as_posix()),
            ).fetchall()
        ]
        entry = dict(
            original_path=str(target),
            staging_name=uuid4().hex,
            task_ids=json.dumps(sorted(set(task_ids))),
            archive_ids=json.dumps(archive_ids),
        )
        with _db_lock, conn:
            conn.execute(
                "INSERT INTO archive_deletions VALUES "
                "(:original_path, :staging_name, :task_ids, :archive_ids)",
                entry,
            )
        return entry

    def _finish(self, entry: dict, root: Path, sync) -> dict:
        target, staged = self._stage(entry, root)
        task_ids = json.loads(entry["task_ids"])
        # A task may have both an intermediate output and a final archive.
        # Move every registered output before removing that task's records.
        for peer in _get_conn().execute("SELECT * FROM archive_deletions").fetchall():
            if set(task_ids).intersection(json.loads(peer["task_ids"])):
                self._stage(dict(peer), root)
        for task_id in task_ids:
            self._clear_references(root, task_id)
        for archive_id in json.loads(entry["archive_ids"]):
            sync._record_delete(archive_id)
        for task_id in task_ids:
            get_task_store().delete(UUID(task_id))
        if staged.exists():
            shutil.rmtree(staged)
        conn = _get_conn()
        with _db_lock, conn:
            conn.execute("DELETE FROM archive_deletions WHERE original_path = ?", (str(target),))
        return {"message": "Deleted", "path": str(target), "task_deleted": bool(task_ids)}

    def _stage(self, entry: dict, root: Path) -> tuple[Path, Path]:
        target = archive_directory(entry["original_path"], root)
        name = entry["staging_name"]
        if len(name) != 32 or any(c not in "0123456789abcdef" for c in name):
            raise ValueError("Invalid deletion staging name")
        staged = managed_child(get_workspace_paths(root).temporary("deleting") / name, root)
        if target.exists():
            if staged.exists():
                raise FileExistsError("Both archive and deletion staging directory exist")
            if any(
                t.status in ACTIVE_STATUSES and task_uses_directory(t, target)
                for t in get_task_store().list(limit=-1)
            ):
                raise ArchiveBusyError("Archive directory is used by an active task")
            staged.parent.mkdir(parents=True, exist_ok=True)
            target.rename(staged)
        return target, staged

    @uses_workspace
    def delete_task(self, task_id: UUID) -> dict | None:
        from app.core.archive_sync import get_archive_sync_service

        root = Path(get_runtime_settings().data_root).resolve()
        sync = get_archive_sync_service()
        with sync._reconcile_lock:
            task = get_task_store().get(task_id)
            if task is None:
                entries = [
                    dict(row)
                    for row in _get_conn().execute("SELECT * FROM archive_deletions").fetchall()
                    if str(task_id) in json.loads(row["task_ids"])
                ]
                if not entries:
                    return None
                for entry in entries:
                    self._finish(entry, root, sync)
                return {
                    "status": "deleted",
                    "errors": [],
                    "deleted_paths": [e["original_path"] for e in entries],
                }
            if task.status in ACTIVE_STATUSES:
                raise ArchiveBusyError("Stop the task before deleting its outputs")
            result = task.result or {}
            archive = result.get("archive") or {}
            values = [result.get("output_dir"), archive.get("output_dir")]
            targets = list(
                dict.fromkeys(archive_directory(value, root) for value in values if value)
            )
            entries = []
            for target in targets:
                pending = (
                    _get_conn()
                    .execute(
                        "SELECT * FROM archive_deletions WHERE original_path = ?", (str(target),)
                    )
                    .fetchone()
                )
                if pending:
                    entries.append(dict(pending))
                elif target.exists():
                    entries.append(self._prepare(target))
            for entry in entries:
                self._stage(entry, root)
            for entry in entries:
                self._finish(entry, root, sync)
            self._clear_references(root, str(task_id))
            get_task_store().delete(task_id)
            return {
                "status": "deleted",
                "deleted_paths": [e["original_path"] for e in entries],
                "errors": [],
            }

    @staticmethod
    def _clear_references(root: Path, task_id: str) -> None:
        if get_workspace_paths(root).kb_db.exists():
            from app.services.kb.store import get_kb_store

            get_kb_store().delete_task(task_id)
        if (get_workspace_paths(root).voiceprints / "library.db").exists():
            from app.services.voiceprint.store import get_voiceprint_store

            get_voiceprint_store().detach_task(task_id)

    @uses_workspace
    def recover(self) -> list[dict]:
        """Retry durable deletions; leave failed entries for a later attempt."""
        from app.core.archive_sync import get_archive_sync_service

        root = Path(get_runtime_settings().data_root).resolve()
        sync = get_archive_sync_service()
        failures = []
        with sync._reconcile_lock:
            entries = _get_conn().execute("SELECT * FROM archive_deletions").fetchall()
            for entry in entries:
                try:
                    self._finish(dict(entry), root, sync)
                except Exception as exc:
                    logger.warning(
                        "Archive deletion pending for %s: %s", entry["original_path"], exc
                    )
                    failures.append({"path": entry["original_path"], "error": str(exc)})
        return failures


_lifecycle = ArchiveLifecycle()


def get_archive_lifecycle() -> ArchiveLifecycle:
    return _lifecycle
