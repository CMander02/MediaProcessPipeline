"""Cleanup service for managing temporary files."""

import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.database import get_task_store
from app.core.paths import (
    ACTIVE_STATUSES,
    SYSTEM_DIRECTORIES,
    archive_directory,
    is_directory_link,
    managed_child,
    task_output_paths,
    task_uses_directory,
)
from app.core.settings import get_runtime_settings

logger = logging.getLogger(__name__)


class CleanupService:
    """Service for cleaning up temporary and orphaned files."""

    def get_data_root(self) -> Path:
        """Get the data root directory."""
        rt = get_runtime_settings()
        return Path(rt.data_root).resolve()

    @staticmethod
    def _directory_size(path: Path) -> int:
        total = 0
        for directory, dirs, files in os.walk(path, followlinks=False):
            dirs[:] = [name for name in dirs if not is_directory_link(Path(directory) / name)]
            for name in files:
                item = Path(directory) / name
                if not item.is_symlink():
                    total += item.stat().st_size
        return total

    def cleanup_failed_task(self, task_id: str, dry_run: bool = False) -> dict[str, Any]:
        """
        Clean up files from a failed task.

        Args:
            task_id: The task ID (full UUID)

        Returns:
            Dict with cleanup results
        """
        cleaned = []
        candidates: list[dict[str, Any]] = []
        errors = []

        # Find output_dir from task record
        from uuid import UUID

        store = get_task_store()
        try:
            task = store.get(UUID(task_id))
        except ValueError:
            task = None
        if task is None:
            errors.append({"task_id": task_id, "error": "Task not found"})
        elif str(task.status) not in {"failed", "cancelled"}:
            errors.append(
                {
                    "task_id": task_id,
                    "error": f"Task status {task.status} is not eligible for failed-task cleanup",
                }
            )
            task = None
        result = (task.result or {}) if task else {}
        archive = result.get("archive")
        output = result.get("output_dir") or (
            archive.get("output_dir") if isinstance(archive, dict) else None
        )
        if task and output:
            task_dir: Path | None = Path(output)
            try:
                task_dir = archive_directory(task_dir, self.get_data_root())
                if any(
                    other.id != task.id and other.status in ACTIVE_STATUSES
                    and task_uses_directory(other, task_dir)
                    for other in store.list(limit=-1)
                ):
                    raise ValueError("Directory is used by an active task")
            except ValueError as exc:
                errors.append({"path": str(task_dir), "error": str(exc)})
                task_dir = None
            if task_dir is not None and task_dir.is_dir():
                candidates.append(
                    {
                        "path": str(task_dir),
                        "bytes": self._directory_size(task_dir),
                        "reason": f"task_{task.status}",
                    }
                )
                if dry_run:
                    return {
                        "task_id": task_id,
                        "dry_run": True,
                        "candidates": candidates,
                        "cleaned": [],
                        "errors": [],
                    }
                try:
                    current = store.get(task.id)
                    if current is None or current.status not in {"failed", "cancelled"}:
                        raise ValueError("Task is no longer eligible for cleanup")
                    archive_directory(output, self.get_data_root())
                    if task_dir not in task_output_paths(current) or any(
                        other.id != task.id and other.status in ACTIVE_STATUSES
                        and task_uses_directory(other, task_dir)
                        for other in store.list(limit=-1)
                    ):
                        raise ValueError("Directory ownership changed during cleanup")
                    shutil.rmtree(task_dir)
                    cleaned.append(str(task_dir))
                    logger.info(f"Cleaned up task directory: {task_dir}")
                except Exception as e:
                    errors.append({"path": str(task_dir), "error": str(e)})
                    logger.error(f"Failed to clean up {task_dir}: {e}")
            elif task_dir is not None:
                errors.append({"path": str(task_dir), "error": "Output directory not found"})
        elif task is not None:
            errors.append({"task_id": task_id, "error": "Task has no output directory"})

        return {
            "task_id": task_id,
            "dry_run": dry_run,
            "candidates": candidates,
            "cleaned": cleaned,
            "errors": errors,
        }

    def cleanup_orphaned_files(
        self, max_age_hours: int = 24, dry_run: bool = False
    ) -> dict[str, Any]:
        """Clean unreferenced staging uploads; report other unclassified directories."""
        data_root = self.get_data_root()
        store = get_task_store()
        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)

        cleaned = []
        candidates: list[dict[str, Any]] = []
        errors = []
        skipped = []

        all_tasks = store.list(limit=-1)
        known_dirs = {path for task in all_tasks for path in task_output_paths(task)}
        unclassified = []
        for item in data_root.iterdir() if data_root.exists() else []:
            if item.is_dir() and item.name.casefold() not in SYSTEM_DIRECTORIES:
                if not item.name.startswith(".") and item.resolve() not in known_dirs:
                    if not (item / "metadata.json").exists():
                        unclassified.append(str(item))

        staging = data_root / "_staging"
        if staging.exists():
            try:
                managed_child(staging, data_root)
                for item in staging.iterdir():
                    try:
                        managed_child(item, data_root)
                        if not item.is_dir():
                            continue
                        if any(task_uses_directory(task, item) for task in all_tasks):
                            skipped.append(str(item))
                            continue
                        if datetime.fromtimestamp(item.stat().st_mtime) > cutoff_time:
                            skipped.append(str(item))
                            continue
                        candidates.append({
                            "path": str(item), "bytes": self._directory_size(item),
                            "reason": f"unreferenced_upload_older_than_{max_age_hours}h",
                        })
                        if not dry_run:
                            self.delete_staged_directory(item)
                            cleaned.append(str(item))
                    except (OSError, ValueError) as exc:
                        errors.append({"path": str(item), "error": str(exc)})
            except (OSError, ValueError) as exc:
                errors.append({"path": str(staging), "error": str(exc)})

        return {
            "max_age_hours": max_age_hours,
            "dry_run": dry_run,
            "candidates": candidates,
            "cleaned": cleaned,
            "skipped": skipped,
            "errors": errors,
            "unclassified": unclassified,
        }

    def delete_staged_directory(self, path: Path) -> None:
        root = self.get_data_root()
        target = managed_child(path, root)
        if target.parent != root / "_staging":
            raise ValueError("Expected one staging upload directory")
        if any(task_uses_directory(task, target) for task in get_task_store().list(limit=-1)):
            raise ValueError("Staged upload is referenced by a task")
        if target.exists():
            shutil.rmtree(target)

    def get_disk_usage(self) -> dict[str, Any]:
        """
        Get disk usage statistics for the data directory.

        Returns:
            Dict with usage statistics
        """
        data_root = self.get_data_root()

        total_size = 0
        file_count = 0
        dir_count = 0

        type_sizes = {
            "video": 0,
            "audio": 0,
            "transcript": 0,
            "other": 0,
        }

        video_exts = {".mp4", ".mkv", ".avi", ".webm", ".mov"}
        audio_exts = {".mp3", ".wav", ".flac", ".m4a", ".ogg"}
        transcript_exts = {".srt", ".txt", ".md", ".json"}

        for item in data_root.rglob("*"):
            if item.is_file():
                file_count += 1
                try:
                    size = item.stat().st_size
                    total_size += size

                    suffix = item.suffix.lower()
                    if suffix in video_exts:
                        type_sizes["video"] += size
                    elif suffix in audio_exts:
                        type_sizes["audio"] += size
                    elif suffix in transcript_exts:
                        type_sizes["transcript"] += size
                    else:
                        type_sizes["other"] += size
                except OSError:
                    pass
            elif item.is_dir():
                dir_count += 1

        def format_size(bytes: int) -> str:
            if bytes < 1024:
                return f"{bytes} B"
            elif bytes < 1024 * 1024:
                return f"{bytes / 1024:.1f} KB"
            elif bytes < 1024 * 1024 * 1024:
                return f"{bytes / (1024 * 1024):.1f} MB"
            else:
                return f"{bytes / (1024 * 1024 * 1024):.2f} GB"

        return {
            "path": str(data_root),
            "total_size": total_size,
            "total_size_formatted": format_size(total_size),
            "file_count": file_count,
            "directory_count": dir_count,
            "by_type": {
                k: {"bytes": v, "formatted": format_size(v)} for k, v in type_sizes.items()
            },
        }


# Global instance
_service: CleanupService | None = None


def get_cleanup_service() -> CleanupService:
    """Get or create the cleanup service instance."""
    global _service
    if _service is None:
        _service = CleanupService()
    return _service


async def cleanup_failed_task(task_id: str, dry_run: bool = False) -> dict[str, Any]:
    """Clean up files from a failed task."""
    return get_cleanup_service().cleanup_failed_task(task_id, dry_run=dry_run)


async def cleanup_orphaned_files(max_age_hours: int = 24, dry_run: bool = False) -> dict[str, Any]:
    """Clean up orphaned temporary files."""
    return get_cleanup_service().cleanup_orphaned_files(max_age_hours, dry_run=dry_run)


async def get_disk_usage() -> dict[str, Any]:
    """Get disk usage statistics."""
    return get_cleanup_service().get_disk_usage()
