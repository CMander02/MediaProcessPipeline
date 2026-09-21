from __future__ import annotations

import asyncio
import inspect
import json
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core import database, pipeline, settings
from app.core.pipeline_steps import download, transcription
from app.models import Task, TaskStatus, TaskType

SRT = "1\n00:00:00,000 --> 00:00:02,000\n这是一段用于验证阶段交接和断点恢复的完整文本。\n"
SEGMENTS = [{"start": 0, "end": 2, "text": "这是一段用于验证阶段交接和断点恢复的完整文本。"}]


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from app.core import queue
    from app.services import analysis, recognition
    from app.services.analysis import language_detect, llm, speaker_identity

    root = tmp_path / "library"
    rt = settings.RuntimeSettings(
        data_root=str(root), enable_voiceprint=False, kb_enabled=False, generate_video_detail=False
    )
    monkeypatch.setattr(settings, "_runtime_settings", rt)
    database.reset_db_path(root)
    task_queue = SimpleNamespace(
        gpu_semaphore=asyncio.Semaphore(1),
        advance_to_gpu=AsyncMock(),
        advance_to_postprocess=AsyncMock(),
    )
    monkeypatch.setattr(queue, "get_task_queue", lambda: task_queue)
    asr = AsyncMock(return_value={"segments": SEGMENTS, "srt": SRT})
    monkeypatch.setattr(recognition, "transcribe_audio", asr)
    for stage in (download, transcription):
        monkeypatch.setattr(
            stage,
            "_prepare_source_context",
            AsyncMock(return_value={"title": "fixture"}),
            raising=False,
        )
    polish = AsyncMock(return_value=SRT)
    analyze = AsyncMock(
        return_value={"language": "zh", "proper_nouns": [], "main_topics": ["验证"]}
    )
    monkeypatch.setattr(analysis, "polish_text", polish)
    monkeypatch.setattr(analysis, "analyze_content", analyze)
    identify = AsyncMock(return_value={"status": "skipped", "mappings": []})
    monkeypatch.setattr(speaker_identity, "infer_speaker_names", identify)
    summarize = AsyncMock(return_value={"tldr": "阶段验证", "key_facts": []})
    mindmap = AsyncMock(return_value="# 阶段验证\n- 完成")
    detail = AsyncMock(return_value="阶段验证完成。")
    monkeypatch.setattr(analysis, "summarize_text", summarize)
    monkeypatch.setattr(analysis, "generate_mindmap", mindmap)
    monkeypatch.setattr(analysis, "generate_detail", detail)
    released = []
    monkeypatch.setattr(llm, "offload_local_llm", lambda: released.append(True))
    monkeypatch.setattr(language_detect, "detect_transcript_language", AsyncMock(return_value="zh"))
    source = tmp_path / "素材.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0" * 32000)
    yield SimpleNamespace(
        root=root,
        source=source,
        queue=task_queue,
        asr=asr,
        polish=polish,
        analyze=analyze,
        identify=identify,
        summarize=summarize,
        mindmap=mindmap,
        detail=detail,
        released=released,
    )
    database.close_db()


def create_task(source, **kwargs):
    task = Task(
        task_type=TaskType.PIPELINE,
        status=TaskStatus.PROCESSING,
        source=str(source),
        options={"skip_separation": True},
        **kwargs,
    )
    database.get_task_store().save(task)
    return task


@pytest.mark.asyncio
async def test_download_transcribe_postprocess_handoffs_restore_outputs(runtime):
    task = create_task(runtime.source)
    store = database.get_task_store()
    await pipeline.run_pipeline(task, _download_worker_call=True)
    runtime.queue.advance_to_gpu.assert_awaited_once_with(task.id)
    runtime.asr.assert_not_awaited()
    task = store.get(task.id)
    assert "download" in task.completed_steps
    if "_stop_after_transcribe" in inspect.signature(pipeline.run_pipeline).parameters:
        await pipeline.run_pipeline(task, _stop_after_transcribe=True)
        runtime.queue.advance_to_postprocess.assert_awaited_once_with(task.id)
        runtime.asr.assert_awaited_once()
        runtime.polish.assert_not_awaited()
        task = store.get(task.id)
        assert "transcribe" in task.completed_steps and "polish" not in task.completed_steps
    await pipeline.run_pipeline(task)
    runtime.asr.assert_awaited_once()
    runtime.polish.assert_awaited_once()
    runtime.analyze.assert_awaited_once()
    assert "archive" in task.completed_steps
    directory = Path(task.result["output_dir"])
    assert (directory / "summary.md").exists()
    assert (directory / "mindmap.md").exists()
    assert (directory / runtime.source.name).exists()
    assert store.get_artifact(task.id, "transcript_polished.srt")["content"] == SRT
    assert runtime.queue.gpu_semaphore._value == 1


