import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.analysis.llm import LLMService
from app.services.analysis.long_text import byte_size, generate_bounded, split_overlapping


def test_overlap_covers_all_unicode_text_and_respects_budget():
    text = "".join(f"{i}: 中文🎧段落 English {i}\n\n" for i in range(120))
    chunks = split_overlapping(text, 400, 60)
    position = 0
    previous_end = 0
    for index, chunk in enumerate(chunks):
        start = text.find(chunk, position)
        assert start >= 0
        assert start <= previous_end
        if index:
            assert start < previous_end
        assert byte_size(chunk) <= 400
        previous_end = start + len(chunk)
        position = start + 1
    assert previous_end == len(text)


def test_oversized_single_paragraph_is_split_without_losing_tail():
    text = "音频🎧" * 3000 + "END_MARKER"
    chunks = split_overlapping(text, 1024, 100)
    assert len(chunks) > 2
    assert all(byte_size(chunk) <= 1024 for chunk in chunks)
    assert chunks[-1].endswith("END_MARKER")


@pytest.mark.asyncio
async def test_all_map_and_recursive_reduce_requests_fit_context():
    calls = []

    async def call(prompt, *, system_prompt):
        assert byte_size(prompt + system_prompt) + 512 <= 4096
        calls.append(prompt)
        return "- digest " + "detail " * 24

    text = "事实数据" * 18000 + "LAST_FACT"
    result = await generate_bounded(
        text, lambda part: "Input:\n" + part, call,
        system_prompt="JSON or list", context_size=4096, output_tokens=512,
    )
    assert result.startswith("- digest")
    assert any("LAST_FACT" in prompt for prompt in calls)
    # Numerous partial digests exceed one request and must be reduced in layers.
    assert sum(prompt.count("- digest") > 1 for prompt in calls) > 1


@pytest.mark.asyncio
async def test_intermediate_digest_builder_preserves_tail_and_final_schema_builder():
    calls = []

    def schema_prompt(part):
        return "SCHEMA_PROMPT\n" + part

    def digest_prompt(part):
        return "DIGEST_PROMPT\n" + part

    async def call(prompt, *, system_prompt):
        assert byte_size(prompt + system_prompt) + 512 <= 4096
        calls.append(prompt)
        if prompt.startswith("DIGEST_PROMPT"):
            return '{"facts":["TAIL_FACT"]}'
        return '{"tldr":"final schema"}'

    result = await generate_bounded(
        "opening facts " * 1200 + "TAIL_FACT",
        schema_prompt,
        call,
        system_prompt="summary schema",
        context_size=4096,
        output_tokens=512,
        build_digest_prompt=digest_prompt,
    )

    assert result == '{"tldr":"final schema"}'
    assert any(prompt.startswith("DIGEST_PROMPT") and "TAIL_FACT" in prompt for prompt in calls)
    assert calls[-1].startswith("SCHEMA_PROMPT")
    assert all(prompt.startswith("DIGEST_PROMPT") for prompt in calls[:-1])


@pytest.mark.asyncio
async def test_context_rejection_splits_instead_of_retrying_identical_request():
    rejected = []
    accepted = []

    async def call(prompt, *, system_prompt):
        if len(prompt) > 1800:
            rejected.append(prompt)
            raise RuntimeError("exceed_context_size_error")
        accepted.append(prompt)
        return "- condensed facts"

    await generate_bounded(
        "x" * 5000 + "LAST_FACT", lambda part: part, call,
        system_prompt="", context_size=16384, output_tokens=4096,
    )
    assert len(rejected) == len(set(rejected))
    assert any("LAST_FACT" in prompt for prompt in accepted)


@pytest.mark.asyncio
async def test_unrelated_bad_request_is_not_split_or_retried():
    calls = []

    async def call(prompt, **kwargs):
        calls.append(prompt)
        raise RuntimeError("400 invalid API parameter")

    with pytest.raises(RuntimeError, match="invalid API parameter"):
        await generate_bounded(
            "short", lambda part: part, call, system_prompt="",
            context_size=4096, output_tokens=512,
        )
    assert calls == ["short"]


@pytest.mark.asyncio
async def test_nonshrinking_model_response_stops_without_discarding_source():
    async def call(prompt, **kwargs):
        return prompt

    with pytest.raises(RuntimeError, match="未缩短"):
        await generate_bounded(
            "长段落" * 5000, lambda part: part, call, system_prompt="",
            context_size=4096, output_tokens=512,
        )


@pytest.fixture
def local_service(monkeypatch):
    from app.core import model_router
    monkeypatch.setattr(model_router, "resolve_llm_binding", lambda *a, **kw: SimpleNamespace(
        provider="local", request_kwargs={"ctx": 16384, "max_new_tokens": 4096},
    ))
    return LLMService()


@pytest.mark.asyncio
async def test_summary_without_timeline_keeps_start_middle_and_tail(local_service, monkeypatch):
    async def call(prompt, *, system_prompt, **kwargs):
        assert byte_size(prompt + system_prompt) + 4096 <= 16384
        markers = sorted(set(re.findall(r"FACT_\d+", prompt)))
        return json.dumps(
            {"tldr": "summary", "key_facts": markers, "topics": [], "action_items": []}
        )

    monkeypatch.setattr(local_service, "_call", call)
    text = "FACT_1\n" + "开头内容" * 4000 + "FACT_2\n" + "结尾内容" * 4000 + "FACT_3"
    result = await local_service.summarize(text)
    assert result["key_facts"] == ["FACT_1", "FACT_2", "FACT_3"]


@pytest.mark.asyncio
async def test_large_chapter_mindmap_and_reduce_are_bounded(local_service, monkeypatch):
    seen = []

    async def call(prompt, *, system_prompt, **kwargs):
        assert byte_size(prompt + system_prompt) + 4096 <= 16384
        seen.append(prompt)
        schema = kwargs.get("response_schema") or {}
        if "points" in schema.get("properties", {}):
            sources = re.findall(
                r"^SOURCE (\d+)(?: \[[^\]]+\])?: (.+)$", prompt, flags=re.MULTILINE
            )
            assert sources
            source_id, source_text = sources[-1]
            return json.dumps(
                {
                    "points": [
                        {
                            "title": "量子材料研究",
                            "evidence_ids": [int(source_id)],
                            "quote": source_text[:24],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        return "- 量子材料 [00:00:01 - 00:01:00]"

    monkeypatch.setattr(local_service, "_call", call)
    text = "1\n00:00:01,000 --> 00:01:00,000\n" + "量子材料的研究" * 4000 + "TAIL_FACT\n"
    result = await local_service.mindmap(text, metadata={
        "chapters": [{"title": "材料研究", "start_time": 0}],
    })
    assert "材料研究" in result
    assert any("TAIL_FACT" in prompt for prompt in seen)
    assert any("00:00:01" in prompt for prompt in seen)
    await local_service._mindmap_reduce({str(i): "- 研究结论\n" * 1500 for i in range(4)})


@pytest.mark.asyncio
async def test_short_summary_stays_single_pass(local_service, monkeypatch):
    calls = []

    async def call(prompt, **kwargs):
        calls.append(prompt)
        return '{"tldr":"短摘要","key_facts":[]}'

    monkeypatch.setattr(local_service, "_call", call)
    assert (await local_service.summarize("短内容"))["tldr"] == "短摘要"
    assert len(calls) == 1
