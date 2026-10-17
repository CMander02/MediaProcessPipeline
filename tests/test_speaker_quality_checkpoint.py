import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core import settings
from app.services.analysis import llm, subtitle_speakers
from app.services.analysis.long_text import record_windows
from app.services.analysis.source_context import build_deterministic_source_context, speaker_constraints
from app.services.analysis.transcript_outputs import _parse_srt


def source(count=30):
    return "\n\n".join(
        f"{i}\n00:00:00,000 --> 00:00:01,000\n{'我是张明。' if i == 1 else '继续讨论。'}"
        for i in range(1, count + 1)
    )


def answer(prompt):
    payload = json.loads(prompt.split("字幕与来源：\n")[1])
    roster = payload["roster"]
    return json.dumps({
        "speakers": [] if roster else [{
            "id": "SPEAKER_00", "name": "张明", "role": "", "evidence_indexes": [1], "reason": "自我介绍",
        }],
        "assignments": [{"index": i, "speaker": "SPEAKER_00"} for i in payload["editable_indexes"]],
    }, ensure_ascii=False)


@pytest.fixture
def model(monkeypatch):
    call = AsyncMock(side_effect=lambda prompt, **kwargs: answer(prompt))
    monkeypatch.setattr(llm, "get_llm_service", lambda: SimpleNamespace(_call=call))
    monkeypatch.setattr(settings, "_runtime_settings", settings.RuntimeSettings())
    return call


@pytest.mark.asyncio
async def test_cancel_resume_keeps_successful_batches_and_original_overlap(model, tmp_path):
    path = tmp_path / "speaker.checkpoint.json"

    async def cancel(processed, total):
        if processed == 1:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await subtitle_speakers.infer_subtitle_speakers(source(), checkpoint_path=path, on_progress=cancel)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["report"]["status"] == "running"
    assert saved["completed_indexes"] == list(range(1, 25))
    assert model.await_count == 1
    report = await subtitle_speakers.infer_subtitle_speakers(source(), checkpoint_path=path)
    assert report["status"] == "completed"
    assert model.await_count == 2
    payload = json.loads(model.call_args.args[0].split("字幕与来源：\n")[1])
    assert payload["editable_indexes"] == list(range(25, 31))
    assert [cue["index"] for cue in payload["context_before"]] == list(range(21, 25))
    assert len(report["assignments"]) == 30


@pytest.mark.asyncio
async def test_changed_constraints_or_text_invalidate_checkpoint(model, tmp_path):
    path = tmp_path / "checkpoint.json"
    await subtitle_speakers.infer_subtitle_speakers(source(4), checkpoint_path=path, speaker_limit=1)
    await subtitle_speakers.infer_subtitle_speakers(source(4), checkpoint_path=path, speaker_limit=1)
    assert model.await_count == 1
    await subtitle_speakers.infer_subtitle_speakers(source(4), checkpoint_path=path, speaker_limit=2)
    assert model.await_count == 2
    await subtitle_speakers.infer_subtitle_speakers(source(4) + "补充", checkpoint_path=path, speaker_limit=2)
    assert model.await_count == 3


@pytest.mark.asyncio
async def test_single_cue_output_exhaustion_preserves_text_and_continues():
    calls = []

    async def call(prompt):
        calls.append(prompt)
        if "bad" in prompt:
            raise RuntimeError("输出被截断")
        return "ok"

    rows = [{"index": 1, "text": "bad" * 30}, {"index": 2, "text": "good"}]
    results = [item async for item in record_windows(
        rows, lambda t, v: json.dumps(t), call, budget=4000, chunk_size=1,
        overlap=0, keep_errors=True,
    )]
    assert len(calls) == 2
    assert results[0][0] == rows[:1]
    assert isinstance(results[0][2], RuntimeError)
    assert results[1][2] == "ok"


@pytest.mark.asyncio
async def test_caption_annotations_never_seed_roster_and_mixed_body_survives(model):
    srt = source(1).replace("我是张明。", "[风铃与脚步] 今天讨论这个问题。")
    model.side_effect = None
    model.return_value = json.dumps({
        "speakers": [{"id": "SPEAKER_00", "name": "", "role": "", "evidence_indexes": [1], "reason": "有人发言"}],
        "assignments": [{"index": 1, "speaker": "SPEAKER_00", "kind": "mixed", "event_description": "风铃与脚步"}],
    })
    report = await subtitle_speakers.infer_subtitle_speakers(srt, speaker_limit=1)
    payload = json.loads(model.call_args.args[0].split("字幕与来源：\n")[1])
    assert payload["roster"] == []
    assert report["event_segments"] == 1
    result = subtitle_speakers.apply_subtitle_speakers(srt, report)
    assert _parse_srt(result)[0]["text"] == "[SPEAKER_00] [风铃与脚步] 今天讨论这个问题。"


@pytest.mark.asyncio
async def test_unsupported_name_keeps_assignments(model):
    model.side_effect = None
    model.return_value = answer("字幕与来源：\n" + json.dumps({"roster": [], "editable_indexes": [1]})).replace("张明", "某位导师")
    report = await subtitle_speakers.infer_subtitle_speakers(
        source(1), context={"description": "讨论某位导师的研究"},
    )
    assert report["status"] == "completed"
    assert report["speakers"][0]["name"] == ""
    assert report["assignments"][0]["speaker"] == "SPEAKER_00"
    assert model.await_count == 1


def test_metadata_roster_cannot_create_hard_count():
    context = build_deterministic_source_context({
        "title": "两人对谈", "description": "主持人：张明；嘉宾：李华", "uploader": "科学频道",
    })
    assert speaker_constraints(context) == (None, None, None)
    assert speaker_constraints(context, {"num_speakers": 2}) == (2, 2, 2)


@pytest.mark.asyncio
async def test_call_budget_leaves_targeted_failures(model):
    report = await subtitle_speakers.infer_subtitle_speakers(source(), max_calls=1)
    assert model.await_count == 1
    assert report["status"] == "partial"
    assert report["failed_chunks"][0]["indexes"] == list(range(25, 31))
