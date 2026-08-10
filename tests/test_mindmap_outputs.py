from app.core.settings import RuntimeSettings
from app.services.analysis.llm import (
    LLMService,
    mindmap_markdown_without_timestamps,
    mindmap_markdown_to_timed_tree,
)


def test_runtime_settings_can_disable_video_detail_generation():
    settings = RuntimeSettings(generate_video_detail=False)

    assert settings.generate_video_detail is False


def test_mindmap_markdown_export_removes_inline_timestamps():
    markdown = """- AI 破解 80 年数学难题 [00:00:03 - 00:45:10]
  - 背景 [00:00:03]
    - OpenAI Podcast 访谈 [00:00:03 - 00:01:12]
"""

    assert mindmap_markdown_without_timestamps(markdown) == """## AI 破解 80 年数学难题
### 背景
#### OpenAI Podcast 访谈"""


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
