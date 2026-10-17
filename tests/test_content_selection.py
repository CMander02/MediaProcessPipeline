import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.analysis.content_selection import select_main_content


def _srt(*rows: tuple[int, str, str]) -> str:
    return "\n\n".join(f"{index}\n{timestamp}\n{text}" for index, timestamp, text in rows)


@pytest.mark.asyncio
async def test_excludes_only_opening_teaser_when_later_evidence_repeats_it() -> None:
    rows = [
        (1, "00:00:00,000 --> 00:00:02,000", "[host] Best Paper 奖项值得取消。"),
        (2, "00:00:02,000 --> 00:00:04,000", "[host] AI 应当辅助分析并保留人的判断。"),
        (3, "00:00:04,000 --> 00:00:08,000", "[host] 今天请来徐教授，讨论系统可靠性。"),
        (4, "00:00:08,000 --> 00:00:10,000", "[guest] 这是访谈的正式开场。"),
        (5, "00:00:10,000 --> 00:00:12,000", "[guest] 我们先说明系统研究的背景。"),
    ]
    rows.extend(
        (
            index,
            f"00:01:{(index - 6) * 2:02d},000 --> 00:01:{(index - 5) * 2:02d},000",
            "[guest] 后文继续讨论系统可靠性和研究方法。",
        )
        for index in range(6, 25)
    )
    rows.append(
        (
            25,
            "00:02:00,000 --> 00:02:05,000",
            "[guest] 我一直提倡取消 Best Paper，因为它误导研究者。",
        )
    )
    rows.append((26, "00:02:06,000 --> 00:02:10,000", "[guest] AI 应当辅助分析并保留人的判断。"))
    source = _srt(*rows)
    seen: dict[str, str] = {}

    async def call(prompt: str) -> str:
        seen["prompt"] = prompt
        return json.dumps(
            {
                "exclude_ids": ["cue:1", "cue:2"],
                "reason": "片头以预告方式重复后文两个论点。",
                "evidence": ["cue:25", "cue:26"],
            },
            ensure_ascii=False,
        )

    result = await select_main_content(source, {"chapters": [{"title": "访谈"}]}, call)

    assert result["report"]["status"] == "filtered"
    assert result["report"]["excluded_ids"] == ["cue:1", "cue:2"]
    assert result["report"]["excluded_ranges"] == [
        {
            "start": 0.0,
            "end": 4.0,
            "start_time": "00:00:00,000",
            "end_time": "00:00:04,000",
            "ids": ["cue:1", "cue:2"],
        }
    ]
    assert "Best Paper 奖项值得取消" not in result["filtered_text"]
    assert "今天请来徐教授" in result["filtered_text"]
    assert "opening candidates" in seen["prompt"]
    assert len(seen["prompt"].encode("utf-8")) <= 8192


@pytest.mark.asyncio
async def test_keeps_genuine_opening_argument_when_model_returns_empty_selection() -> None:
    source = _srt(
        (1, "00:00:00,000 --> 00:00:10,000", "[host] 今天我们讨论系统可靠性，这是本期真正的问题。"),
        (2, "00:00:10,000 --> 00:00:18,000", "[guest] 我从配置错误开始说明这个问题。"),
    )

    async def call(_prompt: str) -> str:
        return '{"exclude_ids": [], "reason": "开头构成正式导语。", "evidence": []}'

    result = await select_main_content(source, {}, call)

    assert result["report"]["status"] == "unchanged"
    assert result["filtered_text"] == source


@pytest.mark.asyncio
async def test_invalid_ids_fail_closed_and_preserve_long_plain_text() -> None:
    source = (
        "# 长文标题\n\n"
        "这是正式开头，提出问题。\n\n"
        + ("后文持续展开这个问题。" * 800)
        + "\n\n结尾总结。"
    )

    async def call(_prompt: str) -> str:
        return '{"exclude_ids": ["para:999"], "reason": "bad", "evidence": []}'

    result = await select_main_content(source, {"title": "长文"}, call)

    assert result["report"]["status"] == "fallback"
    assert result["filtered_text"] == source


@pytest.mark.asyncio
async def test_non_contiguous_opening_selection_fails_closed() -> None:
    source = _srt(
        (1, "00:00:00,000 --> 00:00:01,000", "片头一"),
        (2, "00:00:01,000 --> 00:00:02,000", "片头二"),
        (3, "00:00:02,000 --> 00:00:03,000", "正文开场"),
    )

    async def call(_prompt: str) -> str:
        return '{"exclude_ids": ["cue:1", "cue:3"], "reason": "跳过", "evidence": []}'

    result = await select_main_content(source, {}, call)

    assert result["report"]["status"] == "fallback"
    assert result["filtered_text"] == source


