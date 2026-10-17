from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import database, pipeline, queue, settings
from app.models import Task, TaskStatus, TaskType


@pytest.mark.asyncio
@pytest.mark.parametrize("download_worker", [False, True], ids=["gpu", "download"])
async def test_immediate_resume_waits_for_old_pipeline_cancellation_cleanup(tmp_path, monkeypatch, download_worker):
    monkeypatch.setattr(settings, "_runtime_settings", settings.RuntimeSettings(data_root=str(tmp_path)))
    monkeypatch.setattr(queue, "_flush_gpu_models", lambda: None)
    database.reset_db_path(tmp_path)
    store = database.get_task_store()
    destination = tmp_path / "archive"
    destination.mkdir()
    (destination / "metadata.json").write_text('{"status":"queued"}', encoding="utf-8")
    task = Task(task_type=TaskType.PIPELINE, source="https://example.com/video" if download_worker else str(tmp_path / "audio.wav"),
                result={"output_dir": str(destination)})
    store.save(task)
    task_queue = queue.TaskQueue()
    task_queue.set_pipeline(pipeline.process_task)
    started, cancelling, allow_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def stage(current, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelling.set()
                await allow_cleanup.wait()
                raise
        current.completed_steps = ["archive"]
        current.result = {"output_dir": str(destination)}

    monkeypatch.setattr(pipeline, "run_pipeline", stage)
    task_queue._running = True
    work_queue = task_queue._download_queue if download_worker else task_queue._gpu_queue
    worker = asyncio.create_task(task_queue._download_worker() if download_worker else task_queue._gpu_worker())
    try:
        await task_queue.submit(task.id)
        await asyncio.wait_for(started.wait(), 3)
        assert await task_queue.pause(task.id)
        resume = asyncio.create_task(task_queue.resume(task.id))
        await asyncio.wait_for(cancelling.wait(), 3)
        await asyncio.sleep(0.02)
        assert not resume.done()
        assert store.get(task.id).status == TaskStatus.PAUSED
        allow_cleanup.set()
        assert await asyncio.wait_for(resume, 3)
        await asyncio.wait_for(work_queue.join(), 3)
        assert calls == 2
        assert store.get(task.id).status == TaskStatus.COMPLETED
        assert store.get(task.id).error is None
        assert all(event["event_type"] != "cancelled" for event in store.list_events(task.id))
        assert json.loads((destination / "metadata.json").read_text())["status"] != "cancelled"
    finally:
        allow_cleanup.set()
        task_queue._running = False
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        database.close_db()
