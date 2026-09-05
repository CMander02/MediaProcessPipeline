"""Media purpose records and explicit, per-archive space recovery."""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
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
            except ValueError:
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
        task.status in ACTIVE_STATUSES and task_uses_directory(task, directory) for task in tasks
    ):
        return "活动或暂停任务仍需使用媒体"
    owners = [task for task in tasks if task_uses_directory(task, directory)]
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
        path = Path(value)
        if not path.is_absolute():
            path = directory / path
        if _key(path) in removed:
            return None
    return value


def _legacy_playback(directory, metadata):
    raw = metadata.get("file_path")
    if not isinstance(raw, str) or not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = directory / path
    try:
        path = managed_child(path, directory)
    except ValueError:
        return None
    return path.relative_to(directory).as_posix() if path.is_file() else None


class MediaRetentionService:
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
                except (ValueError, OSError):
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
                except ValueError:
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
        from app.core.archive_sync import get_archive_sync_service
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
                removed = {_key(directory / item["path"]) for item in cleaned}
                metadata = _clear_removed_paths(
                    _read(directory / "metadata.json"), directory, removed
                )
                metadata["media_retention_policy"] = preview["policy"]
                metadata["media_removed"] = not any(_media_files(directory))
                media_path = metadata.get("file_path")
                if media_path:
                    candidate = Path(media_path)
                    if not candidate.is_absolute():
                        candidate = directory / candidate
                    if not candidate.exists():
                        metadata["file_path"] = None
                try:
                    get_artifact_store().write(
                        metadata.get("task_id"),
                        directory,
                        "metadata.json",
                        json.dumps(metadata, ensure_ascii=False, indent=2),
                    )
                except OSError as exc:
                    errors.append({"path": "metadata.json", "error": str(exc)})
                store = get_task_store()
                for task in store.list(limit=-1):
                    if task_uses_directory(task, directory):
                        result = _clear_removed_paths(task.result, directory, removed)
                        if result != task.result:
                            store.update_status(task.id, task.status, result=result)
                get_archive_sync_service().flush_changes()
            return {
                **self.preview(path, policy),
                "cleaned": cleaned,
                "errors": errors,
                "reclaimed_bytes": sum(item["bytes"] for item in cleaned),
            }


_service = MediaRetentionService()


def get_media_retention_service() -> MediaRetentionService:
    return _service
