import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core import pipeline as pipeline_core  # noqa: E402
from app.core.pipeline import PipelineStep  # noqa: E402
from app.models import Task, TaskStatus, TaskType  # noqa: E402


class RecordingStore:
    def __init__(self):
        self.updates: list[tuple[object, object, dict]] = []

    def update_status(self, task_id, status, **kwargs):
        self.updates.append((task_id, status, kwargs))


class RecordingBus:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


def _task_with_url_flow() -> Task:
    return Task(
        task_type=TaskType.PIPELINE,
        status=TaskStatus.PROCESSING,
        source="https://example.com/video",
        completed_steps=["download"],
        flow={
            "id": "url_platform_video_subtitle",
            "label": "平台字幕优先",
            "platform": "bilibili_video",
            "current_step": "download",
            "current_step_index": 2,
            "current_step_label": "下载媒体",
            "total_steps": 6,
            "progress": 2 / 6,
            "status": "processing",
            "steps": [
                {"id": "resolve", "label": "识别平台"},
                {"id": "subtitle_probe", "label": "探测字幕"},
                {"id": "download", "label": "下载媒体"},
                {"id": "transcribe", "label": "处理字幕"},
                {"id": "analyze", "label": "生成摘要与导图"},
                {"id": "archive", "label": "归档"},
            ],
            "completed_steps": ["resolve", "download"],
        },
    )


@pytest.mark.asyncio
async def test_uvr_fractional_progress_updates_task_flow_and_events(monkeypatch):
    store = RecordingStore()
    bus = RecordingBus()
    monkeypatch.setattr(pipeline_core, "get_task_store", lambda: store)
    monkeypatch.setattr(pipeline_core, "get_event_bus", lambda: bus)
    task = _task_with_url_flow()

    await pipeline_core._update_step_progress(
        task,
        PipelineStep.SEPARATE,
        0.5,
        "分离人声：第 4/8 段",
        details={
            "phase": "separating",
            "current_chunk": 4,
            "completed_chunks": 3,
            "total_chunks": 8,
        },
    )

    assert task.progress == pytest.approx((1 + 0.5) / 6)
    assert task.message == "分离人声：第 4/8 段"
    assert task.current_step == PipelineStep.SEPARATE
    assert task.flow["current_step"] == "transcribe"
    assert task.flow["step_progress"] == pytest.approx(0.5)
    assert task.flow["progress"] == pytest.approx((2 + 0.5) / 6)

    assert [event.event_type for event in bus.events] == ["step", "flow_step"]
    assert bus.events[0].data["step_progress"] == pytest.approx(0.5)
    assert bus.events[0].data["current_chunk"] == 4
    assert bus.events[1].data["flow"]["progress"] == pytest.approx((2 + 0.5) / 6)
    assert store.updates[-1][2]["flow"]["step_progress"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_flow_keeps_uvr_fraction_until_transcription_completes(monkeypatch):
    store = RecordingStore()
    bus = RecordingBus()
    monkeypatch.setattr(pipeline_core, "get_task_store", lambda: store)
    monkeypatch.setattr(pipeline_core, "get_event_bus", lambda: bus)
    task = _task_with_url_flow()

    await pipeline_core._update_step_progress(
        task,
        PipelineStep.SEPARATE,
        0.75,
        "分离人声：第 7/8 段",
    )
    await pipeline_core._update_step(task, PipelineStep.SEPARATE, completed=True)
    await pipeline_core._update_step(task, PipelineStep.TRANSCRIBE)

    assert task.flow["current_step"] == "transcribe"
    assert task.flow["step_progress"] == pytest.approx(0.75)

    await pipeline_core._update_step(task, PipelineStep.TRANSCRIBE, completed=True)

    assert "step_progress" not in task.flow
    assert "transcribe" in task.flow["completed_steps"]
    assert task.flow["progress"] == pytest.approx(3 / 6)
