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
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
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
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    metadata["source_url"] = str(external)
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (directory / "samples").mkdir()
    sample = directory / "samples" / "voice.wav"
    sample.write_bytes(b"sample")
    record_media(directory, external, "source")
    (directory / "summary.md").unlink()
    assert service.preview(str(directory), "text")["protected_reason"] == "正文尚未持久化"
    (directory / "summary.md").write_text("body", encoding="utf-8")
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
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
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


def completed_separation(directory, *, status=TaskStatus.COMPLETED):
    (directory / "transcript.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n正文\n", encoding="utf-8")
    media(directory, "source.wav", "source")
    task = Task(
        task_type=TaskType.PIPELINE,
        source="https://example.com/video",
        status=status,
        completed_steps=["download", "separate", "transcribe", "archive"],
        result={"output_dir": str(directory)},
    )
    database.get_task_store().save(task)
    return task


def test_automatic_cleanup_preserves_originals_policy_and_checkpoints(library):
    directory, service, _ = library
    task = completed_separation(directory)
    original = media(directory, "original.mp4", "source", playback=True)
    working = media(directory, "original.wav", "working")
    vocals = media(directory, "original_(Vocals).wav", "separated", size=456)
    playback = media(directory, "playback.wav", "separated", playback=True)
    unknown = directory / "Vocals collection.wav"
    unknown.write_bytes(b"keep")
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    metadata.update(file_path=str(original), media_retention_policy="all")
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    database.get_task_store().update_status(
        task.id, task.status,
        result={"output_dir": str(directory), "vocals_path": str(vocals)},
    )
    preview = service.cleanup_separated(str(directory))
    assert selected(preview) == [vocals.name]
    assert vocals.exists()  # Preview is safe to run across a library.
    result = service.cleanup_separated(str(directory), dry_run=False)
    assert result["reclaimed_bytes"] == 456 and not result["errors"]
    assert not vocals.exists()
    assert all(path.exists() for path in (original, working, playback, unknown))
    saved = database.get_task_store().get(task.id)
    assert saved.result["vocals_path"] is None
    assert saved.completed_steps == task.completed_steps
    assert saved.status == TaskStatus.COMPLETED
    assert json.loads((directory / "metadata.json").read_text(encoding="utf-8"))["media_retention_policy"] == "all"
    assets = json.loads((directory / "media_assets.json").read_text(encoding="utf-8"))["assets"]
    assert vocals.name not in assets and original.name in assets
    assert service.cleanup_separated(str(directory), dry_run=False)["reclaimed_bytes"] == 0


@pytest.mark.parametrize(
    "status", [TaskStatus.PROCESSING, TaskStatus.PAUSED, TaskStatus.FAILED]
)
def test_separation_cleanup_preserves_unfinished_checkpoints(library, status):
    directory, service, _ = library
    completed_separation(directory, status=status)
    vocals = media(directory, "original_(Vocals).wav", "separated")
    result = service.cleanup_separated(str(directory), dry_run=False)
    assert result["protected_reason"] and not result["cleaned"]
    assert vocals.exists()


def test_separation_cleanup_requires_transcript_and_respects_uploads(library):
    directory, service, _ = library
    completed_separation(directory)
    vocals = media(directory, "original_(Vocals).wav", "separated")
    with uploading_media(directory):
        assert not service.cleanup_separated(str(directory), dry_run=False)["cleaned"]
    (directory / "transcript.srt").unlink()
    result = service.cleanup_separated(str(directory), dry_run=False)
    assert result["protected_reason"] == "转写结果尚未持久化"
    assert vocals.exists()


def test_separation_cleanup_keeps_only_remaining_media(library):
    directory, service, _ = library
    completed_separation(directory)
    (directory / "source.wav").unlink()
    vocals = media(directory, "original_(Vocals).wav", "separated")
    result = service.cleanup_separated(str(directory), dry_run=False)
    assert not result["cleaned"] and vocals.exists()
    # A persisted regenerate_from reference also protects externally owned inputs.
    external = directory.parent / "external.wav"
    external.write_bytes(b"original")
    record_media(directory, vocals, "separated", regenerate_from=external)
    assert selected(service.cleanup_separated(str(directory))) == [vocals.name]
    service.cleanup_separated(str(directory), dry_run=False)
    assert external.exists() and not vocals.exists()