@pytest.mark.asyncio
async def test_platform_subtitle_checkpoint_skips_asr(runtime, monkeypatch):
    from app.core.pipeline_steps import speaker_review as stage
    from app.services.recognition import subtitle_processor

    directory = runtime.root / "archives" / "platform"
    (directory / "subtitles").mkdir(parents=True)
    (directory / "subtitles" / "zh.srt").write_text(SRT, encoding="utf-8")
    (directory / "metadata.json").write_text(
        json.dumps(
            {
                "title": "platform",
                "media_type": "video",
                "platform": "youtube",
                "duration_seconds": 2,
                "status": "processing",
            }
        ),
        encoding="utf-8",
    )
    subtitles = AsyncMock(return_value={"srt": SRT, "segments": SEGMENTS, "polished_srt": SRT})
    monkeypatch.setattr(subtitle_processor, "process_subtitles", subtitles)
    infer = AsyncMock(return_value={
        "status": "completed", "speakers": [], "assignments": [], "failed_chunks": [],
    })
    monkeypatch.setattr(stage, "infer_subtitle_speakers", infer)
    task = create_task(
        "https://www.youtube.com/watch?v=fixture",
        completed_steps=["download"],
        result={"output_dir": str(directory)},
    )
    await pipeline.run_pipeline(task)
    subtitles.assert_awaited_once()
    infer.assert_awaited_once()
    runtime.asr.assert_not_awaited()
    assert task.result["subtitle_source"] == "platform"
    assert (directory / "summary.md").exists()


@pytest.mark.asyncio
async def test_incomplete_subtitle_checkpoint_falls_back_to_asr(runtime, monkeypatch):
    import shutil

    from app.core.pipeline_steps import speaker_review as stage
    from app.core.pipeline_steps.subtitles import restore_validated_subtitles

    directory = runtime.root / "archives" / "incomplete"
    (directory / "subtitles").mkdir(parents=True)
    (directory / "subtitles" / "zh.srt").write_text(SRT, encoding="utf-8")
    shutil.copy2(runtime.source, directory / "audio.wav")
    (directory / "metadata.json").write_text(json.dumps({
        "title": "incomplete", "media_type": "video", "platform": "youtube",
        "duration_seconds": 1000,
    }), encoding="utf-8")
    infer = AsyncMock()
    monkeypatch.setattr(stage, "infer_subtitle_speakers", infer)
    task = create_task(
        "https://www.youtube.com/watch?v=fixture", completed_steps=["download"],
        result={"output_dir": str(directory)},
    )
    await pipeline.run_pipeline(task)
    runtime.asr.assert_awaited_once()
    infer.assert_not_awaited()
    assert task.result["subtitle_source"] == "asr"
    assert task.flow["id"] == "url_platform_video_asr"
    assert restore_validated_subtitles(directory) == (True, None)
    report = json.loads((directory / "subtitle_validation.json").read_text(encoding="utf-8"))
    assert report["tracks"][0]["timing"]["reason"] == "ends_too_early"


