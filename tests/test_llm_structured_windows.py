"""Regression coverage for long cue processing and shared speaker identities."""
import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.analysis.long_text import byte_size, record_windows
from app.services.analysis import llm, speaker_identity, speaker_review
from app.services.analysis.source_context import build_source_context, speaker_constraints


@pytest.mark.asyncio
async def test_windows_cover_each_editable_cue_once_with_read_only_overlap():
    rows = [{"index": i, "text": "中文🎧" * 30} for i in range(50)]
    edited, visible_ids = [], []
    async def call(prompt):
        assert byte_size(prompt) <= 1800
        return "[]"
    async for targets, visible, response in record_windows(
        rows, lambda t, v: json.dumps({"editable": [r["index"] for r in t], "cues": v}, ensure_ascii=False),
        call, budget=1800, chunk_size=12, overlap=2,
    ):
        edited.extend(r["index"] for r in targets)
        visible_ids.extend(r["index"] for r in visible)
    assert edited == list(range(50))
    assert any(count > 1 for count in Counter(visible_ids).values())


@pytest.mark.asyncio
async def test_single_giant_cue_keeps_unicode_speaker_and_all_text():
    body = "中文🎧 English " * 500 + "LAST_WORD"
    rows = [{"index": 9, "timestamp": "00:00:01,000 --> 00:00:02,000", "text": "[SPEAKER_00] " + body}]
    fragments = []
    async def call(prompt):
        assert byte_size(prompt) <= 1100
        return prompt
    async for targets, visible, response in record_windows(
        rows, lambda t, v: json.dumps(t, ensure_ascii=False), call, budget=1100,
    ):
        assert len(targets) == 1 and targets[0]["index"] == 9
        assert targets[0]["timestamp"] == rows[0]["timestamp"]
        assert targets[0]["text"].startswith("[SPEAKER_00] ")
        fragments.append(targets[0]["text"].removeprefix("[SPEAKER_00] "))
    assert "".join(fragments) == body


@pytest.mark.asyncio
async def test_provider_context_rejection_shrinks_windows_without_losing_cues():
    rows = [{"index": i, "text": "source" * 100} for i in range(24)]
    seen = []
    async def call(prompt):
        if byte_size(prompt) > 2100:
            raise RuntimeError("exceed_context_size_error")
        return "[]"
    async for targets, _, _ in record_windows(
        rows, lambda t, v: json.dumps(v), call, budget=12000, chunk_size=24, overlap=2,
    ):
        seen.extend(r["index"] for r in targets)
    assert seen == list(range(24))


@pytest.mark.asyncio
async def test_identity_conflicting_windows_keep_anonymous_label(monkeypatch):
    rows = [f"{i}\n00:00:00,000 --> 00:00:01,000\n[SPEAKER_00] 我是{'张明' if i < 81 else '李华'}。" for i in range(1, 161)]
    async def call(prompt, **kwargs):
        cues = json.loads(prompt.split("本窗口字幕与来源：\n")[1])["cues"]
        cue = cues[len(cues) // 2]
        name = "张明" if cue["index"] < 81 else "李华"
        return json.dumps({"mappings": [{"source_label": "SPEAKER_00", "current_name": name,
                           "evidence_indexes": [cue["index"]], "reason": "自我介绍"}]}, ensure_ascii=False)
    monkeypatch.setattr(llm, "get_llm_service", lambda: SimpleNamespace(_call=call))
    report = await speaker_identity.infer_speaker_names("\n\n".join(rows))
    assert report["status"] == "completed"
    assert report["mappings"] == []
    assert report["conflicting_labels"] == ["SPEAKER_00"]


@pytest.mark.asyncio
async def test_inferred_guest_count_does_not_override_explicit_speaker_limit(monkeypatch):
    async def call(prompt, **kwargs):
        return json.dumps({"speaker_count_hint": {"exact": 3, "confidence": 0.99, "evidence": "guest list"}})
    monkeypatch.setattr(llm, "get_llm_service", lambda: SimpleNamespace(_call=call))
    metadata = {"title": "科学播客", "description": "本期邀请嘉宾张明聊科学。", "uploader": "科学频道"}
    inferred = await build_source_context(metadata)
    assert speaker_constraints(inferred) == (None, None, None)
    explicit = await build_source_context(metadata, {"num_speakers": 2})
    assert speaker_constraints(explicit) == (2, 2, 2)


@pytest.mark.asyncio
async def test_polish_giant_single_cue_preserves_text_timestamp_and_speaker(monkeypatch):
    from app.core import model_router
    from app.services.analysis.transcript_outputs import _parse_srt, _split_speaker_prefix
    monkeypatch.setattr(model_router, "resolve_llm_binding", lambda *a, **kw: SimpleNamespace(
        provider="local", request_kwargs={"ctx": 16384, "max_new_tokens": 4096}))
    service = llm.LLMService()
    body = "这是完整的一句话，保留全部正文。" * 1000 + "结尾。"
    srt = "1\n00:00:01,000 --> 00:10:02,000\n[SPEAKER_00] " + body
    calls = []
    async def call(prompt, **kwargs):
        assert byte_size(prompt + kwargs["system_prompt"]) + 4096 <= 16384
        rows = _parse_srt(prompt)
        # The prompt contains prose around the SRT; isolate its bounded subtitle section.
        import re
        matched = re.search(r"(1\n00:00:01,000 --> 00:10:02,000\n[^\n]+)", prompt)
        rows = _parse_srt(matched.group(1))
        calls.extend(rows)
        return json.dumps(rows, ensure_ascii=False)
    monkeypatch.setattr(service, "_call", call)
    result = await service.polish(srt)
    cues = _parse_srt(result)
    assert len(calls) > 1
    assert cues[0]["timestamp"].startswith("00:00:01,000")
    assert cues[-1]["timestamp"].endswith("00:10:02,000")
    assert {_split_speaker_prefix(cue["text"])[0] for cue in cues} == {"SPEAKER_00"}
    assert "".join(_split_speaker_prefix(cue["text"])[1] for cue in cues) == body


@pytest.mark.asyncio
async def test_invalid_window_json_is_repaired_within_the_request_budget():
    replies = iter(["[{broken", "[]"])
    calls = []
    async def call(prompt):
        assert byte_size(prompt) <= 4096
        calls.append(prompt)
        return next(replies)
    results = [response async for _, _, response in record_windows(
        [{"index": 1, "text": "字幕正文"}], lambda targets, visible: json.dumps(targets), call,
        budget=4096, validate=lambda response, targets, visible: json.loads(response),
    )]
    assert results == [[]] and len(calls) == 2


@pytest.mark.asyncio
async def test_output_truncation_preserves_large_fixed_prompt_budget():
    rows = [{"index": i, "text": "content" * 40} for i in range(8)]
    seen = []
    async def call(prompt):
        payload = json.loads(prompt[4000:])
        if len(payload) > 2:
            raise RuntimeError("模型输出被截断")
        return "[]"
    async for targets, _, _ in record_windows(
        rows, lambda t, v: "x" * 4000 + json.dumps(t), call,
        budget=10000, chunk_size=8, overlap=0,
    ):
        seen.extend(row["index"] for row in targets)
    assert seen == list(range(8))


@pytest.mark.parametrize("message", ["prompt is too long: 18000 tokens", "input is too long", "too many tokens"])
def test_cloud_context_errors_use_the_same_window_recovery(message):
    from app.services.analysis.long_text import is_context_overflow
    assert is_context_overflow(RuntimeError(message))
    assert not is_context_overflow(RuntimeError("invalid API key"))
