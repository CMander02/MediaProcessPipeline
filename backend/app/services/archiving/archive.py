"""Archive service for generating Obsidian-compatible output."""

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
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

## Mind Map
```markmap
{mindmap}
```
"""


class ArchiveService:
    def __init__(self):
        self._settings = get_settings()

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

        # Summary with mindmap
        if summary or mindmap:
            sum_path = output_dir / "summary.md"
            content = SUMMARY_TEMPLATE.format(
                title=metadata.title,
                source_url=metadata.source_url or "",
                date=date_str,
                tldr=summary.get("tldr", "") if summary else "",
                key_facts=self._fmt_list(summary.get("key_facts", []) if summary else []),
                mindmap=mindmap or "- No mindmap",
            )
            sum_path.write_text(content, encoding="utf-8")
            files["summary"] = str(sum_path)

        # Sync to Obsidian
        if self._settings.obsidian_vault_path:
            self._sync_obsidian(output_dir)

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
            # Skip system files
            if task_dir.name.startswith('.') or task_dir.name == 'settings.json' or task_dir.name == 'history.json':
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

            # Check for media files in source directory
            source_dir = task_dir / "source"
            has_video = False
            has_audio = False
            media_file = None
            if source_dir.exists():
                for f in source_dir.iterdir():
                    if f.suffix.lower() in ['.mp4', '.mkv', '.avi', '.webm', '.mov']:
                        has_video = True
                        media_file = str(f)
                        break
                    elif f.suffix.lower() in ['.mp3', '.wav', '.flac', '.m4a', '.ogg']:
                        has_audio = True
                        media_file = str(f)

            archives.append({
                "path": str(task_dir),
                "date": datetime.fromtimestamp(task_dir.stat().st_mtime).strftime("%Y-%m-%d"),
                "title": metadata.get("title", task_dir.name),
                "has_transcript": (task_dir / "transcript_polished.srt").exists() or (task_dir / "transcript.srt").exists(),
                "has_summary": (task_dir / "summary.md").exists(),
                "has_mindmap": (task_dir / "summary.md").exists(),
                "has_video": has_video,
                "has_audio": has_audio,
                "media_file": media_file,
                "metadata": metadata,
                "analysis": analysis,
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

    def _sync_obsidian(self, output_dir: Path):
        vault = Path(self._settings.obsidian_vault_path)
        if not vault.exists():
            return
        dest = vault / "MediaPipeline" / output_dir.name
        dest.mkdir(parents=True, exist_ok=True)
        for md in output_dir.glob("*.md"):
            shutil.copy2(md, dest / md.name)


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
