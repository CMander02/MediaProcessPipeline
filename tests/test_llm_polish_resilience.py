import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.analysis import llm as llm_module  # noqa: E402
from app.services.analysis.llm import LLMService  # noqa: E402


def _srt_segment(index: int, text: str) -> dict[str, object]:
    start = index - 1
    return {
        "index": index,
        "timestamp": f"00:00:{start:02d},000 --> 00:00:{index:02d},000",
        "text": text,
    }


def _srt(segments: list[dict[str, object]]) -> str:
    return "\n\n".join(
        f"{segment['index']}\n{segment['timestamp']}\n{segment['text']}"
        for segment in segments
    )


def test_polish_constraints_restore_cue_fields_and_prevent_speaker_invention():
    service = LLMService()
    original = [
        {
            "index": 1,
            "timestamp": "00:00:00,000 --> 00:00:01,000",
            "text": "Evolvent 发布成果",
        },
        {
            "index": 2,
            "timestamp": "00:00:01,000 --> 00:00:02,000",
            "text": "[SPEAKER_00] 原始内容",
        },
    ]
    polished = [
        {
            "index": 1,
            "timestamp": "00:09:00,000 --> 00:09:01,000",
            "text": "[主持人] Evolvent 发布了成果。",
        },
        {
            "index": 2,
            "timestamp": "00:09:01,000 --> 00:09:02,000",
            "text": "[嘉宾] 润色内容。",
        },
    ]
    context = {
        "entities": [
            {
                "canonical": "Evolvent AI",
                "aliases": ["Evolvent"],
                "type": "organization",
                "evidence": "title",
            }
        ]
    }

    constrained = service._enforce_polish_constraints(polished, original, context)

    assert constrained[0] == {
        "index": 1,
        "timestamp": original[0]["timestamp"],
        "text": "Evolvent AI 发布了成果。",
    }
    assert constrained[1]["timestamp"] == original[1]["timestamp"]
    assert constrained[1]["text"] == "[SPEAKER_00] 润色内容。"


@pytest.mark.asyncio
async def test_summary_preserves_source_timeline(monkeypatch):
    service = LLMService()
    source_context = {
        "timeline": [
            {"start": 0, "title": "开场", "source": "derived"},
            {"start": 75, "title": "第一章", "source": "source_chapter"},
        ],
        "entities": [],
    }

    async def fake_call(_prompt, **_kwargs):
        return json.dumps(
            {
                "tldr": "摘要",
                "key_facts": [],
                "action_items": [],
                "topics": [],
                "timeline": [
                    {"start": 75, "title": "模型改写的标题", "summary": "章节摘要"}
                ],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(service, "_call", fake_call)
    result = await service.summarize("正文", source_context=source_context)

    assert [(item["start"], item["title"]) for item in result["timeline"]] == [
        (0, "开场"),
        (75, "第一章"),
    ]
    assert result["timeline"][1]["summary"] == "章节摘要"


@pytest.mark.asyncio
async def test_long_summary_maps_each_source_chapter_before_reduce(monkeypatch):
    service = LLMService()
    source_context = {
        "timeline": [
            {"start": 0, "title": "开场", "source": "derived"},
            {"start": 60, "title": "第一章", "source": "source_chapter"},
        ],
        "entities": [],
    }
    long_srt = (
        "1\n00:00:00,000 --> 00:00:59,000\n" + "甲" * 8000 + "\n\n"
        "2\n00:01:00,000 --> 00:02:00,000\n" + "乙" * 8000
    )
    calls = []

    async def fake_call(prompt, **_kwargs):
        calls.append(prompt)
        return json.dumps(
            {
                "tldr": "章节摘要",
                "key_facts": ["事实"],
                "action_items": [],
                "topics": ["主题"],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(service, "_call", fake_call)
    result = await service.summarize(long_srt, source_context=source_context)

    assert len(calls) == 3
    assert [(item["start"], item["title"]) for item in result["timeline"]] == [
        (0, "开场"),
        (60, "第一章"),
    ]
    assert all(item["summary"] == "章节摘要" for item in result["timeline"])


@pytest.mark.asyncio
async def test_parallel_polish_retries_only_transiently_failed_chunk(monkeypatch):
    service = LLMService()
    segments = [_srt_segment(index, f"cue-{index}") for index in range(1, 7)]
    chunks = [segments[0:2], segments[2:4], segments[4:6]]
    calls = [0, 0, 0]

    monkeypatch.setattr(service, "_effective_provider", lambda _override="": "deepseek")
    monkeypatch.setattr(
        llm_module,
        "get_runtime_settings",
        lambda: SimpleNamespace(llm_polish_concurrency=2),
    )

    async def fake_call(prompt, **_kwargs):
        chunk_index = next(
            index for index, chunk in enumerate(chunks) if str(chunk[0]["text"]) in prompt
        )
        calls[chunk_index] += 1
        if chunk_index == 1 and calls[chunk_index] == 1:
            raise TimeoutError("temporary timeout")
        return json.dumps(chunks[chunk_index], ensure_ascii=False)

    monkeypatch.setattr(service, "_call", fake_call)

    polished = await service.polish_with_context_parallel(
        _srt(segments),
        {},
        chunk_size=2,
        overlap=0,
        max_concurrency=2,
    )

    assert calls == [1, 2, 1]
    assert all(polished.count(f"cue-{index}") == 1 for index in range(1, 7))


@pytest.mark.asyncio
async def test_parallel_polish_reports_chunk_after_retry_is_exhausted(monkeypatch):
    service = LLMService()
    segments = [_srt_segment(index, f"cue-{index}") for index in range(1, 5)]
    calls = 0

    monkeypatch.setattr(service, "_effective_provider", lambda _override="": "deepseek")
    monkeypatch.setattr(
        llm_module,
        "get_runtime_settings",
        lambda: SimpleNamespace(llm_polish_concurrency=1),
    )

    async def fake_call(prompt, **_kwargs):
        nonlocal calls
        if "cue-3" in prompt:
            calls += 1
            raise ConnectionError("proxy unavailable")
        return json.dumps(segments[0:2], ensure_ascii=False)

    monkeypatch.setattr(service, "_call", fake_call)

    with pytest.raises(RuntimeError, match=r"Polish chunk 2/2 failed"):
        await service.polish_with_context_parallel(
            _srt(segments),
            {},
            chunk_size=2,
            overlap=0,
            max_concurrency=1,
        )

    assert calls == 2
