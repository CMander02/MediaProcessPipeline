from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import settings
from app.services.analysis import llm, speaker_review
from app.services.analysis.transcript_outputs import _parse_srt, _split_speaker_prefix


def transcript(count=4):
    return (
        "\n\n".join(
            f"{index}\n00:00:{index:02d},000 --> 00:00:{index + 1:02d},000\n"
            f"[SPEAKER_{index % 2:02d}] 这是第 {index} 条字幕。"
            for index in range(1, count + 1)
        )
        + "\n"
    )


def correction(index=2, speaker="SPEAKER_01", **kwargs):
    return {
        "index": index,
        "speaker": speaker,
        "evidence_indexes": [1, 2, 3],
        "reason": "延续上一句未结束的叙述",
        **kwargs,
    }


@pytest.fixture
def model(monkeypatch):
    call = AsyncMock(return_value="[]")
    monkeypatch.setattr(llm, "get_llm_service", lambda: SimpleNamespace(_call=call))
    monkeypatch.setattr(settings, "_runtime_settings", settings.RuntimeSettings())
    return call


@pytest.mark.asyncio
async def test_review_changes_labels_only_and_logs_evidence(model, caplog):
    source = transcript()
    model.return_value = json.dumps([correction()])
    progress = AsyncMock()
    with caplog.at_level("INFO"):
        report = await speaker_review.review_speakers(source, on_progress=progress)
    result = speaker_review.apply_speaker_changes(source, report["changes"])
    original, reviewed = _parse_srt(source), _parse_srt(result)
    assert report["status"] == "completed"
    assert report["changes"][0]["from_speaker"] == "SPEAKER_00"
    assert report["changes"][0]["evidence_indexes"] == [1, 2, 3]
    assert reviewed[1]["text"].startswith("[SPEAKER_01]")
    for before, after in zip(original, reviewed, strict=True):
        assert before["index"] == after["index"]
        assert before["timestamp"] == after["timestamp"]
        assert _split_speaker_prefix(before["text"])[1] == _split_speaker_prefix(after["text"])[1]
    assert "llm.speaker_review.started" in caplog.text
    assert "llm.speaker_review.completed" in caplog.text
    assert model.call_args.kwargs["stage"] == "speaker_review"
    progress.assert_awaited_once_with(1, 1)


@pytest.mark.parametrize(
    "change",
    [
        correction(speaker="SPEAKER_99"),
        correction(index=99),
        correction(index=True),
        correction(evidence_indexes=[99]),
        correction(evidence_indexes=[]),
        correction(reason=""),
    ],
)
@pytest.mark.asyncio
async def test_invalid_changes_preserve_source_and_report_failure(model, change):
    source = transcript()
    model.return_value = json.dumps([change])
    report = await speaker_review.review_speakers(source)
    assert report["status"] == "failed"
    assert len(report["failed_chunks"]) == 1
    assert report["changes"] == []
    assert _parse_srt(speaker_review.apply_speaker_changes(source, [])) == _parse_srt(source)


@pytest.mark.asyncio
async def test_chunks_have_read_only_overlap_and_keep_successful_changes(model):
    # Cue 24 is visible in batch 2, but only batch 1 may edit it.
    model.side_effect = [json.dumps([correction()]), json.dumps([correction(24)])]
    report = await speaker_review.review_speakers(transcript(30))
    assert report["status"] == "partial"
    assert [change["index"] for change in report["changes"]] == [2]
    assert report["failed_chunks"][0]["chunk"] == 2
    prompts = [call.args[0] for call in model.call_args_list]
    payloads = [json.loads(prompt.split("待复核数据：\n", 1)[1]) for prompt in prompts]
    assert payloads[0]["speakers"] == payloads[1]["speakers"]
    assert payloads[0]["editable_indexes"] == list(range(1, 25))
    assert payloads[1]["editable_indexes"] == list(range(25, 31))
    assert payloads[1]["cues"][0]["index"] == 21


@pytest.mark.asyncio
async def test_single_speaker_skips_model(model):
    report = await speaker_review.review_speakers(transcript(1))
    assert report["status"] == "skipped"
    model.assert_not_awaited()


@pytest.mark.asyncio
async def test_final_json_block_replaces_draft_in_local_model_response(model):
    model.return_value = (
        '```json\n[{"index": 99}]\n```\nRevised final output:\n```json\n'
        + json.dumps([correction()]) + '\n```'
    )
    report = await speaker_review.review_speakers(transcript())
    assert report["status"] == "completed"
    assert [change["index"] for change in report["changes"]] == [2]


@pytest.mark.asyncio
async def test_cancellation_stops_review_without_requesting_next_chunk(model):
    progress = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await speaker_review.review_speakers(transcript(30), on_progress=progress)
    assert model.await_count == 1


def test_queue_checkpoint_restores_speaker_review_before_polish(tmp_path):
    from app.core.queue import TaskQueue
    from app.models import Task, TaskType

    (tmp_path / "transcript.srt").write_text(transcript(), encoding="utf-8")
    report = tmp_path / "speaker_review.json"
    report.write_text('{"status": "completed", "changes": []}', encoding="utf-8")
    task = Task(
        task_type=TaskType.PIPELINE,
        source="fixture.wav",
        steps=["download", "separate", "transcribe", "voiceprint", "polish", "analyze", "archive"],
        result={"output_dir": str(tmp_path)},
    )
    queue = TaskQueue()
    retained = queue._checkpoint_completed_steps(task)
    assert retained[-1] == "speaker_review"
    assert queue._next_pipeline_step(retained) == "polish"
    report.unlink()
    retained = queue._checkpoint_completed_steps(task)
    assert queue._next_pipeline_step(retained) == "speaker_review"
