from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import settings
from app.services.analysis import llm, speaker_identity
from app.services.analysis.participants import source_participants, title_collaborators
from app.services.analysis.prompts.speaker_identity import PARTICIPANT_BINDING_SYSTEM_PROMPT
from app.services.analysis.source_context import build_deterministic_source_context

TITLE = "美国人要用AI创死所有人？DSA要学习MAGA？【瓜熟迪落拉XEric Hovagim02】"


@pytest.mark.parametrize(("title", "uploader", "expected"), [
    (TITLE, "瓜熟迪落拉", ["瓜熟迪落拉", "Eric Hovagim"]),
    ("【瓜熟迪落拉×张三】聊聊大选", "瓜熟迪落拉", ["瓜熟迪落拉", "张三"]),
    ("罗永浩 x 张一鸣 对谈：创业", "", ["罗永浩", "张一鸣"]),
    ("Andrej Karpathy & Dwarkesh Patel — AGI is still a decade away", "", ["Andrej Karpathy", "Dwarkesh Patel"]),
    ("【小明和老王】一起吃饭", "小明", ["小明", "老王"]),
    ("【AI和人类】未来会怎样", "某UP", []),
    ("EP26 | 开发者软件与物理AI的未来：与NVIDIA对话", "NVIDIA", []),
    ("清华姚班四人对谈", "涌现Lab", []),
    ("GPT-5 vs Claude 5 实测对比", "测评君", []),
])
def test_title_collaborations(title, uploader, expected):
    assert title_collaborators(title, uploader) == expected


def test_uploader_in_a_collaboration_title_hosts_and_garbled_entities_are_dropped():
    context = build_deterministic_source_context({"title": TITLE, "uploader": "瓜熟迪落拉"}, {})
    assert [(item.name, item.role) for item in context.speaker_candidates] == [("瓜熟迪落拉", "host"), ("Eric Hovagim", "guest")]
    assert "XEric Hovagim02" not in context.asr_hotwords and "Eric Hovagim" in context.asr_hotwords
    people = source_participants({"title": TITLE, "uploader": "瓜熟迪落拉",
                                  "speaker_candidates": [{"name": "Eric Hovagim", "role": "guest"}]})
    assert [(row["name"], row["role"]) for row in people] == [("瓜熟迪落拉", "host"), ("Eric Hovagim", "guest")]
    # The uploader alone is not a participant.
    assert source_participants({"title": "数据中心的真相", "uploader": "瓜熟迪落拉"}) == []


def transcript(rows):
    return "\n\n".join(
        f"{index}\n00:00:{index:02d},000 --> 00:00:{index:02d},900\n[{speaker}] {text}"
        for index, (speaker, text) in enumerate(rows, 1)
    ) + "\n"


SOURCE = transcript([
    ("Unknown-e64e", "Welcome back. Today I'm talking with a Marxist organizer about data centers. Why are Americans so angry?"),
    ("Unknown-c072", "Yeah, it's very clear cut. The data centers are built to maximize immediate return on investment."),
    ("Unknown-c072", "Corporations go in, bribe city councils and get sweetheart deals on energy and water."),
    ("Unknown-e64e", "So what should the left learn from MAGA?"),
    ("Unknown-6610", "MAGA's methods can be studied, but we should never trust its will."),
    ("Unknown-0a09", "This is not the American dream that we were promised."),
])
CONTEXT = {
    "title": TITLE, "uploader": "瓜熟迪落拉", "description": "美国马克思主义者埃里克认为……",
    "speaker_activity": [
        {"speaker": "Unknown-c072", "share": 0.62}, {"speaker": "Unknown-e64e", "share": 0.25},
        {"speaker": "Unknown-6610", "share": 0.1}, {"speaker": "Unknown-0a09", "share": 0.02},
    ],
}


@pytest.fixture
def model(monkeypatch):
    call = AsyncMock()
    monkeypatch.setattr(llm, "get_llm_service", lambda: SimpleNamespace(_call=call))
    monkeypatch.setattr(settings, "_runtime_settings", settings.RuntimeSettings())
    return call


def answers(binding):
    async def answer(prompt, **kwargs):
        if kwargs["system_prompt"] == PARTICIPANT_BINDING_SYSTEM_PROMPT:
            return binding() if callable(binding) else binding
        return json.dumps({"mappings": []})
    return answer


