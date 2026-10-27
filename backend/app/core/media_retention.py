"""Media purpose records and explicit, per-archive space recovery."""

from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from typing import Literal

from app.core.paths import (
    ACTIVE_STATUSES,
    archive_directory,
    is_directory_link,
    managed_child,
    task_uses_directory,
)
from app.core.workspace_lifecycle import uses_workspace

MediaPolicy = Literal["all", "playback", "text"]
MEDIA_SUFFIXES = {
    ".mp4",
    ".mkv",
    ".avi",
    ".webm",
    ".mov",
    ".m4v",
    ".mp3",
    ".wav",
    ".flac",
    ".m4a",
    ".ogg",
    ".aac",
    ".opus",
    ".wma",
    ".aiff",
}
MANIFEST = "media_assets.json"
INTERMEDIATE_ROLES = frozenset({"working", "separated", "segment"})
_lock = threading.RLock()
_uploads: dict[str, int] = {}


def _key(path):
    return os.path.normcase(str(Path(path).resolve()))


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return value if isinstance(value, dict) else {}


def record_media(
    directory: Path,
    path: str | Path | None,
    role: str,
    *,
    playback: bool = False,
    regenerate_from: str | Path | None = None,
) -> None:
    """Called by a producer after creating a file; records portable relative paths."""
    if not path:
        return
    from app.core.artifacts import get_artifact_store
    from app.core.settings import get_runtime_settings

    try:
        directory = archive_directory(directory, get_runtime_settings().data_root)
        target = managed_child(path, directory)
    except ValueError:
        return  # Temporary producer directories and external sources have their own lifecycle.
    if not target.is_file():
        return
    with _lock:
        manifest = _read(directory / MANIFEST)
        assets = manifest.setdefault("assets", {})
        relative = target.relative_to(directory).as_posix()
        previous = assets.get(relative, {})
        source = str(regenerate_from or "")
        if source and not source.startswith(("http://", "https://")):
            try:
                source = Path(source).resolve().relative_to(directory).as_posix()
            except (OSError, ValueError, RuntimeError):
                pass
        assets[relative] = {
            "role": role,
            "playback": playback or previous.get("playback", False),
            "regenerate_from": source,
        }
        get_artifact_store().write(
            None, directory, MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2)
        )


@contextmanager
def uploading_media(directory: Path):
    """Keep an archive stable while a remote upload reads its media."""
    key = _key(directory)
    with _lock:
        _uploads[key] = _uploads.get(key, 0) + 1
    try:
        yield
    finally:
        with _lock:
            _uploads[key] -= 1
            if not _uploads[key]:
                del _uploads[key]


def _protected_reason(directory, metadata, tasks):
    if _uploads.get(_key(directory)):
        return "归档正在上传"
    if any(
        task.status in ACTIVE_STATUSES and _task_uses_directory(task, directory) for task in tasks
    ):
        return "活动或暂停任务仍需使用媒体"
    owners = [task for task in tasks if _task_uses_directory(task, directory)]
    if any(str(task.status) != "completed" for task in owners):
        return "关联任务尚未完成"
    if metadata.get("status") != "completed":
        return "归档尚未完成"
    if not any(
        (directory / name).is_file() and (directory / name).stat().st_size > 0
        for name in ("transcript.srt", "transcript_polished.srt", "source.md", "summary.md")
    ):
        return "正文尚未持久化"
    return ""


def _media_files(directory):
    for parent, dirs, files in os.walk(directory, followlinks=False):
        # Samples are independently owned by the voiceprint library.
        dirs[:] = [
            name
            for name in dirs
            if name not in {"voiceprints", "samples", "speaker_samples"}
            and not is_directory_link(Path(parent, name))
        ]
        for name in files:
            path = Path(parent, name)
            if path.suffix.lower() in MEDIA_SUFFIXES:
                yield path


def _reference_path(value, directory, *, foreign_basename=False):
    """Interpret stored paths without treating a Windows path as a POSIX filename."""
    if not isinstance(value, str) or not value or "\0" in value:
        return None
    windows = PureWindowsPath(value)
    if os.name != "nt" and windows.is_absolute():
        # Migrated metadata may still name its original Windows archive. A
        # same-named local file is protected conservatively, never deletion proof.
        return directory / windows.name if foreign_basename and windows.name else None
    path = Path(value)
    return path if path.is_absolute() else directory / path


def _reference_key(value, directory, *, foreign_basename=False):
    path = _reference_path(value, directory, foreign_basename=foreign_basename)
    if path is None:
        return None
    try:
        return _key(path)
    except (OSError, ValueError, RuntimeError):
        return None  # Invalid/obsolete metadata does not authorize deleting anything.


