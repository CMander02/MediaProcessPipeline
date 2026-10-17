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
async def test_postprocess_keeps_analysis_full_but_routes_selected_source_to_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.pipeline_steps import postprocess
    from app.core.pipeline_steps.context import PipelineContext
    from app.models import ChapterInfo, MediaMetadata, Task, TaskStatus, TaskType
    from app.services import analysis, archiving
    from app.services.analysis import source_context, summary_source, text_locale

    original_srt = (
        "1\n00:00:00,000 --> 00:00:05,000\n片头预告，介绍本期讨论主题。\n\n"
        "2\n00:00:10,000 --> 00:00:20,000\n正文讨论研究背景、系统设计与实践方法。\n"
    )
    polished_srt = original_srt.replace("片头预告", "片头预告（已润色）")
    selected_srt = (
        "2\n00:00:10,000 --> 00:00:20,000\n正文讨论研究背景、系统设计与实践方法。\n"
    )
    original_context = {
        "timeline": [
            {"start": 0, "title": "片头预告"},
            {"start": 10, "title": "正式讨论"},
        ],
        "entities": [],
    }
    selected_context = {
        "timeline": [{"start": 10, "title": "正式讨论"}],
        "entities": [],
    }
    task_dir = tmp_path / "archive"
    task_dir.mkdir()
    task = Task(
        task_type=TaskType.PIPELINE,
        status=TaskStatus.PROCESSING,
        source="https://example.test/video",
        result={},
    )
    metadata = MediaMetadata(
        title="示例视频",
        media_type="video",
        chapters=[ChapterInfo(title="旧章节", start_time=0)],
    )
    ctx = PipelineContext(
        task=task,
        rt=SimpleNamespace(generate_video_detail=False),
        task_dir=task_dir,
        metadata=metadata,
        srt=original_srt,
        transcript="片头预告，介绍本期讨论主题。正文讨论研究背景、系统设计与实践方法。",
        source_context=original_context,
    )

    monkeypatch.setattr(postprocess, "review_transcript_speakers", AsyncMock())
    monkeypatch.setattr(postprocess, "identify_transcript_speakers", AsyncMock())
    monkeypatch.setattr(postprocess, "_update_step", AsyncMock())
    monkeypatch.setattr(postprocess, "_raise_if_cancelled", AsyncMock())
    monkeypatch.setattr(postprocess, "_emit_file_ready", AsyncMock())
    monkeypatch.setattr(postprocess, "_write_summary_files", AsyncMock())
    monkeypatch.setattr(postprocess, "_write_mindmap_files", AsyncMock())
    monkeypatch.setattr(postprocess, "_write_detail_file", AsyncMock())
    monkeypatch.setattr(postprocess, "_schedule_kb_index", lambda *_args: None)
    monkeypatch.setattr(
        postprocess,
        "write_metadata_json",
        lambda _directory, _metadata, status: task_dir / "metadata.json",
    )

    async def write_artifact(_task, directory: Path, filename: str, content: str) -> Path:
        path = directory / filename
        path.write_text(content, encoding="utf-8")
        return path

    monkeypatch.setattr(postprocess, "_write_text_artifact", write_artifact)
    monkeypatch.setattr(
        archiving, "archive_result", AsyncMock(return_value={"path": str(task_dir)})
    )

    analysis_calls: dict[str, object] = {}

    async def polish(text: str, **_kwargs) -> str:
        return polished_srt

    async def analyze(text: str, title: str, *, metadata: dict) -> dict:
        analysis_calls["text"] = text
        analysis_calls["metadata"] = metadata
        return {"language": "zh", "main_topics": ["系统设计"], "proper_nouns": []}

    output_calls: dict[str, object] = {}

    async def mindmap(text: str, *, metadata: dict, user_language: str | None) -> str:
        output_calls["mindmap_text"] = text
        output_calls["mindmap_metadata"] = metadata
        return "- 系统设计 [00:00:10]"

    async def summarize(text: str, *, user_language: str | None, source_context: dict) -> dict:
        output_calls["summary_text"] = text
        output_calls["summary_context"] = source_context
        return {"tldr": "正文摘要", "key_facts": []}

    monkeypatch.setattr(analysis, "polish_text", polish)
    monkeypatch.setattr(analysis, "srt_to_markdown", lambda text, _title: text)
    monkeypatch.setattr(analysis, "analyze_content", analyze)
    monkeypatch.setattr(analysis, "generate_mindmap", mindmap)
    monkeypatch.setattr(analysis, "summarize_text", summarize)
    monkeypatch.setattr(source_context, "merge_hotwords", lambda *_args: [])
    monkeypatch.setattr(source_context, "canonicalize_text", lambda text, _context: text)
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
        lambda context, _mindmap: {**context, "topic_outline": ["系统设计"]},
    )

    await postprocess.postprocess(ctx)

    assert "片头预告（已润色）" in str(analysis_calls["text"])
    assert output_calls["selection_context"] is original_context
    assert output_calls["mindmap_text"] == selected_srt
    assert output_calls["summary_text"] == "正文讨论研究背景、系统设计与实践方法。"
    assert output_calls["summary_context"]["timeline"] == selected_context["timeline"]
    assert output_calls["summary_context"]["topic_outline"] == ["系统设计"]
    assert output_calls["mindmap_metadata"]["chapters"] == [
        {"title": "正式讨论", "start_time": 10}
    ]
    selection = json.loads((task_dir / "content_selection.json").read_text(encoding="utf-8"))
    assert selection["status"] == "filtered"
