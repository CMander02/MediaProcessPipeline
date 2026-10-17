import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.analysis.mindmap_outputs import mindmap_markdown_to_timed_tree
from app.services.analysis.outline_organization import (
    organize_outline,
    outline_blocks,
    validate_groups,
)


def sample():
    return "\n".join(
        f"- 原章节{i} [00:00:{i:02d}]\n  - 观点{i}及其限制 [00:00:{i:02d} - 00:01:00]"
        for i in range(1, 13)
    )


def plan():
    return {
        "groups": [
            {"title": "论证", "section_ids": list(range(1, 7))},
            {"title": "实践", "section_ids": list(range(7, 13))},
        ]
    }


def test_organization_preserves_every_original_subtree_and_timestamp():
    async def call(*args, **kwargs):
        return json.dumps(plan(), ensure_ascii=False)

    original = mindmap_markdown_to_timed_tree(sample())
    result, report = asyncio.run(organize_outline(sample(), call, language="zh-CN"))
    tree = mindmap_markdown_to_timed_tree(result)
    assert report["status"] == "organized"
    assert [child for group in tree["children"] for child in group["children"]] == original[
        "children"
    ]


@pytest.mark.parametrize("bad_ids", [[1, 2, 2], [1, 2], [1, 2, 99], [1, True, 3]])
def test_rejects_missing_duplicate_unknown_or_boolean_ids(bad_ids):
    with pytest.raises(ValueError):
        validate_groups(json.dumps({"groups": [{"title": "主题", "section_ids": bad_ids}]}), 3)


def test_repairs_invalid_assignment_once():
    calls = []

    async def call(prompt, **kwargs):
        calls.append(prompt)
        return "{}" if len(calls) == 1 else json.dumps(plan())

    _, report = asyncio.run(organize_outline(sample(), call))
    assert report["status"] == "organized"
    assert len(calls) == 3
    assert "Fix this validation error" in calls[1]


def test_failed_global_organization_retains_all_content():
    async def call(*args, **kwargs):
        return '{"groups": []}'

    result, report = asyncio.run(organize_outline(sample(), call))
    assert result == sample()
    assert report["status"] == "fallback"


def test_short_coherent_outline_needs_no_extra_call():
    async def call(*args, **kwargs):
        raise AssertionError("unnecessary model call")

    result, report = asyncio.run(organize_outline("- 开场论点\n  - 正文论证", call))
    assert report["status"] == "already_compact"
    assert result == "- 开场论点\n  - 正文论证"


def test_heading_article_outlines_keep_three_levels():
    assert outline_blocks("## 论点\n### 依据\n#### 限制\n## 结论") == [
        "- 论点\n  - 依据\n    - 限制",
        "- 结论",
    ]