@pytest.mark.asyncio
async def test_refuses_to_remove_the_entire_srt_source() -> None:
    source = _srt(
        (1, "00:00:00,000 --> 00:00:02,000", "片头预告一。"),
        (2, "00:00:02,000 --> 00:00:04,000", "片头预告二。"),
    )

    async def call(_prompt: str) -> str:
        return json.dumps(
            {
                "exclude_ids": ["cue:1", "cue:2"],
                "reason": "完整预告",
                "evidence": ["cue:1"],
            },
            ensure_ascii=False,
        )

    result = await select_main_content(source, {}, call)

    assert result["report"]["status"] == "fallback"
    assert result["filtered_text"] == source


@pytest.mark.asyncio
async def test_long_single_paragraph_keeps_prompt_bounded() -> None:
    source = "这是一个很长的单段文章开头。" + ("后文继续展开研究问题。" * 1600)
    seen: dict[str, str] = {}

    async def call(prompt: str) -> str:
        seen["prompt"] = prompt
        return '{"exclude_ids": [], "reason": "正式文章开头", "evidence": []}'

    result = await select_main_content(source, {}, call)

    assert result["report"]["status"] == "unchanged"
    assert result["filtered_text"] == source
    assert len(seen["prompt"].encode("utf-8")) <= 9000


@pytest.mark.asyncio
async def test_oversized_metadata_is_compacted_without_hanging() -> None:
    source = "# 标题\n\n正式文章开头。\n\n正文。"
    metadata = {
        "title": "标题" * 10000,
        "main_topics": ["主题" * 5000],
        "chapters": [{"title": "章节" * 5000}],
        "timeline": [{"title": "时间线" * 5000}],
    }
    seen: dict[str, str] = {}

    async def call(prompt: str) -> str:
        seen["prompt"] = prompt
        return '{"exclude_ids": [], "reason": "没有明确预告", "evidence": []}'

    result = await select_main_content(source, metadata, call)

    assert result["report"]["status"] == "unchanged"
    assert len(seen["prompt"].encode("utf-8")) <= 9000


@pytest.mark.asyncio
async def test_exclusion_without_evidence_fails_closed() -> None:
    source = _srt(
        (1, "00:00:00,000 --> 00:00:01,000", "[host] 片头精华。"),
        (2, "00:00:01,000 --> 00:00:02,000", "[guest] 正式开场。"),
    )

    async def call(_prompt: str) -> str:
        return '{"exclude_ids": ["cue:1"], "reason": "重复", "evidence": []}'

    result = await select_main_content(source, {}, call)

    assert result["report"]["status"] == "fallback"
    assert result["report"]["error"] == (
        "ValueError: excluded_content_requires_reason_and_evidence"
    )
    assert result["filtered_text"] == source


@pytest.mark.asyncio
async def test_explicit_promotional_preview_can_use_opening_evidence() -> None:
    source = _srt(
        (1, "00:00:00,000 --> 00:00:03,000", "[host] 本期你将看到三个令人震惊的故事。"),
        (2, "00:00:03,000 --> 00:00:06,000", "[host] 现在进入正式访谈。"),
        (3, "00:00:06,000 --> 00:00:10,000", "[guest] 我们先从研究背景开始。"),
    )

    async def call(_prompt: str) -> str:
        return json.dumps(
            {
                "exclude_ids": ["cue:1"],
                "reason": "这是明确的 promotional preview 片头预告。",
                "evidence": ["cue:1"],
            },
            ensure_ascii=False,
        )

    result = await select_main_content(source, {}, call)

    assert result["report"]["status"] == "filtered"
    assert result["report"]["evidence"] == ["cue:1"]
    assert "本期你将看到" not in result["filtered_text"]
    assert "正式访谈" in result["filtered_text"]


