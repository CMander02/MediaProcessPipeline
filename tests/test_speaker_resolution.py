from __future__ import annotations

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
from app.services.analysis.speaker_resolution import apply_resolutions, cue_acoustic_candidates, resolve_unassigned
from app.services.analysis.transcript_outputs import _parse_srt, _split_speaker_prefix


def transcript(rows):
    return "\n\n".join(
        f"{index}\n00:00:{index * 2:02d},000 --> 00:00:{index * 2 + 2:02d},000\n[{speaker}] {text}"
        for index, (speaker, text) in enumerate(rows, 1)
    ) + "\n"


def labels(srt):
    return [_split_speaker_prefix(cue["text"])[0] for cue in _parse_srt(srt)]


SOURCE = transcript([
    ("Host", "So why are Americans so angry about data centers?"),
    ("unknown", "Yeah, it's very clear cut, and there's a reason."),
    ("Guest", "They are built to maximize immediate return."),
    ("unknown", "Right. And the second thing is water."),
    ("Guest", "Utah is a good example."),
    ("unknown", "[music]"),
])

# These cases need the model/fallback path. Different neighbors prevent the
# existing continuity rule from deciding cue 4 before that path is reached.
AMBIGUOUS_SOURCE = SOURCE.replace("[Guest] Utah", "[Host] Utah")


def write_diarization(directory: Path, turns):
    (directory / "diarization.json").write_text(json.dumps({"turns": turns}), encoding="utf-8")
    (directory / "speaker_map.json").write_text(json.dumps({"mappings": [
        {"source_label": "SPEAKER_00", "current_name": "Host"},
        {"source_label": "SPEAKER_01", "current_name": "Guest"},
        {"source_label": "SPEAKER_02", "current_name": "Guest"},
    ]}), encoding="utf-8")


def test_acoustic_candidates_use_current_names_and_add_up_split_voices(tmp_path):
    write_diarization(tmp_path, [
        {"start": 0.0, "end": 4.6, "speaker": "SPEAKER_00"},
        {"start": 4.4, "end": 6.0, "speaker": "SPEAKER_01"},
        {"start": 7.9, "end": 9.0, "speaker": "SPEAKER_02"},
        {"start": 9.0, "end": 10.0, "speaker": "SPEAKER_01"},
    ])
    acoustic = cue_acoustic_candidates(tmp_path, SOURCE)
    assert acoustic[2] == {"Host": 0.6, "Guest": 1.6}
    assert acoustic[4] == {"Guest": 2.0}
    assert 6 not in acoustic
    assert cue_acoustic_candidates(tmp_path / "missing", SOURCE) == {}


@pytest.mark.asyncio
async def test_clear_majority_needs_no_model_and_context_decides_the_rest():
    call = AsyncMock(return_value=json.dumps({"assignments": [
        {"index": 2, "speaker": "Guest", "reason": "回答上一句提问"},
        {"index": 2, "speaker": "Host", "reason": "重复的行被忽略"},
        {"index": 99, "speaker": "Guest", "reason": "不存在"},
    ]}, ensure_ascii=False))
    acoustic = {2: {"Host": 1.2, "Guest": 1.4}, 4: {"Guest": 1.9, "Host": 0.1}}
    rows = await resolve_unassigned(SOURCE, acoustic=acoustic, skip={6}, call=call)
    assert [(row["index"], row["speaker"], row["method"]) for row in rows] == [
        (2, "Guest", "context"), (4, "Guest", "acoustic_majority"),
    ]
    payload = json.loads(call.call_args.args[0].split("待定字幕与前后文：\n")[1])
    assert [target["index"] for target in payload] == [2]
    assert payload[0]["candidates"] == {"Host": 1.2, "Guest": 1.4}
    assert [row["speaker"] for row in payload[0]["context"]][:3] == ["Host", "待定", "Guest"]
    resolved = apply_resolutions(SOURCE, rows)
    assert labels(resolved) == ["Host", "Guest", "Guest", "Guest", "Guest", "unknown"]
    assert [_split_speaker_prefix(cue["text"])[1] for cue in _parse_srt(resolved)] == [
        _split_speaker_prefix(cue["text"])[1] for cue in _parse_srt(SOURCE)
    ]


@pytest.mark.asyncio
async def test_choice_outside_the_heard_voices_falls_back_to_a_majority_or_stays_pending():
    call = AsyncMock(return_value=json.dumps({"assignments": [
        {"index": 2, "speaker": "Somebody", "reason": "不在候选中"},
        {"index": 4, "speaker": None, "reason": "判断不了"},
    ]}))
    acoustic = {2: {"Host": 0.5, "Guest": 1.5}, 4: {"Guest": 1.0, "Host": 1.0}}
    rows = await resolve_unassigned(AMBIGUOUS_SOURCE, acoustic=acoustic, skip={6}, call=call)
    call.assert_awaited_once()
    assert [(row["index"], row["speaker"], row["method"]) for row in rows] == [(2, "Guest", "acoustic_majority")]