@pytest.mark.asyncio
async def test_caption_inference_resumes_before_polish_with_raw_srt_preserved(runtime, monkeypatch):
    from app.core.pipeline_steps import speaker_review as stage

    directory = runtime.root / "archives" / "caption-resume"
    (directory / "subtitles").mkdir(parents=True)
    (directory / "subtitles" / "zh.srt").write_text(SRT, encoding="utf-8")
    (directory / "metadata.json").write_text(json.dumps({
        "title": "caption-resume", "media_type": "video", "platform": "youtube",
        "duration_seconds": 2,
    }), encoding="utf-8")
    infer = AsyncMock(return_value={
        "status": "completed", "source": "subtitle_inference",
        "speakers": [{"id": "SPEAKER_00", "name": "讲者"}],
        "assignments": [{"index": 1, "speaker": "SPEAKER_00"}], "failed_chunks": [],
    })
    monkeypatch.setattr(stage, "infer_subtitle_speakers", infer)
    runtime.polish.side_effect = RuntimeError("caption interrupted before polish")
    task = create_task(
        "https://www.youtube.com/watch?v=fixture", completed_steps=["download"],
        result={"output_dir": str(directory)},
    )
    with pytest.raises(RuntimeError, match="caption interrupted"):
        await pipeline.run_pipeline(task)
    reviewed = runtime.polish.call_args.args[0]
    assert "[讲者]" in reviewed
    assert "[讲者]" not in (directory / "transcript.srt").read_text(encoding="utf-8")
    assert (directory / "subtitle_speakers.json").exists()
    runtime.polish.reset_mock(side_effect=True)
    runtime.polish.return_value = reviewed
    saved = database.get_task_store().get(task.id)
    await pipeline.run_pipeline(saved)
    infer.assert_awaited_once()
    runtime.asr.assert_not_awaited()
    assert runtime.polish.call_args.args[0] == reviewed
    assert saved.result["metadata"]["extra"]["speaker_source"] == "subtitle_inference"
    assert saved.result["metadata"]["extra"]["speakers"] == ["讲者"]


@pytest.mark.asyncio
async def test_subtitle_fast_path_infers_speakers_before_polish(runtime, monkeypatch):
    from app.core.pipeline_steps import speaker_review as stage
    from app.core.pipeline_steps import subtitle_fast_path as fast
    from app.models import MediaMetadata

    directory = runtime.root / "archives" / "fast-caption"
    (directory / "subtitles").mkdir(parents=True)
    subtitle = directory / "subtitles" / "zh.srt"
    subtitle.write_text(SRT, encoding="utf-8")
    monkeypatch.setattr(
        fast, "_prepare_source_context", AsyncMock(return_value={"title": "fixture"}),
    )
    infer = AsyncMock(return_value={
        "status": "completed", "speakers": [{"id": "SPEAKER_00", "name": "讲者"}],
        "assignments": [{"index": 1, "speaker": "SPEAKER_00"}], "failed_chunks": [],
    })
    monkeypatch.setattr(stage, "infer_subtitle_speakers", infer)
    runtime.polish.side_effect = lambda text, **kwargs: text
    task = create_task(
        "https://www.youtube.com/watch?v=fixture", result={"output_dir": str(directory)},
    )
    metadata = MediaMetadata(title="fixture", media_type="video", duration_seconds=2)
    result = await fast._run_subtitle_fast_path(task, directory, {
        "subtitle_path": str(subtitle), "subtitle_format": "srt", "subtitle_lang": "zh",
    }, metadata)
    infer.assert_awaited_once()
    assert "[讲者]" in runtime.polish.call_args.args[0]
    assert "[讲者]" not in result["srt"]
    assert result["analysis"]["speakers_detected"] == 1
    assert (directory / "subtitle_speakers.json").exists()


