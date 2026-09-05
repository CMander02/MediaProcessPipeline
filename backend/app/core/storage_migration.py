"""Explicit, resumable migration of a library to the separated storage layout."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from app.core.atomic_file import atomic_write_text
from app.core.paths import (
    LAYOUT_FILE,
    MIGRATION_FILE,
    WorkspacePaths,
    decode_workspace_paths,
    encode_workspace_paths,
    get_workspace_paths,
    is_absolute_file_path,
    is_directory_link,
    iter_archive_directories,
    managed_child,
    reset_workspace_paths,
)
from app.core.workspace_lifecycle import workspace_change


@contextmanager
def _database(path: Path, *, readonly: bool = False):
    location = f"file:{quote(path.as_posix(), safe='/:')}?mode=ro" if readonly else str(path)
    conn = sqlite3.connect(location, uri=readonly)
    conn.row_factory = sqlite3.Row
    try:
        virtual = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE sql LIKE '%USING vec0%' LIMIT 1"
        ).fetchone()
        if virtual:
            import sqlite_vec

            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        yield conn
    finally:
        conn.close()


def _counts(conn) -> dict[str, int]:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE sql LIKE '%USING vec0%' LIMIT 1").fetchone():
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    available = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    names = {
        "tasks",
        "task_artifacts",
        "task_events",
        "kb_chunks",
        "persons",
        "sample_meta",
        "task_speaker_map",
        "archive_sync_index",
        "archive_sync_changes",
        "vec_chunks",
        "voiceprint_samples",
    } & available
    return {
        name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in sorted(names)
    }


def _inventory(path: Path, *, skip_databases: bool = False) -> dict[str, int]:
    """Measure ordinary files without following Windows junctions or symlinks."""
    if not path.exists():
        return {}
    if is_directory_link(path):
        raise ValueError(f"Linked migration source: {path}")
    if path.is_file():
        return {"": path.stat().st_size}
    files = {}
    for directory, dirs, names in os.walk(path, followlinks=False):
        for name in dirs + names:
            candidate = Path(directory) / name
            if is_directory_link(candidate):
                raise ValueError(f"Linked migration source: {candidate}")
        for name in names:
            # SQLite is copied through its backup API, including committed WAL pages.
            if skip_databases and name.endswith((".db", ".db-wal", ".db-shm")):
                continue
            candidate = Path(directory) / name
            files[candidate.relative_to(path).as_posix()] = candidate.stat().st_size
    return files


class StorageMigration:
    def __init__(
        self,
        source: Path | str,
        target: Path | str | None = None,
        original_root: Path | str | None = None,
    ):
        self.source = Path(source).resolve()
        saved_path = self.source / MIGRATION_FILE
        if target is None and saved_path.exists():
            saved = json.loads(saved_path.read_text(encoding="utf-8"))
            if saved.get("status") not in {"complete", "rolled_back"}:
                target = saved["target"]
        self.target = Path(target).resolve() if target is not None else self.source
        self.original_root = Path(original_root).resolve() if original_root else self.source
        if self.target != self.source and (
            self.source in self.target.parents or self.target in self.source.parents
        ):
            raise ValueError("Migration roots must be separate directories")
        self.journal_path = self.source / MIGRATION_FILE

    def _journal(self) -> dict | None:
        if not self.journal_path.exists():
            return None
        journal = json.loads(self.journal_path.read_text(encoding="utf-8"))
        if Path(journal["source"]) != self.source or Path(journal["target"]) != self.target:
            if journal["status"] in {"complete", "rolled_back"}:
                return None
            raise ValueError("The existing migration journal names a different destination")
        return journal

    def _save(self, journal: dict) -> None:
        atomic_write_text(self.journal_path, json.dumps(journal, ensure_ascii=False, indent=2))
        if journal.get("backup"):
            atomic_write_text(
                Path(journal["backup"]) / "migration.json",
                json.dumps(journal, ensure_ascii=False, indent=2),
            )
        if self.target != self.source:
            target_record = dict(journal)
            if journal["status"] == "rolled_back":
                target_record["status"] = "incomplete_copy"
            atomic_write_text(
                self.target / MIGRATION_FILE,
                json.dumps(target_record, ensure_ascii=False, indent=2),
            )
        reset_workspace_paths()

    def preview(self) -> dict:
        pending = self._journal()
        if pending and pending["status"] not in {"complete", "rolled_back"}:
            return pending
        paths = get_workspace_paths(self.source)
        if paths.version == 2 and self.target == self.source:
            return {
                "source": str(self.source),
                "target": str(self.target),
                "status": "complete",
                "operations": [],
                "conflicts": [],
                "external_sources": [],
            }
        target_paths = WorkspacePaths(self.target, 2)
        operations = []
        conflicts = []
        destinations: set[Path] = set()

        def add(source: Path, target: Path, kind: str, archive_id=None):
            if not source.exists() or source == target:
                return
            source = managed_child(source, self.source)
            target = managed_child(target, self.target)
            try:
                inventory = _inventory(source, skip_databases=kind == "state")
            except ValueError as exc:
                conflicts.append(str(exc))
                return
            if target.exists() or target in destinations:
                conflicts.append(f"Destination exists: {target}")
            destinations.add(target)
            operations.append(
                {
                    "source": source.relative_to(self.source).as_posix(),
                    "target": target.relative_to(self.target).as_posix(),
                    "kind": kind,
                    "archive_id": archive_id,
                    "inventory": inventory,
                    "skip_databases": kind == "state",
                    "done": False,
                }
            )

        if (self.source / "archives" / "metadata.json").exists():
            conflicts.append(
                "A legacy archive named 'archives' conflicts with the layout directory"
            )
        for parent in (self.source, self.source / "archives"):
            if parent.is_dir():
                for item in parent.iterdir():
                    if is_directory_link(item):
                        conflicts.append(f"Linked migration source: {item}")
        for archive in iter_archive_directories(self.source):
            if not any(
                (archive / name).exists()
                for name in ("metadata.json", "summary.md", "transcript.srt", "source.md")
            ):
                continue
            metadata = {}
            if (archive / "metadata.json").exists():
                try:
                    metadata = json.loads(
                        (archive / "metadata.json").read_text(encoding="utf-8-sig")
                    )
                    if not isinstance(metadata, dict):
                        raise ValueError("metadata must be an object")
                except (ValueError, UnicodeError) as exc:
                    conflicts.append(f"Invalid metadata: {archive}: {exc}")
                    continue
            archive_id = metadata.get("archive_id") or metadata.get("task_id")
            add(archive, target_paths.archives / archive.name, "archive", archive_id)
        for name in ("voiceprints", "auth"):
            add(getattr(paths, name), getattr(target_paths, name), "state")
        for owner in (
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
        ):
            add(paths.temporary(owner), target_paths.temporary(owner), "temporary")
        add(paths.logs, target_paths.logs, "logs")
        add(paths.state / ".mpp-daemon.json", target_paths.state / ".mpp-daemon.json", "state")

        databases = []
        for name, source, target in (
            ("tasks", paths.tasks_db, target_paths.tasks_db),
            ("kb", paths.kb_db, target_paths.kb_db),
            (
                "voiceprints",
                paths.voiceprints / "library.db",
                target_paths.voiceprints / "library.db",
            ),
        ):
            if source.exists():
                with _database(source, readonly=True) as conn:
                    counts = _counts(conn)
                    if name == "tasks" and "tasks" in counts:
                        if conn.execute(
                            "SELECT 1 FROM tasks WHERE status IN "
                            "('pending','queued','processing','paused') LIMIT 1"
                        ).fetchone():
                            conflicts.append("Active tasks must be stopped before migration")
                        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
                        if (
                            "archive_deletions" in tables
                            and conn.execute("SELECT 1 FROM archive_deletions LIMIT 1").fetchone()
                        ):
                            conflicts.append("Finish pending archive deletions before migration")
                databases.append(
                    {
                        "name": name,
                        "source": source.relative_to(self.source).as_posix(),
                        "target": target.relative_to(self.target).as_posix(),
                        "counts": counts,
                        "bytes": source.stat().st_size,
                    }
                )
                if target.exists() and source != target:
                    conflicts.append(f"Database destination exists: {target}")

        external_sources = []
        if paths.tasks_db.exists():
            with _database(paths.tasks_db, readonly=True) as conn:
                if "tasks" in _counts(conn):
                    for row in conn.execute("SELECT source FROM tasks"):
                        value = row[0]
                        if is_absolute_file_path(value):
                            try:
                                Path(value).relative_to(self.original_root)
                            except ValueError:
                                external_sources.append(value)
        covered = {Path(op["source"]).parts[0] for op in operations}
        excluded = covered | {
            "archives",
            "state",
            "tmp",
            "backups",
            "tasks.db",
            "kb.db",
            LAYOUT_FILE,
            MIGRATION_FILE,
        }
        if self.target != self.source:
            for item in self.source.iterdir():
                if item.name not in excluded and not item.name.endswith((".db-wal", ".db-shm")):
                    add(item, self.target / item.name, "retained")
        return {
            "source": str(self.source),
            "target": str(self.target),
            "original_root": str(self.original_root),
            "source_version": paths.version,
            "status": "preview",
            "mode": "move" if self.source == self.target else "copy",
            "operations": operations,
            "databases": databases,
            "conflicts": conflicts,
            "files": sum(len(op["inventory"]) for op in operations) + len(databases),
            "bytes": sum(sum(op["inventory"].values()) for op in operations)
            + sum(entry["bytes"] for entry in databases),
            "external_sources": sorted(set(external_sources)),
        }

    def _backup(self, journal: dict) -> None:
        backup = (
            self.source / "backups" / f"layout-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
        )
        backup.mkdir(parents=True, exist_ok=False)
        journal["backup"] = str(backup)
        for entry in journal["databases"]:
            destination = backup / f"{entry['name']}.db"
            with _database(self.source / entry["source"], readonly=True) as source:
                with _database(destination) as target:
                    source.backup(target)
                    if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise ValueError("SQLite backup verification failed")
                    entry["counts"] = _counts(target)
            entry["backup"] = str(destination)
        if (self.source / LAYOUT_FILE).exists():
            shutil.copy2(self.source / LAYOUT_FILE, backup / LAYOUT_FILE)
        # Rewritten JSON remains available for rollback even after directory moves.
        for operation in journal["operations"]:
            if operation["kind"] == "archive":
                for filename in operation["inventory"]:
                    if filename.lower().endswith(".json"):
                        original = self.source / operation["source"] / filename
                        destination = backup / "json" / operation["source"] / filename
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(original, destination)

    def _move_or_copy(self, operation: dict, journal: dict) -> None:
        source = managed_child(self.source / operation["source"], self.source)
        target = managed_child(self.target / operation["target"], self.target)
        if not operation["done"]:
            target.parent.mkdir(parents=True, exist_ok=True)
            if journal["mode"] == "move":
                if source.exists():
                    if target.exists():
                        raise FileExistsError(str(target))
                    source.rename(target)
            elif source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                shutil.copy2(source, target)
            if (
                _inventory(target, skip_databases=operation["skip_databases"])
                != operation["inventory"]
            ):
                raise ValueError(f"Migration file count or size mismatch: {target}")
            operation["done"] = True
            self._save(journal)

    def _map_value(self, value, journal: dict):
        if isinstance(value, dict):
            return {key: self._map_value(item, journal) for key, item in value.items()}
        if isinstance(value, list):
            return [self._map_value(item, journal) for item in value]
        if isinstance(value, str) and is_absolute_file_path(value):
            path = Path(value)
            try:
                relative = path.relative_to(Path(journal["original_root"]))
            except ValueError:
                return value
            for operation in sorted(
                journal["operations"], key=lambda op: len(op["source"]), reverse=True
            ):
                try:
                    suffix = relative.relative_to(operation["source"])
                    return str(self.target / operation["target"] / suffix)
                except ValueError:
                    continue
            return str(self.target / relative)
        return value

    def _rewrite_databases(self, journal: dict) -> None:
        from app.core.database import SCHEMA, _apply_migrations

        for entry in journal["databases"]:
            target = managed_child(self.target / entry["target"], self.target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry["backup"], target)
            for suffix in ("-wal", "-shm"):
                Path(str(target) + suffix).unlink(missing_ok=True)
            with _database(target) as conn:
                if entry["name"] == "tasks":
                    conn.executescript(SCHEMA)
                    _apply_migrations(conn)
                    for row in conn.execute("SELECT * FROM tasks").fetchall():
                        payload = {
                            key: json.loads(row[key]) if row[key] else None
                            for key in ("options", "result", "flow")
                        }
                        payload["source"] = row["source"]
                        payload = decode_workspace_paths(
                            payload, json.loads(row["path_fields"]), Path(journal["original_root"])
                        )
                        payload = self._map_value(payload, journal)
                        encoded, fields = encode_workspace_paths(payload, self.target)
                        conn.execute(
                            "UPDATE tasks SET source=?, options=?, result=?, flow=?, "
                            "path_fields=?, external_source=? WHERE id=?",
                            (
                                encoded["source"],
                                json.dumps(encoded["options"]),
                                json.dumps(encoded["result"]) if encoded["result"] else None,
                                json.dumps(encoded["flow"]) if encoded["flow"] else None,
                                json.dumps(fields),
                                int(
                                    is_absolute_file_path(payload["source"])
                                    and ["source"] not in fields
                                ),
                                row["id"],
                            ),
                        )
                    for table in ("archive_sync_index", "archive_sync_changes"):
                        key = "archive_id" if table.endswith("index") else "revision"
                        for row in conn.execute(f"SELECT * FROM {table}").fetchall():
                            if row["snapshot"]:
                                snapshot = json.loads(row["snapshot"])
                                fields = snapshot.pop("_path_fields", [])
                                snapshot = decode_workspace_paths(
                                    snapshot, fields, Path(journal["original_root"])
                                )
                                snapshot = self._map_value(snapshot, journal)
                                snapshot, fields = encode_workspace_paths(snapshot, self.target)
                                snapshot["_path_fields"] = fields
                                conn.execute(
                                    f"UPDATE {table} SET snapshot=? WHERE {key}=?",
                                    (json.dumps(snapshot, ensure_ascii=False), row[key]),
                                )
                            if table.endswith("index"):
                                old = str(Path(journal["original_root"]) / row["archive_path"])
                                new = Path(self._map_value(old, journal))
                                try:
                                    stored_path = new.relative_to(self.target).as_posix()
                                except ValueError:
                                    stored_path = str(new)
                                conn.execute(
                                    f"UPDATE {table} SET archive_path=? WHERE {key}=?",
                                    (stored_path, row[key]),
                                )
                    for row in conn.execute("SELECT id, data FROM task_events").fetchall():
                        conn.execute(
                            "UPDATE task_events SET data=? WHERE id=?",
                            (
                                json.dumps(
                                    self._map_value(json.loads(row["data"]), journal),
                                    ensure_ascii=False,
                                ),
                                row["id"],
                            ),
                        )
                    for row in conn.execute(
                        "SELECT task_id, filename, content FROM task_artifacts "
                        "WHERE filename LIKE '%.json'"
                    ).fetchall():
                        try:
                            payload = json.loads(row["content"])
                        except (ValueError, TypeError):
                            continue
                        conn.execute(
                            "UPDATE task_artifacts SET content=? WHERE task_id=? AND filename=?",
                            (
                                json.dumps(self._map_value(payload, journal), ensure_ascii=False),
                                row["task_id"],
                                row["filename"],
                            ),
                        )
                elif entry["name"] == "kb":
                    for row in conn.execute("SELECT id, archive_path FROM kb_chunks").fetchall():
                        old = str(Path(journal["original_root"]) / row["archive_path"])
                        path = Path(self._map_value(old, journal))
                        try:
                            value = path.relative_to(self.target).as_posix()
                        except ValueError:
                            value = str(path)
                        conn.execute(
                            "UPDATE kb_chunks SET archive_path=? WHERE id=?", (value, row["id"])
                        )
                elif entry["name"] == "voiceprints":
                    old_clips = (
                        WorkspacePaths(
                            Path(journal["original_root"]), journal["source_version"]
                        ).voiceprints
                        / "clips"
                    )
                    new_clips = WorkspacePaths(self.target, 2).voiceprints / "clips"
                    for row in conn.execute(
                        "SELECT sample_id, audio_clip_path FROM sample_meta"
                    ).fetchall():
                        if row["audio_clip_path"]:
                            path = Path(
                                self._map_value(str(old_clips / row["audio_clip_path"]), journal)
                            )
                            try:
                                value = path.relative_to(new_clips).as_posix()
                            except ValueError:
                                value = str(path)
                            conn.execute(
                                "UPDATE sample_meta SET audio_clip_path=? WHERE sample_id=?",
                                (value, row["sample_id"]),
                            )
                conn.commit()
                actual = _counts(conn)
                if any(actual.get(key, 0) != value for key, value in entry["counts"].items()):
                    raise ValueError(f"Database row counts changed: {entry['name']}")

    def _rewrite_json(self, journal: dict) -> None:
        tasks_entry = next(
            (entry for entry in journal["databases"] if entry["name"] == "tasks"), None
        )
        for operation in journal["operations"]:
            if operation["kind"] != "archive":
                continue
            directory = self.target / operation["target"]
            for filename in operation["inventory"]:
                if not filename.lower().endswith(".json"):
                    continue
                original = Path(journal["backup"]) / "json" / operation["source"] / filename
                try:
                    payload = json.loads(original.read_text(encoding="utf-8-sig"))
                except (ValueError, UnicodeError):
                    continue
                content = json.dumps(
                    self._map_value(payload, journal), ensure_ascii=False, indent=2
                )
                atomic_write_text(directory / filename, content)
            metadata_path = directory / "metadata.json"
            if tasks_entry and metadata_path.exists():
                payload = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
                task_id = payload.get("task_id") if isinstance(payload, dict) else None
                if task_id:
                    with _database(self.target / tasks_entry["target"]) as conn:
                        for row in conn.execute(
                            "SELECT filename FROM task_artifacts WHERE task_id=?", (str(task_id),)
                        ).fetchall():
                            artifact = managed_child(directory / row["filename"], directory)
                            if artifact.is_file():
                                conn.execute(
                                    "UPDATE task_artifacts SET content=? "
                                    "WHERE task_id=? AND filename=?",
                                    (
                                        artifact.read_text(encoding="utf-8"),
                                        str(task_id),
                                        row["filename"],
                                    ),
                                )
                        conn.commit()

    def apply(self) -> dict:
        from app.core.database import _database_root, close_db, reset_db_path
        from app.services.kb.store import reset_kb_store
        from app.services.voiceprint.store import reset_voiceprint_store

        previous_root = _database_root()
        with workspace_change(check_tasks=False):
            close_db()
            reset_kb_store()
            reset_voiceprint_store()
            journal = self.preview()
            if journal["status"] == "complete":
                return journal
            if journal["conflicts"]:
                raise ValueError("; ".join(journal["conflicts"]))
            if journal["status"] == "preview":
                self._backup(journal)
                journal["status"] = "moving"
                self._save(journal)
            try:
                for operation in journal["operations"]:
                    self._move_or_copy(operation, journal)
                journal["status"] = "rewriting"
                self._save(journal)
                self._rewrite_databases(journal)
                self._rewrite_json(journal)
                atomic_write_text(self.target / LAYOUT_FILE, '{"version": 2}\n')
                if self.source == self.target:
                    for entry in journal["databases"]:
                        old = self.source / entry["source"]
                        if old.exists() and entry["source"] != entry["target"]:
                            destination = Path(journal["backup"]) / "original" / entry["source"]
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            old.rename(destination)
                journal["status"] = "complete"
                self._save(journal)
                return journal
            finally:
                reset_db_path(previous_root)

    def rollback(self) -> dict:
        from app.core.database import _database_root, close_db, reset_db_path
        from app.services.kb.store import reset_kb_store
        from app.services.voiceprint.store import reset_voiceprint_store

        journal = self._journal()
        if journal is None or not journal.get("backup"):
            raise ValueError("No migration backup is available")
        if journal["status"] == "rolled_back":
            return journal
        previous_root = _database_root()
        with workspace_change(check_tasks=False):
            close_db()
            reset_kb_store()
            reset_voiceprint_store()
            if journal["mode"] == "move":
                if journal["status"] == "complete":
                    # Preserve edits made after migration before restoring the earlier snapshot.
                    retained = Path(journal["backup"]) / "before-rollback"
                    retained.mkdir(parents=True, exist_ok=True)
                    for entry in journal["databases"]:
                        with _database(self.target / entry["target"], readonly=True) as current:
                            with _database(retained / f"{entry['name']}.db") as saved:
                                current.backup(saved)
                    for operation in journal["operations"]:
                        if operation["kind"] == "archive":
                            for filename in operation["inventory"]:
                                current = self.target / operation["target"] / filename
                                if filename.lower().endswith(".json") and current.is_file():
                                    saved = retained / "json" / operation["source"] / filename
                                    saved.parent.mkdir(parents=True, exist_ok=True)
                                    shutil.copy2(current, saved)
                for operation in reversed(journal["operations"]):
                    source = managed_child(self.source / operation["source"], self.source)
                    target = managed_child(self.target / operation["target"], self.target)
                    if target.exists():
                        if source.exists():
                            raise FileExistsError(f"Rollback destination exists: {source}")
                        source.parent.mkdir(parents=True, exist_ok=True)
                        target.rename(source)
                    operation["done"] = False
                for entry in journal["databases"]:
                    migrated = managed_child(self.target / entry["target"], self.target)
                    if migrated.exists() and entry["source"] != entry["target"]:
                        retained = Path(journal["backup"]) / "rolled-back" / entry["target"]
                        retained.parent.mkdir(parents=True, exist_ok=True)
                        migrated.rename(retained)
                    destination = managed_child(self.source / entry["source"], self.source)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(entry["backup"], destination)
                    for suffix in ("-wal", "-shm"):
                        Path(str(destination) + suffix).unlink(missing_ok=True)
                for operation in journal["operations"]:
                    if operation["kind"] == "archive":
                        saved = Path(journal["backup"]) / "json" / operation["source"]
                        if saved.exists():
                            shutil.copytree(
                                saved, self.source / operation["source"], dirs_exist_ok=True
                            )
                atomic_write_text(
                    self.source / LAYOUT_FILE, json.dumps({"version": journal["source_version"]})
                )
            journal["status"] = "rolled_back"
            self._save(journal)
            reset_db_path(previous_root)
        return journal
