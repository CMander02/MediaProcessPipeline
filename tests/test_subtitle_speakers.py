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
async def test_unknown_speaker_gets_explicit_label_without_changing_text_or_timing(model):
    model.return_value = response(people=[], speaker=None)
    source = transcript()
    report = await subtitle_speakers.infer_subtitle_speakers(source)
    assert report["status"] == "completed"
    assert report["unassigned_segments"] == 4
    result = _parse_srt(subtitle_speakers.apply_subtitle_speakers(source, report))
    for before, after in zip(_parse_srt(source), result, strict=True):
        assert before["index"] == after["index"]
        assert before["timestamp"] == after["timestamp"]
        assert _split_speaker_prefix(after["text"]) == ("unknown", before["text"])


def test_missing_assignments_preserve_existing_labels_and_fill_unlabeled_cues():
    source = transcript().replace("我是张明。", "[张明] 我是张明。").replace(
        "我是李华。", "[李华] 我是李华。"
    )
    report = {"speakers": [], "assignments": [{"index": 1, "speaker": None}]}
    result = subtitle_speakers.apply_subtitle_speakers(source, report)
    assert [_split_speaker_prefix(cue["text"])[0] for cue in _parse_srt(result)] == [
        "unknown", "李华", "unknown", "unknown",
    ]
    for before, after in zip(_parse_srt(source), _parse_srt(result), strict=True):
        assert before["index"] == after["index"]
        assert before["timestamp"] == after["timestamp"]
        assert _split_speaker_prefix(before["text"])[1] == _split_speaker_prefix(after["text"])[1]
    assert subtitle_speakers.apply_subtitle_speakers(result, report) == result


@pytest.mark.asyncio
async def test_reinference_does_not_seed_unknown_as_a_person(model):
    model.return_value = response(indexes=[1])
    source = transcript(1).replace("我是张明。", "[unknown] 我是张明。")
    report = await subtitle_speakers.infer_subtitle_speakers(source, speaker_limit=1, input_labeled=True)
    payload = json.loads(model.call_args.args[0].split("字幕与来源：\n")[1])
    assert payload["roster"] == []
    assert report["status"] == "completed"
    assert [speaker["id"] for speaker in report["speakers"]] == ["SPEAKER_00"]
    result = subtitle_speakers.apply_subtitle_speakers(source, report)
    assert _parse_srt(result)[0]["text"] == "[张明] 我是张明。"


