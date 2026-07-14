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
