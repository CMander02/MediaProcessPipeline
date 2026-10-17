from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


@pytest.mark.asyncio
async def test_fast_path_routes_filtered_source_to_outputs_and_keeps_analysis_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.pipeline_steps import subtitle_fast_path as fast
    from app.models import ChapterInfo, MediaMetadata, Task, TaskStatus, TaskType
    from app.services import analysis
    from app.services.analysis import source_context, summary_source, text_locale
    from app.services.recognition import subtitle_processor

    original_srt = (
        "1\n00:00:00,000 --> 00:00:05,000\n[SPEAKER_00] 片头预告，介绍本期讨论主题。\n\n"
        "2\n00:00:10,000 --> 00:00:20,000\n[SPEAKER_00] 正文讨论研究背景、系统设计与实践方法。\n"
    )
    selected_srt = (
        "1\n00:00:10,000 --> 00:00:20,000\n[SPEAKER_00] 正文讨论研究背景、系统设计与实践方法。\n"
    )
    polished_srt = original_srt.replace("片头预告", "片头预告（已润色）")
    source_context_value = {
        "title": "示例视频",
        "timeline": [
            {"start": 0, "title": "片头预告", "source": "metadata", "evidence": "章节"},
            {"start": 10, "title": "正式讨论", "source": "metadata", "evidence": "章节"},
        ],
        "entities": [{"canonical": "Agent", "evidence": "标题"}],
    }
    selected_context = {
        "title": "示例视频",
        "timeline": [{"start": 10, "title": "正式讨论", "source": "metadata", "evidence": "章节"}],
        "entities": [{"canonical": "Agent", "evidence": "标题"}],
    }
    task_dir = tmp_path / "archive"
    task_dir.mkdir()
    subtitle_path = tmp_path / "source.srt"
    subtitle_path.write_text(original_srt, encoding="utf-8")
    task = Task(
        task_type=TaskType.PIPELINE,
        status=TaskStatus.PROCESSING,
        source="https://example.test/video",
        result={"output_dir": str(task_dir)},
    )
    metadata = MediaMetadata(
        title="示例视频",
        media_type="video",
        chapters=[ChapterInfo(title="旧章节", start_time=0)],
    )

    monkeypatch.setattr(
        fast, "_prepare_source_context", AsyncMock(return_value=source_context_value)
    )
    monkeypatch.setattr(fast, "review_transcript_speakers", AsyncMock())
    monkeypatch.setattr(fast, "identify_transcript_speakers", AsyncMock())
    monkeypatch.setattr(fast, "_update_step", AsyncMock())
    monkeypatch.setattr(fast, "_raise_if_cancelled", AsyncMock())
    monkeypatch.setattr(
        fast,
        "get_runtime_settings",
        lambda: SimpleNamespace(generate_video_detail=True),
    )

    async def write_artifact(_task, directory: Path, filename: str, content: str) -> Path:
        path = directory / filename
        path.write_text(content, encoding="utf-8")
        return path

    monkeypatch.setattr(fast, "_write_text_artifact", write_artifact)
    monkeypatch.setattr(fast, "_write_summary_files", AsyncMock())
    monkeypatch.setattr(fast, "_write_mindmap_files", AsyncMock())
    monkeypatch.setattr(fast, "_write_detail_file", AsyncMock())

    monkeypatch.setattr(
        subtitle_processor,
        "process_subtitles",
        AsyncMock(
            return_value={
                "srt": original_srt,
                "segments": [
                    {
                        "start": 0,
                        "end": 5,
                        "text": "片头预告，介绍本期讨论主题。",
                    },
                    {
                        "start": 10,
                        "end": 20,
                        "text": "正文讨论研究背景、系统设计与实践方法。",
                    },
                ],
            }
        ),
    )

    analysis_calls: dict[str, object] = {}

    async def analyze(text: str, title: str, *, metadata: dict) -> dict:
        analysis_calls["text"] = text
        analysis_calls["metadata"] = metadata
        return {"language": "zh", "main_topics": ["系统设计"], "proper_nouns": []}

    monkeypatch.setattr(analysis, "analyze_content", analyze)
    monkeypatch.setattr(analysis, "polish_text", AsyncMock(return_value=polished_srt))
    monkeypatch.setattr(analysis, "srt_to_markdown", lambda text, _title: text)

    output_calls: dict[str, object] = {}

    async def mindmap(text: str, *, metadata: dict, user_language: str | None) -> str:
        output_calls["mindmap_text"] = text
        output_calls["mindmap_metadata"] = metadata
        return "# 系统设计\n- 正文讨论研究背景"

    async def detail(text: str, *, user_language: str | None) -> str:
        output_calls["detail_text"] = text
        return "正文细节。"

    async def summarize(text: str, *, user_language: str | None, source_context: dict) -> dict:
        output_calls["summary_text"] = text
        output_calls["summary_context"] = source_context
        return {"tldr": "正文摘要", "key_facts": []}

    monkeypatch.setattr(analysis, "generate_mindmap", mindmap)
    monkeypatch.setattr(analysis, "generate_detail", detail)
    monkeypatch.setattr(analysis, "summarize_text", summarize)
    monkeypatch.setattr(source_context, "canonicalize_text", lambda text, _context: text)
    monkeypatch.setattr(source_context, "source_context_to_analysis", lambda _context: {})
    monkeypatch.setattr(source_context, "merge_analysis_with_source", lambda value, _context: value)
    monkeypatch.setattr(
        text_locale, "normalize_chinese_script", lambda text, *_args, **_kwargs: text
    )

    async def prepare(_text: str, metadata_arg: dict, context_arg: dict, _call) -> dict:
        output_calls["selection_metadata"] = metadata_arg
        output_calls["selection_context"] = context_arg
        return {
            "text": selected_srt,
            "source_context": selected_context,
            "selection": {"status": "filtered", "excluded_ids": ["cue:1"]},
        }

    monkeypatch.setattr(summary_source, "prepare_summary_source", prepare)
    monkeypatch.setattr(
        summary_source,
        "outline_guided_context",
        lambda context, mindmap_text: {
            **context,
            "topic_outline": ["系统设计"],
            "outline_input": mindmap_text,
        },
    )

    result = await fast._run_subtitle_fast_path(
        task,
        task_dir,
        {"subtitle_path": str(subtitle_path), "subtitle_format": "srt", "subtitle_lang": "zh"},
        metadata,
    )

    assert "片头预告（已润色）" in str(analysis_calls["text"])
    assert output_calls["selection_context"] is source_context_value
    assert output_calls["mindmap_text"] == selected_srt
    assert output_calls["detail_text"] == selected_srt
    assert output_calls["summary_text"] == "[SPEAKER_00] 正文讨论研究背景、系统设计与实践方法。"
    assert output_calls["summary_context"]["timeline"] == selected_context["timeline"]
    assert output_calls["summary_context"]["topic_outline"] == ["系统设计"]
    assert output_calls["mindmap_metadata"]["chapters"] == [
        {"title": "正式讨论", "start_time": 10}
    ]
    assert output_calls["mindmap_metadata"]["chapters"] != [
        {"title": "旧章节", "start_time": 0}
    ]
    selection = json.loads((task_dir / "content_selection.json").read_text(encoding="utf-8"))
    assert selection == {"status": "filtered", "excluded_ids": ["cue:1"]}
    assert result["mindmap"].startswith("# 系统设计")
