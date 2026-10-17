import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.analysis import speaker_repair as repair
from app.services.analysis.speaker_quality import finalize_speaker_quality

SRT = "1\n00:00:00,000 --> 00:00:01,000\n[A] 原文。\n\n2\n00:00:01,000 --> 00:00:02,000\n[B] 另一人。"


@pytest.mark.asyncio
async def test_new_failed_repair_replaces_stale_work_checkpoint_and_only_writes_preview(tmp_path, monkeypatch):
    source = tmp_path / "transcript_polished.srt"
    source.write_text(SRT, encoding="utf-8")
    checkpoint = tmp_path / "speaker_repair.checkpoint.json"
    checkpoint.write_text('{"stale":true}', encoding="utf-8")
    saved = {"completed_indexes": [1], "inputs": {"srt": SRT}}
    monkeypatch.setattr(repair, "repair_inputs", lambda *args: {
        "directory": tmp_path, "baseline": SRT, "source_file": source.name,
        "srt": SRT, "context": {}, "saved": saved, "caption": False, "input_labeled": True,
    })
    monkeypatch.setattr(repair, "get_task_store", lambda: SimpleNamespace(save=lambda task: None))
    monkeypatch.setattr(repair, "get_artifact_store", lambda: SimpleNamespace(
        write=lambda task_id, directory, name, content: (directory / name).write_text(content, encoding="utf-8")))
    import app.core.pipeline_steps.state as state
    monkeypatch.setattr(state, "_raise_if_cancelled", AsyncMock())

    async def review(*args, **kwargs):
        assert json.loads(checkpoint.read_text(encoding="utf-8")) == saved
        return {"changes": [{"index": 1, "from_speaker": "A", "speaker": "B"}]}

    monkeypatch.setattr(repair, "review_speakers", review)
    task = SimpleNamespace(id="test", options={"speaker_repair": {
        "mode": "failed", "status": "queued", "run_id": "new", "attempt": 2}}, result={"summary": "保留"})
    await repair.run_speaker_repair(task)
    assert source.read_text(encoding="utf-8") == SRT
    preview = json.loads((tmp_path / "speaker_repair.preview.json").read_text(encoding="utf-8"))
    assert preview["candidates"][0]["proposed_speaker"] == "B"
    assert preview["index_scope"] == "raw_cues"
    assert task.result["summary"] == "保留"
    assert task.options["speaker_repair"]["status"] == "needs_review"


@pytest.mark.asyncio
async def test_concurrent_edit_prevents_repair_preview_publication(tmp_path, monkeypatch):
    source = tmp_path / "transcript_polished.srt"
    source.write_text(SRT, encoding="utf-8")
    monkeypatch.setattr(repair, "repair_inputs", lambda *args: {
        "directory": tmp_path, "baseline": SRT, "source_file": source.name,
        "srt": SRT, "context": {}, "saved": None, "caption": False, "input_labeled": True,
    })
    monkeypatch.setattr(repair, "get_task_store", lambda: SimpleNamespace(save=lambda task: None))
    import app.core.pipeline_steps.state as state
    monkeypatch.setattr(state, "_raise_if_cancelled", AsyncMock())

    async def review(*args, **kwargs):
        source.write_text(SRT + "人工补充", encoding="utf-8")
        return {"changes": []}

    monkeypatch.setattr(repair, "review_speakers", review)
    task = SimpleNamespace(id="test", options={"speaker_repair": {
        "mode": "all", "status": "queued", "run_id": "new", "attempt": 1}}, result={})
    with pytest.raises(ValueError, match="字幕发生变化"):
        await repair.run_speaker_repair(task)
    assert not (tmp_path / "speaker_repair.preview.json").exists()
    assert source.read_text(encoding="utf-8").endswith("人工补充")


def test_final_count_gate_preserves_underfill_and_rejects_extra_people():
    extra = {"speaker_quality": {"unassigned_ranges": [{"start": 1, "end": 2}]}}
    finalize_speaker_quality(extra, SRT, 3)
    assert extra["speaker_quality"]["status"] == "constraint_conflict"
    assert extra["speaker_quality"]["observed_count"] == 2
    with pytest.raises(ValueError, match="超过"):
        finalize_speaker_quality({}, SRT + "\n\n2\n00:00:01,000 --> 00:00:02,000\n[B] 另一人", 1)


@pytest.mark.asyncio
async def test_repair_resume_routes_to_serial_postprocess_queue(monkeypatch):
    from app.core import queue as queues
    from app.models import Task, TaskType
    task = Task(task_type=TaskType.PIPELINE, source="https://example.com/video",
                options={"speaker_repair": {"status": "running"}})
    store = SimpleNamespace(get=lambda _: task, update_status=lambda *args, **kwargs: None)
    monkeypatch.setattr(queues, "get_task_store", lambda: store)
    monkeypatch.setattr(queues, "get_event_bus", lambda: SimpleNamespace(publish=AsyncMock()))
    queue = queues.TaskQueue()
    await queue.submit(task.id)
    assert queue._postprocess_queue.get_nowait() == task.id
    assert queue._download_queue.empty() and queue._gpu_queue.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize("action,status", [("cancel", "running"), ("pause", "running"), ("cancel", "queued")])
async def test_stopping_a_repair_returns_the_finished_task_to_completed(tmp_path, monkeypatch, action, status):
    import asyncio
    from app.core import queue as queues
    from app.models import Task, TaskStatus, TaskType
    (tmp_path / "metadata.json").write_text(json.dumps({"status": "processing", "title": "x"}), encoding="utf-8")
    task = Task(task_type=TaskType.PIPELINE, source="https://example.com/video",
                status=TaskStatus.PROCESSING if status == "running" else TaskStatus.QUEUED,
                completed_steps=["download", "transcribe", "speaker_review", "polish", "analyze", "archive"],
                flow={"status": "processing", "steps": [{"id": "transcribe"}, {"id": "archive"}], "current_step": "speaker_review"},
                options={"speaker_repair": {"mode": "auto", "status": status}}, result={"output_dir": str(tmp_path)})
    saved = []
    store = SimpleNamespace(get=lambda _: task, save=saved.append, update_status=lambda *args, **kwargs: pytest.fail("generic stop"))
    events = AsyncMock()
    monkeypatch.setattr(queues, "get_task_store", lambda: store)
    monkeypatch.setattr(queues, "get_event_bus", lambda: SimpleNamespace(publish=events))
    import app.core.pipeline_steps.artifacts as artifacts
    monkeypatch.setattr(artifacts, "get_artifact_store", lambda: SimpleNamespace(
        write=lambda task_id, directory, name, content: (directory / name).write_text(content, encoding="utf-8")))
    queue = queues.TaskQueue()
    running = asyncio.get_running_loop().create_future()
    queue._running_tasks[task.id] = running
    await queue._postprocess_queue.put(task.id)
    assert await getattr(queue, action)(task.id)
    assert task.status == TaskStatus.COMPLETED and task.progress == 1.0
    assert task.options["speaker_repair"]["status"] == "cancelled"
    assert task.flow["status"] == "completed" and task.flow["completed_steps"] == ["transcribe", "archive"]
    assert saved == [task] and running.cancelled() and queue._postprocess_queue.empty()
    assert json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))["status"] == "completed"
    assert events.call_args.args[0].event_type == "completed"
