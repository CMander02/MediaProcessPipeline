"""Resume speaker decisions using exact inputs and atomically saved results."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from app.core.atomic_file import atomic_write_text


def load_checkpoint(path: Path | None, inputs: dict) -> dict | None:
    if path is None or not path.is_file():
        return None
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict) or saved.get("inputs") != inputs:
        return None
    if saved.get("version") != 1 or not isinstance(saved.get("report"), dict) or not saved["report"]:
        return None
    completed = saved.get("completed_indexes")
    if not isinstance(completed, list) or any(type(index) is not int for index in completed):
        return None
    return saved


def begin_checkpoint_attempt(path: Path | None, inputs: dict, stage: str, execution: dict | None = None) -> None:
    if path is None:
        return
    saved = load_checkpoint(path, inputs)
    value = saved or {"version": 1, "inputs": inputs, "completed_indexes": [], "report": {}}
    value.update(stage=stage, run_id=(saved or {}).get("run_id") or str(uuid4()),
                 attempt=int((saved or {}).get("attempt", 0)) + 1,
                 trigger="service_resume" if saved else "pipeline")
    if execution:
        value.update({key: execution[key] for key in ("run_id", "attempt", "trigger") if key in execution})
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def save_checkpoint(path: Path | None, inputs: dict, report: dict, completed: set[int]) -> None:
    if path is None:
        return
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(previous, dict) or previous.get("inputs") != inputs:
            previous = {}
    except (OSError, ValueError):
        previous = {}
    atomic_write_text(path, json.dumps({
        "version": 1,
        "stage": previous.get("stage", inputs.get("stage", "speaker_inference")),
        "run_id": previous.get("run_id") or str(uuid4()),
        "attempt": previous.get("attempt", 1),
        "trigger": previous.get("trigger", "pipeline"),
        "status": report.get("status", "running"),
        "inputs": inputs,
        "completed_indexes": sorted(completed),
        "report": report,
    }, ensure_ascii=False, indent=2))
