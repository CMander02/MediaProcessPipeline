"""Cleanup service for managing temporary files."""

import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.settings import get_runtime_settings
from app.core.database import get_task_store

logger = logging.getLogger(__name__)


class CleanupService:
    """Service for cleaning up temporary and orphaned files."""

    def get_data_root(self) -> Path:
        """Get the data root directory."""
        rt = get_runtime_settings()
        return Path(rt.data_root).resolve()

    def cleanup_failed_task(self, task_id: str) -> dict[str, Any]:
        """
        Clean up files from a failed task.

        Args:
            task_id: The task ID (full UUID)

        Returns:
            Dict with cleanup results
        """
        cleaned = []
        errors = []

        # Find output_dir from task record
        from uuid import UUID
        store = get_task_store()
        task = store.get(UUID(task_id))
        if task and task.result and task.result.get("output_dir"):
            task_dir = Path(task.result["output_dir"])
            if task_dir.is_dir():
                try:
                    shutil.rmtree(task_dir)
                    cleaned.append(str(task_dir))
                    logger.info(f"Cleaned up task directory: {task_dir}")
                except Exception as e:
                    errors.append({"path": str(task_dir), "error": str(e)})
                    logger.error(f"Failed to clean up {task_dir}: {e}")

        return {
            "task_id": task_id,
            "cleaned": cleaned,
            "errors": errors,
        }

    def cleanup_orphaned_files(self, max_age_hours: int = 24) -> dict[str, Any]:
        """
        Clean up orphaned temporary files older than specified age.

        This removes:
        - Directories without metadata.json (incomplete processing)
        - Directories not in history
        - Old temporary segment files

        Args:
            max_age_hours: Maximum age in hours for orphaned files

        Returns:
            Dict with cleanup results
        """
        data_root = self.get_data_root()
        store = get_task_store()
        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)

        cleaned = []
        errors = []
        skipped = []

        # Collect all known output_dir paths from task records
        all_tasks = store.list(limit=1000)
        known_dirs = set()
        for t in all_tasks:
            if t.result and t.result.get("output_dir"):
                known_dirs.add(str(Path(t.result["output_dir"]).resolve()))

        for item in data_root.iterdir():
            # Skip non-directories and system files
            if not item.is_dir():
                continue
            if item.name in ('settings.json', 'history.json') or item.name.startswith('.'):
                continue

            # Check if directory belongs to a known task
            if str(item.resolve()) in known_dirs:
                skipped.append(str(item))
                continue

            # Check age
            try:
                mtime = datetime.fromtimestamp(item.stat().st_mtime)
                if mtime > cutoff_time:
                    skipped.append(str(item))
                    continue
            except OSError:
                continue

            # Check if it has metadata (completed task)
            if (item / "metadata.json").exists():
                skipped.append(str(item))
                continue

            # Clean up orphaned directory
            try:
                shutil.rmtree(item)
                cleaned.append(str(item))
                logger.info(f"Cleaned up orphaned directory: {item}")
            except Exception as e:
                errors.append({"path": str(item), "error": str(e)})
                logger.error(f"Failed to clean up {item}: {e}")

        # Clean up orphaned files in uploads/ directory
        uploads_dir = data_root / "uploads"
        if uploads_dir.is_dir():
            # Collect source paths referenced by active (non-terminal) tasks
            active_sources = set()
            active_statuses = {"pending", "queued", "processing"}
            for t in all_tasks:
                if t.status in active_statuses:
                    active_sources.add(str(Path(t.source).resolve()))

            for f in uploads_dir.iterdir():
                if not f.is_file():
                    continue
                # Skip files referenced by active tasks
                if str(f.resolve()) in active_sources:
                    skipped.append(str(f))
                    continue
                try:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime)
                    if mtime > cutoff_time:
                        skipped.append(str(f))
                        continue
                except OSError:
                    continue
                try:
                    f.unlink()
                    cleaned.append(str(f))
                    logger.info(f"Cleaned up orphaned upload: {f}")
                except Exception as e:
                    errors.append({"path": str(f), "error": str(e)})
                    logger.error(f"Failed to clean up upload {f}: {e}")

        return {
            "max_age_hours": max_age_hours,
            "cleaned": cleaned,
            "skipped": skipped,
            "errors": errors,
        }

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

        video_exts = {'.mp4', '.mkv', '.avi', '.webm', '.mov'}
        audio_exts = {'.mp3', '.wav', '.flac', '.m4a', '.ogg'}
        transcript_exts = {'.srt', '.txt', '.md', '.json'}

        for item in data_root.rglob('*'):
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
                k: {"bytes": v, "formatted": format_size(v)}
                for k, v in type_sizes.items()
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


async def cleanup_failed_task(task_id: str) -> dict[str, Any]:
    """Clean up files from a failed task."""
    return get_cleanup_service().cleanup_failed_task(task_id)


async def cleanup_orphaned_files(max_age_hours: int = 24) -> dict[str, Any]:
    """Clean up orphaned temporary files."""
    return get_cleanup_service().cleanup_orphaned_files(max_age_hours)


async def get_disk_usage() -> dict[str, Any]:
    """Get disk usage statistics."""
    return get_cleanup_service().get_disk_usage()
