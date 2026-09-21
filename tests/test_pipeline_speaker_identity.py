from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.pipeline_steps import speaker_review as stage
from app.core.pipeline_steps.context import PipelineContext
from app.core.settings import RuntimeSettings
from app.models import MediaMetadata, Task, TaskType
from app.services.analysis import speaker_identity, speaker_identity_artifacts

SRT = (
    "1\n00:00:00,000 --> 00:00:02,000\n"
    "[SPEAKER_00] 我叫张三，请把 SPEAKER_00 写在备注里。\n\n"
    "2\n00:00:02,000 --> 00:00:04,000\n[SPEAKER_01] 谢谢你的介绍。\n"
)


def report(name="张三", **extra):
    return {
        "status": "completed",
        "context_stage": "analyzed",
        "mappings": [{
            "source_label": "SPEAKER_00",
            "current_name": name,
            "evidence_indexes": [1],
            "reason": "完整字幕中的自我介绍",
        }],
        **extra,
    }


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    ctx = PipelineContext(
        task=Task(task_type=TaskType.PIPELINE, source="fixture.wav"),
        rt=RuntimeSettings(),
        task_dir=tmp_path,
        metadata=MediaMetadata(title="人物访谈", description="嘉宾分享", extra={
            "segments": [{"speaker": "SPEAKER_00", "text": "讨论 SPEAKER_00"}],
        }),
        srt=SRT,
        reviewed_srt=SRT,
        polished=SRT.replace("谢谢你的介绍", "感谢你的介绍"),
        done={"polish", "analyze"},
        recognition_segments=[{"speaker": "SPEAKER_00", "text": "我叫 SPEAKER_00"}],
        source_context={"speaker_candidates": [{"name": "张三"}]},
        analysis={
            "content_type": "技术讲座",
            "speakers": [{"name": "SPEAKER_00", "description": "介绍 SPEAKER_00"}],
            "main_topics": ["SPEAKER_00 的工作"],
        },
        summary={"tldr": "张三主讲，SPEAKER_00分享工作。", "key_facts": ["SPEAKER_01提出问题。"]},
        mindmap="# SPEAKER_00 的工作\n- SPEAKER_01 的问题",
        detail="SPEAKER_00详细介绍项目；SPEAKER_01追问。",
    )
    infer = AsyncMock(return_value=report())
    monkeypatch.setattr(speaker_identity, "infer_speaker_names", infer)
    persist = Mock(return_value=["transcript_polished.srt", "speaker_identity.json"])
    monkeypatch.setattr(speaker_identity_artifacts, "persist_speaker_names", persist)
    emit = AsyncMock()
    warning = AsyncMock()
    monkeypatch.setattr(stage, "_emit_file_ready", emit)
    monkeypatch.setattr(stage, "_emit_timeline_event", warning)
    monkeypatch.setattr(stage, "_raise_if_cancelled", AsyncMock())
    return SimpleNamespace(ctx=ctx, infer=infer, persist=persist, emit=emit, warning=warning)


