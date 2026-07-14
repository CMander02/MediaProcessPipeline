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
        meta_path.write_text(json.dumps(metadata.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")
        files["metadata"] = str(meta_path)

        # Analysis (LLM extracted metadata)
        if analysis:
            analysis_path = output_dir / "analysis.json"
            analysis_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
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
            summary_json_path = output_dir / "summary.json"
            summary_json_path.write_text(
                json.dumps(summary, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            files["summary_json"] = str(summary_json_path)

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

        # Mindmap (separate file): mindmap.md is export-friendly Markdown with
        # timestamps stripped; mindmap.json keeps optional timing for frontend use.
        if mindmap:
            from app.services.analysis.llm import (
                mindmap_markdown_to_timed_tree,
                mindmap_markdown_without_timestamps,
            )

            export_markdown = mindmap_markdown_without_timestamps(mindmap) or mindmap
            mm_path = output_dir / "mindmap.md"
            mm_path.write_text(export_markdown, encoding="utf-8")
            files["mindmap"] = str(mm_path)

            tree_path = output_dir / "mindmap.json"
            tree_path.write_text(
                json.dumps(mindmap_markdown_to_timed_tree(mindmap), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            files["mindmap_json"] = str(tree_path)

        logger.info(f"Archived to: {output_dir}")
        return {"output_dir": str(output_dir), "files": files}

    def list_archives(self, limit: int = 0, *, lite: bool = False) -> list[dict[str, Any]]:
        """List archives from flat data directory structure."""
        rt = get_runtime_settings()
        data_root = Path(rt.data_root).resolve()

        if not data_root.exists():
            return []

        dir_to_task = self._archive_task_map()

        archives = []
        for task_dir in data_root.iterdir():
            item = self._archive_item(task_dir, dir_to_task, lite=lite)
            if not item:
                continue
            archives.append(item)

        archives.sort(key=lambda item: item["created_at"], reverse=True)
        if limit:
            archives = archives[:limit]

        return archives

    def get_archive(self, path: str | Path, *, lite: bool = False) -> dict[str, Any] | None:
        rt = get_runtime_settings()
        data_root = Path(rt.data_root).resolve()
        task_dir = Path(path)
        if not task_dir.is_dir():
            return None
        try:
            task_dir.resolve().relative_to(data_root)
        except ValueError:
            return None
        return self._archive_item(task_dir, self._archive_task_map(), lite=lite)

    def _archive_task_map(self) -> dict[str, dict[str, str]]:
        dir_to_task: dict[str, dict[str, str]] = {}
        try:
            from app.core.database import get_task_store

            store = get_task_store()
            for task in store.list(limit=10000):
                result = task.result or {}
                out_dir = result.get("output_dir") or (result.get("archive") or {}).get("output_dir")
                if out_dir:
                    dir_to_task[str(Path(out_dir).resolve())] = {
                        "id": str(task.id),
                        "created_at": task.created_at.isoformat(),
                    }
        except Exception:
            pass
        return dir_to_task

    def _archive_item(
        self,
        task_dir: Path,
        dir_to_task: dict[str, dict[str, str]],
        *,
        lite: bool = False,
    ) -> dict[str, Any] | None:
        if not task_dir.is_dir():
            return None
        if task_dir.name.startswith('.') or task_dir.name in (
            'settings.json', 'history.json', 'uploads', 'manual_task',
        ):
            return None

        has_any_output = (
            (task_dir / "metadata.json").exists()
            or (task_dir / "transcript.srt").exists()
            or (task_dir / "summary.md").exists()
            or (task_dir / "source.md").exists()
        )
        if not has_any_output:
            return None

        metadata = self._read_json(task_dir / "metadata.json")
        analysis = {} if lite else self._read_json(task_dir / "analysis.json")

        video_exts = {'.mp4', '.mkv', '.avi', '.webm', '.mov'}
        audio_exts = {'.mp3', '.wav', '.flac', '.m4a', '.ogg'}
        image_exts = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.avif'}
        has_video = False
        has_audio = False
        has_image = False
        media_file = None
        media_is_external = False

        for f in task_dir.iterdir():
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            if ext in video_exts:
                has_video = True
                media_file = str(f)
                break
            if ext in audio_exts:
                has_audio = True
                media_file = str(f)
            elif ext in image_exts and f.stem.lower() not in ("cover", "thumbnail"):
                has_image = True
        if not has_image:
            images_dir = task_dir / "images"
            if images_dir.exists() and any(
                item.is_file() and item.suffix.lower() in image_exts
                for item in images_dir.iterdir()
            ):
                has_image = True

        meta_status = metadata.get("status", "completed")
        processing = meta_status in ("queued", "processing", "paused")
        task_info = dir_to_task.get(str(task_dir.resolve()), {})
        task_id = (
            task_info.get("id")
            or metadata.get("task_id")
            or (task_dir.name.split("_")[0] if "_" in task_dir.name else None)
        )
        metadata_path = task_dir / "metadata.json"
        created_at = task_info.get("created_at")
        if not created_at:
            timestamp = metadata_path.stat().st_mtime if metadata_path.exists() else task_dir.stat().st_mtime
            created_at = datetime.fromtimestamp(timestamp).isoformat()
        duration_seconds = metadata.get("duration_seconds") or metadata.get("duration")
        if not duration_seconds:
            duration_seconds = self._duration_from_srt(task_dir)

        return {
            "path": str(task_dir),
            "date": datetime.fromisoformat(created_at).strftime("%Y-%m-%d"),
            "created_at": created_at,
            "title": metadata.get("title", task_dir.name),
            "has_transcript": (task_dir / "transcript_polished.srt").exists() or (task_dir / "transcript.srt").exists(),
            "has_summary": (task_dir / "summary.md").exists(),
            "has_mindmap": (task_dir / "mindmap.md").exists(),
            "has_video": has_video,
            "has_audio": has_audio,
            "has_image": has_image,
            "media_file": media_file,
            "media_is_external": media_is_external,
            "processing": processing,
            "task_id": task_id,
            "metadata": self._lite_metadata(metadata) if lite else metadata,
            "analysis": analysis,
            "duration_seconds": duration_seconds,
        }

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _lite_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        keys = {
            "title",
            "source_url",
            "original_url",
            "webpage_url",
            "url",
            "file_path",
            "thumbnail",
            "uploader",
            "uploader_id",
            "platform",
            "upload_date",
            "duration",
            "duration_seconds",
            "media_type",
            "content_subtype",
            "status",
            "task_id",
        }
        lite = {key: metadata[key] for key in keys if key in metadata}
        extra = metadata.get("extra")
        if isinstance(extra, dict):
            extra_keys = {
                "platform",
                "source_url",
                "original_url",
                "webpage_url",
                "url",
                "file_path",
                "thumbnail",
                "cover",
                "cover_url",
                "bilibili_type",
                "bvid",
                "opus_id",
                "article_url",
                "article_id",
            }
            lite_extra = {key: extra[key] for key in extra_keys if key in extra}
            if lite_extra:
                lite["extra"] = lite_extra
        return lite

    @staticmethod
    def _duration_from_srt(task_dir: Path) -> float | None:
        """Derive duration from the max end-time across all subtitle entries."""
        import re
        for name in ("transcript_polished.srt", "transcript.srt"):
            srt_path = task_dir / name
            if srt_path.exists():
                try:
                    content = srt_path.read_text(encoding="utf-8")
                    timestamps = re.findall(
                        r"(\d{2}):(\d{2}):(\d{2}),(\d{3})",
                        content,
                    )
                    if timestamps:
                        max_sec = 0.0
                        for h, m, s, ms in timestamps:
                            t = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
                            if t > max_sec:
                                max_sec = t
                        return max_sec if max_sec > 0 else None
                except Exception:
                    pass
        return None

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


async def list_archives(lite: bool = False) -> list[dict[str, Any]]:
    return get_archive_service().list_archives(lite=lite)


async def get_archive(path: str | Path, lite: bool = False) -> dict[str, Any] | None:
    return get_archive_service().get_archive(path, lite=lite)