@pytest.mark.asyncio
async def test_failed_batches_and_missing_audio_data():
    call = AsyncMock(side_effect=RuntimeError("offline"))
    rows = await resolve_unassigned(AMBIGUOUS_SOURCE, acoustic={2: {"Host": 0.4, "Guest": 1.6}, 4: {"Host": 1, "Guest": 1}},
                                    skip={6}, call=call)
    call.assert_awaited_once()
    assert [(row["index"], row["speaker"]) for row in rows] == [(2, "Guest")]
    # Platform subtitles have no diarization: every known speaker is a candidate.
    call = AsyncMock(return_value=json.dumps({"assignments": [{"index": 4, "speaker": "Host", "reason": "称呼"}]}))
    rows = await resolve_unassigned(AMBIGUOUS_SOURCE, skip={6}, call=call)
    payload = json.loads(call.call_args.args[0].split("待定字幕与前后文：\n")[1])
    assert payload[0]["candidates"] == {"Guest": 0, "Host": 0}
    assert [(row["index"], row["speaker"]) for row in rows] == [(4, "Host")]
    assert await resolve_unassigned(transcript([("unknown", "嗯")]), call=call) == []


@pytest.mark.asyncio
async def test_same_heard_speaker_on_both_sides_resolves_without_model():
    call = AsyncMock(side_effect=RuntimeError("model should not be needed"))
    rows = await resolve_unassigned(SOURCE, acoustic={4: {"Host": 1, "Guest": 1}},
                                    skip={2, 6}, call=call)
    assert [(row["index"], row["speaker"], row["method"]) for row in rows] == [(4, "Guest", "continuity")]
    call.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_review_resolves_pending_lines_and_restores_them(tmp_path, monkeypatch):
    srt = transcript([("Unknown-a", "Why?"), ("unknown", "Because the deals are cheap."), ("Unknown-b", "Exactly.")])
    write_diarization(tmp_path, [{"start": 4.0, "end": 5.0, "speaker": "SPEAKER_00"},
                                 {"start": 5.0, "end": 6.0, "speaker": "SPEAKER_01"}])
    (tmp_path / "speaker_map.json").write_text(json.dumps({"mappings": [
        {"source_label": "SPEAKER_00", "current_name": "Unknown-a"},
        {"source_label": "SPEAKER_01", "current_name": "Unknown-b"},
    ]}), encoding="utf-8")
    ctx = PipelineContext(task=Task(task_type=TaskType.PIPELINE, source="fixture.wav"), rt=RuntimeSettings(),
                          task_dir=tmp_path, metadata=MediaMetadata(title="访谈"), source_context={"title": "访谈"}, srt=srt)
    review = AsyncMock(return_value={"status": "completed", "input_segments": 3, "speakers": ["Unknown-a", "Unknown-b"],
                                     "changes": [], "failed_chunks": []})
    resolver = AsyncMock(return_value=[{"index": 2, "speaker": "Unknown-b", "method": "context", "reason": "回答提问"}])

    async def write(task, directory, filename, text):
        (directory / filename).write_text(text, encoding="utf-8")

    from app.services.analysis import speaker_resolution
    monkeypatch.setattr(speaker_resolution, "resolve_unassigned", resolver)
    monkeypatch.setattr(stage, "review_speakers", review)
    monkeypatch.setattr(stage, "_update_step", AsyncMock())
    monkeypatch.setattr(stage, "_emit_timeline_event", AsyncMock())
    monkeypatch.setattr(stage, "_raise_if_cancelled", AsyncMock())
    monkeypatch.setattr(stage, "_write_text_artifact", write)
    monkeypatch.setattr("app.core.pipeline_steps.participant_evidence.prepare_participant_evidence", AsyncMock())

    await stage.review_transcript_speakers(ctx)
    assert labels(ctx.reviewed_srt) == ["Unknown-a", "Unknown-b", "Unknown-b"]
    assert resolver.call_args.kwargs["acoustic"][2] == {"Unknown-a": 1.0, "Unknown-b": 1.0}
    assert ctx.metadata.extra["speaker_quality"]["unassigned_segments"] == 0
    saved = json.loads((tmp_path / "speaker_review.json").read_text(encoding="utf-8"))
    assert saved["resolved_unassigned"][0]["speaker"] == "Unknown-b"

    # A restored report re-applies the stored decisions without asking again.
    resolver.reset_mock()
    review.reset_mock()
    ctx.reviewed_srt = None
    await stage.review_transcript_speakers(ctx)
    review.assert_not_awaited()
    resolver.assert_not_awaited()
    assert labels(ctx.reviewed_srt) == ["Unknown-a", "Unknown-b", "Unknown-b"]