def _task_uses_directory(task, directory):
    try:
        return task_uses_directory(task, directory)
    except (OSError, ValueError, RuntimeError):
        # A stale foreign task.source must not hide a valid output_dir owner.
        result = task.result or {}
        archive = result.get("archive")
        values = [result.get("output_dir"), task.source]
        if isinstance(archive, dict):
            values.append(archive.get("output_dir"))
        for value in values:
            key = _reference_key(value, Path.cwd())
            if key and (Path(key) == directory or directory in Path(key).parents):
                return True
        return False


def _clear_removed_paths(value, directory, removed, *, key=""):
    if isinstance(value, dict):
        return {
            name: _clear_removed_paths(
                item, directory, removed, key=name if key != "files" else "files"
            )
            for name, item in value.items()
        }
    if isinstance(value, list):
        return [_clear_removed_paths(item, directory, removed, key=key) for item in value]
    if isinstance(value, str) and (key.endswith(("_path", "_file")) or key == "files"):
        if _reference_key(value, directory) in removed:
            return None
    return value


def _legacy_playback(directory, metadata):
    raw = metadata.get("file_path")
    if not isinstance(raw, str) or not raw:
        return None
    path = _reference_path(raw, directory, foreign_basename=True)
    if path is None:
        return None
    try:
        path = managed_child(path, directory)
        return path.relative_to(directory).as_posix() if path.is_file() else None
    except (OSError, ValueError, RuntimeError):
        return None


def _source_paths(directory, metadata, tasks):
    """Original inputs remain protected even if an old manifest mislabels them."""
    values = [metadata.get("file_path"), metadata.get("source_url")]
    values.extend(task.source for task in tasks if _task_uses_directory(task, directory))
    protected = set()
    for value in values:
        if not isinstance(value, str) or not value or value.startswith(("http://", "https://")):
            continue
        key = _reference_key(value, directory, foreign_basename=True)
        if key:
            protected.add(key)
    return protected


def _has_preserved_source(media, directory, asset, assets, sources, removing=frozenset()):
    """Do not delete the last usable media if its original input disappeared."""
    candidates = list(sources)
    for relative, item in assets.items():
        if item.get("role") in {"source", "working"} or item.get("playback"):
            candidates.append(directory / relative)
    source = asset.get("regenerate_from")
    if isinstance(source, str) and source and not source.startswith(("http://", "https://")):
        source = _reference_path(source, directory)
        if source is not None:
            candidates.append(source)
    for candidate in candidates:
        candidate = Path(candidate)
        try:
            if (
                candidate.suffix.lower() not in MEDIA_SUFFIXES
                or _key(candidate) == _key(media)
                or _key(candidate) in removing
                or not candidate.is_file()
            ):
                continue
            try:
                relative = candidate.resolve().relative_to(directory).as_posix()
            except ValueError:
                relative = None
            if relative and assets.get(relative, {}).get("role") == "separated":
                continue
            if candidate.stat().st_size > 0:
                return True
        except (OSError, ValueError, RuntimeError):
            continue
    return False


