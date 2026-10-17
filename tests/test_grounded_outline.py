import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.analysis.grounded_outline import grounded_chapter_outline
from app.services.analysis.long_text import byte_size


@pytest.mark.asyncio
async def test_large_provider_context_still_reviews_later_chapter_windows():
    import re

    seen_ids = []
    prompts = []
    async def call(prompt, *, system_prompt):
        prompts.append(prompt)
        rows = re.findall(r"^SOURCE (\d+): (.+)$", prompt, re.MULTILINE)
        seen_ids.extend(int(identifier) for identifier, _ in rows)
        identifier, text = rows[-1]
        return json.dumps({"points": [{"title": text[:25],
            "evidence_ids": [int(identifier)], "quote": text[:25]}]}, ensure_ascii=False)

    source = "\n\n".join(f"讨论问题{i}：" + "解释这个问题的条件与依据。" * 15 for i in range(100))
    await grounded_chapter_outline("较长的单一来源章节", source, call, budget=100000)
    assert len(prompts) >= 3
    assert all(byte_size(prompt) <= 12000 for prompt in prompts)
    assert seen_ids == list(range(1, 101))


@pytest.mark.asyncio
async def test_timed_outline_uses_source_ids_for_programmatic_time_ranges():
    prompts = []

    async def call(prompt, *, system_prompt):
        prompts.append((prompt, system_prompt))
        return json.dumps(
            {
                "points": [
                    {
                        "title": "嘉宾在给定条件下保留人工判断",
                        "evidence_ids": [1, 3],
                        "quote": "人工判断仍然重要",
                    }
                ]
            },
            ensure_ascii=False,
        )

    result = await grounded_chapter_outline(
        "短章节",
        "[00:00:01,000 --> 00:00:02,000] [徐天音] 嘉宾认为工具可以自动测量。\n"
        "[00:00:03,000 --> 00:00:04,000] 但条件和风险需要解释。\n"
        "[00:00:05,000 --> 00:00:06,000] 人工判断仍然重要",
        call,
    )

    assert result == "- 嘉宾在给定条件下保留人工判断 [00:00:01,000 - 00:00:06,000]"
    assert "全局时间轴" in prompts[0][0]
    assert "SOURCE 1" in prompts[0][0] and "SOURCE 3" in prompts[0][0]
    assert "source IDs" in prompts[0][1]


@pytest.mark.asyncio
async def test_plain_article_never_invents_timestamps():
    async def call(prompt, *, system_prompt):
        return json.dumps(
            {
                "points": [
                    {
                        "title": "摘要保留政策条件",
                        "evidence_ids": [2],
                        "quote": "政策必须根据证据调整",
                    }
                ]
            },
            ensure_ascii=False,
        )

    result = await grounded_chapter_outline(
        "文章章节",
        "摘要提出核心问题。\n\n政策必须根据证据调整。",
        call,
    )
    assert result == "- 摘要保留政策条件"
    assert "[" not in result


@pytest.mark.asyncio
async def test_bad_quote_gets_one_evidence_repair():
    calls = []

    async def call(prompt, *, system_prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return (
                '{"points":[{"title":"未经支持的结论",'
                '"evidence_ids":[1],"quote":"模型猜到的句子"}]}'
            )
        return '{"points":[{"title":"可核对的结论","evidence_ids":[1],"quote":"原文事实"}]}'

    result = await grounded_chapter_outline("章节", "原文事实", call)
    assert result == "- 可核对的结论"
    assert len(calls) == 2
    assert "未通过验证" in calls[1]
    assert "point[0]" in calls[1]
    assert "source IDs" in calls[1]


@pytest.mark.asyncio
async def test_second_invalid_response_falls_back_to_source_excerpt():
    calls = []

    async def call(prompt, *, system_prompt):
        calls.append(prompt)
        return '{"points":[{"title":"幻觉","evidence_ids":[1],"quote":"不存在"}]}'

    result = await grounded_chapter_outline("章节", "只有原文", call)
    assert result == "- 原文片段：只有原文"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_repair_keeps_valid_points_and_replaces_only_invalid_point():
    calls = []

    async def call(prompt, *, system_prompt):
        calls.append(prompt)
        return json.dumps(
            {
                "points": [
                    {"title": "有效事实", "evidence_ids": [1], "quote": "原文事实"},
                    {"title": "无效事实", "evidence_ids": [2], "quote": "模型杜撰"},
                ]
            },
            ensure_ascii=False,
        )

    result = await grounded_chapter_outline("章节", "原文事实\n\n第二段原文", call)
    assert calls and len(calls) == 2
    assert "- 有效事实" in result
    assert "- 原文片段：第二段原文" in result
    assert result.count("- ") == 2


@pytest.mark.asyncio
async def test_short_chapter_can_return_zero_points():
    async def call(prompt, *, system_prompt):
        return '{"points":[]}'

    assert await grounded_chapter_outline("片头", "一句很短的话", call) == ""


@pytest.mark.asyncio
async def test_long_plain_paragraph_keeps_tail_and_respects_budget():
    prompts = []

    async def call(prompt, *, system_prompt):
        prompts.append(prompt)
        return '{"points":[]}'

    text = "一段很长的正文。" * 700 + " TAIL_MARKER"
    await grounded_chapter_outline("长文章", text, call, budget=900)
    assert len(prompts) > 1
    assert any("TAIL_MARKER" in prompt for prompt in prompts)
    assert all(byte_size(prompt) <= 900 for prompt in prompts)


@pytest.mark.asyncio
async def test_duplicate_same_segment_points_are_merged():
    async def call(prompt, *, system_prompt):
        return json.dumps(
            {
                "points": [
                    {"title": "同一事实", "evidence_ids": [1], "quote": "同一句事实"},
                    {"title": "同一事实", "evidence_ids": [1], "quote": "同一句事实"},
                ]
            },
            ensure_ascii=False,
        )

    result = await grounded_chapter_outline("章节", "同一句事实", call)
    assert result == "- 同一事实"


@pytest.mark.asyncio
async def test_quote_can_span_adjacent_cues_without_speaker_prefixes():
    async def call(prompt, *, system_prompt):
        return json.dumps(
            {
                "points": [
                    {
                        "title": "跨 cue 的连续事实",
                        "evidence_ids": [1, 2],
                        "quote": "第一句第二句",
                    }
                ]
            },
            ensure_ascii=False,
        )

    result = await grounded_chapter_outline(
        "章节",
        "[00:00:01,000 --> 00:00:02,000] [甲] 第一句\n"
        "[00:00:02,000 --> 00:00:03,000] [乙] 第二句",
        call,
    )
    assert result == "- 跨 cue 的连续事实 [00:00:01,000 - 00:00:03,000]"