@pytest.mark.asyncio
async def test_postprocess_identifies_after_summary_and_archives_names(runtime, monkeypatch):
    from app.core.pipeline_steps import speaker_review as stage

    source = (
        "1\n00:00:00,000 --> 00:00:20,000\n[SPEAKER_00] 今天分享我的项目。\n\n"
        "2\n00:00:20,000 --> 00:00:21,000\n[SPEAKER_01] 谢谢分享。\n"
    )
    polished = source.replace("今天分享", "今天介绍")
    runtime.asr.return_value = {
        "srt": source,
        "segments": [
            {"start": 0, "end": 20, "speaker": "SPEAKER_00", "text": "今天分享我的项目。"},
            {"start": 20, "end": 21, "speaker": "SPEAKER_01", "text": "谢谢分享。"},
        ],
    }
    monkeypatch.setattr(stage, "review_speakers", AsyncMock(return_value={
        "status": "completed", "changes": [], "failed_chunks": [],
    }))
    settings.get_runtime_settings().generate_video_detail = True
    calls = []

    def polish(*args, **kwargs):
        calls.append("polish")
        return polished

    def identify(srt, **kwargs):
        calls.append("identify")
        assert srt == polished
        assert {"summary", "mindmap", "detail"} <= set(calls[:-1])
        assert kwargs["context"]["analysis"]["content_type"] == "技术讲座"
        assert kwargs["context"]["summary"]["tldr"] == "张三主讲，SPEAKER_00介绍项目。"
        assert kwargs["context"]["speaker_activity"]
        return {"status": "completed", "mappings": [{
            "source_label": "SPEAKER_00", "current_name": "张三",
            "evidence_indexes": [1], "reason": "摘要明确主讲人，声学标签时长占比显著",
        }]}

    def analyze(text, *args, **kwargs):
        calls.append("analyze")
        assert "[SPEAKER_00]" in text
        assert "今天介绍我的项目" in text
        return {
            "language": "zh", "proper_nouns": [], "content_type": "技术讲座",
            "main_topics": ["SPEAKER_00 的项目"],
        }

    def summarize(*args, **kwargs):
        calls.append("summary")
        return {"tldr": "张三主讲，SPEAKER_00介绍项目。", "key_facts": ["SPEAKER_00建议实践。"]}

    def mindmap(*args, **kwargs):
        calls.append("mindmap")
        return "# 项目分享\n- SPEAKER_00 的实践"

    def detail(*args, **kwargs):
        calls.append("detail")
        return "SPEAKER_00介绍实现细节。"

    runtime.polish.side_effect = polish
    runtime.identify.side_effect = identify
    runtime.analyze.side_effect = analyze
    runtime.summarize.side_effect = summarize
    runtime.mindmap.side_effect = mindmap
    runtime.detail.side_effect = detail
    task = create_task(runtime.source)
    await pipeline.run_pipeline(task)

    assert calls[:2] == ["polish", "analyze"]
    assert calls[-1] == "identify"
    directory = Path(task.result["output_dir"])
    for filename in (
        "transcript.srt", "transcript_polished.srt", "transcript_polished.md",
        "analysis.json", "summary.json", "summary.md", "mindmap.md", "mindmap.json", "detail.md",
    ):
        content = (directory / filename).read_text(encoding="utf-8")
        assert "张三" in content
        assert "SPEAKER_00" not in content
        assert database.get_task_store().get_artifact(task.id, filename)["content"] == content
    identity = json.loads((directory / "speaker_identity.json").read_text(encoding="utf-8"))
    assert identity["mappings"][0]["current_name"] == "张三"
    assert identity["context_stage"] == "analyzed"
    assert set(task.result["metadata"]["extra"]["speakers"]) == {"张三", "SPEAKER_01"}
    assert task.result["analysis"]["main_topics"] == ["张三 的项目"]
    assert "[SPEAKER_01] 谢谢分享。" in (
        directory / "transcript_polished.srt"
    ).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_subtitle_fast_path_identifies_names_after_summary(runtime, monkeypatch):
    from app.core.pipeline_steps import speaker_review as stage
    from app.core.pipeline_steps import subtitle_fast_path as fast
    from app.models import MediaMetadata

    raw = "1\n00:00:00,000 --> 00:00:02,000\n我叫张三，今天分享我的项目。\n"
    directory = runtime.root / "archives" / "fast-identity"
    (directory / "subtitles").mkdir(parents=True)
    subtitle = directory / "subtitles" / "zh.srt"
    subtitle.write_text(raw, encoding="utf-8")
    candidates = [{"name": "张三", "evidence": "嘉宾介绍"}]
    monkeypatch.setattr(fast, "_prepare_source_context", AsyncMock(return_value={
        "title": "fixture", "speaker_candidates": candidates,
    }))
    monkeypatch.setattr(stage, "infer_subtitle_speakers", AsyncMock(return_value={
        "status": "completed", "speakers": [{"id": "SPEAKER_00", "name": None}],
        "assignments": [{"index": 1, "speaker": "SPEAKER_00"}], "failed_chunks": [],
    }))
    runtime.polish.side_effect = lambda text, **kwargs: text.replace("今天分享", "今天介绍")
    runtime.summarize.return_value = {"tldr": "张三主讲本次项目分享。", "key_facts": []}

    def identify(srt, **kwargs):
        assert runtime.polish.await_count == 1
        assert runtime.analyze.await_count == 1
        assert runtime.summarize.await_count == 1
        assert runtime.mindmap.await_count == 1
        assert "[SPEAKER_00]" in srt and "今天介绍我的项目" in srt
        assert kwargs["context"]["summary"]["tldr"] == "张三主讲本次项目分享。"
        assert kwargs["context"]["analysis"]["language"] == "zh"
        candidate = kwargs["context"]["speaker_candidates"][0]
        assert candidate["name"] == "张三" and candidate["evidence"] == "嘉宾介绍"
        return {"status": "completed", "mappings": [{
            "source_label": "SPEAKER_00", "current_name": "张三",
            "evidence_indexes": [1], "reason": "字幕中有明确自我介绍",
        }]}

    runtime.identify.side_effect = identify
    task = create_task(
        "https://www.youtube.com/watch?v=fixture", result={"output_dir": str(directory)},
    )
    metadata = MediaMetadata(title="fixture", media_type="video", duration_seconds=2)
    result = await fast._run_subtitle_fast_path(task, directory, {
        "subtitle_path": str(subtitle), "subtitle_format": "srt", "subtitle_lang": "zh",
    }, metadata)

    runtime.identify.assert_awaited_once()
    analyzed = runtime.analyze.call_args.args[0]
    assert "[SPEAKER_00]" in analyzed and "今天介绍我的项目" in analyzed
    assert "[张三]" in result["polished"]
    assert "张三" in result["polished_md"]
    for filename in ("transcript_polished.srt", "transcript_polished.md"):
        content = (directory / filename).read_text(encoding="utf-8")
        assert "张三" in content
        assert "SPEAKER_00" not in content
        assert database.get_task_store().get_artifact(task.id, filename)["content"] == content
    assert metadata.extra["speakers"] == ["张三"]
    assert result["analysis"]["speakers_detected"] == 1


