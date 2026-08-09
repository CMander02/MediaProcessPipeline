"""Keep speaker names consistent across generated text artifacts."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.core.database import get_task_store

logger = logging.getLogger(__name__)


SPEAKER_ARTIFACTS = (
    "transcript.srt",
    "transcript_polished.srt",
    "transcript.md",
    "transcript_polished.md",
    "metadata.json",
    "analysis.json",
    "summary.json",
    "summary.md",
    "mindmap.json",
    "mindmap.md",
    "detail.md",
    "source_context.json",
    "speaker_map.json",
)


def _replace_json_strings(value: Any, old_name: str, new_name: str) -> Any:
    if isinstance(value, str):
        return value.replace(old_name, new_name)
    if isinstance(value, list):
        return [_replace_json_strings(item, old_name, new_name) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_json_strings(item, old_name, new_name)
            for key, item in value.items()
        }
    return value


def _updated_content(path: Path, old_name: str, new_name: str) -> str | None:
    original = path.read_text(encoding="utf-8")
    if old_name not in original:
        return None

    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(original)
        except json.JSONDecodeError:
            logger.warning("Skipping malformed speaker artifact JSON: %s", path)
            return None
        updated = json.dumps(
            _replace_json_strings(payload, old_name, new_name),
            ensure_ascii=False,
            indent=2,
        )
        return f"{updated}\n"

    return original.replace(old_name, new_name)


def sync_speaker_artifacts(
    task_id: str,
    output_dir: Path | str,
    old_name: str,
    new_name: str,
) -> list[str]:
    """Rename one speaker in every generated artifact and its SQLite mirror."""
    if not old_name or old_name == new_name:
        return []

    archive_dir = Path(output_dir).resolve()
    if not archive_dir.is_dir():
        return []

    task_store = get_task_store()
    changed: list[str] = []
    for filename in SPEAKER_ARTIFACTS:
        path = archive_dir / filename
        if not path.is_file():
            continue
        try:
            updated = _updated_content(path, old_name, new_name)
        except (OSError, UnicodeError) as exc:
            logger.warning("Could not update speaker artifact %s: %s", path, exc)
            continue
        if updated is None:
            continue

        path.write_text(updated, encoding="utf-8")
        content_type = "application/json" if path.suffix.lower() == ".json" else "text/markdown"
        if path.suffix.lower() == ".srt":
            content_type = "application/x-subrip"
        task_store.save_artifact(task_id, filename, updated, content_type)
        changed.append(filename)

    return changed
