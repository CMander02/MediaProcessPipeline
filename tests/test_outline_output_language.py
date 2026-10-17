"""Keep Chinese outline prose separate from English source evidence."""

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.analysis.grounded_outline import grounded_chapter_outline
from app.services.analysis.outline_organization import organize_outline
from app.services.analysis.prompts.mindmap import (
    get_detail_prompt,
    get_mindmap_map_prompt,
    get_mindmap_prompt,
    get_mindmap_reduce_prompt,
)


@pytest.mark.asyncio
async def test_english_windows_and_repairs_keep_chinese_policy_and_verbatim_evidence():
    calls = []

    async def call(prompt, *, system_prompt):
        calls.append(prompt)
        assert "in Simplified Chinese" in system_prompt
        assert "Evidence quotes must remain verbatim" in system_prompt
        assert "primary language of the supplied source" not in system_prompt
        if len(calls) == 1:
            return "{}"  # Exercise the evidence repair request as well.
        identifier, source = re.findall(r"^SOURCE (\d+): (.+)$", prompt, re.MULTILINE)[-1]
        return json.dumps({"points": [{"title": "模型能力仍受条件限制",
            "evidence_ids": [int(identifier)], "quote": source}]}, ensure_ascii=False)

    text = "\n\n".join(f"Discussion {i}: model performance depends on conditions." for i in range(90))
    result = await grounded_chapter_outline("English podcast", text, call, budget=1400)
    assert len(calls) > 3
    assert "Discussion 89:" in calls[-1]
    assert "上一份响应未通过验证" in calls[1]
    assert all(line == "- 模型能力仍受条件限制" for line in result.splitlines())


@pytest.mark.asyncio
async def test_english_source_language_does_not_change_grouping_or_review_language():
    calls = []

    async def call(prompt, *, system_prompt):
        calls.append(prompt)
        assert "overview in Simplified Chinese" in prompt
        assert "in Simplified Chinese" in system_prompt
        return json.dumps({"groups": [{"title": "讨论主题", "first": 1, "last": 12}]})

    outline = "\n".join(f"- 观点{i}" for i in range(12))
    result, report = await organize_outline(outline, call, language="English")
    assert report["reviewed"]
    assert len(calls) == 2
    assert result.startswith("- 讨论主题\n")


@pytest.mark.parametrize("language", [None, "English", "Simplified Chinese"])
def test_all_outline_prompt_variants_use_chinese(language):
    prompts = [
        get_mindmap_prompt("English source", language),
        get_detail_prompt("English source", language),
        get_mindmap_map_prompt("Chapter", "English source", "Context", language),
        get_mindmap_reduce_prompt("Part", "English summary", language),
    ]
    for prompt in prompts:
        assert "in Simplified Chinese" in prompt
        assert "including the final sections" in prompt
        assert "Do NOT collapse a multilingual transcript" not in prompt
