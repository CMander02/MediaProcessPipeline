"""Explicitly confirm current subtitle labels without rewriting transcript prose."""
from __future__ import annotations

import json

from app.core.artifacts import _write_lock, get_artifact_store
from app.core.atomic_file import atomic_write_text
from app.core.database import get_task_store
from app.services.analysis.speaker_repair import repair_directory
from app.services.analysis.speaker_quality import quality_report, finalize_speaker_quality


def reconcile_speakers(task, expected_srt: str) -> dict:
    if task.status != "completed":
        raise ValueError("请等待任务完成后确认人物")
    directory = repair_directory(task)
    source = next((directory / name for name in ("transcript_polished.srt", "transcript.srt") if (directory / name).is_file()), None)
    if source is None:
        raise ValueError("任务缺少字幕")
    journal_path = directory / "speaker_reconciliation.pending.json"
    store = get_artifact_store()
    with _write_lock:
        if source.read_text(encoding="utf-8") != expected_srt:
            raise ValueError("字幕已更新，请刷新后重新确认")
        if journal_path.is_file():
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            if journal["baseline_srt"] != expected_srt or journal["source_file"] != source.name:
                raise ValueError("上次人物对账未完成且字幕已变化，请先检查 speaker_reconciliation.pending.json")
        else:
            metadata_path = directory / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
            extra = metadata.setdefault("extra", {})
            if source.name == "transcript.srt" and extra.get("subtitle_source") != "asr" and extra.get("raw_speaker_label_contract") != 1:
                raise ValueError("原始字幕没有人物标签约定，请先生成或编辑整理字幕")
            revision = int(extra.get("speaker_revision") or 0) + 1
            quality = quality_report(expected_srt, {}, task.options.get("num_speakers"))
            extra["speaker_quality"] = {key: value for key, value in quality.items() if key != "input_srt"}
            finalize_speaker_quality(extra, expected_srt, task.options.get("num_speakers"))
            labels = extra["speakers"]
            supersedes = [name for name in ("subtitle_speakers.json", "speaker_review.json", "speaker_identity.json") if (directory / name).is_file()]
            report = {"version": 1, "revision": revision, "status": "confirmed", "source_file": source.name,
                      "baseline_srt": expected_srt, "speakers": labels, "supersedes": supersedes,
                      "reason": "用户确认当前字幕中的人物标签；原文与时间保持不变"}
            previous_map = directory / "speaker_map.json"
            old_map = json.loads(previous_map.read_text(encoding="utf-8")) if previous_map.is_file() else {}
            mappings = []
            for index, name in enumerate(labels):
                matches = [item for item in old_map.get("mappings", []) if item.get("current_name") == name]
                mappings.extend([{**item, "status": "manual_confirmed"} for item in matches] or [
                    {"source_label": f"MANUAL_{revision}_{index}", "current_name": name, "status": "manual_confirmed"}])
            extra.update(speaker_revision=revision, speaker_reconciliation={"revision": revision, "status": "confirmed", "supersedes": supersedes},
                         derived_content_stale=True, derived_content_stale_reason="人物归属已确认，已有摘要与导图待更新")
            payloads = {
                "speaker_map.json": {"version": 1, "source": "manual_reconciliation", "confirmed": True, "mappings": mappings},
                "speaker_reconciliation.json": report,
                "metadata.json": metadata,
            }
            entries = [{"filename": name, "before": (directory / name).read_text(encoding="utf-8") if (directory / name).is_file() else None,
                        "after": json.dumps(payload, ensure_ascii=False, indent=2)} for name, payload in payloads.items()]
            journal = {"version": 1, "source_file": source.name, "baseline_srt": expected_srt, "entries": entries}
            atomic_write_text(journal_path, json.dumps(journal, ensure_ascii=False, indent=2))
        # Check every file before resuming a partially written publication.
        for entry in journal["entries"]:
            path = directory / entry["filename"]
            current = path.read_text(encoding="utf-8") if path.is_file() else None
            if current not in (entry["before"], entry["after"]):
                raise ValueError(f"人物对账遇到外部修改：{entry['filename']}，已保留恢复记录")
        for entry in journal["entries"]:
            store.write(task.id, directory, entry["filename"], entry["after"])
        metadata = json.loads(journal["entries"][-1]["after"])
        task.result = {**(task.result or {}), "metadata": metadata}
        get_task_store().save(task)
        atomic_write_text(directory / "speaker_reconciliation.backup.json", json.dumps(journal, ensure_ascii=False, indent=2))
        journal_path.unlink()
    return {"status": "confirmed", "speakers": metadata["extra"]["speakers"], "revision": metadata["extra"]["speaker_revision"]}
