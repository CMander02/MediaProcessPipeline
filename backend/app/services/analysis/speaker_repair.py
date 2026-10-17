"""Queue-owned speaker repair proposals. Existing transcripts stay authoritative."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.artifacts import get_artifact_store
from app.core.database import get_task_store
from app.core.paths import managed_child, task_output_paths
from app.core.settings import get_runtime_settings
from app.services.analysis.speaker_review import review_speakers, apply_speaker_changes
from app.services.analysis.subtitle_speakers import infer_subtitle_speakers, apply_subtitle_speakers
from app.services.analysis.transcript_outputs import _parse_srt, _split_speaker_prefix


def repair_directory(task) -> Path:
    directory = next((p for p in task_output_paths(task) if p.is_dir()), None)
    if directory is None:
        raise ValueError("任务缺少归档目录")
    return managed_child(directory, get_runtime_settings().data_root)


def repair_inputs(task, mode: str) -> dict:
    if mode not in {"failed", "all", "auto"}:
        raise ValueError("不支持的人物复核模式")
    directory = repair_directory(task)
    def read_json(name):
        path = directory / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    metadata = read_json("metadata.json")
    extra = metadata.get("extra", {})
    source = next((directory / name for name in ("transcript_polished.srt", "transcript.srt") if (directory / name).is_file()), None)
    if source is None:
        raise ValueError("任务缺少字幕")
    baseline = source.read_text(encoding="utf-8")
    if mode == "auto":
        labeled = source.name == "transcript_polished.srt" or extra.get("subtitle_source") == "asr" \
            or extra.get("raw_speaker_label_contract") == 1
        if not labeled:
            raise ValueError("这份字幕还没有人物标签，请先整理字幕")
        context = {**read_json("source_context.json"),
                   **{key: metadata.get(key) or "" for key in ("title", "description", "uploader")},
                   "participant_evidence": extra.get("participant_evidence", {})}
        return {"directory": directory, "baseline": baseline, "source_file": source.name, "srt": baseline,
                "context": context, "metadata": metadata}
    if mode == "failed":
        checkpoint = next((directory / name for name in ("subtitle_speakers.checkpoint.json", "speaker_review.checkpoint.json") if (directory / name).is_file()), None)
        if checkpoint is None:
            raise ValueError("旧档没有可核对的目标字幕记录，不支持精确续修；请选择全量人物复核")
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        inputs = saved.get("inputs", {})
        if saved.get("version") != 1 or inputs.get("prompt_version") != 2 or not isinstance(saved.get("completed_indexes"), list):
            raise ValueError("旧人物检查点不兼容，请选择全量人物复核")
        from app.services.analysis.prompts.subtitle_speakers import SUBTITLE_SPEAKERS_SYSTEM_PROMPT
        from app.services.analysis.prompts.speaker_review import SPEAKER_REVIEW_SYSTEM_PROMPT
        expected_prompt = SUBTITLE_SPEAKERS_SYSTEM_PROMPT if checkpoint.name.startswith("subtitle") else SPEAKER_REVIEW_SYSTEM_PROMPT
        if inputs.get("system_prompt") != expected_prompt:
            raise ValueError("人物提示词已变化，请选择全量人物复核")
        raw = directory / "transcript.srt"
        if not raw.is_file() or inputs.get("srt") != raw.read_text(encoding="utf-8"):
            raise ValueError("原始字幕已变化，无法精确续修；请选择全量人物复核")
        from app.core.model_router import resolve_polish_llm_binding
        binding = resolve_polish_llm_binding(get_runtime_settings())
        if inputs.get("provider") != binding.provider or inputs.get("model") != binding.model or inputs.get("parameters") != {k: v for k, v in binding.request_kwargs.items() if k != "api_key"}:
            raise ValueError("人物模型或参数已变化，请选择全量人物复核")
        context = inputs.get("context") or {}
        if any(context.get(key) != metadata.get(key) for key in ("title", "uploader", "description")):
            raise ValueError("来源信息已变化，请选择全量人物复核")
        if "speaker_limit" in inputs and inputs["speaker_limit"] != task.options.get("num_speakers"):
            raise ValueError("人数约束已变化，请选择全量人物复核")
        return {"directory": directory, "baseline": baseline, "source_file": source.name,
                "srt": inputs["srt"], "context": context, "saved": saved,
                "caption": checkpoint.name.startswith("subtitle"), "input_labeled": inputs.get("input_labeled", True)}
    # Without a verified lineage, historical paragraph numbers cannot be mapped
    # back to raw cue IDs. Review the currently edited transcript directly.
    context = {**read_json("source_context.json"),
               **{key: metadata.get(key) for key in ("title", "description", "uploader")},
               "participant_evidence": extra.get("participant_evidence", {})}
    return {"directory": directory, "baseline": baseline, "source_file": source.name,
            "srt": baseline, "context": context, "saved": None,
            "caption": extra.get("speaker_source") == "subtitle_inference",
            "input_labeled": source.name == "transcript_polished.srt" or extra.get("speaker_label_contract") == 1}


def _publish_auto_fill(task, value: dict, filled: str, report: dict, rows: list[dict]) -> dict:
    """Write the filled transcript and its names; labels the user set stay untouched."""
    from app.core.artifacts import _write_lock
    from app.services.analysis.speaker_identity import apply_speaker_names
    from app.services.analysis.transcript_outputs import srt_to_markdown
    from app.services.analysis.speaker_identity_artifacts import identity_hints, voice_suggestions
    from app.services.analysis.speaker_quality import finalize_speaker_quality, quality_report

    directory, store = value["directory"], get_artifact_store()
    mappings = report.get("mappings", []) if report.get("status") in {"completed", "partial"} else []
    names = {item["source_label"]: item["current_name"] for item in mappings}
    final = apply_speaker_names(filled, mappings)
    with _write_lock:
        source = directory / value["source_file"]
        if source.read_text(encoding="utf-8") != value["baseline"]:
            raise ValueError("补全期间字幕被修改，结果没有覆盖当前字幕")
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8")) \
            if (directory / "metadata.json").is_file() else dict(value["metadata"])
        extra = metadata.setdefault("extra", {})
        title = str(metadata.get("title") or "")
        pending: dict[str, str] = {}
        if final != value["baseline"]:
            pending[source.name] = final
            markdown = source.with_suffix(".md")
            # A Markdown export edited by hand is not regenerated.
            if not markdown.is_file() or markdown.read_text(encoding="utf-8").strip() == srt_to_markdown(value["baseline"], title).strip():
                pending[markdown.name] = srt_to_markdown(final, title)
        raw = directory / "transcript.srt"
        if names and raw.is_file() and source.name != raw.name and extra.get("subtitle_source") != "platform":
            original = raw.read_text(encoding="utf-8")
            if (updated := apply_speaker_names(original, mappings)) != original:
                pending[raw.name] = updated
        if names:
            map_path = directory / "speaker_map.json"
            speaker_map = json.loads(map_path.read_text(encoding="utf-8")) if map_path.is_file() else {"version": 1, "mappings": []}
            for mapping in mappings:
                rows_for = [row for row in speaker_map.setdefault("mappings", [])
                            if row.get("current_name", row.get("source_label")) == mapping["source_label"]]
                if not rows_for:
                    rows_for = [{"source_label": mapping["source_label"]}]
                    speaker_map["mappings"].extend(rows_for)
                for row in rows_for:
                    row.update(current_name=mapping["current_name"], reason=mapping.get("reason", ""),
                               status="source_inferred" if mapping.get("basis") == "source_participants" else "text_inferred")
            speaker_map["confirmed"] = False
            pending["speaker_map.json"] = json.dumps(speaker_map, ensure_ascii=False, indent=2)
        target = (extra.get("speaker_quality") or {}).get("target_count")
        quality = quality_report(final, {}, target)
        extra["speaker_quality"] = {key: item for key, item in quality.items() if key != "input_srt"}
        finalize_speaker_quality(extra, final, target)
        labels = set(extra.get("speakers") or [])
        try:
            suggestions = voice_suggestions(str(task.id), directory, final, names)
        except Exception:
            suggestions = []
        hints = identity_hints({**report, "mappings": mappings, "suggestions": suggestions}, labels)
        # Earlier inferences stay unless a voice was renamed since.
        hints["speaker_name_sources"] = {
            **{name: reason for name, reason in (extra.get("speaker_name_sources") or {}).items() if name in labels},
            **hints["speaker_name_sources"],
        }
        extra.update(hints)
        extra["speaker_autofill"] = {"resolved_lines": len(rows), "names": len(mappings), "status": report.get("status")}
        if final != value["baseline"]:
            extra["speaker_revision"] = int(extra.get("speaker_revision") or 0) + 1
            extra["derived_content_stale"] = True
            extra["derived_content_stale_reason"] = "人物已自动补全，已有摘要与导图待更新"
        pending["metadata.json"] = json.dumps(metadata, ensure_ascii=False, indent=2)
        pending["speaker_autofill.json"] = json.dumps(
            {"version": 1, "source_file": source.name, "resolved_lines": rows, "identity": report},
            ensure_ascii=False, indent=2)
        for filename, content in pending.items():
            store.write(task.id, directory, filename, content,
                        expected_content=value["baseline"] if filename == source.name else None)
    return metadata


async def run_auto_fill(task, progress=None) -> dict:
    """Decide pending lines, then put names on anonymous voices, on the current transcript."""
    from app.services.analysis.speaker_identity import infer_speaker_names
    from app.services.analysis.speaker_identity_artifacts import load_speaker_activity
    from app.services.analysis.speaker_resolution import (
        apply_resolutions, cue_acoustic_candidates, cue_voice_similarity, resolve_unassigned,
    )

    value = repair_inputs(task, "auto")
    directory, baseline = value["directory"], value["baseline"]
    if progress:
        await progress("判断待定字幕")
    rows = await resolve_unassigned(baseline, acoustic=cue_acoustic_candidates(directory, baseline),
                                    voice=cue_voice_similarity(directory, baseline))
    filled = apply_resolutions(baseline, rows)
    if progress:
        await progress("识别人物姓名")
    context = {**value["context"], "speaker_activity": load_speaker_activity(directory, filled),
               "analysis": {"content_type": value["context"].get("content_type", "")}}
    report = await infer_speaker_names(filled, context)
    metadata = _publish_auto_fill(task, value, filled, report, rows)
    return {"metadata": metadata, "resolved_lines": len(rows), "names": len(report.get("mappings", [])),
            "suggestions": len(report.get("suggestions", []))}


async def run_speaker_repair(task) -> None:
    from app.core.atomic_file import atomic_write_text
    from app.core.pipeline_steps.state import PipelineStep, _raise_if_cancelled, _update_step_progress
    intent = task.options["speaker_repair"]
    if intent["mode"] == "auto":
        intent["status"] = "running"
        get_task_store().save(task)

        async def step(message):
            await _raise_if_cancelled(task.id)
            await _update_step_progress(task, PipelineStep.SPEAKER_REVIEW, 0.5, f"补全人物：{message}")

        try:
            result = await run_auto_fill(task, step)
            intent["status"] = "applied"
            task.result = {**(task.result or {}), "metadata": result.pop("metadata"),
                           "speaker_repair": {"status": "applied", **result}}
        except Exception as exc:
            # A failed fill leaves the finished task and its transcript as they were.
            import logging
            logging.getLogger(__name__).warning("Speaker auto-fill failed for %s: %s", task.id, exc)
            intent["status"] = "failed"
            task.result = {**(task.result or {}), "speaker_repair": {"status": "failed", "error": str(exc)}}
        get_task_store().save(task)
        return
    value = repair_inputs(task, intent["mode"])
    directory = value["directory"]
    checkpoint = directory / "speaker_repair.checkpoint.json"
    if intent["mode"] == "failed" and (intent.get("status") == "queued" or not checkpoint.is_file()):
        atomic_write_text(checkpoint, json.dumps(value["saved"], ensure_ascii=False, indent=2))
    if intent["mode"] == "all" and intent.get("status") == "queued":
        checkpoint.unlink(missing_ok=True)
    intent["status"] = "running"
    get_task_store().save(task)
    async def progress(done, total):
        await _raise_if_cancelled(task.id)
        await _update_step_progress(task, PipelineStep.SPEAKER_REVIEW, done / max(1, total),
                                    f"人物复核：{done}/{total} 批")
    if value["caption"]:
        report = await infer_subtitle_speakers(value["srt"], context=value["context"],
            speaker_limit=task.options.get("num_speakers"), on_progress=progress,
            input_labeled=value["input_labeled"], checkpoint_path=checkpoint, checkpoint_execution=intent)
        proposed = apply_subtitle_speakers(value["srt"], report)
    else:
        report = await review_speakers(value["srt"], context=value["context"],
                                      on_progress=progress, checkpoint_path=checkpoint, checkpoint_execution=intent)
        proposed = apply_speaker_changes(value["srt"], report["changes"])
    await _raise_if_cancelled(task.id)
    if (directory / value["source_file"]).read_text(encoding="utf-8") != value["baseline"]:
        raise ValueError("复核期间字幕发生变化，候选结果没有覆盖当前字幕")
    old = {cue["index"]: cue for cue in _parse_srt(value["srt"])}
    candidates = []
    for cue in _parse_srt(proposed):
        previous = old[cue["index"]]
        old_name = _split_speaker_prefix(previous["text"])[0] if value["input_labeled"] else None
        new_name = _split_speaker_prefix(cue["text"])[0]
        if old_name != new_name:
            candidates.append({"index": cue["index"], "timestamp": cue["timestamp"],
                               "old_speaker": old_name, "proposed_speaker": new_name,
                               "text": previous["text"]})
    preview = {"version": 1, "run_id": intent["run_id"], "attempt": intent["attempt"],
               "status": "needs_review", "source_file": value["source_file"],
               "baseline_srt": value["baseline"], "input_srt": value["srt"],
               "index_scope": "raw_cues" if intent["mode"] == "failed" else "current_transcript",
               "candidates": candidates, "report": report,
               "message": "候选人物归属需在当前字幕中人工确认；摘要与已有字幕保持原状"}
    get_artifact_store().write(task.id, directory, "speaker_repair.preview.json", json.dumps(preview, ensure_ascii=False, indent=2))
    intent["status"] = "needs_review"
    task.result = {**(task.result or {}), "speaker_repair": {"status": "needs_review", "candidates": len(candidates),
                                                          "preview": "speaker_repair.preview.json"}}
    get_task_store().save(task)