@pytest.mark.asyncio
async def test_title_and_uploader_name_the_voices_that_ask_and_answer(model):
    model.side_effect = answers(json.dumps({
        "mappings": [
            {"source_label": "Unknown-e64e", "current_name": "瓜熟迪落拉", "evidence_indexes": [1, 4, 2], "reason": "开场并提问"},
            {"source_label": "Unknown-c072", "current_name": "Eric Hovagim", "evidence_indexes": [2], "reason": "长篇回答"},
            {"source_label": "Unknown-0a09", "current_name": "Eric Hovagim", "reason": "重复的名字"},
            {"source_label": "Unknown-9999", "current_name": "瓜熟迪落拉", "reason": "不存在的声音"},
        ],
    }, ensure_ascii=False))
    report = await speaker_identity.infer_speaker_names(SOURCE, CONTEXT)
    assert report["status"] == "completed"
    named = {item["source_label"]: item for item in report["mappings"]}
    assert {label: item["current_name"] for label, item in named.items()} == {
        "Unknown-e64e": "瓜熟迪落拉", "Unknown-c072": "Eric Hovagim",
    }
    assert named["Unknown-e64e"]["basis"] == "source_participants"
    assert named["Unknown-e64e"]["evidence_indexes"] == [1, 4]  # Only that voice's own lines count.
    assert named["Unknown-e64e"]["reason"].startswith("标题联名，且是上传者")
    # A second voice for an already named person is left to the voiceprint comparison.
    assert "suggestions" not in report
    binding_prompt = next(call.args[0] for call in model.call_args_list
                          if call.kwargs["system_prompt"] == PARTICIPANT_BINDING_SYSTEM_PROMPT)
    voices = json.loads(binding_prompt.split("资料：\n")[1])["voices"]
    assert {voice["id"]: voice["questions"] for voice in voices}["Unknown-e64e"] == 2
    named_srt = speaker_identity.apply_speaker_names(SOURCE, report["mappings"])
    assert named_srt.count("[Eric Hovagim]") == 2 and named_srt.count("[瓜熟迪落拉]") == 2
    assert "[Unknown-6610]" in named_srt


@pytest.mark.asyncio
async def test_binding_failure_keeps_the_report_and_without_participants_nothing_is_asked(model):
    model.side_effect = answers(lambda: "not json")
    report = await speaker_identity.infer_speaker_names(SOURCE, CONTEXT)
    assert report["status"] == "completed" and report["mappings"] == []
    assert "participant_binding_error" in report
    model.reset_mock()
    model.side_effect = answers(lambda: pytest.fail("no participants to bind"))
    plain = await speaker_identity.infer_speaker_names(SOURCE, {**CONTEXT, "title": "数据中心的真相"})
    assert plain == {"status": "completed", "mappings": []}


@pytest.mark.asyncio
async def test_self_introduction_wins_and_binding_only_fills_the_rest(model):
    source = transcript([
        ("Unknown-a1", "大家好，我是瓜熟迪落拉，今天请到了一位嘉宾。"),
        ("Unknown-b2", "谢谢邀请，很高兴来到节目。"),
    ])

    async def answer(prompt, **kwargs):
        if kwargs["system_prompt"] == PARTICIPANT_BINDING_SYSTEM_PROMPT:
            payload = json.loads(prompt.split("资料：\n")[1])
            assert [voice["id"] for voice in payload["voices"]] == ["Unknown-b2"]
            assert [row["name"] for row in payload["participants"]] == ["Eric Hovagim"]
            return json.dumps({"mappings": [{"source_label": "Unknown-b2", "current_name": "Eric Hovagim", "reason": "嘉宾"}]})
        return json.dumps({"mappings": [{"source_label": "Unknown-a1", "current_name": "瓜熟迪落拉",
                                         "evidence_indexes": [1], "reason": "自我介绍"}]}, ensure_ascii=False)

    model.side_effect = answer
    report = await speaker_identity.infer_speaker_names(source, {**CONTEXT, "speaker_activity": []})
    assert [(item["source_label"], item["current_name"]) for item in report["mappings"]] == [
        ("Unknown-a1", "瓜熟迪落拉"), ("Unknown-b2", "Eric Hovagim"),
    ]
    assert "basis" not in report["mappings"][0]


