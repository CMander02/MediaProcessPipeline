"""Artifacts responsibilities for the media pipeline."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.artifacts import get_artifact_store
from app.core.events import TaskEvent, get_event_bus
from app.core.logging_setup import log_event
from app.core.paths import get_workspace_paths
from app.core.settings import get_runtime_settings
from app.core.workspace_lifecycle import run_in_thread
from app.models import MediaMetadata, Task

logger = logging.getLogger(__name__)

_WIN_RESERVED_FILENAME_RE = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?$",
    re.IGNORECASE,
)


def _sanitize_filename(name: str) -> str:
    """Sanitize string for use as filename."""
    name = str(name or "")
    name = re.sub(r"[\x00-\x1f]+", " ", name)
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name)
    name = name.strip(" .")
    if _WIN_RESERVED_FILENAME_RE.match(name):
        name = f"_{name}"
    name = name[:100].rstrip(" .")
    return name


def _unique_child_dir(parent: Path, dir_name: str, current_dir: Path | None = None) -> Path:
    """Return an available child directory path under parent."""
    candidate = parent / dir_name
    if current_dir is not None and candidate.resolve() == current_dir.resolve():
        return current_dir
    if not candidate.exists():
        return candidate

    counter = 2
    while True:
        candidate = parent / f"{dir_name} ({counter})"
        if current_dir is not None and candidate.resolve() == current_dir.resolve():
            return current_dir
        if not candidate.exists():
            return candidate
        counter += 1


def create_task_dir(task_id: UUID, title: str | None = None) -> Path:
    """Create a dedicated directory for this task under data/{title}/."""
    settings = get_runtime_settings()
    data_root = get_workspace_paths(settings.data_root).archives

    if title:
        dir_name = _sanitize_filename(title) or str(task_id)[:8]
    else:
        dir_name = str(task_id)[:8]

    task_dir = _unique_child_dir(data_root, dir_name)
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


def _rename_task_dir_to_title(task_dir: Path, title: str | None) -> tuple[Path, Path | None]:
    """Move a placeholder task directory to a unique metadata-title directory."""
    if not title:
        return task_dir, None

    dir_name = _sanitize_filename(title)
    if not dir_name:
        return task_dir, None

    target = _unique_child_dir(task_dir.parent, dir_name, current_dir=task_dir)
    if target.resolve() == task_dir.resolve():
        return task_dir, None

    task_dir.rename(target)
    return target, task_dir


def write_metadata_json(
    task_dir: Path,
    metadata: "MediaMetadata | dict",
    status: str = "processing",
    task_id: str | None = None,
) -> Path:
    """Write or update metadata.json in the task directory."""
    import json

    meta_path = task_dir / "metadata.json"
    if isinstance(metadata, MediaMetadata):
        data = metadata.model_dump(mode="json")
    else:
        data = dict(metadata)
    data["status"] = status
    if task_id:
        data["task_id"] = task_id
    if meta_path.exists():
        existing = json.loads(meta_path.read_text(encoding="utf-8"))
        for key in ("task_id", "archive_id"):
            if existing.get(key) and not data.get(key):
                data[key] = existing[key]
    get_artifact_store().write(
        data.get("task_id"),
        task_dir,
        "metadata.json",
        json.dumps(data, indent=2, ensure_ascii=False),
    )
    return meta_path


def _sync_task_from_metadata(task: "Task", metadata: "MediaMetadata") -> None:
    """Copy denormalized metadata fields onto the task object for DB + SSE exposure."""
    if metadata.platform:
        task.platform = metadata.platform
    if metadata.uploader_id:
        task.uploader_id = metadata.uploader_id
    if metadata.content_subtype:
        task.content_subtype = metadata.content_subtype


def update_metadata_status(task_dir: Path | None, status: str) -> None:
    """Update only metadata.json status when a task ends outside the normal archive path."""
    if task_dir is None:
        return
    meta_path = task_dir / "metadata.json"
    if not meta_path.exists():
        return
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        data["status"] = status
        get_artifact_store().write(
            data.get("task_id"),
            task_dir,
            "metadata.json",
            json.dumps(data, indent=2, ensure_ascii=False),
        )
    except Exception:
        log_event(
            logger,
            logging.DEBUG,
            "metadata_status.update_failed",
            status=status,
            path=meta_path,
            exc_info=True,
        )


async def _write_summary_files(
    task: Task,
    task_dir: Path,
    metadata: MediaMetadata,
    summary: dict[str, Any],
) -> None:
    """Persist structured and rendered summary outputs."""
    await _write_text_artifact(
        task, task_dir, "summary.json", json.dumps(summary, indent=2, ensure_ascii=False)
    )

    from app.services.archiving.archive import SUMMARY_TEMPLATE, get_archive_service

    _svc = get_archive_service()
    sum_content = SUMMARY_TEMPLATE.format(
        title=metadata.title,
        source_url=metadata.source_url or "",
        date=datetime.now().strftime("%Y-%m-%d"),
        tldr=summary.get("tldr", ""),
        key_facts=_svc._fmt_list(summary.get("key_facts", [])),
    )
    await _write_text_artifact(task, task_dir, "summary.md", sum_content)


async def _emit_file_ready(task: Task, filename: str, file_path: str) -> None:
    """Emit a file_ready SSE event when a file is written to disk."""
    bus = get_event_bus()
    await bus.publish(
        TaskEvent(
            task.id,
            "file_ready",
            {
                "file": filename,
                "path": file_path,
            },
        )
    )


async def _write_text_artifact(task: Task, task_dir: Path, filename: str, content: str) -> Path:
    """Write text artifact to disk, mirror it to SQLite, then emit file_ready."""
    artifact_path = get_artifact_store().write(task.id, task_dir, filename, content)
    await _emit_file_ready(task, filename, str(artifact_path))
    return artifact_path


async def _prepare_source_context(
    task: Task,
    task_dir: Path,
    metadata: MediaMetadata,
) -> dict[str, Any]:
    """Build or restore the source-grounded context before ASR and polishing."""
    from app.services.analysis.source_context import load_or_build_source_context

    context_path = task_dir / "source_context.json"
    context = await load_or_build_source_context(
        metadata,
        task.options,
        context_path,
    )
    payload = context.model_dump(mode="json")
    await _write_text_artifact(
        task,
        task_dir,
        "source_context.json",
        json.dumps(payload, indent=2, ensure_ascii=False),
    )
    metadata.extra["source_context_file"] = "source_context.json"
    metadata.extra["source_context"] = {
        "entities": len(context.entities),
        "timeline": len(context.timeline),
        "speaker_candidates": len(context.speaker_candidates),
        "speaker_count_hint": context.speaker_count_hint.model_dump(mode="json"),
    }
    return payload


async def _write_speaker_map(
    task: Task,
    task_dir: Path,
    segments: list[dict[str, Any]],
    source_context: dict[str, Any] | None,
    resolutions: dict[str, Any] | None = None,
) -> None:
    speakers = sorted(
        {
            str(segment.get("speaker") or "").strip()
            for segment in segments
            if str(segment.get("speaker") or "").strip()
        }
    )
    candidates = list((source_context or {}).get("speaker_candidates") or [])
    mappings = []
    for speaker in speakers:
        resolution = (resolutions or {}).get(speaker)
        mappings.append(
            {
                "source_label": speaker,
                "current_name": (
                    str(resolution.person_name) if resolution is not None else speaker
                ),
                "status": (
                    "voiceprint_new"
                    if resolution is not None and resolution.is_new_person
                    else "voiceprint_matched"
                    if resolution is not None
                    else "anonymous"
                ),
            }
        )
    payload = {
        "version": 1,
        "mappings": mappings,
        "source_candidates": candidates,
        "confirmed": all(item["status"] == "voiceprint_matched" for item in mappings)
        if mappings
        else False,
    }
    await _write_text_artifact(
        task,
        task_dir,
        "speaker_map.json",
        json.dumps(payload, indent=2, ensure_ascii=False),
    )


def _rewrite_path_after_dir_move(value: Any, old_dir: Path, new_dir: Path) -> Any:
    if not isinstance(value, str) or not value:
        return value
    try:
        path = Path(value)
        if not path.is_absolute():
            return value
        relative = path.resolve().relative_to(old_dir.resolve())
        return str(new_dir / relative)
    except Exception:
        return value


def _rewrite_ingest_paths_after_task_dir_move(
    ingest: dict[str, Any], metadata: "MediaMetadata", old_dir: Path, new_dir: Path
) -> None:
    """Keep note/webpage asset paths valid after renaming the task directory."""

    def rewrite_extra(extra: Any) -> None:
        if not isinstance(extra, dict):
            return
        extra["source_markdown_path"] = _rewrite_path_after_dir_move(
            extra.get("source_markdown_path"),
            old_dir,
            new_dir,
        )
        images = extra.get("images")
        if isinstance(images, list):
            for item in images:
                if isinstance(item, dict):
                    item["path"] = _rewrite_path_after_dir_move(item.get("path"), old_dir, new_dir)

    rewrite_extra(metadata.extra)
    info = ingest.get("info") if isinstance(ingest, dict) else None
    if isinstance(info, dict):
        rewrite_extra(info.get("extra"))
        info["thumbnail"] = _rewrite_path_after_dir_move(info.get("thumbnail"), old_dir, new_dir)


async def _write_mindmap_files(task: Task, task_dir: Path, mindmap: str) -> None:
    """Persist frontend tree JSON plus clean Markdown export for the mindmap."""
    from app.services.analysis.llm import (
        mindmap_markdown_to_timed_tree,
        mindmap_markdown_without_timestamps,
    )

    export_markdown = mindmap_markdown_without_timestamps(mindmap) or mindmap
    await _write_text_artifact(task, task_dir, "mindmap.md", export_markdown)

    tree = mindmap_markdown_to_timed_tree(mindmap)
    await _write_text_artifact(
        task,
        task_dir,
        "mindmap.json",
        json.dumps(tree, indent=2, ensure_ascii=False),
    )


async def _write_detail_file(task: Task, task_dir: Path, detail: str) -> None:
    """Persist optional former deep mindmap as detail.md."""
    await _write_text_artifact(task, task_dir, "detail.md", detail)


def _schedule_kb_index(task_id: str, archive_path: str) -> None:
    """Fire-and-forget KB indexing after archive completes."""
    import asyncio

    async def _do_index():
        try:
            from app.core.settings import get_runtime_settings

            rt = get_runtime_settings()
            if not rt.kb_enabled or not rt.kb_embedding_api_base:
                return
            from app.services.kb.indexer import index_task

            log_event(logger, logging.INFO, "kb.index.started", archive_path=archive_path)
            await run_in_thread(index_task, task_id, archive_path)
            log_event(logger, logging.INFO, "kb.index.completed", archive_path=archive_path)
        except Exception as e:
            log_event(logger, logging.WARNING, "kb.index.failed", task_id=task_id, error=e)

    asyncio.ensure_future(_do_index())