def test_separation_cleanup_preserves_mislabeled_sources_and_links(library):
    directory, service, _ = library
    task = completed_separation(directory)
    source = media(directory, "original.wav", "separated")
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    metadata["file_path"] = str(source)
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    external = directory.parent / "external.wav"
    external.write_bytes(b"keep")
    linked = directory / "external_(Vocals).wav"
    linked.symlink_to(external)
    manifest = json.loads((directory / "media_assets.json").read_text(encoding="utf-8"))
    manifest["assets"][linked.name] = {"role": "separated"}
    (directory / "media_assets.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = service.cleanup_separated(str(directory), dry_run=False)
    assert not result["cleaned"]
    assert source.exists() and linked.is_symlink() and external.read_bytes() == b"keep"
    # The task source is protected independently of metadata and manifest roles.
    metadata.pop("file_path")
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    task.source = str(source)
    database.get_task_store().save(task)
    assert not service.cleanup_separated(str(directory), dry_run=False)["cleaned"]


def test_separation_cleanup_preserves_unknown_legacy_files(library):
    directory, service, _ = library
    completed_separation(directory)
    source = media(directory, "original.wav", "source")
    legacy = directory / "original_(Vocals).wav"
    legacy.write_bytes(b"unknown purpose")
    result = service.cleanup_separated(str(directory), dry_run=False)
    assert not result["cleaned"]
    assert source.exists() and legacy.exists()


def test_separation_cleanup_failure_keeps_other_candidates_retryable(library, monkeypatch):
    directory, service, _ = library
    completed_separation(directory)
    first = media(directory, "first.wav", "separated")
    second = media(directory, "second.wav", "separated")
    unlink = Path.unlink

    def fail_one(path, *args, **kwargs):
        if path == first:
            raise PermissionError("busy")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_one)
    result = service.cleanup_separated(str(directory), dry_run=False)
    assert first.exists() and not second.exists()
    assert result["reclaimed_bytes"] == 123
    assert len(result["errors"]) == 1
    monkeypatch.setattr(Path, "unlink", unlink)
    assert service.cleanup_separated(str(directory), dry_run=False)["reclaimed_bytes"] == 123


