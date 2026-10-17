import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.settings import RuntimeSettings  # noqa: E402
from app.services.analysis.llm import (  # noqa: E402
    LLMService,
    mindmap_markdown_to_timed_tree,
    mindmap_markdown_without_timestamps,
)
from app.services.analysis.mindmap_outputs import (  # noqa: E402
    _chapter_key,
    _compose_chapter_mindmap,
    _split_segments_by_chapters,
)
from app.services.analysis.prompts.mindmap import get_mindmap_prompt  # noqa: E402
from app.services.analysis.text_locale import normalize_chinese_script  # noqa: E402


def test_runtime_settings_can_disable_video_detail_generation():
    settings = RuntimeSettings(generate_video_detail=False)

    assert settings.generate_video_detail is False


def test_simplified_chinese_mindmap_prompt_specifies_script():
    prompt = get_mindmap_prompt("数学推动文明进步", user_language="Simplified Chinese")

    assert "Use Simplified Chinese characters" in prompt


def test_mindmap_output_is_normalized_to_simplified_chinese():
    text = "- 人類大腦的優勢與局限\n  - 幫助祖先創造文明\n  - OpenAI 推動數學進步"

    assert normalize_chinese_script(text, "zh-CN") == (
        "- 人类大脑的优势与局限\n  - 帮助祖先创造文明\n  - OpenAI 推动数学进步"
    )


def test_mindmap_script_can_be_inferred_from_simplified_source():
    source = "数学进步依赖计算机辅助证明，人类使用工具创造现代文明。"
    generated = "- 數學進步\n  - 計算機輔助證明\n  - 人類創造現代文明"

    assert normalize_chinese_script(generated, None, source_text=source) == (
        "- 数学进步\n  - 计算机辅助证明\n  - 人类创造现代文明"
    )


def test_mindmap_service_normalizes_model_output(monkeypatch):
    service = LLMService()

    async def fake_call(*args, **kwargs):
        return '{"points":[{"title":"人類大腦使用工具創造文明","evidence_ids":[1],"quote":"人类大脑使用工具创造文明"}]}'

    monkeypatch.setattr(service, "_call", fake_call)

    result = asyncio.run(service.mindmap("人类大脑使用工具创造文明。", user_language="zh-CN"))

    assert result == "- 人类大脑使用工具创造文明"


@pytest.mark.asyncio
async def test_short_clip_anchors_are_calculated_from_source_cues(monkeypatch):
    service = LLMService()
    async def call(prompt, **kwargs):
        return '{"points":[{"title":"开场说明实验目的","evidence_ids":[1],"quote":"今天说明实验目的"},{"title":"实验保留条件限制","evidence_ids":[2],"quote":"结果只适用于当前样本"}]}'
    monkeypatch.setattr(service, "_call", call)
    result = await service.mindmap(
        "1\n00:00:03,217 --> 00:00:05,684\n今天说明实验目的。\n\n"
        "2\n00:00:10,371 --> 00:00:14,019\n结果只适用于当前样本。",
        user_language="zh-CN",
    )
    assert "[00:00:03,217 - 00:00:05,684]" in result
    assert "[00:00:10,371 - 00:00:14,019]" in result


def test_mindmap_markdown_export_removes_inline_timestamps():
    markdown = """- AI 破解 80 年数学难题 [00:00:03 - 00:45:10]
  - 背景 [00:00:03]
    - OpenAI Podcast 访谈 [00:00:03 - 00:01:12]
"""

    assert (
        mindmap_markdown_without_timestamps(markdown)
        == """## AI 破解 80 年数学难题
### 背景
#### OpenAI Podcast 访谈"""
    )


def test_heading_mindmap_can_be_reloaded_without_losing_hierarchy():
    markdown = """## 主话题
### 子话题
#### 结论"""

    assert mindmap_markdown_without_timestamps(markdown) == markdown
    tree = mindmap_markdown_to_timed_tree(markdown)
    assert tree["title"] == "主话题"
    assert tree["children"][0]["title"] == "子话题"
    assert tree["children"][0]["children"][0]["title"] == "结论"


