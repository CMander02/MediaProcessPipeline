"""Apply transcript-grounded names to a task's existing archive."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.artifacts import get_artifact_store
from app.core.database import get_task_store
from app.core.workspace_lifecycle import uses_workspace
from app.models import TaskStatus
from app.services.analysis.speaker_identity import apply_speaker_names, infer_speaker_names
from app.services.analysis.transcript_outputs import srt_to_markdown


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def load_speaker_activity(output_dir: Path | str, fallback_srt: str) -> list[dict]:
    """Use acoustic speaking time where available, retaining the stable speaker map."""
    from app.services.analysis.speaker_identity import build_speaker_activity

    directory = Path(output_dir)
    diarization = _read_json(directory / "diarization.json")
    raw_path = directory / "transcript.srt"
    raw_srt = raw_path.read_text(encoding="utf-8") if raw_path.is_file() else fallback_srt
    activity = build_speaker_activity(
        raw_srt,
        turns=diarization.get("turns"),
        speaker_map=_read_json(directory / "speaker_map.json"),
    )
    return activity or build_speaker_activity(fallback_srt)


def _replace_references(value: Any, names: dict[str, str]) -> Any:
    """Replace whole placeholder references in derived analysis, preserving IDs."""
    if isinstance(value, str):
        pattern = r"(?<![A-Za-z0-9_-])(" + "|".join(map(re.escape, names)) + r")(?![A-Za-z0-9_-])"
        return re.sub(pattern, lambda match: names[match[1]], value)
    if isinstance(value, list):
        return [_replace_references(item, names) for item in value]
    if isinstance(value, dict):
        return {
            key: item if key in {"source_label", "id", "person_id", "task_id"}
            else _replace_references(item, names)
            for key, item in value.items()
        }
    return value


def persist_speaker_names(task_id: str, output_dir: Path | str, report: dict) -> list[str]:
    """Write names and evidence through the existing file/SQLite artifact store."""
    directory = Path(output_dir)
    artifacts = get_artifact_store()
    mappings = report.get("mappings", []) if report["status"] == "completed" else []
    names = {item["source_label"]: item["current_name"] for item in mappings}
    pending: dict[str, str] = {}
    metadata = _read_json(directory / "metadata.json")

    if names:
        for filename in ("transcript.srt", "transcript_polished.srt"):
            path = directory / filename
            if not path.is_file():
                continue
            original = path.read_text(encoding="utf-8")
            updated = apply_speaker_names(original, mappings)
            if updated == original:
                continue
            pending[filename] = updated
            md_name = path.with_suffix(".md").name
            md_path = directory / md_name
            title = str(metadata.get("title") or "")
            if md_path.exists():
                markdown = md_path.read_text(encoding="utf-8")
                # Regenerate canonical exports; preserve edits to standalone Markdown.
                canonical = srt_to_markdown(original, title)
                if markdown.strip() == canonical.strip():
                    pending[md_name] = srt_to_markdown(updated, title)
                else:
                    pending[md_name] = re.sub(
                        r"(?m)^(\s*(?:\*\*)?)\[([^\]\r\n]+)\]",
                        lambda match: f"{match[1]}[{names.get(match[2], match[2])}]",
                        markdown,
                    )
            else:
                pending[md_name] = srt_to_markdown(updated, title)

        for filename in (
            "analysis.json", "summary.json", "summary.md", "mindmap.json",
            "mindmap.md", "detail.md",
        ):
            path = directory / filename
            if not path.is_file():
                continue
            original = path.read_text(encoding="utf-8")
            value = json.loads(original) if path.suffix == ".json" else original
            updated_value = _replace_references(value, names)
            if updated_value != value:
                pending[filename] = (
                    json.dumps(updated_value, ensure_ascii=False, indent=2)
                    if path.suffix == ".json" else updated_value
                )

        speaker_map = _read_json(directory / "speaker_map.json") or {
            "version": 1, "mappings": [], "confirmed": False,
        }
        for mapping in mappings:
            matches = [
                item for item in speaker_map["mappings"]
                if item.get("current_name", item.get("source_label"))
                in {mapping["source_label"], mapping["current_name"]}
            ]
            if not matches:
                matches = [{"source_label": mapping["source_label"]}]
                speaker_map["mappings"].extend(matches)
            for item in matches:
                item.update({
                    "current_name": mapping["current_name"], "status": "text_inferred",
                    "evidence_indexes": mapping["evidence_indexes"], "reason": mapping["reason"],
                })
        speaker_map["confirmed"] = False
        pending["speaker_map.json"] = json.dumps(speaker_map, ensure_ascii=False, indent=2)

    if metadata:
        extra = metadata.setdefault("extra", {})
        if names:
            for target in (metadata, extra):
                if isinstance(target.get("speakers"), list):
                    target["speakers"] = [names.get(label, label) for label in target["speakers"]]
        extra["speaker_identity"] = {
            "status": report["status"], "names_identified": len(mappings),
        }
        pending["metadata.json"] = json.dumps(metadata, ensure_ascii=False, indent=2)

    pending["speaker_identity.json"] = json.dumps(report, ensure_ascii=False, indent=2)
    changed = []
    for filename, content in pending.items():
        path = directory / filename
        if path.exists() and path.read_text(encoding="utf-8") == content:
            continue
        artifacts.write(task_id, directory, filename, content)
        changed.append(filename)
    return changed


@uses_workspace
async def identify_task_speakers(task_id: UUID) -> dict:
    """Identify names in a completed task without repeating recognition or polish."""
    store = get_task_store()
    task = store.get(task_id)
    if task is None:
        raise FileNotFoundError("任务不存在")
    if task.status != TaskStatus.COMPLETED:
        raise ValueError("请在任务处理完成后识别说话人姓名")
    result = task.result or {}
    archive = result.get("archive") or {}
    output_dir = result.get("output_dir") or archive.get("output_dir")
    if not output_dir or not Path(output_dir).is_dir():
        raise FileNotFoundError("任务归档目录不存在")
    directory = Path(output_dir)
    source = next((directory / name for name in (
        "transcript_polished.srt", "transcript.srt",
    ) if (directory / name).is_file()), None)
    if source is None:
        raise FileNotFoundError("任务没有可识别姓名的字幕文件")
    original = source.read_text(encoding="utf-8")
    metadata = _read_json(directory / "metadata.json") or result.get("metadata", {})
    context = {
        **_read_json(directory / "source_context.json"),
        "title": metadata.get("title", ""),
        "description": metadata.get("description", ""),
        "analysis": _read_json(directory / "analysis.json") or result.get("analysis", {}),
        "summary": _read_json(directory / "summary.json"),
        "speaker_activity": load_speaker_activity(directory, original),
    }
    report = await infer_speaker_names(original, context=context)
    current = store.get(task_id)
    if (
        current is None or current.status != TaskStatus.COMPLETED
        or not source.is_file() or source.read_text(encoding="utf-8") != original
    ):
        raise ValueError("识别期间字幕或任务状态已更新，请重新识别")
    changed = persist_speaker_names(str(task_id), directory, report)
    current.result = dict(current.result or {})
    if (directory / "metadata.json").exists():
        saved_metadata = _read_json(directory / "metadata.json")
        saved_metadata.pop("status", None)
        current.result["metadata"] = saved_metadata
    if (directory / "analysis.json").exists():
        current.result["analysis"] = _read_json(directory / "analysis.json")
    store.save(current)
    from app.core.pipeline_steps.artifacts import _emit_file_ready, _schedule_kb_index

    for filename in changed:
        await _emit_file_ready(current, filename, str(directory / filename))
    if report.get("mappings"):
        _schedule_kb_index(str(task_id), str(directory))
    return {**report, "changed_files": changed}