@pytest.mark.parametrize(
    "bad",
    [
        response(people=[person(2)]),
        response(people=[person(evidence=99)]),
        response(indexes=[1, 2, 3, 99]),
        response(indexes=[1, 1, 2, 3, 4]),
        response(indexes=[1, 2, 3]),
        response(speaker="SPEAKER_99"),
        json.dumps({"speakers": [person(), person(1, "李华", 2)], "assignments": [
            {"index": i, "speaker": f"SPEAKER_{i % 2:02d}"} for i in range(1, 5)
        ]}),
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
    assert [cue["index"] for cue in payload["editable_cues"]] == list(range(25, 31))
    assert [cue["index"] for cue in payload["context_before"]] == list(range(21, 25))
    assert payload["context_after"] == []
    first_payload = json.loads(model.call_args_list[0].args[0].split("字幕与来源：\n")[1])
    assert first_payload["context_before"] == []
    assert [cue["index"] for cue in first_payload["context_after"]] == list(range(25, 29))
    model.reset_mock()
    model.side_effect = [
        response(range(1, 29)),
        response(range(21, 49), people=[], speaker=None),
    ]
    report = await subtitle_speakers.infer_subtitle_speakers(transcript(48))
    assert report["status"] == "completed"
    assert model.await_count == 2
    assert [item["index"] for item in report["assignments"]] == list(range(1, 49))
    # The second batch's read-only assignments cannot overwrite the first batch.
    assert all(item["speaker"] == "SPEAKER_00" for item in report["assignments"][:24])
    assert all(item["speaker"] is None for item in report["assignments"][24:])
    assert report["unassigned_segments"] == 24


@pytest.mark.parametrize("replayed_evidence", [[], [1], [1, 2]])
@pytest.mark.asyncio
async def test_unchanged_roster_replay_keeps_historical_evidence_and_reason(
    model, replayed_evidence
):
    original = {**person(), "role": "嘉宾", "evidence_indexes": [1, 2]}
    echoed = {**original, "evidence_indexes": replayed_evidence, "reason": "沿用已有身份"}
    model.side_effect = [
        response(range(1, 25), people=[original]),
        response(range(25, 31), people=[echoed]),
    ]
    report = await subtitle_speakers.infer_subtitle_speakers(transcript(30))
    assert report["status"] == "completed"
    assert report["failed_chunks"] == []
    assert model.await_count == 2
    assert len(report["assignments"]) == 30
    assert len(report["speakers"]) == 1
    saved = report["speakers"][0]
    assert saved["name"] == original["name"]
    assert saved["role"] == original["role"]
    assert saved["evidence_indexes"] == [1, 2]
    assert saved["reason"] == original["reason"]


@pytest.mark.parametrize(
    ("record", "error_parts"),
    [
        ({**person(), "role": "主持人", "evidence_indexes": []}, ["SPEAKER_00", "当前字幕证据"]),
        ({**person(), "role": "主持人"}, ["SPEAKER_00", "1", "不在当前上下文"]),
        ({**person(1, "李华"), "evidence_indexes": []}, ["SPEAKER_01", "当前字幕证据"]),
        (person(1, "李华"), ["SPEAKER_01", "1", "不在当前上下文"]),
        (person(evidence=999), ["SPEAKER_00", "999", "不在当前上下文"]),
        ({**person(), "evidence_indexes": [1, 999]}, ["SPEAKER_00", "999", "不在当前上下文"]),
    ],
)
def test_roster_replay_still_rejects_unsupported_changes_and_unknown_evidence(record, error_parts):
    surrounding = [
        {"index": i, "text": "我是李华。" if i == 25 else "继续讨论。"}
        for i in range(21, 31)
    ]
    original = {**person(), "role": "嘉宾"}
    roster = {"SPEAKER_00": original}
    with pytest.raises(ValueError) as error:
        subtitle_speakers._parse_inference(
            response(range(25, 31), people=[record]),
            surrounding[4:],
            surrounding,
            roster,
            {},
            None,
        )
    for part in error_parts:
        assert part in str(error.value)
    assert roster == {"SPEAKER_00": {**person(), "role": "嘉宾"}}


@pytest.mark.parametrize("start", [49, 1249])
def test_extra_trailing_context_keeps_target_assignments_and_new_speaker(start):
    surrounding = [
        {"index": i, "text": "我是李华。" if i == start + 1 else "继续讨论。"}
        for i in range(start - 4, start + 28)
    ]
    targets = surrounding[4:28]
    roster = {"SPEAKER_00": person()}
    updated, assignments = subtitle_speakers._parse_inference(
        response(
            range(start, start + 28),
            people=[person(1, "李华", start + 1)],
            speaker="SPEAKER_01",
        ),
        targets,
        surrounding,
        roster,
        {},
        None,
    )
    assert [item["index"] for item in assignments] == list(range(start, start + 24))
    assert all(item["speaker"] == "SPEAKER_01" for item in assignments)
    assert updated["SPEAKER_00"]["name"] == "张明"
    assert updated["SPEAKER_01"]["name"] == "李华"
    assert "SPEAKER_01" not in roster


@pytest.mark.parametrize(
    ("indexes", "speaker", "error_parts"),
    [
        (list(range(1249, 1273)) + [1300], "SPEAKER_00", ["字幕序号", "1300"]),
        (list(range(1249, 1273)) + ["1273"], "SPEAKER_00", ["字幕序号", "1273"]),
        (list(range(1249, 1273)) + [True], "SPEAKER_00", ["字幕序号", "True"]),
        (list(range(1249, 1273)) + [1249], "SPEAKER_00", ["重复字幕序号", "1249"]),
        (list(range(1250, 1273)), "SPEAKER_00", ["缺少字幕序号", "1249"]),
        (list(range(1249, 1273)), "SPEAKER_99", ["未知人物 ID", "SPEAKER_99"]),
        (list(range(1249, 1273)), 99, ["未知人物 ID", "99"]),
        (list(range(1249, 1273)), ["SPEAKER_00"], ["未知人物 ID", "['SPEAKER_00']"]),
    ],
)
def test_invalid_target_assignments_identify_offending_index_or_speaker(
    indexes, speaker, error_parts
):
    surrounding = [{"index": i, "text": "继续讨论。"} for i in range(1245, 1277)]
    with pytest.raises(ValueError) as error:
        subtitle_speakers._parse_inference(
            response(indexes, people=[], speaker=speaker),
            surrounding[4:28],
            surrounding,
            {"SPEAKER_00": person()},
            {},
            None,
        )
    for part in error_parts:
        assert part in str(error.value)


@pytest.mark.asyncio
async def test_cancellation_stops_before_model_call(model):
    with pytest.raises(asyncio.CancelledError):
        await subtitle_speakers.infer_subtitle_speakers(
            transcript(),
            on_progress=AsyncMock(side_effect=asyncio.CancelledError()),
        )
    model.assert_not_awaited()


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("Local llama.cpp server did not become ready"),
        ConnectionError("Connection refused"),
        TimeoutError("Request timed out"),
        ValueError("Invalid provider configuration"),
    ],
)
@pytest.mark.asyncio
async def test_request_failure_stops_stage_without_retrying_later_batches(model, error, caplog):
    model.side_effect = error
    progress = AsyncMock()
    with pytest.raises(type(error), match=str(error)):
        await subtitle_speakers.infer_subtitle_speakers(transcript(30), on_progress=progress)
    assert model.await_count == 1
    assert [call.args for call in progress.await_args_list] == [(0, 2)]
    assert "llm.subtitle_speakers.failed" in caplog.text
    assert "llm.subtitle_speakers.completed" not in caplog.text


@pytest.mark.asyncio
async def test_validation_failure_retries_prompt_and_reports_each_progress_once(model):
    model.side_effect = [
        "invalid output",
        response(range(1, 25)),
        response(range(25, 31), people=[person(evidence=25)]),
    ]
    progress = AsyncMock()
    report = await subtitle_speakers.infer_subtitle_speakers(transcript(30), on_progress=progress)
    assert report["status"] == "completed"
    assert model.await_count == 3
    assert "上次输出校验失败" in model.await_args_list[1].args[0]
    assert [call.args for call in progress.await_args_list] == [(0, 2), (1, 2), (2, 2)]


@pytest.mark.asyncio
async def test_failed_batch_retains_failure_report_and_processed_progress(model):
    model.side_effect = [
        "invalid output",
        "still invalid",
        response(range(25, 31), people=[], speaker=None),
    ]
    progress = AsyncMock()
    report = await subtitle_speakers.infer_subtitle_speakers(transcript(30), on_progress=progress)
    assert report["status"] == "partial"
    assert [failure["chunk"] for failure in report["failed_chunks"]] == [1]
    assert len(report["assignments"]) == 6
    assert [call.args for call in progress.await_args_list] == [(0, 2), (1, 2), (2, 2)]
