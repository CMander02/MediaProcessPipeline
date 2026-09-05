"""Revisioned archive index and safe non-media synchronization helpers."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import stat
import threading
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile

from app.core.workspace_lifecycle import uses_workspace

from app.core.database import _db_lock, _get_conn
from app.core.paths import iter_archive_directories, encode_workspace_paths, decode_workspace_paths
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

MAX_UPLOAD_BYTES = 10 * 1024 * 1024 * 1024
MAX_MEMBER_BYTES = 10 * 1024 * 1024 * 1024
MAX_EXTRACTED_BYTES = 20 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000
MAX_COMPRESSION_RATIO = 200
_COMPRESSION_RATIO_ALLOWANCE = 10 * 1024 * 1024
_MAX_STRUCTURED_JSON_BYTES = 16 * 1024 * 1024
_BOUNDED_JSON_FILENAMES = {"metadata.json", "analysis.json"}
_SKIP_ARCHIVE_SUFFIXES = {".part", ".tmp", ".zip"}
_WINDOWS_RESERVED = re.compile(
    r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?$", re.IGNORECASE
)


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

    def __init__(self) -> None:
        self._reconcile_lock = threading.Lock()
        self._dirty_lock = threading.Lock()
        self._dirty_paths: set[tuple[str, str]] = set()
        self._indexing = False

    def mark_changed(self, archive_dir: Path | str) -> None:
        """Collect artifact changes for the incremental archive index."""
        from app.core.paths import archive_directory
        try:
            directory = archive_directory(archive_dir, get_runtime_settings().data_root)
        except ValueError:
            return
        root = str(Path(get_runtime_settings().data_root).resolve())
        with self._dirty_lock:
            self._dirty_paths.add((root, str(directory)))

    def _take_pending(self) -> list[str]:
        root = str(Path(get_runtime_settings().data_root).resolve())
        with self._dirty_lock:
            pending = {entry for entry in self._dirty_paths if entry[0] == root}
            self._dirty_paths.difference_update(pending)
        return [path for _, path in sorted(pending)]

    @uses_workspace
    def flush_changes(self) -> int:
        """Publish only archive paths changed through application writes."""
        root = str(Path(get_runtime_settings().data_root).resolve())
        with self._dirty_lock:
            pending_here = any(entry[0] == root for entry in self._dirty_paths)
        if not pending_here:
            return self.current_revision()
        with self._reconcile_lock:
            pending = self._take_pending()
            for index, path in enumerate(pending):
                try:
                    self._index_directory(Path(path))
                except Exception:
                    for remaining in pending[index:]:
                        self.mark_changed(remaining)
                    raise
        return self.current_revision()

    def _index_directory(self, archive_dir: Path, task_map=None) -> str | None:
        from app.core.database import get_task_store

        service = get_archive_service()
        if task_map is None:
            owner = get_task_store().find_task_by_output_dir(archive_dir)
            task_map = {service._path_key(archive_dir): {
                "id": str(owner.id), "created_at": owner.created_at.isoformat(),
                "status": owner.status,
            }} if owner else {}
        item = service._archive_item(archive_dir, task_map, lite=True)
        root = Path(get_runtime_settings().data_root).resolve()
        if item is None:
            rows = _get_conn().execute(
                "SELECT archive_id FROM archive_sync_index WHERE archive_path IN (?,?) AND deleted=0",
                (str(archive_dir), archive_dir.relative_to(root).as_posix()),
            ).fetchall()
            for row in rows:
                self._record_delete(str(row["archive_id"]))
            return None
        archive_id = self._ensure_archive_id(archive_dir, item.get("task_id"))
        # Metadata edits retain the creation time first observed for standalone archives.
        if service._path_key(archive_dir) not in task_map:
            previous = _get_conn().execute(
                "SELECT snapshot FROM archive_sync_index WHERE archive_id=?", (archive_id,)
            ).fetchone()
            if previous:
                snapshot = json.loads(previous["snapshot"])
                item["created_at"] = snapshot["created_at"]
                item["date"] = snapshot["date"]
        # _ensure_archive_id may have just added the ID to metadata.
        item["metadata"]["archive_id"] = archive_id
        self._upsert_if_changed(archive_id, archive_dir, self._file_state(archive_dir), item)
        return archive_id

    @uses_workspace
    def reconcile(self) -> int:
        """Index current archives and create tombstones for removed archives."""
        with self._reconcile_lock:
            self._indexing = True
            try:
                self._take_pending()
                return self._reconcile()
            finally:
                self._indexing = False

    def _reconcile(self) -> int:
        data_root = Path(get_runtime_settings().data_root).resolve()
        data_root.mkdir(parents=True, exist_ok=True)
        archive_service = get_archive_service()
        task_map = archive_service._archive_task_map()
        current_ids: set[str] = set()

        for archive_dir in sorted(iter_archive_directories(data_root), key=lambda item: item.name.casefold()):
            archive_id = self._index_directory(archive_dir, task_map)
            if archive_id:
                current_ids.add(archive_id)

        conn = _get_conn()
        active_rows = conn.execute(
            "SELECT archive_id FROM archive_sync_index WHERE deleted = 0"
        ).fetchall()
        for row in active_rows:
            archive_id = str(row["archive_id"])
            if archive_id not in current_ids:
                self._record_delete(archive_id)
        with _db_lock, conn:
            conn.execute("UPDATE archive_sync_meta SET last_reconciled_at=? WHERE id=1",
                         (datetime.now().astimezone().isoformat(),))
        return self.current_revision()

    @uses_workspace
    def index_status(self) -> dict:
        row = _get_conn().execute(
            "SELECT current_revision,last_reconciled_at FROM archive_sync_meta WHERE id=1"
        ).fetchone()
        return {"workspace_id": str(Path(get_runtime_settings().data_root).resolve()),
                "revision": row["current_revision"], "last_reconciled_at": row["last_reconciled_at"],
                "indexing": self._indexing}

    @uses_workspace
    def list_page(self, *, page=1, page_size=28, search="", media="all", source="all", sort="created_desc") -> dict:
        from app.core.archive_index import query_page

        self.flush_changes()
        rows, total, page = query_page(_get_conn(), page=page, page_size=page_size,
                                      search=search, media=media, source=source, sort=sort)
        root = Path(get_runtime_settings().data_root).resolve()
        return {"archives": [self.decode_snapshot(json.loads(row["snapshot"]), root) for row in rows],
                "total": total, "page": page, "page_size": page_size, **self.index_status()}

    @uses_workspace
    def list_all(self) -> list[dict]:
        self.flush_changes()
        rows = _get_conn().execute(
            "SELECT snapshot FROM archive_sync_index WHERE deleted=0 ORDER BY created_at DESC, archive_id ASC"
        ).fetchall()
        root = Path(get_runtime_settings().data_root).resolve()
        return [self.decode_snapshot(json.loads(row["snapshot"]), root) for row in rows]

    @uses_workspace
    def current_revision(self) -> int:
        row = _get_conn().execute(
            "SELECT current_revision FROM archive_sync_meta WHERE id = 1"
        ).fetchone()
        return int(row["current_revision"]) if row else 0

    @uses_workspace
    def changes(self, cursor: int, limit: int) -> dict[str, Any]:
        self.flush_changes()
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
        root = Path(get_runtime_settings().data_root).resolve()
        for row in page:
            change: dict[str, Any] = {
                "revision": int(row["revision"]),
                "archive_id": str(row["archive_id"]),
                "operation": str(row["operation"]),
                "changed_at": str(row["changed_at"]),
            }
            if row["snapshot"]:
                change["archive"] = self.decode_snapshot(json.loads(row["snapshot"]), root)
            changes.append(change)
        next_cursor = int(page[-1]["revision"]) if page else cursor
        return {
            "changes": changes,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "server_revision": self.current_revision(),
        }

    @uses_workspace
    def manifest(self, archive_id: str) -> dict[str, Any] | None:
        record = self._active_record(archive_id)
        if not record:
            return None
        archive_dir = Path(record["archive_path"])
        with self._reconcile_lock:
            self._index_directory(archive_dir)
        record = self._active_record(archive_id)
        if not record:
            return None
        files = [entry.as_dict() for entry in self._iter_sync_files(archive_dir, with_hash=True)]
        return {
            "archive_id": archive_id,
            "revision": int(record["revision"]),
            "files": files,
            "total_size": sum(int(entry["size"]) for entry in files),
        }

    @uses_workspace
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

    @uses_workspace
    def rebuild(self) -> dict[str, int]:
        """Re-index current archives while preserving monotonic revision history."""
        conn = _get_conn()
        with _db_lock, conn:
            conn.execute("UPDATE archive_sync_index SET file_state='' WHERE deleted=0")
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
            existing_path = Path(get_runtime_settings().data_root).resolve() / str(existing["archive_path"])
            if existing_path.exists() and existing_path.resolve() != archive_dir.resolve():
                archive_id = str(uuid4())

        if metadata.get("archive_id") != archive_id:
            metadata["archive_id"] = archive_id
            from app.core.artifacts import get_artifact_store
            from app.core.database import get_task_store
            owner = get_task_store().find_task_by_output_dir(archive_dir)
            get_artifact_store().write(owner.id if owner else None, archive_dir, "metadata.json",
                                       json.dumps(metadata, indent=2, ensure_ascii=False), notify_index=False)
        return archive_id

    def _upsert_if_changed(
        self,
        archive_id: str,
        archive_dir: Path,
        file_state: str,
        item: dict[str, Any],
    ) -> None:
        conn = _get_conn()
        row = conn.execute(
            "SELECT archive_path, revision, file_state, snapshot, deleted "
            "FROM archive_sync_index WHERE archive_id = ?",
            (archive_id,),
        ).fetchone()
        root = Path(get_runtime_settings().data_root).resolve()
        archive_path = archive_dir.resolve().relative_to(root).as_posix()
        snapshot = dict(item)
        snapshot["archive_id"] = archive_id
        encoded, fields = encode_workspace_paths(snapshot, root)
        encoded["_path_fields"] = fields
        previous = json.loads(row["snapshot"]) if row else {}
        previous.pop("revision", None)
        if (
            row
            and not int(row["deleted"])
            and str(row["file_state"]) == file_state
            and previous == encoded
            and (root / str(row["archive_path"])).resolve() == archive_dir.resolve()
        ):
            return

        now = datetime.now().astimezone().isoformat()
        with _db_lock, conn:
            revision = self._next_revision_locked(conn)
            encoded["revision"] = revision
            snapshot_json = json.dumps(encoded, ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO archive_sync_index
                    (archive_id, archive_path, revision, fingerprint, file_state, snapshot, deleted, updated_at)
                VALUES (?, ?, ?, '', ?, ?, 0, ?)
                ON CONFLICT(archive_id) DO UPDATE SET
                    archive_path = excluded.archive_path,
                    revision = excluded.revision,
                    fingerprint = '',
                    file_state = excluded.file_state,
                    snapshot = excluded.snapshot,
                    deleted = 0,
                    updated_at = excluded.updated_at
                """,
                (archive_id, archive_path, revision, file_state, snapshot_json, now),
            )
            from app.core.archive_index import index_fields
            query_fields = index_fields(item)
            conn.execute(
                "UPDATE archive_sync_index SET " + ",".join(f"{key}=?" for key in query_fields)
                + " WHERE archive_id=?", (*query_fields.values(), archive_id),
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
        with _db_lock, conn:
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
    def decode_snapshot(snapshot: dict, root: Path | None = None) -> dict:
        fields = snapshot.pop("_path_fields", [])
        return decode_workspace_paths(snapshot, fields, root or Path(get_runtime_settings().data_root).resolve())

    @staticmethod
    def _active_record(archive_id: str):
        try:
            normalized_id = str(UUID(archive_id))
        except ValueError:
            return None
        row = _get_conn().execute(
            """
            SELECT archive_id, archive_path, revision, snapshot
            FROM archive_sync_index
            WHERE archive_id = ? AND deleted = 0
            """,
            (normalized_id,),
        ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["archive_path"] = str(Path(get_runtime_settings().data_root).resolve() / record["archive_path"])
        return record


    def _file_state(self, archive_dir: Path) -> str:
        files = []
        for entry in self._iter_sync_files(archive_dir, with_hash=False):
            target = archive_dir / Path(*PurePosixPath(entry.relative_path).parts)
            stat = target.stat()
            files.append([entry.relative_path, stat.st_size, stat.st_mtime_ns])
        return json.dumps(files, ensure_ascii=False, separators=(",", ":"))

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


async def stream_upload_to_path(
    upload: UploadFile,
    destination: Path,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> tuple[int, str]:
    """Stream an uploaded ZIP to disk with a hard size limit and checksum."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    digest = hashlib.sha256()
    try:
        with destination.open("wb") as output:
            while chunk := await upload.read(8 * 1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(413, "Archive upload exceeds the 10 GB limit")
                digest.update(chunk)
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return written, digest.hexdigest()


def _safe_member_path(raw_name: str) -> PurePosixPath:
    normalized = raw_name.replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or "\x00" in normalized
        or any(ord(char) < 32 for char in normalized)
    ):
        raise ValueError(f"unsafe archive member path: {raw_name!r}")
    path = PurePosixPath(normalized)
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe archive member path: {raw_name!r}")
    for part in path.parts:
        if ":" in part or part.endswith((" ", ".")) or _WINDOWS_RESERVED.match(part):
            raise ValueError(f"unsafe archive member path: {raw_name!r}")
    return path


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_IFMT(unix_mode) == stat.S_IFLNK


def safe_extract_zip(zip_path: Path, extraction_dir: Path) -> Path:
    """Validate and extract a portable archive without path traversal."""
    extraction_dir.mkdir(parents=True, exist_ok=False)
    extracted_total = 0
    seen: set[str] = set()
    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_ARCHIVE_MEMBERS:
                raise ValueError("archive has an invalid number of members")
            validated: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
            for info in infos:
                member_path = _safe_member_path(info.filename)
                collision_key = member_path.as_posix().casefold()
                if collision_key in seen:
                    raise ValueError(f"duplicate archive member: {info.filename!r}")
                seen.add(collision_key)
                if info.flag_bits & 0x1:
                    raise ValueError("encrypted ZIP members are unsupported")
                if _zip_member_is_symlink(info):
                    raise ValueError(f"symbolic links are unsupported: {info.filename!r}")
                if info.file_size < 0 or info.file_size > MAX_MEMBER_BYTES:
                    raise ValueError(f"archive member is too large: {info.filename!r}")
                if (
                    member_path.name.casefold() in _BOUNDED_JSON_FILENAMES
                    and info.file_size > _MAX_STRUCTURED_JSON_BYTES
                ):
                    raise ValueError(f"structured JSON member is too large: {info.filename!r}")
                extracted_total += info.file_size
                if extracted_total > MAX_EXTRACTED_BYTES:
                    raise ValueError("archive expands beyond the 20 GB limit")
                if (
                    not info.is_dir()
                    and info.file_size > _COMPRESSION_RATIO_ALLOWANCE
                    and info.file_size
                    > info.compress_size * MAX_COMPRESSION_RATIO + _COMPRESSION_RATIO_ALLOWANCE
                ):
                    raise ValueError(f"suspicious ZIP compression ratio: {info.filename!r}")
                validated.append((info, member_path))

            root = extraction_dir.resolve()
            actual_total = 0
            for info, member_path in validated:
                target = (root / Path(*member_path.parts)).resolve()
                target.relative_to(root)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                member_written = 0
                with archive.open(info, "r") as source, target.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        member_written += len(chunk)
                        actual_total += len(chunk)
                        if member_written > info.file_size or actual_total > MAX_EXTRACTED_BYTES:
                            raise ValueError("archive expanded beyond its declared size")
                        output.write(chunk)
                if member_written != info.file_size:
                    raise ValueError(f"archive member size mismatch: {info.filename!r}")

        if (extraction_dir / "metadata.json").is_file():
            return extraction_dir
        children = list(extraction_dir.iterdir())
        if (
            len(children) == 1
            and children[0].is_dir()
            and (children[0] / "metadata.json").is_file()
        ):
            return children[0]
        raise ValueError("archive must contain metadata.json at its root")
    except Exception:
        shutil.rmtree(extraction_dir, ignore_errors=True)
        raise


def publish_archive(extracted_root: Path, destination: Path, data_root: Path) -> None:
    """Atomically publish a synchronized archive under data_root."""
    root = data_root.resolve()
    target = destination.resolve()
    target.relative_to(root)
    if target == root:
        raise ValueError("archive destination cannot be data_root")
    backup = target.parent / f".{target.name}.sync-backup-{uuid4().hex}"
    moved_existing = False
    try:
        if target.exists():
            os.replace(target, backup)
            moved_existing = True
        os.replace(extracted_root, target)
    except Exception:
        if target.exists() and not moved_existing:
            shutil.rmtree(target, ignore_errors=True)
        if moved_existing and backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    else:
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)


def archive_file_manifest(archive_dir: Path) -> dict[str, str]:
    """Map portable relative file names to published absolute paths."""
    return {
        path.relative_to(archive_dir).as_posix(): str(path.resolve())
        for path in sorted(archive_dir.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def build_archive_zip(
    archive_dir: Path,
    destination: Path,
    *,
    include_media: bool = False,
) -> dict[str, Any]:
    """Create a portable archive ZIP and embed a checksum manifest."""
    archive_root = archive_dir.resolve()
    if not (archive_root / "metadata.json").is_file():
        raise ValueError("archive directory is missing metadata.json")
    candidates: list[tuple[Path, str]] = []
    for path in sorted(archive_root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"archive contains a symbolic link: {path}")
        if not path.is_file():
            continue
        relative = _safe_member_path(path.relative_to(archive_root).as_posix()).as_posix()
        if relative == "_sync_manifest.json" or path.suffix.casefold() in _SKIP_ARCHIVE_SUFFIXES:
            continue
        if not include_media and path.suffix.casefold() in _MEDIA_EXTENSIONS:
            continue
        candidates.append((path, relative))
    if len(candidates) + 1 > MAX_ARCHIVE_MEMBERS:
        raise ValueError("archive has too many files")

    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_files: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(
            destination,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            allowZip64=True,
        ) as archive:
            for source_path, relative in candidates:
                digest = hashlib.sha256()
                size = 0
                with source_path.open("rb") as source, archive.open(
                    relative, "w", force_zip64=True
                ) as output:
                    while chunk := source.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_MEMBER_BYTES:
                            raise ValueError(f"archive member is too large: {relative}")
                        digest.update(chunk)
                        output.write(chunk)
                manifest_files.append(
                    {"path": relative, "size": size, "sha256": digest.hexdigest()}
                )
            manifest = {
                "version": 1,
                "include_media": include_media,
                "files": manifest_files,
            }
            archive.writestr(
                "_sync_manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return {
        "include_media": include_media,
        "file_count": len(manifest_files),
        "files": manifest_files,
    }
