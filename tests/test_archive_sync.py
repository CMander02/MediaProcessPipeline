from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.api.routes import sync as sync_route  # noqa: E402
from app.core import database, settings as settings_module  # noqa: E402
from app.core.archive_sync import get_archive_sync_service  # noqa: E402
from app.core.settings import RuntimeSettings  # noqa: E402


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
    assert all(change["operation"] == "upsert" for change in first_page["changes"] + page_two["changes"])

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
    (archive / "transcript_polished.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n你好", encoding="utf-8")
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

    image_entry = next(entry for entry in manifest["files"] if entry["relative_path"] == "images/00.jpg")
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