@pytest.mark.asyncio
async def test_speaker_review_precedes_polish_and_resumes_from_report(runtime, monkeypatch):
    from app.core.pipeline_steps import speaker_review as stage
    from app.services.analysis.transcript_outputs import _parse_srt

    source = (
        "1\n00:00:00,000 --> 00:00:02,000\n[SPEAKER_00] 我想介绍一下这个项目，\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\n[SPEAKER_01] 它是我去年开始做的。\n\n"
        "3\n00:00:04,000 --> 00:00:06,000\n[SPEAKER_01] 你遇到了哪些困难？\n"
    )
    runtime.asr.return_value = {"segments": [
        {"start": 0, "end": 2, "speaker": "SPEAKER_00", "text": "我想介绍一下这个项目，"},
        {"start": 2, "end": 4, "speaker": "SPEAKER_01", "text": "它是我去年开始做的。"},
        {"start": 4, "end": 6, "speaker": "SPEAKER_01", "text": "你遇到了哪些困难？"},
    ], "srt": source}
    change = {
        "index": 2, "from_speaker": "SPEAKER_01", "speaker": "SPEAKER_00",
        "evidence_indexes": [1, 2], "reason": "继续介绍本人项目",
    }
    review = AsyncMock(return_value={
        "status": "completed", "changes": [change], "failed_chunks": [],
    })
    monkeypatch.setattr(stage, "review_speakers", review)
    # Interrupt after correction was persisted, before polish produced output.
    runtime.polish.side_effect = RuntimeError("interrupted before polish")
    directory = runtime.root / "archives" / "speaker-review"
    directory.mkdir(parents=True)
    task = create_task(runtime.source, result={"output_dir": str(directory)})
    with pytest.raises(RuntimeError, match="interrupted before polish"):
        await pipeline.run_pipeline(task)
    review.assert_awaited_once()
    reviewed = runtime.polish.call_args.args[0]
    assert "[SPEAKER_00] 它是我去年开始做的。" in reviewed
    runtime.analyze.assert_not_awaited()
    saved = database.get_task_store().get(task.id)
    assert "speaker_review" in saved.completed_steps
    directory = Path(saved.result["output_dir"])
    original = (directory / "transcript.srt").read_text(encoding="utf-8")
    assert _parse_srt(original) == _parse_srt(source)
    report = json.loads((directory / "speaker_review.json").read_text(encoding="utf-8"))
    assert report["changes"] == [change]
    runtime.polish.reset_mock(side_effect=True)
    runtime.polish.return_value = reviewed
    await pipeline.run_pipeline(saved)
    review.assert_awaited_once()
    assert runtime.polish.call_args.args[0] == reviewed
    runtime.analyze.assert_awaited_once()
    assert saved.result["metadata"]["extra"]["speaker_review"]["changes"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [TaskStatus.PAUSED, TaskStatus.CANCELLED])
