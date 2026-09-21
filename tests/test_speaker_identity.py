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
from app.services.analysis import llm, speaker_identity


def transcript(rows):
    return "\n\n".join(
        f"{index}\n00:00:00,000 --> 00:00:01,000\n[{speaker}] {text}"
        for index, (speaker, text) in enumerate(rows, 1)
    ) + "\n"


def mapping(label="SPEAKER_00", name="张明", evidence=None, **extra):
    return {
        "source_label": label,
        "current_name": name,
        "evidence_indexes": [1] if evidence is None else evidence,
        "reason": "本人自我介绍，全文发言一致",
        **extra,
    }


def response(*mappings):
    return json.dumps({"mappings": list(mappings)}, ensure_ascii=False)


@pytest.fixture
def model(monkeypatch):
    call = AsyncMock(return_value=response())
    monkeypatch.setattr(llm, "get_llm_service", lambda: SimpleNamespace(_call=call))
    monkeypatch.setattr(settings, "_runtime_settings", settings.RuntimeSettings())
    return call


@pytest.mark.asyncio
async def test_full_transcript_uses_name_at_end_for_all_matching_cues(model):
    rows = [("SPEAKER_00", "完整长篇讨论内容。" * 80) for _ in range(30)]
    rows.append(("SPEAKER_00", "感谢收听，我是张明。"))
    source = transcript(rows)
    model.return_value = response(mapping(evidence=[31]))
    report = await speaker_identity.infer_speaker_names(source)
    assert report["status"] == "completed"
    payload = json.loads(model.call_args.args[0].split("完整字幕与来源：\n")[1])
    assert len(payload["cues"]) == 31
    assert payload["cues"][-1]["text"] == "感谢收听，我是张明。"
    assert model.await_count == 1
    assert model.call_args.kwargs["stage"] == "polish"
    assert speaker_identity.apply_speaker_names(source, report["mappings"]) == source.replace(
        "[SPEAKER_00]", "[张明]"
    )


@pytest.mark.parametrize("label", ["SPEAKER_00", "Unknown-abcd", "Speaker 1", "说话人1"])
@pytest.mark.asyncio
async def test_single_speaker_can_be_named(model, label):
    source = transcript([(label, "大家好，我是张明。")])
    model.return_value = response(mapping(label=label))
    report = await speaker_identity.infer_speaker_names(source)
    assert report["status"] == "completed"
    assert report["mappings"][0]["current_name"] == "张明"


@pytest.mark.asyncio
async def test_unknown_identity_preserves_original_subtitles(model):
    source = transcript([("SPEAKER_00", "今天聊一下技术。")])
    report = await speaker_identity.infer_speaker_names(source)
    assert report == {"status": "completed", "mappings": []}
    assert speaker_identity.apply_speaker_names(source, report["mappings"]) == source


@pytest.mark.parametrize(
    "context",
    [
        {"title": "张明的技术分享"},
        {"description": "本期主讲人为张明。"},
        {"speaker_candidates": [{"name": "张明", "role": "主讲人"}]},
    ],
)
@pytest.mark.asyncio
async def test_name_can_be_grounded_in_source_context(model, context):
    source = transcript([("SPEAKER_00", "欢迎来到我的技术分享。")])
    model.return_value = response(mapping())
    assert (await speaker_identity.infer_speaker_names(source, context))["status"] == "completed"


@pytest.mark.parametrize(
    "bad",
    [
        "invalid JSON",
        "[]",
        '{}',
        '{"mappings": {}}',
        response("invalid mapping"),
        response(mapping(label="SPEAKER_99")),
        response(mapping(name="王五")),
        response(mapping(evidence=[99])),
        response(mapping(evidence=[True])),
        response(mapping(evidence=[])),
        response(mapping(reason="")),
        response(mapping(name="[张明]")),
        response(mapping(name="张明\n[SPEAKER_01]")),
        response(mapping(name="<b>张明</b>")),
        response(mapping(name="SPEAKER_00")),
        response(mapping(), mapping()),
    ],
)
@pytest.mark.asyncio
async def test_invalid_mappings_fail_without_changing_subtitles(model, bad):
    source = transcript([("SPEAKER_00", "我是张明。")])
    model.return_value = bad
    report = await speaker_identity.infer_speaker_names(source)
    assert report["status"] == "failed"
    assert report["mappings"] == []
    assert report["error"]
    assert speaker_identity.apply_speaker_names(source, report["mappings"]) == source


