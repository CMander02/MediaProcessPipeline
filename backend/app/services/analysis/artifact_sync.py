"""Keep speaker names consistent across generated text artifacts."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.core.database import get_task_store
from app.core.artifacts import ArtifactStore

logger = logging.getLogger(__name__)


SPEAKER_ARTIFACTS = (
    "transcript.srt",
    "transcript_polished.srt",
    "transcript.md",
    "transcript_polished.md",
    "metadata.json",
    "analysis.json",
    "speaker_map.json",
)


def _replace_json_strings(value: Any, old_name: str, new_name: str) -> Any:
    if isinstance(value, list):
        return [_replace_json_strings(item, old_name, new_name) for item in value]
    if isinstance(value, dict):
        updated = {}
        for key, item in value.items():
            if key in {"speaker", "current_name"} and item == old_name:
                updated[key] = new_name
            elif key == "speakers" and isinstance(item, list):
                updated[key] = list(dict.fromkeys(new_name if x == old_name else x for x in item)) if all(isinstance(x, str) for x in item) else _replace_json_strings(item, old_name, new_name)
            else:
                updated[key] = _replace_json_strings(item, old_name, new_name)
        if isinstance(updated.get("speakers"), list) and all(isinstance(x, str) for x in updated["speakers"]):
            updated["speaker_count"] = len([x for x in updated["speakers"] if x.casefold() != "unknown"])
        return updated
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

    if path.suffix == ".srt":
        pattern = r"(?m)(^\d+\r?\n[^\r\n]*-->[^\r\n]*\r?\n\s*)\[" + re.escape(old_name) + r"\]"
        return re.sub(pattern, lambda match: match[1] + f"[{new_name}]", original)
    # Only explicit speaker headers in transcript exports; never replace body text.
    pattern = r"(?m)^(\s*(?:\*\*)?)\[" + re.escape(old_name) + r"\]"
    return re.sub(pattern, lambda match: match[1] + f"[{new_name}]", original)


def sync_speaker_artifacts(
    task_id: str,
    output_dir: Path | str,
    old_name: str,
    new_name: str,
) -> list[str]:
    """Rename explicit speaker fields; mark derived prose as needing regeneration."""
    if not old_name or old_name == new_name:
        return []

    archive_dir = Path(output_dir).resolve()
    if not archive_dir.is_dir():
        return []

    artifacts = ArtifactStore(get_task_store())
    changed: list[str] = []
    metadata_path = archive_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
    extra = metadata.get("extra") or {}
    raw_platform = extra.get("subtitle_source") == "platform" or extra.get("raw_speaker_label_contract") == 0
    for filename in SPEAKER_ARTIFACTS:
        if filename == "transcript.srt" and raw_platform:
            continue
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

        artifacts.write(task_id, archive_dir, filename, updated)
        changed.append(filename)

    metadata_path = archive_dir / "metadata.json"
    if metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        extra = metadata.setdefault("extra", {})
        from app.services.analysis.speaker_quality import finalize_speaker_quality
        transcript_path = archive_dir / "transcript_polished.srt"
        if not transcript_path.is_file() and not raw_platform:
            transcript_path = archive_dir / "transcript.srt"
        if transcript_path.is_file():
            finalize_speaker_quality(extra, transcript_path.read_text(encoding="utf-8"),
                                     (extra.get("speaker_quality") or {}).get("target_count"))
        extra["speaker_revision"] = int(extra.get("speaker_revision") or 0) + 1
        extra["derived_content_stale"] = True
        extra["derived_content_stale_reason"] = "人物名称已手动修改，摘要与导图待更新"
        artifacts.write(task_id, archive_dir, "metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        if "metadata.json" not in changed:
            changed.append("metadata.json")
        store = get_task_store()
        task = store.get(task_id)
        if task is not None:
            task.result = {**(task.result or {}), "metadata": metadata}
            store.save(task)

    return changed
