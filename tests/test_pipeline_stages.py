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
    from app.services.analysis import language_detect, llm

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
    monkeypatch.setattr(
        analysis, "summarize_text", AsyncMock(return_value={"tldr": "阶段验证", "key_facts": []})
    )
    monkeypatch.setattr(analysis, "generate_mindmap", AsyncMock(return_value="# 阶段验证\n- 完成"))
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
        released=released,
    )
    database.close_db()


def create_task(source, **kwargs):
    task = Task(
        task_type=TaskType.PIPELINE,
        status=TaskStatus.PROCESSING,
        source=str(source),
        options={"skip_separation": True, "use_platform_subtitle_reference": False},
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
                "status": "processing",
            }
        ),
        encoding="utf-8",
    )
    subtitles = AsyncMock(return_value={"srt": SRT, "segments": SEGMENTS, "polished_srt": SRT})
    monkeypatch.setattr(subtitle_processor, "process_subtitles", subtitles)
    task = create_task(
        "https://www.youtube.com/watch?v=fixture",
        completed_steps=["download"],
        result={"output_dir": str(directory)},
    )
    await pipeline.run_pipeline(task)
    subtitles.assert_awaited_once()
    runtime.asr.assert_not_awaited()
    assert task.result["subtitle_source"] == "platform"
    assert (directory / "summary.md").exists()


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