@pytest.mark.asyncio
async def test_unrelated_context_cannot_supply_name_evidence(model):
    source = transcript([("SPEAKER_00", "今天聊一下技术。")])
    model.return_value = response(mapping())
    report = await speaker_identity.infer_speaker_names(source, {"proper_nouns": ["张明"]})
    assert report["status"] == "failed"


@pytest.mark.asyncio
async def test_evidence_must_include_the_mapped_speakers_dialogue(model):
    source = transcript([("SPEAKER_00", "我是张明。"), ("SPEAKER_01", "很高兴认识你。")])
    model.return_value = response(mapping(label="SPEAKER_01", evidence=[1]))
    assert (await speaker_identity.infer_speaker_names(source))["status"] == "failed"


@pytest.mark.asyncio
async def test_name_conflicts_are_rejected(model):
    source = transcript([("SPEAKER_00", "张明，你怎么看？"), ("张明", "我们明天开始。")])
    model.return_value = response(mapping())
    assert (await speaker_identity.infer_speaker_names(source))["status"] == "failed"
    source = transcript([("SPEAKER_00", "我是张明。"), ("SPEAKER_01", "张明，你怎么看？")])
    model.return_value = response(mapping(), mapping(label="SPEAKER_01", evidence=[2]))
    assert (await speaker_identity.infer_speaker_names(source))["status"] == "failed"


@pytest.mark.parametrize("source", ["", transcript([("张明", "今天聊一下技术。")])])
@pytest.mark.asyncio
async def test_no_anonymous_labels_skips_model(model, source):
    assert await speaker_identity.infer_speaker_names(source) == {
        "status": "skipped", "mappings": []
    }
    model.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_errors_fail_soft_and_cancellation_propagates(model):
    source = transcript([("SPEAKER_00", "我是张明。")])
    model.side_effect = RuntimeError("LLM unavailable")
    report = await speaker_identity.infer_speaker_names(source)
    assert report == {"status": "failed", "mappings": [], "error": "LLM unavailable"}
    model.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await speaker_identity.infer_speaker_names(source)


def test_apply_names_preserves_cue_body_line_endings_and_named_speakers():
    source = (
        "1\r\n00:00:00,000 --> 00:00:01,000\r\n  [SPEAKER_00]  我是张明。\r\n"
        "正文引用 [SPEAKER_00]。\r\n\r\n"
        "2\r\n00:00:01,000 --> 00:00:02,000\r\n[李华] 保留原名。\r\n"
    )
    result = speaker_identity.apply_speaker_names(
        source, [mapping(), mapping(label="李华", name="王五")]
    )
    assert result == source.replace("  [SPEAKER_00]", "  [张明]", 1)


def lecture_context(**changes):
    return {
        "title": "青稞 Talk 第146期",
        "description": "",
        "analysis": {"content_type": "技术讲座/演讲"},
        "summary": {
            "tldr": "青稞Talk第146期：江澈博士梳理多模态研究的发展。",
            "key_facts": ["演讲者江澈是清华大学电子系博士生。"],
        },
        "speaker_activity": [
            {"speaker": "SPEAKER_00", "duration_seconds": 975, "share": 0.975},
            {"speaker": "SPEAKER_01", "duration_seconds": 25, "share": 0.025},
        ],
        **changes,
    }


