"""Apply transcript-grounded names to a task's existing archive."""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import UUID

from app.core.artifacts import get_artifact_store
from app.core.workspace_lifecycle import uses_workspace
from app.services.analysis.speaker_identity import apply_speaker_names
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


def voice_suggestions(task_id: str, output_dir: Path | str, srt: str, renames: dict[str, str] | None = None) -> list[dict]:
    """Anonymous voices that sound like another voice of the same task: a likely split of one person.

    Uses the task's stored voiceprints; similar enough to suggest, not enough to merge on its own.
    """
    import numpy as np

    from app.core.settings import get_runtime_settings
    from app.services.analysis.speaker_identity import is_placeholder_speaker
    from app.services.analysis.speaker_quality import speaker_labels
    from app.services.voiceprint import get_voiceprint_store

    current = set(speaker_labels(srt))
    if not any(is_placeholder_speaker(label) for label in current):
        return []
    names = {
        row["source_label"]: row.get("current_name") or row["source_label"]
        for row in _read_json(Path(output_dir) / "speaker_map.json").get("mappings", [])
        if isinstance(row, dict) and row.get("source_label")
    }
    store = get_voiceprint_store()
    speakers = store.list_task_speakers(str(task_id))
    vectors = store.sample_embeddings([row["sample_id"] for row in speakers])
    voices: dict[str, list] = {}
    for row in speakers:
        name = names.get(row["speaker_label"], row.get("person_name") or row["speaker_label"])
        name = (renames or {}).get(name, name)
        if name in current and row["sample_id"] in vectors:
            vector = vectors[row["sample_id"]]
            voices.setdefault(name, []).append(vector / (np.linalg.norm(vector) or 1))
    threshold = float(getattr(get_runtime_settings(), "voiceprint_suggest_threshold", 0.60))
    suggestions = []
    for name, own in voices.items():
        if not is_placeholder_speaker(name):
            continue
        scored = [(max(float(np.dot(a, b)) for a in own for b in other), target)
                  for target, other in voices.items() if target != name]
        if not scored:
            continue
        similarity, target = max(scored)
        if similarity >= threshold:
            suggestions.append({"source_label": name, "current_name": target, "similarity": round(similarity, 3),
                                "reason": f"声音相似度 {similarity:.2f}"})
    return suggestions


def identity_hints(report: dict, labels: set[str] | list[str]) -> dict:
    """What the result page shows next to names: why a name was inferred, and voices that may be one person."""
    mappings = report.get("mappings", []) if report.get("status") in {"completed", "partial"} else []
    sources = {item["current_name"]: item.get("reason", "") for item in mappings if item.get("basis") == "source_participants"}
    suggestions = [row for row in report.get("suggestions", []) if row.get("source_label") in set(labels)]
    participants = [row["name"] for row in report.get("participants", []) if isinstance(row, dict) and row.get("name")]
    return {"speaker_name_sources": sources, "speaker_suggestions": suggestions, "speaker_participants": participants}


def persist_speaker_names(task_id: str, output_dir: Path | str, report: dict) -> list[str]:
    """Write names and evidence through the existing file/SQLite artifact store."""
    directory = Path(output_dir)
    artifacts = get_artifact_store()
    mappings = report.get("mappings", []) if report["status"] in {"completed", "partial"} else []
    names = {item["source_label"]: item["current_name"] for item in mappings}
    pending: dict[str, str] = {}
    metadata = _read_json(directory / "metadata.json")

    if names:
        for filename in ("transcript.srt", "transcript_polished.srt"):
            if filename == "transcript.srt" and (report.get("preserve_raw") or report.get("raw_input_labeled") is False):
                continue
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
                    "current_name": mapping["current_name"],
                    "status": "source_inferred" if mapping.get("basis") == "source_participants" else "text_inferred",
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
        labels = {name for name in (names.get(label, label) for label in report.get("input_labels") or [])}
        extra.update(identity_hints(report, labels | set(names)))
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
    """Kept for callers that need a clear migration error."""
    raise ValueError("人物自动识别在润色前完成；已有结果请手动编辑人物名称")