@pytest.mark.asyncio
async def test_body_start_expands_a_multi_topic_montage_prefix() -> None:
    rows = [
        (1, "00:00:00,000 --> 00:00:02,000", "[guest] Best Paper 奖项值得取消。"),
        (2, "00:00:02,000 --> 00:00:04,000", "[guest] AI 不应取代人的判断。"),
        (3, "00:00:04,000 --> 00:00:06,000", "[guest] Reward hacking 会制造系统漏洞。"),
        (4, "00:00:06,000 --> 00:00:08,000", "[guest] 操作系统课程训练工程思维。"),
        (5, "00:00:08,000 --> 00:00:10,000", "[guest] Research 是一件美好的事情。"),
        (6, "00:00:10,000 --> 00:00:14,000", "[host] 这里是本期访谈，下面请嘉宾介绍自己的经历。"),
        (7, "00:00:14,000 --> 00:00:18,000", "[guest] 我们先从我的研究背景开始。"),
        (8, "00:01:00,000 --> 00:01:05,000", "[guest] 我确实长期讨论 Best Paper 和研究方法。"),
        (9, "00:01:05,000 --> 00:01:10,000", "[guest] Research 是一件美好的事情。"),
    ]
    source = _srt(*rows)

    async def call(_prompt: str) -> str:
        return json.dumps(
            {
                "exclude_ids": ["cue:1"],
                "body_start_id": "cue:6",
                "reason": "前五个短句是多话题片头 montage，cue:6 转入正式主持介绍。",
                "evidence": ["cue:8", "cue:9"],
            },
            ensure_ascii=False,
        )

    result = await select_main_content(source, {}, call)

    assert result["report"]["status"] == "filtered"
    assert result["report"]["excluded_ids"] == [
        "cue:1",
        "cue:2",
        "cue:3",
        "cue:4",
        "cue:5",
    ]
    assert result["report"]["body_start_id"] == "cue:6"
    assert "这里是本期访谈" in result["filtered_text"]
    assert "Best Paper 奖项值得取消" not in result["filtered_text"]


@pytest.mark.asyncio
async def test_canonicalises_unprefixed_numeric_ids_when_visible_form_is_unique() -> None:
    source = _srt(
        (1, "00:00:00,000 --> 00:00:02,000", "片头预告观点一。"),
        (2, "00:00:02,000 --> 00:00:04,000", "片头预告观点二。"),
        (3, "00:00:04,000 --> 00:00:06,000", "正式开场。"),
    )

    async def call(_prompt: str) -> str:
        return json.dumps(
            {
                "exclude_ids": ["1", "2"],
                "body_start_id": "3",
                "reason": "前两句是预览片头，第三句开始正式内容。",
                "evidence": ["1", "2"],
            },
            ensure_ascii=False,
        )

    result = await select_main_content(source, {}, call)

    assert result["report"]["status"] == "filtered"
    assert result["report"]["excluded_ids"] == ["cue:1", "cue:2"]
    assert result["report"]["body_start_id"] == "cue:3"
    assert result["report"]["evidence"] == ["cue:1", "cue:2"]


@pytest.mark.asyncio
async def test_preview_reason_cannot_delete_a_standalone_opening_talk():
    source = _srt(
        (1, "00:00:03,000 --> 00:00:20,000", "大家好，我用几分钟介绍社区的发展和技术架构。"),
        (2, "00:00:20,000 --> 00:00:40,000", "项目从存储格式发展为开放基础设施。"),
        (3, "00:00:40,000 --> 00:01:00,000", "欢迎大家一起建设社区。"),
    )
    async def call(prompt):
        return json.dumps({'exclude_ids': ['cue:1', 'cue:2'],
                           'reason': 'promotional preview before the actual event',
                           'evidence': ['cue:1', 'cue:2']})
    result = await select_main_content(source, {}, call)
    assert result['filtered_text'] == source
    assert result['report']['status'] == 'fallback'


@pytest.mark.asyncio
async def test_repeated_teaser_evidence_does_not_authorize_deleting_host_intro():
    source = _srt(
        (1, "00:00:00,000 --> 00:00:05,000", "AI 记忆需要在上下文与长期存储之间取舍。"),
        (2, "00:00:05,000 --> 00:00:15,000", "欢迎两位嘉宾来到现场，请分别介绍自己。"),
        (3, "00:00:15,000 --> 00:00:30,000", "我们先讨论研究背景。"),
        (4, "00:00:30,000 --> 00:00:40,000", "AI 记忆需要在上下文与长期存储之间取舍。"),
    )
    async def call(prompt):
        return json.dumps({'exclude_ids': ['cue:1', 'cue:2'],
                           'reason': '片头预告和嘉宾介绍都在后面重复', 'evidence': ['cue:4']})
    result = await select_main_content(source, {}, call)
    assert result['report']['excluded_ids'] == ['cue:1']
    assert result['report']['boundary_guard']['retained_ids'] == ['cue:2']
    assert '欢迎两位嘉宾' in result['filtered_text']


@pytest.mark.asyncio
async def test_ambiguous_numeric_id_fails_closed_for_duplicate_cues() -> None:
    source = _srt(
        (1, "00:00:00,000 --> 00:00:01,000", "片头一。"),
        (1, "00:00:01,000 --> 00:00:02,000", "片头二。"),
        (2, "00:00:02,000 --> 00:00:03,000", "正文。"),
    )

    async def call(_prompt: str) -> str:
        return '{"exclude_ids": ["1"], "reason": "预览", "evidence": ["2"]}'

    result = await select_main_content(source, {}, call)

    assert result["report"]["status"] == "fallback"
    assert result["filtered_text"] == source