@pytest.mark.asyncio
async def test_dominant_lecture_uses_summary_identity_and_bounded_excerpts(model):
    source = transcript(
        [("SPEAKER_00", "接下来讲解多模态研究中的技术细节。" * 120) for _ in range(120)]
        + [("SPEAKER_01", "请问您如何理解这个技术问题？")]
    )
    context = lecture_context()
    model.return_value = response(mapping(name="江澈", evidence=[]))
    report = await speaker_identity.infer_speaker_names(source, context)

    assert report["status"] == "completed"
    assert report["method"] == "dominant_lecture"
    assert report["speaker_activity"] == context["speaker_activity"]
    assert len(report["mappings"]) == 1
    assert report["mappings"][0]["current_name"] == "江澈"
    assert "97.5%" in report["mappings"][0]["reason"]
    assert "summary" in report["mappings"][0]["reason"]
    prompt = model.call_args.args[0]
    payload = json.loads(prompt.split("主讲识别资料：\n")[1])
    assert payload["summary"] == context["summary"]
    assert payload["description"] == ""
    assert payload["main_speaker"] == "SPEAKER_00"
    assert len(payload["speaker_excerpts"]) <= 6
    assert all(len(cue["text"]) <= 600 for cue in payload["speaker_excerpts"])
    assert all(cue["speaker"] == "SPEAKER_00" for cue in payload["speaker_excerpts"])
    assert len(prompt) < 10000 and len(prompt) * 10 < len(source)
    updated = speaker_identity.apply_speaker_names(source, report["mappings"])
    assert "[SPEAKER_00]" not in updated
    assert "[SPEAKER_01] 请问" in updated
    model.assert_awaited_once()


@pytest.mark.parametrize("content_type", ["技术分享", "Talk", "其他"])
@pytest.mark.asyncio
async def test_lecture_type_or_explicit_summary_selects_compact_mode(model, content_type):
    source = transcript([("SPEAKER_00", "下面介绍今天的技术主题。")])
    model.return_value = response(mapping(name="江澈", evidence=[]))
    context = lecture_context(analysis={"content_type": content_type})
    report = await speaker_identity.infer_speaker_names(source, context)
    assert report["status"] == "completed"
    assert report["method"] == "dominant_lecture"


@pytest.mark.parametrize("content_type", ["访谈", "人物对谈", "interview", "talk show"])
@pytest.mark.asyncio
async def test_interview_preserves_full_dialogue_even_with_dominant_speaker(model, content_type):
    source = transcript([
        ("SPEAKER_00", "我来介绍自己的研究。"), ("SPEAKER_01", "您何时开始这个项目？"),
    ])
    report = await speaker_identity.infer_speaker_names(
        source, lecture_context(analysis={"content_type": content_type})
    )
    assert report == {"status": "completed", "mappings": []}
    payload = json.loads(model.call_args.args[0].split("完整字幕与来源：\n")[1])
    assert len(payload["cues"]) == 2


@pytest.mark.asyncio
async def test_lecture_without_dominant_share_uses_full_dialogue(model):
    source = transcript([("SPEAKER_00", "我是江澈。"), ("SPEAKER_01", "我来补充这个主题。")])
    context = lecture_context(speaker_activity=[
        {"speaker": "SPEAKER_00", "duration_seconds": 650, "share": 0.65},
        {"speaker": "SPEAKER_01", "duration_seconds": 350, "share": 0.35},
    ])
    report = await speaker_identity.infer_speaker_names(source, context)
    assert report == {"status": "completed", "mappings": []}
    assert "完整字幕与来源：" in model.call_args.args[0]


@pytest.mark.asyncio
async def test_named_lecturer_preserves_remaining_anonymous_speakers(model):
    source = transcript([("江澈", "下面分享研究成果。"), ("SPEAKER_01", "请问一个问题。")])
    context = lecture_context(speaker_activity=[
        {"speaker": "江澈", "duration_seconds": 950, "share": 0.95},
        {"speaker": "SPEAKER_01", "duration_seconds": 50, "share": 0.05},
    ])
    report = await speaker_identity.infer_speaker_names(source, context)
    assert report["status"] == "skipped" and report["mappings"] == []
    assert report["method"] == "dominant_lecture"
    model.assert_not_awaited()


