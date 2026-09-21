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
from app.services.analysis.transcript_outputs import _parse_srt, _split_speaker_prefix


def transcript(count=4):
    texts = ["我是张明。", "我是李华。", "李华，你怎么看？", "这个办法可以试试。"]
    return "\n\n".join(
        f"{i}\n00:00:{i:02d},000 --> 00:00:{i + 1:02d},000\n"
        f"{texts[i - 1] if i <= 4 else '继续讨论。'}"
        for i in range(1, count + 1)
    )


def person(number=0, name="张明", evidence=1):
    return {
        "id": f"SPEAKER_{number:02d}",
        "name": name,
        "role": "",
        "evidence_indexes": [evidence],
        "reason": "自我介绍",
    }


def response(indexes=range(1, 5), people=None, speaker="SPEAKER_00"):
    return json.dumps(
        {
            "speakers": [person()] if people is None else people,
            "assignments": [{"index": i, "speaker": speaker} for i in indexes],
        },
        ensure_ascii=False,
    )


@pytest.fixture
def model(monkeypatch):
    call = AsyncMock()
    monkeypatch.setattr(llm, "get_llm_service", lambda: SimpleNamespace(_call=call))
    monkeypatch.setattr(settings, "_runtime_settings", settings.RuntimeSettings())
    return call


@pytest.mark.asyncio
async def test_inference_adds_only_labels_with_names_and_evidence(model, caplog):
    model.return_value = json.dumps(
        {
            "speakers": [person(), person(1, "李华", 2)],
            "assignments": [
                {"index": i, "speaker": f"SPEAKER_{s:02d}"}
                for i, s in [(1, 0), (2, 1), (3, 0), (4, 1)]
            ],
        },
        ensure_ascii=False,
    )
    source = transcript()
    with caplog.at_level("INFO"):
        report = await subtitle_speakers.infer_subtitle_speakers(source, speaker_limit=2)
    assert report["status"] == "completed"
    assert report["source"] == "subtitle_inference"
    result = _parse_srt(subtitle_speakers.apply_subtitle_speakers(source, report))
    assert [_split_speaker_prefix(cue["text"])[0] for cue in result] == [
        "张明",
        "李华",
        "张明",
        "李华",
    ]
    for before, after in zip(_parse_srt(source), result, strict=True):
        assert before["index"] == after["index"]
        assert before["timestamp"] == after["timestamp"]
        assert before["text"] == _split_speaker_prefix(after["text"])[1]
    assert "llm.subtitle_speakers.completed" in caplog.text


@pytest.mark.asyncio
async def test_unknown_speaker_keeps_unlabeled_text(model):
    model.return_value = response(people=[], speaker=None)
    report = await subtitle_speakers.infer_subtitle_speakers(transcript())
    assert report["status"] == "completed"
    assert report["unassigned_segments"] == 4
    assert subtitle_speakers.apply_subtitle_speakers(transcript(), report) == transcript()


@pytest.mark.parametrize(
    "bad",
    [
        response(people=[person(2)]),
        response(people=[person(name="王五")]),
        response(people=[person(evidence=99)]),
        response(indexes=[1, 2, 3, 99]),
        response(indexes=[1, 1, 2, 3, 4]),
        response(indexes=[1, 2, 3]),
        response(speaker="SPEAKER_99"),
        response(people=[person(), person(1, "李华", 2)]),
    ],
)
@pytest.mark.asyncio
async def test_rejects_invalid_ids_invented_names_and_speaker_limit(model, bad):
    model.return_value = bad
    report = await subtitle_speakers.infer_subtitle_speakers(transcript(), speaker_limit=1)
    assert report["status"] == "failed"
    assert report["assignments"] == []
    assert model.await_count == 2


@pytest.mark.asyncio
async def test_global_roster_is_reused_across_batches_and_overlap_is_read_only(model):
    model.side_effect = [
        response(range(1, 25)),
        # Repeating a known name is valid even when its first evidence is outside this batch.
        response(range(25, 31), people=[person(evidence=25)]),
    ]
    report = await subtitle_speakers.infer_subtitle_speakers(transcript(30))
    assert report["status"] == "completed"
    assert len(report["speakers"]) == 1
    payload = json.loads(model.call_args_list[1].args[0].split("字幕与来源：\n")[1])
    assert payload["roster"][0]["name"] == "张明"
    assert payload["editable_indexes"] == list(range(25, 31))
    assert payload["cues"][0]["index"] == 21
    model.reset_mock()
    model.side_effect = [response(range(1, 25)), response(range(24, 31)), response(range(24, 31))]
    report = await subtitle_speakers.infer_subtitle_speakers(transcript(30))
    assert report["status"] == "partial"
    assert len(report["assignments"]) == 24
    assert report["unassigned_segments"] == 6


@pytest.mark.asyncio
async def test_cancellation_stops_before_model_call(model):
    with pytest.raises(asyncio.CancelledError):
        await subtitle_speakers.infer_subtitle_speakers(
            transcript(),
            on_progress=AsyncMock(side_effect=asyncio.CancelledError()),
        )
    model.assert_not_awaited()