@pytest.mark.parametrize("reference_kind", ["windows", "long", "invalid", "missing"])
def test_separation_cleanup_tolerates_stale_foreign_and_invalid_references(library, reference_kind):
    if reference_kind == "windows" and sys.platform == "win32":
        pytest.skip("Windows metadata is a foreign path on POSIX")
    directory, service, _ = library
    task = completed_separation(directory)
    original = media(directory, "迁移会议" * 10 + ".mp4", "source", playback=True)
    working = media(directory, "original.wav", "working")
    vocals = media(directory, "original_(Vocals).wav", "separated")
    references = {
        "windows": "D:\\Video\\MediaProcessPipeline\\" + "迁移会议" * 40 + "\\" + original.name,
        "long": "/unavailable/" + "迁移会议" * 100 + ".mp4",
        "invalid": "/unavailable/invalid\0.mp4",
        "missing": "/unavailable/source.mp4",
    }
    stale = references[reference_kind]
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    metadata.update(file_path=stale, source_url=stale)
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    task.source = stale
    task.result.update(
        vocals_path=str(vocals),
        metadata={"file_path": stale},
        archive={"files": {"old_source": stale, "vocals": str(vocals)}},
    )
    database.get_task_store().save(task)
    manifest = json.loads((directory / "media_assets.json").read_text(encoding="utf-8"))
    manifest["assets"][vocals.name]["regenerate_from"] = stale
    manifest["assets"][stale] = {"role": "working", "regenerate_from": stale}
    (directory / "media_assets.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert selected(service.cleanup_separated(str(directory))) == [vocals.name]
    result = service.cleanup_separated(str(directory), dry_run=False)
    assert not result["errors"] and result["reclaimed_bytes"] == 123
    assert original.exists() and working.exists() and not vocals.exists()
    saved = database.get_task_store().get(task.id)
    assert saved.result["vocals_path"] is None
    assert saved.result["archive"]["files"]["vocals"] is None
    assert saved.result["metadata"]["file_path"] == stale
    assert saved.result["archive"]["files"]["old_source"] == stale
    assert json.loads((directory / "metadata.json").read_text(encoding="utf-8"))["file_path"] == stale


@pytest.mark.skipif(sys.platform == "win32", reason="Windows metadata is foreign on POSIX")
def test_windows_source_reference_protects_mislabeled_original(library):
    directory, service, _ = library
    completed_separation(directory)
    source = media(directory, "original.wav", "separated")
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    metadata["file_path"] = "D:\\Video\\" + "旧文件夹" * 80 + "\\original.wav"
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    assert not service.cleanup_separated(str(directory), dry_run=False)["cleaned"]
    assert source.exists()


def test_invalid_task_source_does_not_hide_unfinished_archive_owner(library):
    directory, service, _ = library
    task = completed_separation(directory, status=TaskStatus.FAILED)
    task.source = "D:\\Video\\" + "旧文件夹" * 100 + "\\original.wav"
    database.get_task_store().save(task)
    vocals = media(directory, "original_(Vocals).wav", "separated")
    result = service.cleanup_separated(str(directory), dry_run=False)
    assert result["protected_reason"] and not result["cleaned"] and vocals.exists()


def test_completed_cleanup_removes_extracted_audio_and_segments_with_video_preserved(library):
    from app.core.media_retention import cleanup_completed_task

    directory, service, _ = library
    task = completed_separation(directory)
    video = media(directory, "original.mp4", "source", playback=True)
    working = media(directory, "original.wav", "working", size=456)
    vocals = media(directory, "original_(Vocals).wav", "separated", size=789)
    segment = media(directory, "segment_000.wav", "segment", size=42)
    unknown = directory / "unclassified.wav"
    unknown.write_bytes(b"keep")
    task.result.update(audio_path=str(working), vocals_path=str(vocals),
                       archive={"files": {"segment": str(segment), "video": str(video)}})
    database.get_task_store().save(task)
    expected = {working.name, vocals.name, segment.name}
    assert set(selected(service.cleanup_intermediates(str(directory)))) == expected
    cleanup_completed_task(task)
    assert all(not path.exists() for path in (working, vocals, segment))
    assert video.exists() and unknown.exists() and (directory / "source.wav").exists()
    assert task.result["audio_path"] is None and task.result["vocals_path"] is None
    assert task.result["archive"]["files"] == {"segment": None, "video": str(video)}
    assert service.cleanup_intermediates(str(directory), dry_run=False)["reclaimed_bytes"] == 0


def test_intermediates_cannot_justify_deleting_each_other(library):
    directory, service, _ = library
    completed_separation(directory)
    (directory / "source.wav").unlink()
    working = media(directory, "working.wav", "working")
    other = media(directory, "other.wav", "working")
    vocals = media(directory, "vocals.wav", "separated")
    record_media(directory, vocals, "separated", regenerate_from=working)
    result = service.cleanup_intermediates(str(directory), dry_run=False)
    assert not result["cleaned"]
    assert all(path.exists() for path in (working, other, vocals))


@pytest.mark.parametrize("status", [TaskStatus.PROCESSING, TaskStatus.PAUSED, TaskStatus.FAILED])
def test_intermediate_cleanup_keeps_unfinished_audio(library, status):
    directory, service, _ = library
    completed_separation(directory, status=status)
    working = media(directory, "working.wav", "working")
    assert not service.cleanup_intermediates(str(directory), dry_run=False)["cleaned"]
    assert working.exists()


def test_intermediate_cleanup_protects_playback_audio_and_uploads(library):
    directory, service, _ = library
    completed_separation(directory)
    playback = media(directory, "playback.wav", "working", playback=True)
    working = media(directory, "working.wav", "working")
    with uploading_media(directory):
        assert not service.cleanup_intermediates(str(directory), dry_run=False)["cleaned"]
    (directory / "transcript.srt").unlink()
    assert not service.cleanup_intermediates(str(directory), dry_run=False)["cleaned"]
    assert playback.exists() and working.exists()
