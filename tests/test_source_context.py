from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.analysis import llm as llm_module  # noqa: E402
from app.services.analysis.source_context import (  # noqa: E402
    build_deterministic_source_context,
    build_source_context,
    canonicalize_text,
    merge_analysis_with_source,
    speaker_constraints,
)
from app.services.archiving.archive import ArchiveService, SUMMARY_TEMPLATE  # noqa: E402


def _apple_metadata() -> dict:
    return {
        "title": "从蒸馏到合成数据到 RSI｜对谈 Evolvent AI 联创孟繁青",
        "uploader": "42章经",
        "media_type": "podcast",
        "content_subtype": "podcast_episode",
        "description": (
            "嘉宾来自 Evolvent AI。导游：曲凯，42章经创始人。"
            "51 号珍藏：孟繁青，Evolvent AI 联合创始人；前 Kimi RL 实习生。"
            "发布 RSIBench-Data，繁青介绍 Self-Evolving、Kimi K2.5 和 Kimi Linear。"
        ),
        "tags": ["Apple Podcasts", "42章经"],
        "chapters": [
            {"title": "在 Kimi 和创业的感受", "start_time": 75},
            {"title": "RSIBench-Data 的目标", "start_time": 3378},
        ],
    }


def test_source_context_extracts_timeline_entities_and_speaker_constraint():
    context = build_deterministic_source_context(_apple_metadata())

    assert [item.start for item in context.timeline] == [0, 75, 3378]
    assert context.timeline[1].source == "source_chapter"
    assert {item.name: item.role for item in context.speaker_candidates} == {
        "曲凯": "host",
        "孟繁青": "guest",
    }
    assert context.speaker_count_hint.exact == 2
    assert speaker_constraints(context) == (2, 2, 2)
    assert {"Evolvent AI", "RSIBench-Data", "Kimi K2.5", "曲凯", "孟繁青"} <= set(
        context.asr_hotwords
    )
    assert "Apple Podcasts" not in context.asr_hotwords


@pytest.mark.asyncio
async def test_source_tagger_repairs_json_and_rejects_unsupported_entities(monkeypatch):
    responses = [
        "invalid",
        json.dumps(
            {
                "language_hint": "zh-CN",
                "content_type": "podcast_interview",
                "entities": [
                    {
                        "canonical": "Evolvent AI",
                        "aliases": ["Evolvent"],
                        "type": "organization",
                        "evidence": "title",
                        "confidence": 0.99,
                    },
                    {
                        "canonical": "Imaginary Corp",
                        "aliases": [],
                        "type": "organization",
                        "evidence": "guess",
                        "confidence": 0.9,
                    },
                ],
                "speaker_candidates": [],
                "speaker_count_hint": {
                    "exact": 2,
                    "confidence": 0.99,
                    "evidence": "host and guest",
                },
            },
            ensure_ascii=False,
        ),
    ]
    calls = []

    class FakeLLM:
        async def _call(self, prompt, **kwargs):
            calls.append((prompt, kwargs))
            return responses.pop(0)

    monkeypatch.setattr(llm_module, "get_llm_service", lambda: FakeLLM())

    context = await build_source_context(_apple_metadata(), enrich=True)

    assert len(calls) == 2
    assert all(call[1]["system_prompt"] for call in calls)
    assert "Imaginary Corp" not in {item.canonical for item in context.entities}
    evolvent = next(item for item in context.entities if item.canonical == "Evolvent AI")
    assert "Evolvent" in evolvent.aliases
    assert canonicalize_text("Evolvent 发布了成果", context) == "Evolvent AI 发布了成果"
    assert canonicalize_text("Evolvent AI 发布了成果", context) == "Evolvent AI 发布了成果"
    assert canonicalize_text("Evolve AI 与孟凡青", context) == "Evolvent AI 与孟繁青"
    assert canonicalize_text("Kimi Lina", context) == "Kimi Linear"


def test_source_analysis_fields_override_untrusted_timeline_and_speaker_count():
    context = build_deterministic_source_context(_apple_metadata())
    merged = merge_analysis_with_source(
        {
            "language": "unknown",
            "timeline": [{"start": 999, "title": "模型猜测"}],
            "speakers_detected": 7,
        },
        context,
    )

    assert merged["timeline"][0]["start"] == 0
    assert merged["timeline"][1]["start"] == 75
    assert merged["speakers_detected"] == 2
    assert merged["language"] == "zh-CN"


def test_summary_markdown_timeline_keeps_source_start_and_title():
    rendered = ArchiveService()._fmt_timeline(
        [
            {"start": 75, "title": "第一章", "summary": "讨论创业感受"},
            {"start": 3378, "title": "最后一章", "summary": "说明产品目标"},
        ]
    )

    assert "[00:01:15] 第一章 — 讨论创业感受" in rendered
    assert "[00:56:18] 最后一章 — 说明产品目标" in rendered


def test_summary_markdown_keeps_timeline_in_structured_artifact_only():
    rendered = SUMMARY_TEMPLATE.format(
        title="示例",
        source_url="https://example.com",
        date="2026-08-10",
        tldr="摘要正文",
        key_facts="- 事实",
    )

    assert "摘要正文" in rendered
    assert "### Key Facts" in rendered
    assert "Timeline" not in rendered
