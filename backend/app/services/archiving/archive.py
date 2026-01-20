"""Archive service for generating Obsidian-compatible output."""

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
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
        polished_text: str | None = None,
        summary: dict[str, Any] | None = None,
        mindmap: str | None = None,
        srt_content: str | None = None,
        work_dir: Path | None = None,
    ) -> dict[str, Any]:
        date_str = datetime.now().strftime("%Y-%m-%d")
        title_safe = self._safe_name(metadata.title)

        # Use work_dir as output location if provided, otherwise use default outputs path
        if work_dir:
            output_dir = Path(work_dir)
        else:
            output_dir = self._settings.data_outputs.resolve() / date_str / title_safe
        output_dir.mkdir(parents=True, exist_ok=True)

        files: dict[str, str] = {}

        # Metadata
        meta_path = output_dir / "metadata.json"
        meta_path.write_text(json.dumps(metadata.model_dump(mode="json"), indent=2, ensure_ascii=False))
        files["metadata"] = str(meta_path)

        # SRT
        if srt_content:
            srt_path = output_dir / "transcript.srt"
            srt_path.write_text(srt_content, encoding="utf-8")
            files["srt"] = str(srt_path)

        # Polished transcript
        if polished_text:
            txt_path = output_dir / "transcript_polished.md"
            txt_path.write_text(f"# {metadata.title}\n\n{polished_text}", encoding="utf-8")
            files["polished"] = str(txt_path)

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
        outputs = self._settings.data_outputs.resolve()
        if not outputs.exists():
            return []

        archives = []
        for date_dir in sorted(outputs.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            for title_dir in date_dir.iterdir():
                if not title_dir.is_dir():
                    continue
                archives.append({
                    "path": str(title_dir),
                    "date": date_dir.name,
                    "title": title_dir.name,
                    "has_transcript": (title_dir / "transcript_polished.md").exists() or (title_dir / "transcript.srt").exists(),
                    "has_summary": (title_dir / "summary.md").exists(),
                    "has_mindmap": (title_dir / "summary.md").exists(),  # mindmap is in summary
                })
                if len(archives) >= limit:
                    return archives
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
    polished_text: str | None = None,
    summary: dict[str, Any] | None = None,
    mindmap: str | None = None,
    srt_content: str | None = None,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    return get_archive_service().archive(metadata, polished_text, summary, mindmap, srt_content, work_dir=work_dir)


async def list_archives(limit: int = 50) -> list[dict[str, Any]]:
    return get_archive_service().list_archives(limit)
