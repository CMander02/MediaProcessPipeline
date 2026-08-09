"""Revisioned archive index and safe non-media synchronization helpers."""

from __future__ import annotations

import hashlib
import json
import mimetypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from uuid import UUID, uuid4

from app.core.database import _db_lock, _get_conn
from app.core.settings import get_runtime_settings
from app.services.archiving.archive import get_archive_service

_MEDIA_EXTENSIONS = {
    ".3gp", ".aac", ".aiff", ".avi", ".flac", ".flv", ".m4a", ".m4v",
    ".mkv", ".mov", ".mp3", ".mp4", ".mpeg", ".mpg", ".oga", ".ogg",
    ".opus", ".wav", ".webm", ".wma", ".wmv",
}
_ROOT_TEXT_EXTENSIONS = {".json", ".md", ".srt", ".txt"}
_IMAGE_EXTENSIONS = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
_ROOT_IMAGE_STEMS = {"cover", "thumbnail"}
_TEXT_DIRECTORIES = {"descriptions"}
_IMAGE_DIRECTORIES = {"images"}
_IGNORED_NAMES = {".lock", "lock", "tmp", "temp"}


@dataclass(frozen=True)
class SyncFile:
    relative_path: str
    size: int
    mime: str
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "size": self.size,
            "mime": self.mime,
            "sha256": self.sha256,
        }


