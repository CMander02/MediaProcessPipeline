from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.async_utils import gather_cancel_on_error  # noqa: E402
from app.core.settings import RuntimeSettings  # noqa: E402
from app.services.analysis import llm as llm_module  # noqa: E402
from app.services.analysis.llm import LLMService  # noqa: E402


@pytest.mark.asyncio
async def test_parallel_failure_waits_for_sibling_cleanup_and_preserves_original_error():
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    finish_cleanup = asyncio.Event()
    cleaned = asyncio.Event()
    failure = RuntimeError("Codex thread/start failed")

    async def fail():
        await started.wait()
        raise failure

    async def sibling():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await finish_cleanup.wait()
            cleaned.set()
            raise ValueError("secondary cleanup failure")

    parent = asyncio.create_task(gather_cancel_on_error(fail(), sibling()))
    await asyncio.wait_for(cleanup_started.wait(), timeout=1)
    assert not parent.done()
    finish_cleanup.set()
    with pytest.raises(RuntimeError) as raised:
        await asyncio.wait_for(parent, timeout=1)
    assert raised.value is failure
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_parent_cancellation_waits_for_cleanup_even_if_cancelled_twice():
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    finish_cleanup = asyncio.Event()
    cleaned = asyncio.Event()

    async def sibling():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await finish_cleanup.wait()
            cleaned.set()

    parent = asyncio.create_task(gather_cancel_on_error(sibling()))
    await asyncio.wait_for(started.wait(), timeout=1)
    parent.cancel()
    await asyncio.wait_for(cleanup_started.wait(), timeout=1)
    parent.cancel()
    await asyncio.sleep(0)
    assert not parent.done()
    finish_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(parent, timeout=1)
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_parallel_results_keep_input_order():
    async def value(result):
        await asyncio.sleep(0)
        return result

    assert await gather_cancel_on_error(value(2), value(1)) == [2, 1]
    assert await gather_cancel_on_error() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["summary", "map", "auto_map", "reduce", "polish"])
async def test_llm_parallel_failure_cleans_up_other_calls(monkeypatch, operation):
    service = LLMService()
    monkeypatch.setattr(service, "_effective_provider", lambda _override="": "codex-oauth")
    monkeypatch.setattr(
        llm_module, "get_runtime_settings", lambda: RuntimeSettings(llm_polish_concurrency=2)
    )
    sibling_started = asyncio.Event()
    sibling_cleaned = asyncio.Event()
    failure = RuntimeError("Codex thread/start failed")
    calls = 0

    async def fake_call(_prompt, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            await sibling_started.wait()
            raise failure
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            sibling_cleaned.set()

    monkeypatch.setattr(service, "_call", fake_call)
    srt = (
        "1\n00:00:00,000 --> 00:00:01,000\nfirst chapter\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nsecond chapter\n"
    )
    if operation == "summary":
        request = service._summarize_by_timeline(
            srt,
            user_language=None,
            provider_override="",
            source_context={"timeline": [{"title": "one", "start": 0},
                                         {"title": "two", "start": 1}]},
        )
    elif operation == "map":
        request = service._mindmap_map_reduce(
            "First chapter. Second chapter.", {},
            [{"title": "one", "start_time": 0}, {"title": "two", "start_time": 1}],
        )
    elif operation == "auto_map":
        request = service._mindmap_map_reduce_auto("a" * 15000, {})
    elif operation == "reduce":
        request = service._mindmap_reduce({str(i): "chapter" for i in range(4)})
    else:
        request = service.polish_with_context_parallel(srt, {}, chunk_size=1, overlap=0)

    with pytest.raises(RuntimeError) as raised:
        await asyncio.wait_for(request, timeout=2)
    assert raised.value is failure
    assert sibling_cleaned.is_set()
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("transient_failure", [False, True])
async def test_polish_cancellation_cleans_up_running_or_draining_batch(
    monkeypatch, transient_failure
):
    service = LLMService()
    monkeypatch.setattr(service, "_effective_provider", lambda _override="": "codex-oauth")
    monkeypatch.setattr(
        llm_module, "get_runtime_settings", lambda: RuntimeSettings(llm_polish_concurrency=2)
    )
    running = asyncio.Event()
    cleanup_started = asyncio.Event()
    finish_cleanup = asyncio.Event()
    cleaned = asyncio.Event()
    calls = 0

    async def fake_call(_prompt, **_kwargs):
        nonlocal calls
        calls += 1
        if transient_failure and calls == 1:
            raise TimeoutError("temporary connection error")
        running.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await finish_cleanup.wait()
            cleaned.set()

    monkeypatch.setattr(service, "_call", fake_call)
    srt = (
        "1\n00:00:00,000 --> 00:00:01,000\nfirst chapter\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nsecond chapter\n"
    )
    parent = asyncio.create_task(
        service.polish_with_context_parallel(srt, {}, chunk_size=1, overlap=0)
    )
    await asyncio.wait_for(running.wait(), timeout=1)
    await asyncio.sleep(0)
    parent.cancel()
    await asyncio.wait_for(cleanup_started.wait(), timeout=1)
    parent.cancel()
    await asyncio.sleep(0)
    assert not parent.done()
    finish_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(parent, timeout=1)
    assert cleaned.is_set()
