from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api.routes import filesystem
from app.core import database, settings
from app.core.artifacts import ArtifactMirrorError, get_artifact_store
from app.models import MediaMetadata, Task, TaskStatus, TaskType
from app.services.archiving.archive import get_archive_service


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "_runtime_settings", settings.RuntimeSettings(data_root=str(tmp_path))
    )
    database.reset_db_path(tmp_path)
    directory = tmp_path / "访谈 🎙"
    directory.mkdir()
    task = Task(
        task_type=TaskType.PIPELINE,
        source="test",
        status=TaskStatus.COMPLETED,
        result={"output_dir": str(directory)},
    )
    database.get_task_store().save(task)
    yield task, directory, get_artifact_store()
    database.close_db()


def test_database_failure_preserves_primary_file_and_can_be_repaired(library):
    task, directory, artifacts = library
    artifacts.write(task.id, directory, "summary.md", "old")
    conn = database._get_conn()
    conn.execute(
        "CREATE TRIGGER fail_copy BEFORE UPDATE ON task_artifacts "
        "BEGIN SELECT RAISE(ABORT, 'copy failed'); END"
    )
    conn.commit()
    with pytest.raises(ArtifactMirrorError, match="File saved"):
        artifacts.write(task.id, directory, "summary.md", "new")
    assert (directory / "summary.md").read_text(encoding="utf-8") == "new"
    assert artifacts.read(task.id, directory, "summary.md")["content"] == "new"
    assert artifacts.inspect(task.id, directory) == [
        {"filename": "summary.md", "state": "mirror_stale"}
    ]
    assert not conn.in_transaction
    conn.execute("DROP TRIGGER fail_copy")
    conn.commit()
    assert artifacts.repair(task.id, directory)[0]["state"] == "synced"
    assert database.get_task_store().get_artifact(task.id, "summary.md")["content"] == "new"


def test_corrupt_file_is_reported_and_preserved(library):
    task, directory, artifacts = library
    artifacts.write(task.id, directory, "summary.json", '{"title":"valid"}')
    path = directory / "summary.json"
    damaged = b'{"title":'
    path.write_bytes(damaged)
    with pytest.raises(ValueError, match="Invalid JSON"):
        artifacts.read(task.id, directory, path.name)
    assert artifacts.repair(task.id, directory)[0]["state"] == "invalid"
    with pytest.raises(FileExistsError):
        artifacts.restore_file(task.id, directory, path.name)
    assert path.read_bytes() == damaged
    assert (
        database.get_task_store().get_artifact(task.id, path.name)["content"] == '{"title":"valid"}'
    )


def test_file_restore_and_nested_artifacts(library):
    task, directory, artifacts = library
    filename = "descriptions/00.md"
    path = artifacts.write(task.id, directory, filename, "picture description")
    path.unlink()
    assert artifacts.read(task.id, directory, filename)["source"] == "sqlite"
    assert artifacts.inspect(task.id, directory)[0]["state"] == "file_missing"
    assert artifacts.restore_file(task.id, directory, filename).read_text() == "picture description"


def test_concurrent_writes_leave_the_same_file_and_copy(library):
    task, directory, artifacts = library
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(artifacts.write, task.id, directory, "summary.md", f"version {i}")
            for i in range(20)
        ]
        for future in futures:
            future.result()
    assert artifacts.inspect(task.id, directory)[0]["state"] == "synced"


def test_api_edits_create_missing_copy_and_restore_file(library):
    task, directory, artifacts = library
    app = FastAPI()
    app.include_router(filesystem.router, prefix="/api")
    with TestClient(app) as client:
        path = directory / "summary.md"
        response = client.post(
            "/api/filesystem/write", json={"path": str(path), "content": "edited"}
        )
        assert response.json()["mirror_saved"] is True
        assert database.get_task_store().get_artifact(task.id, path.name)["content"] == "edited"
        path.unlink()
        response = client.get("/api/filesystem/read", params={"path": str(path)})
        assert response.json()["source"] == "sqlite"
        restored = client.post(
            "/api/filesystem/artifacts/repair",
            json={
                "task_id": str(task.id),
                "filename": path.name,
                "action": "restore-file",
            },
        )
        assert restored.json()["success"] is True
        assert path.read_text() == "edited"


def test_archive_finalization_mirrors_all_generated_text(library):
    task, directory, artifacts = library
    get_archive_service().archive(
        MediaMetadata(title="Interview"),
        task_id=str(task.id),
        work_dir=directory,
        original_srt="1\n00:00:01,000 --> 00:00:02,000\nHello\n",
        polished_srt="1\n00:00:01,000 --> 00:00:02,000\nHello there\n",
        summary={"tldr": "Summary", "key_facts": ["Fact"]},
        analysis={"topics": ["Topic"]},
        mindmap="- Root\n  - Child",
    )
    report = artifacts.inspect(task.id, directory)
    assert len(report) == 9
    assert {item["state"] for item in report} == {"synced"}


def test_output_lookup_includes_tasks_older_than_ten_thousand(library):
    task, directory, artifacts = library
    artifacts.write(task.id, directory, "summary.md", "old task")
    conn = database._get_conn()
    conn.executemany(
        "INSERT INTO tasks(id, task_type, source, created_at, updated_at) "
        "VALUES (?, 'pipeline', 'new', '2099-01-01', '2099-01-01')",
        [(str(uuid4()),) for _ in range(10001)],
    )
    conn.commit()
    found = database.get_task_store().get_artifact_by_output_dir(directory, "summary.md")
    assert found["content"] == "old task"


def test_api_reports_saved_file_when_copy_fails(library, monkeypatch):
    task, directory, artifacts = library
    app = FastAPI()
    app.include_router(filesystem.router, prefix="/api")

    def fail(*args):
        raise OSError("database unavailable")

    monkeypatch.setattr(database.get_task_store(), "save_artifact", fail)
    with TestClient(app) as client:
        response = client.post("/api/filesystem/write", json={
            "path": str(directory / "summary.md"), "content": "keep this edit",
        }).json()
    assert response["success"] is False
    assert response["file_saved"] is True
    assert response["repair_needed"] is True
    assert (directory / "summary.md").read_text() == "keep this edit"