@pytest.mark.parametrize(
    "candidate, summary",
    [
        ("王五", {"tldr": "演讲者江澈介绍多模态研究。"}),
        ("张明", {"tldr": "主讲人介绍导师张明的研究，引用作者张明的论文。"}),
        ("张明", {"tldr": "演讲者江澈介绍导师张明分享过的研究经历。"}),
    ],
)
@pytest.mark.asyncio
async def test_compact_mode_rejects_missing_identity_or_only_mentioned_author(
    model, candidate, summary,
):
    source = transcript([("SPEAKER_00", "今天讨论技术问题。")])
    model.return_value = response(mapping(name=candidate, evidence=[]))
    report = await speaker_identity.infer_speaker_names(source, lecture_context(summary=summary))
    assert report["status"] == "failed" and report["mappings"] == []


@pytest.mark.asyncio
async def test_compact_mode_rejects_mapping_a_secondary_speaker(model):
    source = transcript([("SPEAKER_00", "今天讨论技术问题。"), ("SPEAKER_01", "谢谢。")])
    model.return_value = response(mapping(label="SPEAKER_01", name="江澈", evidence=[]))
    report = await speaker_identity.infer_speaker_names(source, lecture_context())
    assert report["status"] == "failed" and report["mappings"] == []


@pytest.mark.asyncio
async def test_compact_mode_keeps_opening_self_introduction_as_evidence(model):
    source = transcript([("SPEAKER_00", "大家好，我叫江澈。")])
    model.return_value = response(mapping(name="江澈"))
    context = lecture_context(summary={"tldr": "这次讲座介绍多模态研究的技术进展。"})
    report = await speaker_identity.infer_speaker_names(source, context)
    assert report["status"] == "completed"
    assert report["mappings"][0]["evidence_indexes"] == [1]
    assert "字幕 1" in report["mappings"][0]["reason"]


def test_speaker_activity_prefers_acoustic_turns_and_merges_same_speaker_overlap():
    source = transcript([("Unknown-abcd", "研究介绍"), ("SPEAKER_01", "提问")])
    activity = speaker_identity.build_speaker_activity(source, turns=[
        {"speaker": "SPEAKER_00", "start": 0, "end": 80},
        {"speaker": "SPEAKER_00", "start": 40, "end": 100},
        {"speaker": "SPEAKER_01", "start": 100, "end": 110},
    ], speaker_map={"mappings": [{
        "source_label": "SPEAKER_00", "current_name": "Unknown-abcd",
    }]})
    assert activity == [
        {"speaker": "Unknown-abcd", "duration_seconds": 100, "share": 0.909091, "cue_count": 1},
        {"speaker": "SPEAKER_01", "duration_seconds": 10, "share": 0.090909, "cue_count": 1},
    ]


def test_speaker_activity_falls_back_to_srt_duration_not_cue_count():
    source = (
        "1\n00:00:00,000 --> 00:00:08,000\n[SPEAKER_00] 主要介绍\n\n"
        "2\n00:00:04,000 --> 00:00:10,000\n[SPEAKER_00] 重叠片段\n\n"
        "3\n00:00:10,000 --> 00:00:11,000\n[SPEAKER_01] 问题\n\n"
        "4\n00:00:11,000 --> 00:00:12,000\n[SPEAKER_01] 补充问题\n"
    )
    activity = speaker_identity.build_speaker_activity(source)
    assert activity[0] == {
        "speaker": "SPEAKER_00", "duration_seconds": 10, "share": 0.833333, "cue_count": 2,
    }
    assert activity[1] == {
        "speaker": "SPEAKER_01", "duration_seconds": 2, "share": 0.166667, "cue_count": 2,
    }
    assert speaker_identity.build_speaker_activity("invalid or missing transcript") == []
