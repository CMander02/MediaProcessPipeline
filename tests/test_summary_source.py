from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.analysis import summary_source  # noqa: E402, I001


def _srt(*rows: tuple[int, str, str]) -> str:
    return "\n\n".join(
        f"{index}\n{timestamp}\n{text}" for index, timestamp, text in rows
    )


def _long_source() -> str:
    return _srt(
        (
            1,
            "00:00:00,000 --> 00:00:04,000",
            "[host] 片头预告：" + "系统可靠性与研究方法。" * 24,
        ),
        (
            2,
            "00:00:10,000 --> 00:00:16,000",
            "[guest] 正式开场，先从研究背景和问题定义开始。" * 12,
        ),
        (
            3,
            "00:00:30,000 --> 00:00:36,000",
            "[guest] 接着讨论系统设计、容错与 Agent 的关系。" * 12,
        ),
        (
            4,
            "00:01:00,000 --> 00:01:06,000",
            "[guest] 最后说明研究实践与职业选择。" * 12,
        ),
    )


def _timeline() -> list[dict[str, object]]:
    return [
        {"start": 0, "title": "片头预告"},
        {"start": 10, "title": "正式开场"},
        {"start": 30, "title": "深入讨论"},
        {"start": 60, "title": "结语"},
    ]


@pytest.mark.asyncio
async def test_filtered_source_is_used_for_summary_without_mutating_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _long_source()
    filtered = _srt(
        (
            2,
            "00:00:10,000 --> 00:00:16,000",
            "[guest] 正式开场，先从研究背景和问题定义开始。" * 12,
        )
    )
    seen: dict[str, object] = {}

    async def fake_select(text: str, metadata: dict, call) -> dict:
        seen["text"] = text
        seen["metadata"] = metadata
        return {
            "filtered_text": filtered,
            "report": {
                "status": "filtered",
                "excluded_ids": ["cue:1"],
            },
        }

    monkeypatch.setattr(summary_source, "select_main_content", fake_select)
    result = await summary_source.prepare_summary_source(
        original,
        {"title": "示例"},
        {"timeline": _timeline()},
        lambda _prompt: None,
    )

    assert seen["text"] == original
    assert result["text"] == filtered
    assert original == _long_source()


@pytest.mark.asyncio
async def test_filtered_source_removes_teaser_chapter_but_keeps_later_timeline_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _long_source()
    filtered = _srt(
        (
            2,
            "00:00:10,000 --> 00:00:16,000",
            "[guest] 正式开场。" * 80,
        ),
        (
            3,
            "00:00:30,000 --> 00:00:36,000",
            "[guest] 深入讨论。" * 80,
        ),
        (
            4,
            "00:01:00,000 --> 00:01:06,000",
            "[guest] 结语。" * 80,
        ),
    )

    async def fake_select(_text: str, _metadata: dict, _call) -> dict:
        return {
            "filtered_text": filtered,
            "report": {"status": "filtered", "excluded_ids": ["cue:1"]},
        }

    source_context = {"timeline": _timeline(), "entities": [{"canonical": "Agent"}]}
    monkeypatch.setattr(summary_source, "select_main_content", fake_select)
    result = await summary_source.prepare_summary_source(
        source, {}, source_context, lambda _prompt: None
    )

    assert [item["title"] for item in result["source_context"]["timeline"]] == [
        "正式开场",
        "深入讨论",
        "结语",
    ]
    assert [item["title"] for item in source_context["timeline"]] == [
        "片头预告",
        "正式开场",
        "深入讨论",
        "结语",
    ]


@pytest.mark.asyncio
async def test_partial_teaser_overlap_reanchors_first_chapter_to_retained_opening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _srt(
        (1, "00:00:00,000 --> 00:00:05,000", "片头预告。" * 50),
        (2, "00:00:05,000 --> 00:00:15,000", "正文从研究背景开始。" * 50),
    )
    filtered = _srt(
        (2, "00:00:05,000 --> 00:00:15,000", "正文从研究背景开始。" * 50),
    )

    async def fake_select(_text: str, _metadata: dict, _call) -> dict:
        return {
            "filtered_text": filtered,
            "report": {
                "status": "filtered",
                "excluded_ids": ["cue:1"],
                "excluded_ranges": [{"start": 0, "end": 8}],
            },
        }

    source_context = {
        "timeline": [
            {"start": 0, "title": "片头与正式开场"},
            {"start": 20, "title": "深入讨论"},
        ]
    }
    monkeypatch.setattr(summary_source, "select_main_content", fake_select)
    result = await summary_source.prepare_summary_source(
        source, {}, source_context, lambda _prompt: None
    )

    first = result["source_context"]["timeline"][0]
    assert first["title"] == "正文开场"
    assert first["start"] == 5
    assert first["source"] == "selected_opening"
    assert first["source_start"] == 0
    assert len(result["source_context"]["timeline"]) == 1
    assert source_context["timeline"][0]["title"] == "片头与正式开场"


def test_outline_guided_context_adds_global_topics_without_mutating_source_context() -> None:
    source_context = {
        "timeline": [{"start": 0, "title": "系统设计"}],
        "entities": [{"canonical": "Agent"}],
    }
    mindmap = "- 系统设计\n  - 数据流与容错\n- 实践反馈\n  - 部署经验"

    result = summary_source.outline_guided_context(source_context, mindmap)

    assert result["topic_outline"] == ["系统设计", "实践反馈"]
    assert result["timeline"][0]["title"] == "系统设计"
    assert result["timeline"][0]["summary"] == ""
    assert "topic_outline" not in source_context