class ArchiveSyncService:
    """Build and persist a server-owned, monotonically revisioned archive view."""

    def reconcile(self) -> int:
        """Index current archives and create tombstones for removed archives."""
        data_root = Path(get_runtime_settings().data_root).resolve()
        data_root.mkdir(parents=True, exist_ok=True)
        archive_service = get_archive_service()
        task_map = archive_service._archive_task_map()
        current_ids: set[str] = set()

        for archive_dir in sorted(data_root.iterdir(), key=lambda item: item.name.casefold()):
            item = archive_service._archive_item(archive_dir, task_map, lite=True)
            if not item:
                continue
            archive_id = self._ensure_archive_id(archive_dir, item.get("task_id"))
            current_ids.add(archive_id)
            fingerprint = self._fingerprint(archive_dir)
            self._upsert_if_changed(archive_id, archive_dir, fingerprint, item)

        conn = _get_conn()
        active_rows = conn.execute(
            "SELECT archive_id FROM archive_sync_index WHERE deleted = 0"
        ).fetchall()
        for row in active_rows:
            archive_id = str(row["archive_id"])
            if archive_id not in current_ids:
                self._record_delete(archive_id)

        return self.current_revision()

    def current_revision(self) -> int:
        row = _get_conn().execute(
            "SELECT current_revision FROM archive_sync_meta WHERE id = 1"
        ).fetchone()
        return int(row["current_revision"]) if row else 0

    def changes(self, cursor: int, limit: int) -> dict[str, Any]:
        self.reconcile()
        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT revision, archive_id, operation, snapshot, changed_at
            FROM archive_sync_changes
            WHERE revision > ?
            ORDER BY revision ASC
            LIMIT ?
            """,
            (cursor, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        changes: list[dict[str, Any]] = []
        for row in page:
            change: dict[str, Any] = {
                "revision": int(row["revision"]),
                "archive_id": str(row["archive_id"]),
                "operation": str(row["operation"]),
                "changed_at": str(row["changed_at"]),
            }
            if row["snapshot"]:
                change["archive"] = json.loads(row["snapshot"])
            changes.append(change)
        next_cursor = int(page[-1]["revision"]) if page else cursor
        return {
            "changes": changes,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "server_revision": self.current_revision(),
        }

    def manifest(self, archive_id: str) -> dict[str, Any] | None:
        self.reconcile()
        record = self._active_record(archive_id)
        if not record:
            return None
        archive_dir = Path(record["archive_path"])
        files = [entry.as_dict() for entry in self._iter_sync_files(archive_dir, with_hash=True)]
        return {
            "archive_id": archive_id,
            "revision": int(record["revision"]),
            "files": files,
            "total_size": sum(int(entry["size"]) for entry in files),
        }

    def resolve_declared_file(
        self,
        archive_id: str,
        relative_path: str,
    ) -> tuple[Path, SyncFile] | None:
        record = self._active_record(archive_id)
        if not record:
            return None
        normalized = self._normalize_relative_path(relative_path)
        if normalized is None:
            return None
        archive_dir = Path(record["archive_path"]).resolve()
        target = (archive_dir / Path(*PurePosixPath(normalized).parts)).resolve()
        try:
            target.relative_to(archive_dir)
        except ValueError:
            return None
        if not target.is_file() or target.is_symlink():
            return None
        allowed = {
            entry.relative_path: entry
            for entry in self._iter_sync_files(archive_dir, with_hash=True)
        }
        entry = allowed.get(normalized)
        return (target, entry) if entry else None

    def rebuild(self) -> dict[str, int]:
        """Re-index current archives while preserving monotonic revision history."""
        conn = _get_conn()
        with _db_lock:
            conn.execute("UPDATE archive_sync_index SET fingerprint = '' WHERE deleted = 0")
            conn.commit()
        revision = self.reconcile()
        count = conn.execute(
            "SELECT COUNT(*) FROM archive_sync_index WHERE deleted = 0"
        ).fetchone()[0]
        return {"archives": int(count), "revision": revision}

    def _ensure_archive_id(self, archive_dir: Path, task_id: object) -> str:
        metadata_path = archive_dir / "metadata.json"
        metadata: dict[str, Any] = {}
        if metadata_path.is_file():
            try:
                content = metadata_path.read_bytes()
            except OSError:
                content = b""
            for encoding in ("utf-8-sig", "gb18030"):
                try:
                    loaded = json.loads(content.decode(encoding))
                    if isinstance(loaded, dict):
                        metadata = loaded
                    break
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue

        candidates = (metadata.get("archive_id"), task_id, metadata.get("task_id"))
        archive_id: str | None = None
        for candidate in candidates:
            try:
                archive_id = str(UUID(str(candidate)))
                break
            except (TypeError, ValueError, AttributeError):
                continue
        if archive_id is None:
            archive_id = str(uuid4())

        existing = _get_conn().execute(
            "SELECT archive_path FROM archive_sync_index WHERE archive_id = ? AND deleted = 0",
            (archive_id,),
        ).fetchone()
        if existing:
            existing_path = Path(str(existing["archive_path"]))
            if existing_path.exists() and existing_path.resolve() != archive_dir.resolve():
                archive_id = str(uuid4())

        if metadata.get("archive_id") != archive_id:
            metadata["archive_id"] = archive_id
            metadata_path.write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        return archive_id

    def _upsert_if_changed(
        self,
        archive_id: str,
        archive_dir: Path,
        fingerprint: str,
        item: dict[str, Any],
    ) -> None:
        conn = _get_conn()
        row = conn.execute(
            "SELECT archive_path, revision, fingerprint, deleted "
            "FROM archive_sync_index WHERE archive_id = ?",
            (archive_id,),
        ).fetchone()
        archive_path = str(archive_dir.resolve())
        if (
            row
            and not int(row["deleted"])
            and str(row["fingerprint"]) == fingerprint
            and str(row["archive_path"]) == archive_path
        ):
            return

        now = datetime.now().astimezone().isoformat()
        with _db_lock:
            revision = self._next_revision_locked(conn)
            snapshot = dict(item)
            snapshot["archive_id"] = archive_id
            snapshot["revision"] = revision
            snapshot_json = json.dumps(snapshot, ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO archive_sync_index
                    (archive_id, archive_path, revision, fingerprint, snapshot, deleted, updated_at)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(archive_id) DO UPDATE SET
                    archive_path = excluded.archive_path,
                    revision = excluded.revision,
                    fingerprint = excluded.fingerprint,
                    snapshot = excluded.snapshot,
                    deleted = 0,
                    updated_at = excluded.updated_at
                """,
                (archive_id, archive_path, revision, fingerprint, snapshot_json, now),
            )
            conn.execute(
                """
                INSERT INTO archive_sync_changes
                    (revision, archive_id, operation, snapshot, changed_at)
                VALUES (?, ?, 'upsert', ?, ?)
                """,
                (revision, archive_id, snapshot_json, now),
            )
            conn.commit()

    def _record_delete(self, archive_id: str) -> None:
        conn = _get_conn()
        row = conn.execute(
            "SELECT deleted FROM archive_sync_index WHERE archive_id = ?",
            (archive_id,),
        ).fetchone()
        if not row or int(row["deleted"]):
            return
        now = datetime.now().astimezone().isoformat()
        with _db_lock:
            revision = self._next_revision_locked(conn)
            conn.execute(
                """
                UPDATE archive_sync_index
                SET revision = ?, deleted = 1, updated_at = ?
                WHERE archive_id = ?
                """,
                (revision, now, archive_id),
            )
            conn.execute(
                """
                INSERT INTO archive_sync_changes
                    (revision, archive_id, operation, snapshot, changed_at)
                VALUES (?, ?, 'delete', NULL, ?)
                """,
                (revision, archive_id, now),
            )
            conn.commit()

    @staticmethod
    def _next_revision_locked(conn) -> int:
        row = conn.execute(
            "SELECT current_revision FROM archive_sync_meta WHERE id = 1"
        ).fetchone()
        revision = int(row["current_revision"]) + 1
        conn.execute(
            "UPDATE archive_sync_meta SET current_revision = ? WHERE id = 1",
            (revision,),
        )
        return revision

    @staticmethod
    def _active_record(archive_id: str):
        try:
            normalized_id = str(UUID(archive_id))
        except ValueError:
            return None
        return _get_conn().execute(
            """
            SELECT archive_id, archive_path, revision, snapshot
            FROM archive_sync_index
            WHERE archive_id = ? AND deleted = 0
            """,
            (normalized_id,),
        ).fetchone()

    def _fingerprint(self, archive_dir: Path) -> str:
        digest = hashlib.sha256()
        for entry in self._iter_sync_files(archive_dir, with_hash=False):
            target = archive_dir / Path(*PurePosixPath(entry.relative_path).parts)
            stat = target.stat()
            digest.update(entry.relative_path.encode("utf-8"))
            digest.update(str(stat.st_size).encode("ascii"))
            digest.update(str(stat.st_mtime_ns).encode("ascii"))
        return digest.hexdigest()

    def _iter_sync_files(self, archive_dir: Path, *, with_hash: bool) -> Iterable[SyncFile]:
        root = archive_dir.resolve()
        candidates = sorted(
            (path for path in root.rglob("*") if path.is_file()),
            key=lambda path: path.as_posix().casefold(),
        )
        for path in candidates:
            if path.is_symlink():
                continue
            try:
                relative = path.relative_to(root)
                resolved = path.resolve()
                resolved.relative_to(root)
            except ValueError:
                continue
            relative_posix = relative.as_posix()
            if not self._is_allowed(relative):
                continue
            sha256 = self._sha256(resolved) if with_hash else ""
            mime = mimetypes.guess_type(relative_posix)[0] or "application/octet-stream"
            yield SyncFile(relative_posix, resolved.stat().st_size, mime, sha256)

    @staticmethod
    def _is_allowed(relative: Path) -> bool:
        parts = relative.parts
        if not parts or any(part.startswith(".") for part in parts):
            return False
        if any(part.casefold() in _IGNORED_NAMES for part in parts):
            return False
        suffix = relative.suffix.casefold()
        if suffix in _MEDIA_EXTENSIONS:
            return False
        if len(parts) == 1:
            if suffix in _ROOT_TEXT_EXTENSIONS:
                return True
            return suffix in _IMAGE_EXTENSIONS and relative.stem.casefold() in _ROOT_IMAGE_STEMS
        parent = parts[0].casefold()
        if parent in _IMAGE_DIRECTORIES:
            return suffix in _IMAGE_EXTENSIONS
        if parent in _TEXT_DIRECTORIES:
            return suffix in _ROOT_TEXT_EXTENSIONS
        return False

    @staticmethod
    def _normalize_relative_path(relative_path: str) -> str | None:
        raw = relative_path.replace("\\", "/")
        pure = PurePosixPath(raw)
        if not raw or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            return None
        return pure.as_posix()

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


_sync_service: ArchiveSyncService | None = None


def get_archive_sync_service() -> ArchiveSyncService:
    global _sync_service
    if _sync_service is None:
        _sync_service = ArchiveSyncService()
    return _sync_service