def save_report(runtime, payload):
    (runtime.ctx.task_dir / "speaker_identity.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_identifies_complete_polished_transcript_and_updates_speaker_fields(runtime):
    original_polished = runtime.ctx.polished
    assert await stage.identify_transcript_speakers(runtime.ctx)

    runtime.infer.assert_awaited_once()
    assert runtime.infer.call_args.args == (original_polished,)
    context = runtime.infer.call_args.kwargs["context"]
    assert context["title"] == "人物访谈"
    assert context["description"] == "嘉宾分享"
    assert context["speaker_candidates"] == [{"name": "张三"}]
    assert context["analysis"]["content_type"] == "技术讲座"
    assert context["summary"]["tldr"] == "张三主讲，SPEAKER_00分享工作。"
    assert {item["speaker"] for item in context["speaker_activity"]} == {
        "SPEAKER_00", "SPEAKER_01",
    }
    assert "[张三]" in runtime.ctx.srt
    assert "[张三]" in runtime.ctx.reviewed_srt
    assert "[张三]" in runtime.ctx.polished
    assert "请把 SPEAKER_00 写在备注里" in runtime.ctx.polished
    assert "[张三]" in runtime.ctx.transcript
    assert "张三" in runtime.ctx.polished_md
    assert runtime.ctx.recognition_segments == [{"speaker": "张三", "text": "我叫 SPEAKER_00"}]
    assert runtime.ctx.metadata.extra["segments"] == [
        {"speaker": "张三", "text": "讨论 SPEAKER_00"}
    ]
    assert runtime.ctx.analysis["speakers"] == [
        {"name": "张三", "description": "介绍 张三"}
    ]
    assert runtime.ctx.analysis["main_topics"] == ["张三 的工作"]
    assert runtime.ctx.summary == {
        "tldr": "张三主讲，张三分享工作。", "key_facts": ["SPEAKER_01提出问题。"],
    }
    assert runtime.ctx.mindmap == "# 张三 的工作\n- SPEAKER_01 的问题"
    assert runtime.ctx.detail == "张三详细介绍项目；SPEAKER_01追问。"
    assert set(runtime.ctx.metadata.extra["speakers"]) == {"张三", "SPEAKER_01"}
    assert runtime.ctx.analysis["speakers_detected"] == 2
    assert runtime.persist.call_args.args[2]["input_labels"] == ["SPEAKER_00", "SPEAKER_01"]
    assert runtime.persist.call_args.args[2]["context_stage"] == "analyzed"
    assert [call.args[1] for call in runtime.emit.await_args_list] == [
        "transcript_polished.srt", "speaker_identity.json"
    ]


@pytest.mark.asyncio
async def test_analyzed_checkpoint_reuses_applicable_identity_report(runtime):
    save_report(runtime, report(input_labels=["SPEAKER_00", "SPEAKER_01"]))
    assert await stage.identify_transcript_speakers(runtime.ctx)
    runtime.infer.assert_not_awaited()
    assert "[张三]" in runtime.ctx.polished


@pytest.mark.asyncio
async def test_new_summary_rechecks_identity_with_same_speaker_labels(runtime):
    save_report(runtime, report("旧姓名", input_labels=["SPEAKER_00", "SPEAKER_01"]))
    runtime.ctx.done.remove("analyze")
    assert await stage.identify_transcript_speakers(runtime.ctx)
    runtime.infer.assert_awaited_once()
    assert "[张三]" in runtime.ctx.polished
    assert "旧姓名" not in runtime.ctx.polished


@pytest.mark.asyncio
async def test_legacy_pre_summary_report_rechecks_identity(runtime):
    previous = report("旧姓名", input_labels=["SPEAKER_00", "SPEAKER_01"])
    previous.pop("context_stage")
    save_report(runtime, previous)
    assert await stage.identify_transcript_speakers(runtime.ctx)
    runtime.infer.assert_awaited_once()
    assert "[张三]" in runtime.ctx.polished


@pytest.mark.asyncio
async def test_cached_identity_cannot_replace_manually_named_transcript(runtime):
    save_report(runtime, report(input_labels=["SPEAKER_00", "SPEAKER_01"]))
    runtime.ctx.polished = runtime.ctx.polished.replace("[SPEAKER_00]", "[李四]")
    runtime.infer.return_value = {"status": "skipped", "mappings": []}
    assert not await stage.identify_transcript_speakers(runtime.ctx)
    assert "[李四]" in runtime.ctx.polished
    assert "[张三]" not in runtime.ctx.srt
    assert runtime.persist.call_args.args[2]["mappings"] == []


@pytest.mark.asyncio
async def test_cached_name_syncs_raw_fields_when_polished_already_has_name(runtime):
    save_report(runtime, report(input_labels=["SPEAKER_00", "SPEAKER_01"]))
    runtime.ctx.polished = runtime.ctx.polished.replace("[SPEAKER_00]", "[张三]")
    assert not await stage.identify_transcript_speakers(runtime.ctx)
    runtime.infer.assert_not_awaited()
    assert "[张三]" in runtime.ctx.srt
    assert runtime.ctx.recognition_segments[0]["speaker"] == "张三"


@pytest.mark.asyncio
async def test_model_failure_records_warning_and_keeps_transcript(runtime):
    before = runtime.ctx.polished
    runtime.infer.side_effect = RuntimeError("model unavailable")
    assert not await stage.identify_transcript_speakers(runtime.ctx)
    assert runtime.ctx.polished == before
    assert runtime.persist.call_args.args[2]["status"] == "failed"
    assert runtime.ctx.metadata.extra["speaker_identity"]["status"] == "failed"
    runtime.warning.assert_awaited_once()
    assert runtime.warning.call_args.kwargs["level"] == "warning"


@pytest.mark.asyncio
async def test_skipped_checkpoint_reuses_report_without_writing_artifacts(runtime):
    save_report(runtime, {
        "status": "skipped", "mappings": [], "input_labels": ["SPEAKER_00", "SPEAKER_01"],
        "context_stage": "analyzed",
    })
    assert not await stage.identify_transcript_speakers(runtime.ctx)
    runtime.infer.assert_not_awaited()
    runtime.persist.assert_not_called()


@pytest.mark.asyncio
async def test_identity_context_uses_acoustic_turns_and_maps_stable_labels(runtime):
    directory = runtime.ctx.task_dir
    (directory / "diarization.json").write_text(json.dumps({"turns": [
        {"start": 0.0, "end": 90.0, "speaker": "SPEAKER_03"},
        {"start": 90.0, "end": 100.0, "speaker": "SPEAKER_02"},
    ]}), encoding="utf-8")
    (directory / "speaker_map.json").write_text(json.dumps({"mappings": [
        {"source_label": "SPEAKER_03", "current_name": "SPEAKER_00"},
        {"source_label": "SPEAKER_02", "current_name": "SPEAKER_01"},
    ]}), encoding="utf-8")
    (directory / "transcript.srt").write_text(SRT, encoding="utf-8")
    await stage.identify_transcript_speakers(runtime.ctx)
    activity = {
        item["speaker"]: item
        for item in runtime.infer.call_args.kwargs["context"]["speaker_activity"]
    }
    assert activity["SPEAKER_00"]["duration_seconds"] == pytest.approx(90)
    assert activity["SPEAKER_00"]["share"] == pytest.approx(0.9)
    assert activity["SPEAKER_01"]["duration_seconds"] == pytest.approx(10)
    assert activity["SPEAKER_01"]["share"] == pytest.approx(0.1)
    assert "[SPEAKER_01]" in runtime.ctx.polished


@pytest.mark.asyncio
async def test_identity_activity_falls_back_to_raw_timing_before_polished_spans(runtime):
    (runtime.ctx.task_dir / "transcript.srt").write_text(SRT, encoding="utf-8")
    runtime.ctx.polished = runtime.ctx.polished.replace(
        "00:00:02,000", "00:00:20,000"
    ).replace("00:00:04,000", "00:00:21,000")
    await stage.identify_transcript_speakers(runtime.ctx)
    activity = runtime.infer.call_args.kwargs["context"]["speaker_activity"]
    assert {item["speaker"] for item in activity} == {"SPEAKER_00", "SPEAKER_01"}
    assert all(item["duration_seconds"] == pytest.approx(2) for item in activity)
    assert all(item["share"] == pytest.approx(0.5) for item in activity)