def test_recording_without_chapters_has_separate_opening_and_presenter_summaries() -> None:
    source_context = {"timeline": [], "entities": []}
    outline = (
        "- 开场聊天：近期 AI 研究\n"
        "  - 主持人与来宾交流数学研究进展 [00:00:03.200 - 00:04:05.000]\n"
        "- 第一位分享者：课程选择\n"
        "  - 数学课程的先修关系 [00:04:10.000 - 00:10:00.000]\n"
        "- 第二位分享者：实践建议\n"
        "  - 通过项目检验所学 [00:10:02.500 - 00:13:50.000]"
    )
    result = summary_source.outline_guided_context(source_context, outline)
    assert [item["start"] for item in result["timeline"]] == [3.2, 250, 602.5]
    assert result["timeline"][0]["summary"] == "主持人与来宾交流数学研究进展"
    assert result["timeline"][1]["summary"] == "数学课程的先修关系"
    assert all(item["source"] == "organized_outline" for item in result["timeline"])
    assert source_context["timeline"] == []


def test_untimed_article_outline_keeps_timeline_empty() -> None:
    result = summary_source.outline_guided_context(
        {"timeline": []}, "- 文章导语\n  - 提出研究问题\n- 论证\n  - 解释原因"
    )
    assert result["timeline"] == []


def test_short_clip_title_wrapper_keeps_intro_separate():
    outline = (
        "- 整个视频主题\n"
        "  - 开场：提出问题 [00:00:02 - 00:00:20]\n"
        "  - 方法\n"
        "    - 说明实验方法 [00:00:21 - 00:01:20]\n"
        "  - 结论\n"
        "    - 总结实验边界 [00:01:21 - 00:02:00]"
    )
    timeline = summary_source.outline_guided_context({"timeline": []}, outline)["timeline"]
    assert [(item["title"], item["start"]) for item in timeline] == [
        ("开场：提出问题", 2), ("方法", 21), ("结论", 81)
    ]


def test_existing_publisher_chapters_keep_their_anchors() -> None:
    result = summary_source.outline_guided_context(
        {"timeline": [{"start": 0, "title": "开场"}, {"start": 60, "title": "分享"}]},
        "- 合并主题\n  - 开场 [00:00:03 - 00:00:10]\n  - 分享 [00:01:04 - 00:02:00]",
    )
    assert [(item["title"], item["start"]) for item in result["timeline"]] == [
        ("开场", 0), ("分享", 60)
    ]


@pytest.mark.asyncio
async def test_filtered_plain_source_clears_unmapped_timeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "# 标题\n\n" + ("片头内容。" * 80) + "\n\n" + ("正文内容。" * 100)
    filtered = "# 标题\n\n" + ("正文内容。" * 100)

    async def fake_select(_text: str, _metadata: dict, _call) -> dict:
        return {
            "filtered_text": filtered,
            "report": {"status": "filtered", "excluded_ids": ["para:2"]},
        }

    source_context = {"timeline": [{"start": 0, "title": "片头"}, {"start": 60, "title": "正文"}]}
    monkeypatch.setattr(summary_source, "select_main_content", fake_select)

    result = await summary_source.prepare_summary_source(
        source, {}, source_context, lambda _prompt: None
    )

    assert result["source_context"]["timeline"] == []
    assert source_context["timeline"]


@pytest.mark.asyncio
async def test_unchanged_selection_keeps_complete_timeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _long_source()
    source_context = {"timeline": _timeline()}

    async def fake_select(_text: str, _metadata: dict, _call) -> dict:
        return {
            "filtered_text": source,
            "report": {"status": "unchanged", "excluded_ids": []},
        }

    monkeypatch.setattr(summary_source, "select_main_content", fake_select)
    result = await summary_source.prepare_summary_source(
        source, {}, source_context, lambda _prompt: None
    )

    assert result["text"] == source
    assert result["source_context"]["timeline"] == _timeline()


@pytest.mark.asyncio
async def test_genuine_article_introduction_is_kept_without_timeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "# 文章标题\n\n" + ("这是文章真正的导语，提出研究问题与背景。" * 30)
    source_context = {"timeline": [], "entities": []}

    async def fake_select(_text: str, _metadata: dict, _call) -> dict:
        return {
            "filtered_text": source,
            "report": {
                "status": "unchanged",
                "reason": "正式文章导语",
                "excluded_ids": [],
            },
        }

    monkeypatch.setattr(summary_source, "select_main_content", fake_select)
    result = await summary_source.prepare_summary_source(
        source, {"title": "文章"}, source_context, lambda _prompt: None
    )

    assert result["text"] == source
    assert result["source_context"]["timeline"] == []
    assert result["selection"]["reason"] == "正式文章导语"


@pytest.mark.asyncio
async def test_selector_failure_preserves_full_source_and_timeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _long_source()
    source_context = {"timeline": _timeline()}

    async def fake_select(_text: str, _metadata: dict, _call) -> dict:
        return {
            "filtered_text": source,
            "report": {
                "status": "fallback",
                "reason": "model_selection_failed",
                "excluded_ids": [],
            },
        }

    monkeypatch.setattr(summary_source, "select_main_content", fake_select)
    result = await summary_source.prepare_summary_source(
        source, {}, source_context, lambda _prompt: None
    )

    assert result["text"] == source
    assert result["source_context"]["timeline"] == _timeline()
    assert result["selection"]["status"] == "fallback"
