from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core import archive_sync, database, settings
from app.core.media_retention import MediaRetentionService, record_media, uploading_media
from app.models import Task, TaskStatus, TaskType


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "_runtime_settings", settings.RuntimeSettings(data_root=str(tmp_path))
    )
    database.reset_db_path(tmp_path)
    sync = archive_sync.ArchiveSyncService()
    monkeypatch.setattr(archive_sync, "_sync_service", sync)
    directory = tmp_path / "会议 🎧"
    directory.mkdir()
    (directory / "metadata.json").write_text(
        json.dumps(
            {
                "archive_id": str(uuid4()),
                "title": "会议",
                "status": "completed",
                "media_type": "audio",
            }
        ),
        encoding="utf-8",
    )
    (directory / "summary.md").write_text("# 保留正文", encoding="utf-8")
    yield directory, MediaRetentionService(), sync
    database.close_db()


def media(directory, filename, role, *, playback=False, size=123):
    target = directory / filename
    target.write_bytes(b"a" * size)
    record_media(directory, target, role, playback=playback)
    return target


def selected(preview):
    return [item["path"] for item in preview["entries"] if item["delete"]]


def test_default_keeps_all_and_wav_purpose_controls_playback_policy(library):
    directory, service, _ = library
    source = media(directory, "original.wav", "source", playback=True)
    working = media(directory, "working.wav", "working", size=456)
    unknown = directory / "old.wav"
    unknown.write_bytes(b"old")
    assert selected(service.preview(str(directory))) == []
    preview = service.preview(str(directory), "playback")
    assert selected(preview) == ["working.wav"]
    result = service.apply(str(directory), "playback", files=selected(preview))
    assert result["reclaimed_bytes"] == preview["reclaimable_bytes"] == 456
    assert source.exists() and unknown.exists() and not working.exists()
    assert result["reclaimable_bytes"] == 0


def test_text_policy_updates_index_and_keeps_outputs(library):
    directory, service, sync = library
    media(directory, "video.mp4", "source", playback=True)
    media(directory, "working.wav", "working")
    for name in ["transcript.srt", "mindmap.md", "cover.jpg"]:
        (directory / name).write_bytes(b"keep")
    metadata = json.loads((directory / "metadata.json").read_text())
    metadata.update(media_type="video", file_path=str(directory / "video.mp4"))
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    sync.reconcile()
    revision = sync.current_revision()
    preview = service.preview(str(directory), "text")
    result = service.apply(str(directory), "text", files=selected(preview))
    assert result["reclaimed_bytes"] == preview["reclaimable_bytes"] == 246
    archive = sync.list_page()["archives"][0]
    assert not archive["has_video"] and not archive["has_audio"] and not archive["media_file"]
    assert archive["has_summary"] and archive["has_transcript"] and archive["has_mindmap"]
    assert (directory / "cover.jpg").read_bytes() == b"keep"
    assert sync.changes(revision, 10)["changes"][-1]["archive"]["has_video"] is False


@pytest.mark.parametrize(
    "status", [TaskStatus.PENDING, TaskStatus.PROCESSING, TaskStatus.PAUSED, TaskStatus.FAILED]
)
def test_unfinished_task_protects_media(library, status):
    directory, service, _ = library
    source = media(directory, "source.wav", "source")
    task = Task(
        task_type=TaskType.PIPELINE,
        source="input",
        status=status,
        result={"output_dir": str(directory)},
    )
    database.get_task_store().save(task)
    result = service.apply(str(directory), "text", files=[source.name])
    assert result["protected_reason"] and not result["cleaned"]
    assert source.exists()


def test_upload_lease_blocks_apply_then_releases(library):
    directory, service, _ = library
    source = media(directory, "source.wav", "source")
    with uploading_media(directory):
        result = service.apply(str(directory), "text", files=[source.name])
        assert result["protected_reason"] == "归档正在上传"
        assert source.exists()
    assert selected(service.preview(str(directory), "text")) == [source.name]


def test_removed_media_paths_are_cleared_from_task_results(library):
    directory, service, _ = library
    source = media(directory, "source.wav", "source")
    task = Task(
        task_type=TaskType.PIPELINE,
        source="input",
        status=TaskStatus.COMPLETED,
        result={
            "output_dir": str(directory),
            "audio_path": str(source),
            "archive": {"files": {"audio": str(source), "summary": str(directory / "summary.md")}},
        },
    )
    database.get_task_store().save(task)
    service.apply(str(directory), "text", files=[source.name])
    result = database.get_task_store().get(task.id).result
    assert result["audio_path"] is None
    assert result["archive"]["files"]["audio"] is None
    assert result["archive"]["files"]["summary"] == str(directory / "summary.md")