def _sync_removed_paths(directory, cleaned, errors, *, policy=None):
    from app.core.archive_sync import get_archive_sync_service
    from app.core.artifacts import get_artifact_store
    from app.core.database import get_task_store

    removed = {_key(directory / item["path"]) for item in cleaned}
    metadata = _clear_removed_paths(_read(directory / "metadata.json"), directory, removed)
    if policy is not None:
        metadata["media_retention_policy"] = policy
    metadata["media_removed"] = not any(_media_files(directory))
    try:
        get_artifact_store().write(
            metadata.get("task_id"),
            directory,
            "metadata.json",
            json.dumps(metadata, ensure_ascii=False, indent=2),
        )
    except OSError as exc:
        errors.append({"path": "metadata.json", "error": str(exc)})
    manifest = _read(directory / MANIFEST)
    assets = manifest.get("assets", {})
    remaining = {
        name: asset
        for name, asset in assets.items()
        if _reference_key(name, directory) not in removed
    }
    if remaining != assets:
        manifest["assets"] = remaining
        try:
            get_artifact_store().write(
                metadata.get("task_id"),
                directory,
                MANIFEST,
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
        except OSError as exc:
            errors.append({"path": MANIFEST, "error": str(exc)})
    store = get_task_store()
    for task in store.list(limit=-1):
        if _task_uses_directory(task, directory):
            result = _clear_removed_paths(task.result, directory, removed)
            if result != task.result:
                store.update_status(task.id, task.status, result=result)
    get_archive_sync_service().flush_changes()


class MediaRetentionService:
    def cleanup_separated(self, path: str, *, dry_run: bool = True) -> dict:
        """Compatibility entry point for cleaning only separated audio."""
        return self.cleanup_intermediates(path, dry_run=dry_run, roles=frozenset({"separated"}))

    @uses_workspace
    def cleanup_intermediates(
        self, path: str, *, dry_run: bool = True, roles: frozenset[str] = INTERMEDIATE_ROLES,
    ) -> dict:
        """Remove completed working audio, UVR stems and recognition segments.

        The default preview also supports safe backfill of completed archives.
        Failed/paused tasks keep their media for checkpoint recovery.
        """
        from app.core.database import get_task_store

        with _lock:
            preview = self.preview(path, "playback")
            directory = Path(preview["path"])
            metadata = _read(directory / "metadata.json")
            tasks = get_task_store().list(limit=-1)
            assets = _read(directory / MANIFEST).get("assets", {})
            sources = _source_paths(directory, metadata, tasks)
            protected = preview["protected_reason"]
            if not any(
                (directory / name).is_file() and (directory / name).stat().st_size > 0
                for name in ("transcript.srt", "transcript_polished.srt")
            ):
                protected = protected or "转写结果尚未持久化"
            entries = []
            for item in preview["entries"]:
                item = dict(item)
                media = directory / item["path"]
                asset = assets.get(item["path"], {})
                known = item["role"] in INTERMEDIATE_ROLES & roles and media.suffix.lower() not in {
                    ".mp4",
                    ".mkv",
                    ".avi",
                    ".webm",
                    ".mov",
                    ".m4v",
                }
                reason = protected
                if not known:
                    reason = reason or "仅自动清理已确认的中间音频"
                if asset.get("playback") or _reference_key(item["path"], directory) in sources:
                    reason = reason or "保留播放文件和原始来源"
                try:
                    if media.is_symlink() or not managed_child(media, directory).is_file():
                        raise ValueError("linked or unavailable file")
                except (OSError, ValueError, RuntimeError):
                    reason = reason or "链接或无法访问的文件"
                item.update(delete=not reason, reason=reason or "完成后清理中间音频")
                entries.append(item)
            # Candidates must not justify deleting each other. A durable source
            # must remain after the entire batch (not just the first unlink).
            removing = {_key(directory / item["path"]) for item in entries if item["delete"]}
            for item in entries:
                if item["delete"] and not _has_preserved_source(
                    directory / item["path"], directory, assets.get(item["path"], {}),
                    assets, sources, removing,
                ):
                    item.update(delete=False, reason="原始媒体已缺失，保留现有音频")
            result = {
                "path": str(directory),
                "policy": "separated" if roles == {"separated"} else "intermediates",
                "entries": entries,
                "protected_reason": protected,
                "reclaimable_bytes": sum(item["bytes"] for item in entries if item["delete"]),
                "cleaned": [],
                "errors": [],
                "reclaimed_bytes": 0,
            }
            if dry_run:
                return result
            for item in entries:
                if not item["delete"]:
                    continue
                protected = _protected_reason(
                    directory, _read(directory / "metadata.json"), get_task_store().list(limit=-1)
                )
                if protected:
                    result["errors"].append({"path": item["path"], "error": protected})
                    break
                try:
                    managed_child(directory / item["path"], directory).unlink()
                    result["cleaned"].append(item)
                    result["reclaimed_bytes"] += item["bytes"]
                except (OSError, ValueError) as exc:
                    result["errors"].append({"path": item["path"], "error": str(exc)})
            if result["cleaned"]:
                _sync_removed_paths(directory, result["cleaned"], result["errors"])
            return result

    @uses_workspace
    def preview(self, path: str, policy: MediaPolicy | None = None) -> dict:
        from app.core.database import get_task_store
        from app.core.settings import get_runtime_settings

        settings = get_runtime_settings()
        policy = policy or settings.media_retention_policy
        if policy not in {"all", "playback", "text"}:
            raise ValueError("Unknown media retention policy")
        directory = archive_directory(path, settings.data_root)
        metadata = _read(directory / "metadata.json")
        assets = _read(directory / MANIFEST).get("assets", {})
        legacy_playback = _legacy_playback(directory, metadata)
        with _lock:
            protected = _protected_reason(directory, metadata, get_task_store().list(limit=-1))
            entries = []
            for media in _media_files(directory):
                relative = media.relative_to(directory).as_posix()
                asset = assets.get(relative, {})
                if not asset and relative == legacy_playback:
                    asset = {
                        "role": "source",
                        "playback": True,
                        "regenerate_from": metadata.get("source_url", ""),
                    }
                role = asset.get("role", "unknown")
                reason = protected
                try:
                    safe = managed_child(media, directory)
                    if media.is_symlink() or not safe.is_file():
                        raise ValueError("linked file")
                    size = safe.stat().st_size
                except (ValueError, OSError, RuntimeError):
                    entries.append(
                        {
                            "path": relative,
                            "role": role,
                            "bytes": 0,
                            "delete": False,
                            "reason": "链接或无法访问的文件",
                            "recovery": "",
                        }
                    )
                    continue
                if role == "unknown":
                    reason = reason or "用途待确认，保留文件"
                elif role not in {"source", "working", "separated", "segment"}:
                    reason = reason or "独立样本或受保护文件"
                if policy == "all":
                    reason = reason or "保留全部媒体"
                elif policy == "playback" and (asset.get("playback") or role == "source"):
                    reason = reason or "保留播放文件和原始来源"
                source = asset.get("regenerate_from")
                recovery = f"重新生成依赖：{source}" if source else "恢复需要原始来源或备份"
                entries.append(
                    {
                        "path": relative,
                        "role": role,
                        "bytes": size,
                        "delete": not reason,
                        "reason": reason or "按所选策略清理",
                        "recovery": recovery,
                        "impact": "删除后无法播放此文件；重新处理需要来源"
                        if asset.get("playback")
                        else "重新运行相关阶段需要重新生成此文件",
                    }
                )
            source = metadata.get("source_url")
            if isinstance(source, str) and Path(source).is_absolute():
                try:
                    Path(source).resolve().relative_to(directory)
                except (OSError, ValueError, RuntimeError):
                    entries.append(
                        {
                            "path": source,
                            "role": "external_source",
                            "bytes": 0,
                            "delete": False,
                            "reason": "外部源文件保留",
                            "recovery": "用户管理的原始文件",
                        }
                    )
            return {
                "path": str(directory),
                "policy": policy,
                "entries": entries,
                "reclaimable_bytes": sum(item["bytes"] for item in entries if item["delete"]),
                "protected_reason": protected,
            }

    @uses_workspace
    def apply(self, path: str, policy: MediaPolicy | None = None, *, files: list[str]) -> dict:
        from app.core.artifacts import get_artifact_store
        from app.core.database import get_task_store

        with _lock:
            preview = self.preview(path, policy)
            directory = Path(preview["path"])
            selected = set(files)
            cleaned, errors = [], []
            candidates = [
                item for item in preview["entries"] if item["delete"] and item["path"] in selected
            ]
            if candidates:
                metadata = _read(directory / "metadata.json")
                metadata["media_retention_policy"] = preview["policy"]
                metadata["media_retention_applied"] = True
                get_artifact_store().write(
                    metadata.get("task_id"),
                    directory,
                    "metadata.json",
                    json.dumps(metadata, ensure_ascii=False, indent=2),
                )
            for item in preview["entries"]:
                if not item["delete"] or item["path"] not in selected:
                    continue
                protected = _protected_reason(
                    directory, _read(directory / "metadata.json"), get_task_store().list(limit=-1)
                )
                if protected:
                    errors.append({"path": item["path"], "error": protected})
                    break
                try:
                    target = managed_child(directory / item["path"], directory)
                    target.unlink()
                    cleaned.append(item)
                except (OSError, ValueError) as exc:
                    errors.append({"path": item["path"], "error": str(exc)})
            if cleaned:
                _sync_removed_paths(directory, cleaned, errors, policy=preview["policy"])
            return {
                **self.preview(path, policy),
                "cleaned": cleaned,
                "errors": errors,
                "reclaimed_bytes": sum(item["bytes"] for item in cleaned),
            }


_service = MediaRetentionService()


def get_media_retention_service() -> MediaRetentionService:
    return _service


def cleanup_completed_task(task) -> None:
    """Space recovery is best-effort and must not undo a successful pipeline."""
    from app.core.database import get_task_store
    from app.core.logging_setup import log_event
    from app.models import TaskStatus, TaskType

    if (
        task is None
        or task.status != TaskStatus.COMPLETED
        or task.task_type != TaskType.PIPELINE
        or not (task.result or {}).get("output_dir")
    ):
        return
    logger = logging.getLogger(__name__)

    try:
        result = get_media_retention_service().cleanup_intermediates(
            task.result["output_dir"], dry_run=False
        )
        if result["cleaned"] or result["errors"]:
            log_event(
                logger,
                logging.WARNING if result["errors"] else logging.INFO,
                "task.intermediate_audio_cleanup",
                removed_files=[item["path"] for item in result["cleaned"]],
                reclaimed_bytes=result["reclaimed_bytes"],
                errors=result["errors"],
            )
        # Cleanup updates persisted path references; do not expose stale paths.
        saved = get_task_store().get(task.id)
        if saved is not None:
            task.result = saved.result
    except Exception:
        logger.warning("Completed task intermediate cleanup failed: %s", task.id, exc_info=True)
