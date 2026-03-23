"""Archive service for generating structured output."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.settings import get_runtime_settings
from app.models import MediaMetadata

logger = logging.getLogger(__name__)

SUMMARY_TEMPLATE = """---
title: "{title}"
source: "{source_url}"
date: {date}
tags: [media-pipeline]
---

# {title}

## Summary
{tldr}

### Key Facts
{key_facts}
"""


class ArchiveService:
    def archive(
        self,
        metadata: MediaMetadata,
        polished_srt: str | None = None,
        summary: dict[str, Any] | None = None,
        mindmap: str | None = None,
        original_srt: str | None = None,
        work_dir: Path | None = None,
        analysis: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from app.services.analysis import srt_to_markdown

        date_str = datetime.now().strftime("%Y-%m-%d")
        title_safe = self._safe_name(metadata.title)

        # Use work_dir as output location if provided, otherwise use data_root
        if work_dir:
            output_dir = Path(work_dir)
        else:
            rt = get_runtime_settings()
            output_dir = Path(rt.data_root).resolve() / f"{date_str}_{title_safe}"
        output_dir.mkdir(parents=True, exist_ok=True)

        files: dict[str, str] = {}

        # Metadata
        meta_path = output_dir / "metadata.json"
        meta_path.write_text(json.dumps(metadata.model_dump(mode="json"), indent=2, ensure_ascii=False))
        files["metadata"] = str(meta_path)

        # Analysis (LLM extracted metadata)
        if analysis:
            analysis_path = output_dir / "analysis.json"
            analysis_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False))
            files["analysis"] = str(analysis_path)

        # Original SRT (raw transcription)
        if original_srt:
            srt_path = output_dir / "transcript.srt"
            srt_path.write_text(original_srt, encoding="utf-8")
            files["srt"] = str(srt_path)

        # Polished SRT (after LLM processing)
        if polished_srt:
            polished_srt_path = output_dir / "transcript_polished.srt"
            polished_srt_path.write_text(polished_srt, encoding="utf-8")
            files["polished_srt"] = str(polished_srt_path)

            # Generate clean Markdown document from polished SRT
            markdown_content = srt_to_markdown(polished_srt, metadata.title)
            md_path = output_dir / "transcript_polished.md"
            md_path.write_text(markdown_content, encoding="utf-8")
            files["polished_md"] = str(md_path)

        # Summary (without mindmap)
        if summary:
            sum_path = output_dir / "summary.md"
            content = SUMMARY_TEMPLATE.format(
                title=metadata.title,
                source_url=metadata.source_url or "",
                date=date_str,
                tldr=summary.get("tldr", "") if summary else "",
                key_facts=self._fmt_list(summary.get("key_facts", []) if summary else []),
            )
            sum_path.write_text(content, encoding="utf-8")
            files["summary"] = str(sum_path)

        # Mindmap (separate file)
        if mindmap:
            mm_path = output_dir / "mindmap.md"
            mm_path.write_text(mindmap, encoding="utf-8")
            files["mindmap"] = str(mm_path)

        logger.info(f"Archived to: {output_dir}")
        return {"output_dir": str(output_dir), "files": files}

    def list_archives(self, limit: int = 50) -> list[dict[str, Any]]:
        """List archives from flat data directory structure."""
        rt = get_runtime_settings()
        data_root = Path(rt.data_root).resolve()

        if not data_root.exists():
            return []

        archives = []
        # Iterate through data/{task_id}/ directories
        for task_dir in sorted(data_root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not task_dir.is_dir():
                continue
            # Skip system/utility directories and non-archive dirs
            if task_dir.name.startswith('.') or task_dir.name in (
                'settings.json', 'history.json', 'uploads', 'manual_task',
            ):
                continue

            # Skip directories that don't look like archives (no metadata, no transcript, no summary)
            has_any_output = (
                (task_dir / "metadata.json").exists()
                or (task_dir / "transcript.srt").exists()
                or (task_dir / "summary.md").exists()
            )
            if not has_any_output:
                continue

            # Try to load metadata
            metadata = {}
            meta_path = task_dir / "metadata.json"
            if meta_path.exists():
                try:
                    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

            # Try to load analysis
            analysis = {}
            analysis_path = task_dir / "analysis.json"
            if analysis_path.exists():
                try:
                    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

            # Check for media files in source directory, fallback to original path
            video_exts = {'.mp4', '.mkv', '.avi', '.webm', '.mov'}
            audio_exts = {'.mp3', '.wav', '.flac', '.m4a', '.ogg'}
            source_dir = task_dir / "source"
            has_video = False
            has_audio = False
            media_file = None
            media_is_external = False
            if source_dir.exists():
                for f in source_dir.iterdir():
                    if f.suffix.lower() in video_exts:
                        has_video = True
                        media_file = str(f)
                        break
                    elif f.suffix.lower() in audio_exts:
                        has_audio = True
                        media_file = str(f)

            # Fallback: source/ deleted, try original source_url from metadata
            if not media_file and metadata.get("source_url"):
                original = Path(metadata["source_url"])
                if original.exists() and original.is_file():
                    ext = original.suffix.lower()
                    if ext in video_exts:
                        has_video = True
                        media_file = str(original)
                        media_is_external = True
                    elif ext in audio_exts:
                        has_audio = True
                        media_file = str(original)
                        media_is_external = True

            # Determine processing status
            meta_status = metadata.get("status", "completed")  # backward compat
            processing = meta_status in ("queued", "processing")

            # Extract task_id from directory name prefix
            dir_name = task_dir.name
            task_id = dir_name.split("_")[0] if "_" in dir_name else dir_name

            # Extract duration from metadata
            duration_seconds = metadata.get("duration_seconds") or metadata.get("duration")

            archives.append({
                "path": str(task_dir),
                "date": datetime.fromtimestamp(task_dir.stat().st_mtime).strftime("%Y-%m-%d"),
                "title": metadata.get("title", task_dir.name),
                "has_transcript": (task_dir / "transcript_polished.srt").exists() or (task_dir / "transcript.srt").exists(),
                "has_summary": (task_dir / "summary.md").exists(),
                "has_mindmap": (task_dir / "mindmap.md").exists(),
                "has_video": has_video,
                "has_audio": has_audio,
                "media_file": media_file,
                "media_is_external": media_is_external,
                "processing": processing,
                "task_id": task_id,
                "metadata": metadata,
                "analysis": analysis,
                "duration_seconds": duration_seconds,
            })
            if len(archives) >= limit:
                break

        return archives

    def _safe_name(self, name: str) -> str:
        for c in '<>:"/\\|?*':
            name = name.replace(c, "_")
        return name[:100].strip()

    def _fmt_list(self, items: list[str]) -> str:
        return "\n".join(f"- {i}" for i in items) if items else "- None"


_service: ArchiveService | None = None


def get_archive_service() -> ArchiveService:
    global _service
    if _service is None:
        _service = ArchiveService()
    return _service


async def archive_result(
    metadata: MediaMetadata,
    polished_srt: str | None = None,
    summary: dict[str, Any] | None = None,
    mindmap: str | None = None,
    original_srt: str | None = None,
    work_dir: Path | None = None,
    analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return get_archive_service().archive(
        metadata,
        polished_srt=polished_srt,
        summary=summary,
        mindmap=mindmap,
        original_srt=original_srt,
        work_dir=work_dir,
        analysis=analysis
    )


async def list_archives(limit: int = 50) -> list[dict[str, Any]]:
    return get_archive_service().list_archives(limit)