@pytest.mark.parametrize(("text", "addresses", "mentions"), [
    ("So that is the difference, in my opinion. What's your opinion, Eric?", True, True),
    ("Eric, what do you think about MAGA?", True, True),
    ("Thank you, Eric Hovagim, for joining us.", True, True),
    ("I remember I was talking to Eric last time.", False, True),
    ("Hi everyone, I'm Eric Hovagim.", False, False),
    ("The numeric example is generic.", False, False),
])
def test_calling_someone_by_name(text, addresses, mentions):
    assert speaker_identity._addresses(text, "Eric Hovagim") is addresses
    assert speaker_identity._mentions(text, "Eric Hovagim") is mentions


@pytest.mark.asyncio
async def test_a_voice_that_calls_a_person_by_name_is_never_given_that_name(model):
    source = transcript([
        ("Unknown-c072", "Yeah, it's very clear cut. Data centers are built for return on investment."),
        ("Unknown-e64e", "That is the difference between China and the US. What's your opinion, Eric?"),
        ("Unknown-c072", "I would more or less agree with that, and here is why."),
    ])
    # A swapped answer: the voice that asks Eric is labelled Eric.
    model.side_effect = answers(json.dumps({"mappings": [
        {"source_label": "Unknown-e64e", "current_name": "Eric Hovagim", "reason": "错误对应"},
        {"source_label": "Unknown-c072", "current_name": "瓜熟迪落拉", "reason": "错误对应"},
    ]}, ensure_ascii=False))
    report = await speaker_identity.infer_speaker_names(source, {**CONTEXT, "speaker_activity": []})
    assert [(row["source_label"], row["current_name"]) for row in report["mappings"]] == [("Unknown-c072", "瓜熟迪落拉")]
    prompt = next(call.args[0] for call in model.call_args_list if call.kwargs["system_prompt"] == PARTICIPANT_BINDING_SYSTEM_PROMPT)
    voices = {voice["id"]: voice for voice in json.loads(prompt.split("资料：\n")[1])["voices"]}
    assert voices["Unknown-e64e"]["addresses"] == ["Eric Hovagim"]
    assert voices["Unknown-e64e"]["says_names"] == {"Eric Hovagim": 1}
    assert "What's your opinion, Eric?" in [sample["text"][-26:] for sample in voices["Unknown-e64e"]["samples"]][0]


def test_voice_suggestions_offer_a_merge_only_for_similar_voices(tmp_path, monkeypatch):
    import numpy as np

    from app.services import voiceprint
    from app.services.analysis import speaker_identity_artifacts as artifacts

    (tmp_path / "speaker_map.json").write_text(json.dumps({"mappings": [
        {"source_label": "SPEAKER_00", "current_name": "Unknown-0a09"},
        {"source_label": "SPEAKER_01", "current_name": "Unknown-6610"},
        {"source_label": "SPEAKER_02", "current_name": "Unknown-c072"},
        {"source_label": "SPEAKER_03", "current_name": "瓜熟迪落拉"},
    ]}), encoding="utf-8")
    angles = {"SPEAKER_00": [0, 0, 1], "SPEAKER_01": [0.68, 0.733, 0], "SPEAKER_02": [1, 0, 0], "SPEAKER_03": [0, -1, 0]}
    vectors = {f"s{label}": np.array(vector, dtype=np.float32) for label, vector in angles.items()}

    class Store:
        def list_task_speakers(self, task_id):
            return [{"speaker_label": label, "sample_id": f"s{label}", "person_name": ""} for label in angles]

        def sample_embeddings(self, sample_ids):
            return {sample: vectors[sample] for sample in sample_ids}

    monkeypatch.setattr(voiceprint, "get_voiceprint_store", lambda: Store())
    monkeypatch.setattr(settings, "_runtime_settings", settings.RuntimeSettings(data_root=str(tmp_path)))
    srt = transcript([("Eric Hovagim", "a"), ("Unknown-6610", "b"), ("Unknown-0a09", "c"), ("瓜熟迪落拉", "d")])
    # Unknown-c072 was just named Eric Hovagim; 6610 sounds like it (cos 0.68), 0a09 like nobody.
    rows = artifacts.voice_suggestions("task", tmp_path, srt, {"Unknown-c072": "Eric Hovagim"})
    assert [(row["source_label"], row["current_name"]) for row in rows] == [("Unknown-6610", "Eric Hovagim")]
    assert rows[0]["reason"] == "声音相似度 0.68"
    assert artifacts.voice_suggestions("task", tmp_path, transcript([("瓜熟迪落拉", "d")])) == []
