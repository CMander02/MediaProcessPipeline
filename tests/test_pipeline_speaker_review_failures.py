from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.pipeline_steps import speaker_review as stage
from app.core.pipeline_steps.context import PipelineContext
from app.core.settings import RuntimeSettings
from app.models import MediaMetadata, Task, TaskType
from app.services.analysis.transcript_outputs import _parse_srt

SRT = "1\n00:00:00,000 --> 00:00:02,000\n这个问题还需要研究。"


def caption_report(*, failures=False, status=None):
    return {
        "status": status or ("failed" if failures else "completed"),
        "source": "subtitle_inference",
        "input_segments": 1,
        "speakers": [],
        "assignments": [] if failures else [{"index": 1, "speaker": None}],
        "unassigned_segments": 1,
        "failed_chunks": [{"chunk": 1, "error": "invalid JSON"}] if failures else [],
    }


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    ctx = PipelineContext(
        task=Task(task_type=TaskType.PIPELINE, source="fixture.wav"),
        rt=RuntimeSettings(),
        task_dir=tmp_path,
        metadata=MediaMetadata(title="访谈"),
        source_context={"title": "访谈"},
        srt=SRT,
        has_subtitle=True,
    )
    infer = AsyncMock(return_value=caption_report())
    review = AsyncMock()
    update = AsyncMock()
    event = AsyncMock()

    async def write(task, directory, filename, text):
        (directory / filename).write_text(text, encoding="utf-8")

    monkeypatch.setattr(stage, "infer_subtitle_speakers", infer)
    monkeypatch.setattr(stage, "review_speakers", review)
    monkeypatch.setattr(stage, "_update_step", update)
    monkeypatch.setattr(stage, "_emit_timeline_event", event)
    monkeypatch.setattr(stage, "_raise_if_cancelled", AsyncMock())
    monkeypatch.setattr(stage, "_write_text_artifact", write)
    return SimpleNamespace(ctx=ctx, infer=infer, review=review, update=update, event=event)


def save_report(runtime, report):
    from app.services.analysis.prompts.subtitle_speakers import SUBTITLE_SPEAKERS_SYSTEM_PROMPT
    report["input_contract"] = {
        **stage._speaker_model_inputs(runtime.ctx), "prompt": SUBTITLE_SPEAKERS_SYSTEM_PROMPT,
        "version": 2, "srt": runtime.ctx.srt,
        "context": {**runtime.ctx.source_context, "title": runtime.ctx.metadata.title,
                    "uploader": runtime.ctx.metadata.uploader, "description": runtime.ctx.metadata.description, "mixed_speaker_cues": []},
        "speaker_limit": None,
    }
    path = runtime.ctx.task_dir / "subtitle_speakers.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["partial", "failed"])
async def test_caption_failures_persist_diagnostics_and_complete_with_warning(runtime, status):
    report = caption_report(failures=True, status=status)
    runtime.infer.return_value = report

    await stage.review_transcript_speakers(runtime.ctx)

    saved = json.loads((runtime.ctx.task_dir / "subtitle_speakers.json").read_text(encoding="utf-8"))
    assert saved == report
    assert runtime.ctx.metadata.extra["speaker_inference"]["failed_chunks"] == 1
    runtime.event.assert_awaited_once()
    assert runtime.event.await_args.kwargs["level"] == "warning"
    assert runtime.update.await_args.kwargs["completed"] is True
    original, reviewed = _parse_srt(SRT)[0], _parse_srt(runtime.ctx.reviewed_srt)[0]
    assert reviewed == {**original, "text": "[unknown] " + original["text"]}


@pytest.mark.asyncio
async def test_successful_unknown_speaker_is_allowed(runtime):
    await stage.review_transcript_speakers(runtime.ctx)

    assert _parse_srt(runtime.ctx.reviewed_srt)[0]["text"] == "[unknown] 这个问题还需要研究。"
    assert stage._transcript_speaker_labels(runtime.ctx.reviewed_srt) == set()
    runtime.event.assert_not_awaited()
    assert runtime.ctx.metadata.extra["speaker_inference"]["unassigned_segments"] == 1
    assert runtime.update.await_args.kwargs["completed"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["partial", "failed"])
@pytest.mark.parametrize("polish_completed", [False, True])
async def test_failed_report_resumes_without_rerunning_or_invalidating_completed_outputs(
    runtime, status, polish_completed,
):
    report = caption_report(failures=True, status=status)
    saved_path = save_report(runtime, report)
    previous_done = {"download", "separate", "transcribe", "voiceprint"}
    if polish_completed:
        previous_done.update({"speaker_review", "polish", "analyze", "archive"})
    runtime.ctx.done = previous_done.copy()
    runtime.ctx.task.completed_steps = list(previous_done)
    runtime.ctx.polished = "saved subtitle"
    runtime.ctx.polished_md = "saved transcript"

    await stage.review_transcript_speakers(runtime.ctx)

    assert runtime.ctx.done == previous_done
    assert set(runtime.ctx.task.completed_steps) == previous_done
    if polish_completed:
        runtime.infer.assert_not_awaited()
        assert runtime.ctx.polished == "saved subtitle"
        assert runtime.ctx.metadata.extra["speaker_quality"]["status"] == "needs_review"
        assert json.loads(saved_path.read_text(encoding="utf-8")) == report
    else:
        runtime.infer.assert_awaited_once()
        assert runtime.infer.call_args.kwargs["checkpoint_path"].name == "subtitle_speakers.checkpoint.json"
        assert json.loads(saved_path.read_text(encoding="utf-8"))["status"] == "completed"


