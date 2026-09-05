from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.api.routes import sync as sync_route  # noqa: E402
from app.core import database  # noqa: E402
from app.core import settings as settings_module  # noqa: E402
from app.core.archive_sync import (  # noqa: E402
    build_archive_zip,
    get_archive_sync_service,
    safe_extract_zip,
)
from app.core.settings import RuntimeSettings  # noqa: E402
from app.models import Task, TaskStatus, TaskType  # noqa: E402
from app.services.archiving.archive import get_archive_service  # noqa: E402


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    runtime = RuntimeSettings(data_root=str(tmp_path), api_token="")
    monkeypatch.setattr(settings_module, "_runtime_settings", runtime)
    database.reset_db_path(tmp_path)
    app = FastAPI()
    app.include_router(sync_route.router, prefix="/api")
    return TestClient(app)


def _archive(tmp_path: Path, name: str, title: str, archive_id: str | None = None) -> Path:
    archive_dir = tmp_path / name
    archive_dir.mkdir()
    metadata = {"title": title, "platform": "webpage", "status": "completed"}
    if archive_id:
        metadata["archive_id"] = archive_id
    (archive_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
    )
    (archive_dir / "summary.md").write_text(f"# {title}\n", encoding="utf-8")
    return archive_dir


def test_sync_changes_are_revisioned_paginated_and_tombstoned(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    first_id = str(uuid4())
    first = _archive(tmp_path, "first", "第一份", first_id)
    _archive(tmp_path, "second", "第二份")

    page_one = client.get("/api/sync/changes", params={"cursor": 0, "limit": 1})
    assert page_one.status_code == 200
    first_page = page_one.json()
    assert len(first_page["changes"]) == 1
    assert first_page["has_more"] is True
    assert first_page["next_cursor"] == 1

    page_two = client.get(
        "/api/sync/changes",
        params={"cursor": first_page["next_cursor"], "limit": 10},
    ).json()
    assert len(page_two["changes"]) == 1
    assert page_two["has_more"] is False
    assert page_two["server_revision"] == 2
    all_changes = first_page["changes"] + page_two["changes"]
    assert all(change["operation"] == "upsert" for change in all_changes)

    metadata = json.loads((first / "metadata.json").read_text(encoding="utf-8"))
    metadata["title"] = "第一份（已更新）"
    (first / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    update = client.get("/api/sync/changes", params={"cursor": 2, "limit": 10}).json()
    assert update["changes"][0]["archive_id"] == first_id
    assert update["changes"][0]["archive"]["title"] == "第一份（已更新）"
    assert update["changes"][0]["revision"] == 3

    for child in first.iterdir():
        child.unlink()
    first.rmdir()
    deleted = client.get("/api/sync/changes", params={"cursor": 3, "limit": 10}).json()
    assert len(deleted["changes"]) == 1
    assert deleted["changes"][0]["revision"] == 4
    assert deleted["changes"][0]["archive_id"] == first_id
    assert deleted["changes"][0]["operation"] == "delete"
    assert "archive" not in deleted["changes"][0]


def test_sync_manifest_filters_media_and_serves_etag(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    archive_id = str(uuid4())
    archive = _archive(tmp_path, "rich", "同步资料", archive_id)
    (archive / "transcript_polished.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好",
        encoding="utf-8",
    )
    (archive / "video.mp4").write_bytes(b"media")
    (archive / ".browser.mp3").write_bytes(b"browser-media")
    (archive / "random.jpg").write_bytes(b"not-a-cover")
    (archive / "cover.webp").write_bytes(b"cover")
    images = archive / "images"
    images.mkdir()
    (images / "00.jpg").write_bytes(b"image-zero")
    descriptions = archive / "descriptions"
    descriptions.mkdir()
    (descriptions / "00.md").write_text("图片说明", encoding="utf-8")
    work = archive / "work"
    work.mkdir()
    (work / "secret.json").write_text("{}", encoding="utf-8")

    changes = client.get("/api/sync/changes").json()
    assert changes["changes"][0]["archive_id"] == archive_id
    manifest_response = client.get(f"/api/sync/archives/{archive_id}/manifest")
    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    paths = {entry["relative_path"] for entry in manifest["files"]}
    assert paths >= {
        "metadata.json",
        "summary.md",
        "transcript_polished.srt",
        "cover.webp",
        "images/00.jpg",
        "descriptions/00.md",
    }
    assert "video.mp4" not in paths
    assert ".browser.mp3" not in paths
    assert "random.jpg" not in paths
    assert "work/secret.json" not in paths

    image_entry = next(
        entry for entry in manifest["files"] if entry["relative_path"] == "images/00.jpg"
    )
    assert image_entry["sha256"] == hashlib.sha256(b"image-zero").hexdigest()
    downloaded = client.get(f"/api/sync/archives/{archive_id}/files/images/00.jpg")
    assert downloaded.status_code == 200
    assert downloaded.content == b"image-zero"
    assert downloaded.headers["etag"] == f'"{image_entry["sha256"]}"'
    assert downloaded.headers["cache-control"] == "private, no-cache"

    cached = client.get(
        f"/api/sync/archives/{archive_id}/files/images/00.jpg",
        headers={"If-None-Match": downloaded.headers["etag"]},
    )
    assert cached.status_code == 304
    assert client.get(f"/api/sync/archives/{archive_id}/files/video.mp4").status_code == 404


def test_sync_rejects_path_escape_and_symlinks(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    archive_id = str(uuid4())
    archive = _archive(tmp_path, "safe", "安全资料", archive_id)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        link = archive / "linked.txt"
        try:
            os.symlink(outside, link)
        except OSError:
            link = None

        service = get_archive_sync_service()
        service.reconcile()
        assert service.resolve_declared_file(archive_id, "/metadata.json") is None
        assert service.resolve_declared_file(archive_id, "../outside.txt") is None
        assert service.resolve_declared_file(archive_id, "images/../../metadata.json") is None
        if link is not None:
            assert "linked.txt" not in {
                entry["relative_path"] for entry in service.manifest(archive_id)["files"]
            }
    finally:
        outside.unlink(missing_ok=True)


def test_sync_rebuild_recreates_current_index(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _archive(tmp_path, "one", "一")
    _archive(tmp_path, "two", "二")

    response = client.post("/api/sync/rebuild")

    assert response.status_code == 200
    assert response.json() == {"archives": 2, "revision": 2}


def test_sync_migrates_legacy_gb18030_metadata(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    archive = tmp_path / "legacy-encoding"
    archive.mkdir()
    legacy_metadata = {
        "title": "旧编码资料",
        "platform": "webpage",
        "status": "completed",
    }
    (archive / "metadata.json").write_bytes(
        json.dumps(legacy_metadata, ensure_ascii=False).encode("gb18030")
    )
    (archive / "summary.md").write_text("# 旧编码资料\n", encoding="utf-8")

    response = client.get("/api/sync/changes")

    assert response.status_code == 200
    change = response.json()["changes"][0]
    assert change["archive"]["title"] == "旧编码资料"
    migrated = json.loads((archive / "metadata.json").read_text(encoding="utf-8"))
    assert migrated["title"] == "旧编码资料"
    assert migrated["archive_id"] == change["archive_id"]


def test_sync_preserves_id_on_move_and_repairs_copied_id(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    archive_id = str(uuid4())
    original = _archive(tmp_path, "original", "原始资料", archive_id)
    initial = client.get("/api/sync/changes").json()
    assert initial["server_revision"] == 1

    moved = tmp_path / "moved"
    original.rename(moved)
    moved_change = client.get("/api/sync/changes", params={"cursor": 1}).json()
    assert moved_change["changes"][0]["archive_id"] == archive_id
    assert moved_change["changes"][0]["archive"]["path"] == str(moved)

    copied = tmp_path / "copied"
    shutil.copytree(moved, copied)
    copied_changes = client.get(
        "/api/sync/changes",
        params={"cursor": moved_change["next_cursor"]},
    ).json()
    copied_ids = {change["archive_id"] for change in copied_changes["changes"]}
    assert archive_id not in copied_ids
    assert len(copied_ids) == 1
    copied_metadata = json.loads((copied / "metadata.json").read_text(encoding="utf-8"))
    assert copied_metadata["archive_id"] in copied_ids


def test_completed_archive_import_is_idempotent_and_excludes_media(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    source = _archive(tmp_path, "local-source", "本地结果")
    (source / "transcript.srt").write_text("字幕", encoding="utf-8")
    (source / "source.mp4").write_bytes(b"video")
    zip_path = tmp_path / "portable.zip"
    build_archive_zip(source, zip_path, include_media=False)
    sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    task = Task(
        task_type=TaskType.PIPELINE,
        status=TaskStatus.COMPLETED,
        source="https://example.com/video",
        progress=1.0,
        result={"metadata": {"title": "本地结果"}},
    )
    store = database.get_task_store()
    store.save(task)
    store.save_artifact(task.id, "operator-notes.md", "Keep the existing review")
    store.add_event(task.id, "queued", message="Original task history")
    original_event_id = store.list_events(task.id)[0]["id"]

    def upload():
        with zip_path.open("rb") as archive:
            return client.post(
                "/api/sync/import",
                data={
                    "task_json": task.model_dump_json(),
                    "archive_name": "来自本地",
                    "archive_sha256": sha256,
                    "worker_id": "desktop-test",
                },
                files={"archive": ("archive.zip", archive, "application/zip")},
            )

    response = upload()
    assert response.status_code == 200
    assert response.json()["already_synced"] is False
    imported = database.get_task_store().get(task.id)
    destination = Path(imported.result["output_dir"])
    assert (destination / "metadata.json").is_file()
    assert (destination / "transcript.srt").is_file()
    assert not (destination / "source.mp4").exists()
    assert imported.result["remote_sync"]["archive_sha256"] == sha256
    assert store.get_artifact(task.id, "operator-notes.md")["content"] == "Keep the existing review"
    first_events = store.list_events(task.id)
    assert any(event["id"] == original_event_id for event in first_events)

    repeated = upload()
    assert repeated.status_code == 200
    assert repeated.json()["already_synced"] is True
    assert store.list_events(task.id) == first_events


def test_portable_archive_rejects_path_traversal(tmp_path):
    zip_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("../outside.txt", "outside")
        archive.writestr("metadata.json", "{}")

    try:
        safe_extract_zip(zip_path, tmp_path / "extract")
    except ValueError as exc:
        assert "unsafe archive member path" in str(exc)
    else:
        raise AssertionError("unsafe ZIP member was accepted")


def test_task_archive_export_excludes_media_by_default(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    archive = _archive(tmp_path, "export-source", "待传输任务")
    (archive / "transcript.srt").write_text("字幕", encoding="utf-8")
    (archive / "source.mp4").write_bytes(b"video")
    task = Task(
        task_type=TaskType.PIPELINE,
        status=TaskStatus.COMPLETED,
        source="https://example.com/video",
        progress=1.0,
        result={"output_dir": str(archive)},
    )
    database.get_task_store().save(task)

    response = client.get(f"/api/sync/tasks/{task.id}/archive")

    assert response.status_code == 200
    assert response.headers["x-mpp-include-media"] == "false"
    zip_path = tmp_path / "exported.zip"
    zip_path.write_bytes(response.content)
    with zipfile.ZipFile(zip_path) as exported:
        names = set(exported.namelist())
    assert "metadata.json" in names
    assert "transcript.srt" in names
    assert "source.mp4" not in names


def test_portable_archive_keeps_declared_video_task_type_without_media(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    archive = _archive(tmp_path, "video-without-media", "视频任务")
    metadata_path = archive / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["media_type"] = "video"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

    item = get_archive_service().get_archive(archive, lite=True)

    assert item["has_video"] is True
    assert item["has_audio"] is False
    assert item["media_file"] is None
    assert item["media_is_external"] is True


def test_portable_archive_keeps_declared_audio_task_type_without_media(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    archive = _archive(tmp_path, "audio-without-media", "音频任务")
    metadata_path = archive / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["media_type"] = "audio"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

    item = get_archive_service().get_archive(archive, lite=True)

    assert item["has_video"] is False
    assert item["has_audio"] is True
    assert item["media_file"] is None
    assert item["media_is_external"] is True
