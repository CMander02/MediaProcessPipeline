import asyncio
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.analysis.grounded_outline import grounded_chapter_outline
from app.services.analysis.llm import LLMService
from app.services.analysis.local_llm_runtime import LocalLlamaCppStartupError


def _point(title: str, source_id: int, quote: str) -> dict:
    return {"title": title, "evidence_ids": [source_id], "quote": quote}


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    "本地模型 Qwen3.5-4B-Q8 的 mindmap 输出被截断，请缩短输入。",
    "exceed_context_size_error",
])
async def test_one_capacity_failure_keeps_source_and_processes_later_batches(message, caplog):
    calls = []
    source = "\n\n".join(
        f"第 {index} 个结论来自本段证据。" + "解释条件和适用范围。" * 40
        for index in range(6)
    )

    async def call(prompt, *, system_prompt):
        rows = re.findall(r"^SOURCE (\d+): (.+)$", prompt, re.MULTILINE)
        calls.append([int(identifier) for identifier, _ in rows])
        if len(calls) == 2:
            raise RuntimeError(message)
        return json.dumps({"points": [
            _point(text.split("。", 1)[0], int(identifier), text.split("。", 1)[0])
            for identifier, text in rows
        ]}, ensure_ascii=False)

    result = await grounded_chapter_outline("研究讨论", source, call, budget=2000)

    assert len(calls) >= 3
    assert [identifier for batch in calls for identifier in batch] == list(range(1, 7))
    assert "原文片段：" in result
    assert "第 5 个结论来自本段证据" in result
    assert all(f"第 {index} 个结论来自本段证据" in result for index in range(6))
    assert "exceeded model capacity" in caplog.text


@pytest.mark.asyncio
async def test_truncated_repair_keeps_previously_verified_points_and_source_times():
    calls = 0

    async def call(prompt, *, system_prompt):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("模型输出被截断，请缩小输入窗口。")
        return json.dumps({"points": [
            _point("保留人工判断", 1, "人工判断仍然重要"),
            _point("虚构的研究结论", 2, "未出现的证据"),
        ]}, ensure_ascii=False)

    result = await grounded_chapter_outline(
        "研究讨论",
        "[00:00:01,000 --> 00:00:03,000] 人工判断仍然重要。\n"
        "[00:00:04,000 --> 00:00:06,000] 结论需要接受验证。",
        call,
    )

    assert calls == 2
    assert "保留人工判断 [00:00:01,000 - 00:00:03,000]" in result
    assert "原文片段：结论需要接受验证。 [00:00:04,000 - 00:00:06,000]" in result
    assert "虚构" not in result
    assert "未出现的证据" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize("error", [
    asyncio.CancelledError(),
    OSError("disk unavailable"),
    RuntimeError("400 invalid API parameter"),
    LocalLlamaCppStartupError("Local GGUF model does not exist"),
])
async def test_noncapacity_failures_propagate_on_initial_or_repair_call(error, repair):
    calls = 0

    async def call(prompt, *, system_prompt):
        nonlocal calls
        calls += 1
        if repair and calls == 1:
            return '{"points": [{"broken": true}]}'
        raise error

    with pytest.raises(type(error)) as raised:
        await grounded_chapter_outline("研究讨论", "结论需要接受验证。", call)
    assert raised.value is error
    assert calls == 2 if repair else calls == 1


@pytest.mark.asyncio
async def test_local_chapter_mindmap_continues_after_truncation(monkeypatch):
    from app.core import model_router

    monkeypatch.setattr(model_router, "resolve_llm_binding", lambda *args, **kwargs: SimpleNamespace(
        provider="local", request_kwargs={"ctx": 16384, "max_new_tokens": 4096},
    ))
    service = LLMService()
    calls = []

    async def call(prompt, **kwargs):
        calls.append(prompt)
        if len(calls) == 2:
            raise RuntimeError("本地模型 Qwen3.5-4B-Q8 的 mindmap 输出被截断。")
        row = re.search(r"^SOURCE (\d+) \[[^\]]+\]: (.+)$", prompt, re.MULTILINE)
        assert row is not None
        return json.dumps({"points": [_point(row[2], int(row[1]), row[2])]}, ensure_ascii=False)

    monkeypatch.setattr(service, "_call", call)
    result = await service.mindmap(
        "1\n00:00:01,000 --> 00:00:03,000\n第一章的已有结论。\n\n"
        "2\n00:00:11,000 --> 00:00:13,000\n第二章还需要验证。\n\n"
        "3\n00:00:21,000 --> 00:00:23,000\n第三章的后续讨论。\n",
        metadata={"chapters": [
            {"title": "第一章", "start_time": 0},
            {"title": "第二章", "start_time": 10},
            {"title": "第三章", "start_time": 20},
        ]},
    )

    assert len(calls) == 3
    assert "第一章的已有结论。" in result
    assert "原文片段：第二章还需要验证。 [00:00:11,000 - 00:00:13,000]" in result
    assert "第三章的后续讨论。" in result
