from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.models import MediaMetadata  # noqa: E402
from app.services.analysis import llm as llm_module  # noqa: E402
from app.services.analysis.llm import LLMService  # noqa: E402
from app.services.analysis.source_context import (  # noqa: E402
    build_deterministic_source_context,
)
from app.services.recognition.subtitle_processor import process_subtitles  # noqa: E402


@pytest.mark.asyncio
async def test_platform_subtitle_polish_cannot_add_speakers_or_change_timestamps(
    tmp_path,
    monkeypatch,
):
    subtitle = tmp_path / "captions.srt"
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n第一句\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\n第二句",
        encoding="utf-8",
    )
    metadata = MediaMetadata(
        title="对谈 Evolvent AI 联创孟繁青",
        media_type="podcast",
        content_subtype="podcast_episode",
    )
    source_context = build_deterministic_source_context(metadata).model_dump(mode="json")
    service = LLMService()

    async def fake_call(_prompt, **_kwargs):
        return json.dumps(
            [
                {
                    "index": 1,
                    "timestamp": "00:09:00,000 --> 00:09:01,000",
                    "text": "[主持人] 第一句。",
                },
                {
                    "index": 2,
                    "timestamp": "00:09:01,000 --> 00:09:02,000",
                    "text": "[嘉宾] 第二句。",
                },
            ],
            ensure_ascii=False,
        )

    monkeypatch.setattr(service, "_call", fake_call)
    monkeypatch.setattr(llm_module, "get_llm_service", lambda: service)

    result = await process_subtitles(
        str(subtitle),
        "srt",
        metadata,
        source_context=source_context,
    )

    assert result["speakers"] == []
    assert "主持人" not in result["polished_srt"]
    assert "嘉宾" not in result["polished_srt"]
    assert "00:00:00,000 --> 00:00:01,000" in result["polished_srt"]
    assert "00:09" not in result["polished_srt"]
