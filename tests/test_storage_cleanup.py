from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api.routes import pipeline
from app.core import database, settings
from app.models import Task, TaskStatus, TaskType
from app.services.cleanup import CleanupService


@pytest.fixture
def storage(tmp_path, monkeypatch):
    rt = settings.RuntimeSettings(data_root=str(tmp_path))
    monkeypatch.setattr(settings, "_runtime_settings", rt)
    monkeypatch.setattr(pipeline, "get_runtime_settings", lambda: rt)
    database.reset_db_path(tmp_path)
    yield tmp_path, database.get_task_store(), CleanupService()
    database.close_db()


def save_task(store, directory=None, *, source="test.mp4", status=TaskStatus.FAILED):
    task = Task(
        task_type=TaskType.PIPELINE,
        source=source,
        status=status,
        result={"output_dir": str(directory)} if directory else None,
    )
    store.save(task)
    return task


def stale(directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "audio.wav").write_bytes(b"source audio")
    old = time.time() - 72 * 3600
    os.utime(directory / "audio.wav", (old, old))
    os.utime(directory, (old, old))
    return directory


def client():
    app = FastAPI()
    app.include_router(pipeline.router, prefix="/api")
    return TestClient(app)


@pytest.mark.parametrize("name", ["", "auth", "voiceprints", "logs", "tmp", "backups"])
def test_failed_cleanup_protects_root_and_system_directories(storage, name):
    root, store, cleanup = storage
    target = root / name
    target.mkdir(exist_ok=True)
    marker = target / "keep.txt"
    marker.write_text("keep")
    task = save_task(store, target)

    result = cleanup.cleanup_failed_task(str(task.id))

    assert result["errors"]
    assert result["cleaned"] == []
    assert marker.read_text() == "keep"


def test_orphan_cleanup_only_removes_unreferenced_staging(storage):
    root, _store, cleanup = storage
    protected = [stale(root / name) for name in ("auth", "voiceprints", "logs", "unknown")]
    candidate = stale(root / "_staging" / uuid4().hex)
    preview = cleanup.cleanup_orphaned_files(dry_run=True)

    assert [item["path"] for item in preview["candidates"]] == [str(candidate)]
    assert candidate.exists()
    assert str(root / "unknown") in preview["unclassified"]
    result = cleanup.cleanup_orphaned_files()
    assert result["candidates"] == preview["candidates"]
    assert result["cleaned"] == [str(candidate)]
    assert not candidate.exists()
    assert all(path.exists() for path in protected)


def test_staging_sweeper_protects_source_of_early_task(storage):
    root, store, cleanup = storage
    protected = stale(root / "_staging" / uuid4().hex)
    save_task(store, source=str(protected / "audio.wav"), status=TaskStatus.QUEUED)
    for _ in range(1001):
        save_task(store)

    result = cleanup.cleanup_orphaned_files()

    assert result["cleaned"] == []
    assert pipeline.sweep_stale_staging() == 0
    assert (protected / "audio.wav").exists()


def test_failed_cleanup_handles_nested_result_and_shared_active_directory(storage):
    root, store, cleanup = storage
    target = stale(root / "访谈 🎧")
    task = save_task(store)
    store.update_status(task.id, TaskStatus.FAILED, result={"archive": {"output_dir": str(target)}})
    active = save_task(store, target, status=TaskStatus.PAUSED)
    assert cleanup.cleanup_failed_task(str(task.id))["errors"]
    assert target.exists()
    store.delete(active.id)
    result = cleanup.cleanup_failed_task(str(task.id))
    assert result["cleaned"] == [str(target)]
    assert not target.exists()


@pytest.mark.parametrize("name", ["", "auth", "voiceprints", "unregistered"])
def test_archive_api_rejects_unrecognized_or_system_directory(storage, name):
    root, _store, _cleanup = storage
    target = root / name
    target.mkdir(exist_ok=True)
    response = client().request("DELETE", "/api/pipeline/archives", json={"path": str(target)})
    assert response.status_code in (400, 403)
    assert target.exists()


def test_archive_api_accepts_metadata_and_protects_paused_task(storage):
    root, store, _cleanup = storage
    target = stale(root / "访谈 🎧")
    (target / "metadata.json").write_text(json.dumps({"title": "访谈"}), encoding="utf-8")
    task = save_task(store, target, status=TaskStatus.PAUSED)
    response = client().request("DELETE", "/api/pipeline/archives", json={"path": str(target)})
    assert response.status_code == 409
    assert target.exists()
    store.delete(task.id)
    response = client().request("DELETE", "/api/pipeline/archives", json={"path": str(target)})
    assert response.status_code == 200
    assert not target.exists()


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_cleanup_rejects_linked_archive(storage, tmp_path_factory, kind):
    root, store, cleanup = storage
    external = stale(tmp_path_factory.mktemp("external"))
    link = root / "linked"
    try:
        if kind == "junction":
            if os.name != "nt":
                pytest.skip("Windows junction")
            import _winapi
            _winapi.CreateJunction(str(external), str(link))
        else:
            link.symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory link unavailable: {exc}")
    task = save_task(store, link)
    result = cleanup.cleanup_failed_task(str(task.id))
    assert result["errors"]
    assert (external / "audio.wav").exists()


def test_delete_staged_protects_submitted_source(storage):
    root, store, _cleanup = storage
    target = stale(root / "_staging" / uuid4().hex)
    save_task(store, source=str(target / "audio.wav"), status=TaskStatus.PENDING)
    response = client().delete(f"/api/pipeline/stage/{target.name}")
    assert response.status_code == 409
    assert target.exists()


def test_cleanup_rechecks_active_owners_after_preview_calculation(storage, monkeypatch):
    root, store, cleanup = storage
    target = stale(root / "archive")
    task = save_task(store, target)

    def size_and_resume(_path):
        save_task(store, target, status=TaskStatus.PROCESSING)
        return 12

    monkeypatch.setattr(cleanup, "_directory_size", size_and_resume)
    assert cleanup.cleanup_failed_task(str(task.id))["errors"]
    assert target.exists()


def test_cleanup_rejects_junction_to_another_managed_archive(storage):
    if os.name != "nt":
        pytest.skip("Windows junction")
    import _winapi

    root, store, cleanup = storage
    target = stale(root / "real_archive")
    link = root / "alias"
    _winapi.CreateJunction(str(target), str(link))
    task = save_task(store, link)
    assert cleanup.cleanup_failed_task(str(task.id))["errors"]
    assert (target / "audio.wav").exists()


def test_valid_archive_path_accepts_windows_case_variants(storage):
    if os.name != "nt":
        pytest.skip("Windows case-insensitive paths")
    root, store, cleanup = storage
    target = stale(root / "Archive")
    task = save_task(store, str(target).upper())
    result = cleanup.cleanup_failed_task(str(task.id), dry_run=True)
    assert result["errors"] == []
    assert len(result["candidates"]) == 1
    assert target.exists()