def test_missing_body_external_source_unknown_and_samples_are_preserved(library):
    directory, service, _ = library
    media(directory, "known.wav", "source")
    external = directory.parent / "original.wav"
    external.write_bytes(b"original")
    metadata = json.loads((directory / "metadata.json").read_text())
    metadata["source_url"] = str(external)
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (directory / "samples").mkdir()
    sample = directory / "samples" / "voice.wav"
    sample.write_bytes(b"sample")
    record_media(directory, external, "source")
    (directory / "summary.md").unlink()
    assert service.preview(str(directory), "text")["protected_reason"] == "正文尚未持久化"
    (directory / "summary.md").write_text("body")
    result = service.apply(
        str(directory), "text", files=["known.wav", "../original.wav", "samples/voice.wav"]
    )
    assert [item["path"] for item in result["cleaned"]] == ["known.wav"]
    assert external.read_bytes() == b"original" and sample.read_bytes() == b"sample"
    assert any(
        item["role"] == "external_source" and not item["delete"] for item in result["entries"]
    )


def test_partial_failure_retries_remaining_candidates(library, monkeypatch):
    directory, service, _ = library
    first = media(directory, "first.wav", "working")
    second = media(directory, "second.wav", "separated")
    original = Path.unlink

    def fail_one(path, *args, **kwargs):
        if path == second:
            raise PermissionError("file busy")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_one)
    result = service.apply(str(directory), "text", files=[first.name, second.name])
    assert result["reclaimed_bytes"] == 123 and len(result["errors"]) == 1
    assert selected(result) == [second.name]
    monkeypatch.setattr(Path, "unlink", original)
    retried = service.apply(str(directory), "text", files=selected(result))
    assert retried["reclaimed_bytes"] == 123 and retried["errors"] == []
    assert selected(retried) == []


def test_legacy_metadata_identifies_only_its_playback_file(library):
    directory, service, _ = library
    for name in ("original.wav", "same-stem.mp3", "working.wav"):
        (directory / name).write_bytes(b"keep")
    metadata = json.loads((directory / "metadata.json").read_text())
    metadata["file_path"] = str(directory / "original.wav")
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    assert selected(service.preview(str(directory), "playback")) == []
    assert selected(service.preview(str(directory), "text")) == ["original.wav"]


def test_api_requires_explicit_selection_and_valid_policy(library):
    from app.api.routes.pipeline import router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    directory, _, _ = library
    media(directory, "source.wav", "source")
    app = FastAPI()
    app.include_router(router, prefix="/api")
    with TestClient(app) as client:
        payload = {"path": str(directory), "policy": "text"}
        assert client.post("/api/pipeline/media-retention/apply", json=payload).status_code == 400
        preview = client.post("/api/pipeline/media-retention/preview", json=payload).json()
        payload["files"] = selected(preview)
        result = client.post("/api/pipeline/media-retention/apply", json=payload)
        assert result.status_code == 200 and result.json()["reclaimed_bytes"] == 123
        payload["policy"] = "invalid"
        assert client.post("/api/pipeline/media-retention/preview", json=payload).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("with_video", [False, True])
async def test_download_records_source_and_extracted_wav(library, monkeypatch, with_video):
    from types import SimpleNamespace

    from app.models import MediaMetadata
    from app.services.ingestion import ytdlp

    directory, service, _ = library
    audio = directory / "download.wav"
    audio.write_bytes(b"audio")
    video = directory / "download.mp4"
    if with_video:
        video.write_bytes(b"video")
    fake = SimpleNamespace(
        download=lambda *args, **kwargs: {
            "file_path": str(audio),
            "video_path": str(video) if with_video else None,
            "info": {},
        },
        extract_metadata=lambda *args: MediaMetadata(title="download"),
    )
    monkeypatch.setattr(ytdlp, "get_ytdlp_service", lambda: fake)
    await ytdlp.download_media("https://example.com/video", output_dir=directory)
    entries = service.preview(str(directory), "playback")["entries"]
    assert next(item for item in entries if item["path"] == audio.name)["role"] == (
        "working" if with_video else "source"
    )
    assert selected(service.preview(str(directory), "playback")) == (
        [audio.name] if with_video else []
    )


def test_cli_preview_and_apply_use_same_service(library, monkeypatch):
    from types import SimpleNamespace

    from app.cli.commands import operations
    from app.cli.main import app
    from typer.testing import CliRunner

    directory, service, _ = library
    audio = media(directory, "working.wav", "working")

    def request(path, policy=None, files=None):
        return (
            service.preview(path, policy)
            if files is None
            else service.apply(path, policy, files=files)
        )

    monkeypatch.setattr(operations, "client", lambda: SimpleNamespace(media_retention=request))
    args = [
        "--skip-version-check",
        "--plain",
        "storage",
        "media",
        str(directory),
        "--policy",
        "playback",
    ]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert audio.exists() and "working.wav" in result.output
    result = CliRunner().invoke(app, [*args, "--apply", "--yes"])
    assert result.exit_code == 0, result.output
    assert not audio.exists() and "reclaimed_bytes" in result.output