async def test_pausing_during_asr_releases_semaphore_and_keeps_checkpoint(runtime, status):
    directory = runtime.root / "archives" / "pause"
    directory.mkdir(parents=True)
    task = create_task(runtime.source, result={"output_dir": str(directory)})

    async def pause(*args, **kwargs):
        database.get_task_store().update_status(task.id, status)
        raise asyncio.CancelledError

    runtime.asr.side_effect = pause
    with pytest.raises(asyncio.CancelledError):
        await pipeline.process_task(task.id)
    saved = database.get_task_store().get(task.id)
    assert saved.status == status
    assert "download" in saved.completed_steps
    assert runtime.queue.gpu_semaphore._value == 1
    assert runtime.released == [True]
    assert json.loads((directory / "metadata.json").read_text())["status"] == str(status)


@pytest.mark.asyncio
@pytest.mark.parametrize("subtype", ["text_note", "image_note"])
async def test_note_checkpoint_uses_note_stage_without_asr(runtime, subtype):
    directory = runtime.root / "archives" / "note"
    directory.mkdir(parents=True)
    if subtype == "image_note":
        from PIL import Image

        (directory / "images").mkdir()
        Image.new("RGB", (4, 4), "white").save(directory / "images" / "00.png")
    (directory / "metadata.json").write_text(
        json.dumps(
            {
                "title": "笔记",
                "media_type": "other",
                "content_subtype": subtype,
                "platform": "webpage",
                "description": "这是一篇用于验证正文持久化和图文分支的文章。",
                "status": "processing",
            }
        ),
        encoding="utf-8",
    )
    task = create_task(
        "https://example.test/article",
        completed_steps=["download"],
        result={"output_dir": str(directory)},
    )
    await pipeline.run_pipeline(task)
    runtime.asr.assert_not_awaited()
    assert "archive" in task.completed_steps
    assert (directory / "source.md").exists()
    assert (directory / "summary.md").exists()
    assert runtime.queue.gpu_semaphore._value == 1


@pytest.mark.asyncio
async def test_fresh_url_download_renames_directory_and_finishes(runtime, monkeypatch):
    import shutil

    from app.services import ingestion

    async def download(url, output_dir):
        audio = output_dir / "download.wav"
        shutil.copy2(runtime.source, audio)
        return {
            "file_path": str(audio),
            "metadata": {
                "title": "下载测试",
                "media_type": "audio",
                "platform": "youtube",
                "source_url": url,
                "file_path": str(audio),
            },
        }

    monkeypatch.setattr(ingestion, "download_media", download)
    task = create_task("https://www.youtube.com/watch?v=abcdefghijk")
    task.options["force_asr"] = True
    await pipeline.run_pipeline(task)
    directory = Path(task.result["output_dir"])
    assert directory.name == "下载测试"
    assert (directory / "download.wav").exists()
    assert (directory / "summary.md").exists()
    runtime.asr.assert_awaited_once()


@pytest.mark.asyncio
async def test_uvr_failure_releases_model_before_asr_fallback(runtime, monkeypatch):
    import shutil

    from app.core.pipeline_steps import download as download_step
    from app.services import preprocessing

    order = []

    async def unavailable(*args, **kwargs):
        order.append("uvr")
        raise RuntimeError("fixture unavailable")

    async def recognize(*args, **kwargs):
        order.append("asr")
        return {"segments": SEGMENTS, "srt": SRT}

    monkeypatch.setattr(preprocessing, "separate_vocals", unavailable)
    monkeypatch.setattr(
        transcription, "_release_uvr_gpu_resources", lambda: order.append("release")
    )
    runtime.asr.side_effect = recognize
    source_video = runtime.source.with_suffix(".mp4")
    source_video.write_bytes(b"fixture video")
    monkeypatch.setattr(
        download_step,
        "_extract_audio_from_video",
        lambda _source, output: shutil.copy2(runtime.source, output),
    )
    task = create_task(source_video)
    task.options["skip_separation"] = False
    await pipeline.run_pipeline(task)
    assert order == ["uvr", "release", "asr"]
    assert runtime.queue.gpu_semaphore._value == 1