@pytest.mark.asyncio
async def test_partial_report_keeps_valid_assignments_and_failed_cue_text(runtime):
    runtime.ctx.srt = (
        SRT + "\n\n2\n00:00:02,000 --> 00:00:04,000\n[原标签] 下一段继续。"
    )
    report = caption_report(failures=True, status="partial")
    report["speakers"] = [{"id": "SPEAKER_00", "name": "甲"}]
    report["assignments"] = [{"index": 1, "speaker": "SPEAKER_00"}]
    report["input_segments"] = 2
    runtime.infer.return_value = report

    await stage.review_transcript_speakers(runtime.ctx)

    original = _parse_srt(runtime.ctx.srt)
    reviewed = _parse_srt(runtime.ctx.reviewed_srt)
    assert reviewed == [
        {**original[0], "text": "[甲] 这个问题还需要研究。"}, original[1],
    ]
    assert runtime.update.await_args.kwargs["completed"] is True


@pytest.mark.asyncio
async def test_cancelled_resume_does_not_apply_saved_partial_report(runtime, monkeypatch):
    save_report(runtime, caption_report(failures=True))
    monkeypatch.setattr(stage, "_raise_if_cancelled", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await stage.review_transcript_speakers(runtime.ctx)
    runtime.infer.assert_not_awaited()
    runtime.update.assert_not_awaited()
    assert runtime.ctx.reviewed_srt == ""


@pytest.mark.asyncio
async def test_successful_checkpoint_reuses_report(runtime):
    save_report(runtime, caption_report())
    runtime.ctx.done.add("speaker_review")

    await stage.review_transcript_speakers(runtime.ctx)

    runtime.infer.assert_not_awaited()
    runtime.update.assert_not_awaited()
    assert _parse_srt(runtime.ctx.reviewed_srt)[0]["text"] == "[unknown] 这个问题还需要研究。"


@pytest.mark.asyncio
@pytest.mark.parametrize("polish_completed", [False, True])
async def test_speaker_checkpoint_preserves_only_completed_polish(runtime, polish_completed):
    save_report(runtime, caption_report())
    runtime.ctx.done = {"speaker_review"}
    if polish_completed:
        runtime.ctx.done.add("polish")
    (runtime.ctx.task_dir / "transcript.srt").write_text(SRT, encoding="utf-8")
    polished_path = runtime.ctx.task_dir / "transcript_polished.srt"
    polished_path.write_text("existing polished subtitle", encoding="utf-8")
    assert runtime.ctx.restore_transcript()
    runtime.ctx.polished_md = "existing polished markdown"

    await stage.review_transcript_speakers(runtime.ctx)

    runtime.infer.assert_not_awaited()
    assert runtime.ctx.polished == ("existing polished subtitle" if polish_completed else None)
    assert runtime.ctx.polished_md == ("existing polished markdown" if polish_completed else None)
    assert polished_path.read_text(encoding="utf-8") == "existing polished subtitle"


@pytest.mark.asyncio
async def test_asr_review_partial_failure_keeps_existing_warning_behavior(runtime):
    runtime.ctx.has_subtitle = False
    runtime.review.return_value = {
        "status": "partial", "changes": [], "failed_chunks": [{"chunk": 1, "error": "invalid JSON"}],
    }

    await stage.review_transcript_speakers(runtime.ctx)

    runtime.review.assert_awaited_once()
    assert runtime.event.await_args.kwargs["level"] == "warning"
    assert runtime.update.await_args.kwargs["completed"] is True


@pytest.mark.asyncio
async def test_partial_acoustic_review_checkpoint_is_reused(runtime):
    runtime.ctx.has_subtitle = False
    runtime.ctx.done = {"transcribe", "speaker_review", "polish", "analyze", "archive"}
    runtime.ctx.task.completed_steps = list(runtime.ctx.done)
    runtime.ctx.polished = "previous output"
    failed = {"status": "partial", "changes": [], "failed_chunks": [{"chunk": 1, "error": "invalid JSON"}]}
    (runtime.ctx.task_dir / "speaker_review.json").write_text(json.dumps(failed), encoding="utf-8")
    runtime.review.return_value = {"status": "completed", "changes": [], "failed_chunks": []}
    await stage.review_transcript_speakers(runtime.ctx)
    runtime.review.assert_not_awaited()
    assert runtime.ctx.done == {"transcribe", "speaker_review", "polish", "analyze", "archive"}
    assert runtime.ctx.polished == "previous output"
    assert runtime.ctx.metadata.extra["speaker_quality"]["status"] == "needs_review"