def test_mindmap_markdown_to_timed_tree_preserves_hierarchy_and_times():
    markdown = """- AI 破解 80 年数学难题 [00:00:03 - 00:45:10]
  - 背景 [00:00:03]
    - OpenAI Podcast 访谈 [00:00:03 - 00:01:12]
  - 未来影响 [00:40:00]
"""

    tree = mindmap_markdown_to_timed_tree(markdown)

    assert tree["title"] == "AI 破解 80 年数学难题"
    assert tree["start"] == 3.0
    assert tree["end"] == 2710.0
    assert tree["children"][0]["title"] == "背景"
    assert tree["children"][0]["start"] == 3.0
    assert tree["children"][0]["children"][0]["title"] == "OpenAI Podcast 访谈"
    assert tree["children"][1]["title"] == "未来影响"
    assert tree["children"][1]["start"] == 2400.0


def test_source_chapters_are_immutable_mindmap_top_level_nodes():
    service = LLMService()
    mindmap = service._compose_chapter_mindmap(
        [
            {"title": "开场与嘉宾介绍", "start_time": 0},
            {"title": "第一章", "start_time": 75},
        ],
        {
            "开场与嘉宾介绍": "- 人物背景 [00:00:00 - 00:01:14]",
            "第一章": "- 创业感受 [00:01:15 - 00:02:35]",
        },
    )

    assert "- 开场与嘉宾介绍 [00:00:00]" in mindmap
    assert "- 第一章 [00:01:15]" in mindmap
    tree = mindmap_markdown_to_timed_tree(mindmap)
    assert [item["title"] for item in tree["children"]] == [
        "开场与嘉宾介绍",
        "第一章",
    ]


def test_duplicate_titles_keep_distinct_chapter_content_and_fractional_times() -> None:
    chapters = [
        {"title": "导言", "start_time": 0},
        {"title": "导言", "start_time": 28.3},
    ]
    segments = [
        {"timestamp": "00:00:01,000 --> 00:00:02,000", "text": "第一章正文"},
        {"timestamp": "00:00:29,000 --> 00:00:30,000", "text": "第二章正文"},
    ]
    blocks = _split_segments_by_chapters(segments, chapters)
    result = _compose_chapter_mindmap(
        chapters,
        {
            _chapter_key(chapters[0], 0): "- 第一章要点",
            _chapter_key(chapters[1], 1): "- 第二章要点",
        },
    )

    assert blocks[_chapter_key(chapters[0], 0)] == "[00:00:01,000 --> 00:00:02,000] 第一章正文"
    assert blocks[_chapter_key(chapters[1], 1)] == "[00:00:29,000 --> 00:00:30,000] 第二章正文"
    assert "- 导言 [00:00:00]" in result
    assert "- 导言 [00:00:28,300]" in result
    assert "第一章要点" in result and "第二章要点" in result


@pytest.mark.asyncio
async def test_plain_long_text_with_chapters_uses_auto_map_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = LLMService()
    auto_map = AsyncMock(return_value="- auto map")
    chapter_map = AsyncMock(return_value="- chapter map")
    monkeypatch.setattr(service, "_mindmap_map_reduce_auto", auto_map)
    monkeypatch.setattr(service, "_mindmap_map_reduce", chapter_map)
    monkeypatch.setattr(
        service, "_organize_mindmap", AsyncMock(side_effect=lambda value, *_: value)
    )

    text = "正文段落。" * 4000
    result = await service.mindmap(
        text,
        metadata={"chapters": [{"title": "导言", "start_time": 28.3}]},
    )

    assert result == "- auto map"
    auto_map.assert_awaited_once()
    chapter_map.assert_not_awaited()
