from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api.routes import sync as sync_route
from app.core import archive_sync, database, queue, settings
from app.core.artifacts import get_artifact_store
from app.core.paths import get_workspace_paths
from app.models import Task, TaskStatus, TaskType


def _files(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}


def _rows():
    return {name: [tuple(row) for row in database._get_conn().execute(f"SELECT * FROM {name} ORDER BY rowid")]
            for name in ("tasks", "task_artifacts", "task_events", "archive_sync_index",
                         "archive_sync_changes", "archive_sync_meta")}


@pytest.fixture
def imported(tmp_path, monkeypatch):
    root = tmp_path / "资料库"
    monkeypatch.setattr(settings, "_runtime_settings", settings.RuntimeSettings(data_root=str(root)))
    monkeypatch.setattr(queue, "_task_queue", queue.TaskQueue())
    database.reset_db_path(root)
    database.init_db()
    destination = get_workspace_paths(root).archives / "existing"
    destination.mkdir(parents=True)
    task = Task(task_type=TaskType.PIPELINE, status=TaskStatus.COMPLETED,
                source="https://example.com/original", result={"output_dir": str(destination)})
    (destination / "metadata.json").write_text(json.dumps({"title": "Old", "task_id": str(task.id),
            "archive_id": str(task.id), "status": "completed"}), encoding="utf-8")
    (destination / "summary.md").write_text("old summary", encoding="utf-8")
    (destination / "video.mp4").write_bytes(b"original media")
    (destination / "images").mkdir()
    (destination / "images" / "0.png").write_bytes(b"original image")
    store = database.get_task_store()
    store.save(task)
    get_artifact_store().repair(task.id, destination)
    store.save_artifact(task.id, "operator-notes.md", "local review")
    store.add_event(task.id, "original", message="old history")
    archive_sync.get_archive_sync_service().flush_changes()
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    (incoming / "metadata.json").write_text(json.dumps({"title": "New", "status": "completed"}), encoding="utf-8")
    (incoming / "summary.md").write_text("new summary", encoding="utf-8")
    (incoming / "analysis.json").write_text('{"new":true}', encoding="utf-8")
    (incoming / "video.mp4").write_bytes(b"new media")
    zip_path = tmp_path / "incoming.zip"
    archive_sync.build_archive_zip(incoming, zip_path, include_media=True)
    app = FastAPI()
    app.include_router(sync_route.router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=False)

    def upload():
        return client.post("/api/sync/import", data={"task_json": task.model_dump_json(),
            "archive_name": "existing"}, files={"archive": ("archive.zip", zip_path.read_bytes(), "application/zip")})

    yield root, task, destination, incoming, client, upload
    database.close_db()


@pytest.mark.parametrize("status", ["pending", "queued", "processing", "paused"])
def test_import_rejects_active_task_without_replacing_any_files(imported, status):
    root, task, destination, _, _, upload = imported
    database.get_task_store().update_status(task.id, TaskStatus(status))
    before_files, before_rows = _files(destination), _rows()
    response = upload()
    assert response.status_code == 409
    assert _files(destination) == before_files
    assert _rows() == before_rows


def test_import_rechecks_task_after_upload(imported, monkeypatch):
    _, task, destination, _, _, upload = imported
    original = sync_route.stream_upload_to_path

    async def raced_upload(*args, **kwargs):
        result = await original(*args, **kwargs)
        database.get_task_store().update_status(task.id, TaskStatus.PROCESSING)
        return result

    monkeypatch.setattr(sync_route, "stream_upload_to_path", raced_upload)
    before = _files(destination)
    assert upload().status_code == 409
    assert _files(destination) == before
    assert database.get_task_store().get(task.id).status == TaskStatus.PROCESSING


def test_import_rejects_completed_task_with_draining_worker(imported):
    _, task, destination, _, _, upload = imported
    queue.get_task_queue()._active_download_ids.add(task.id)
    before = _files(destination)
    assert upload().status_code == 409
    assert _files(destination) == before


@pytest.mark.parametrize("failure", ["task", "mirrors", "index", "commit"])
@pytest.mark.parametrize("new_task", [False, True], ids=["replace", "new"])
def test_failed_import_restores_all_files_and_all_database_state(imported, monkeypatch, failure, new_task):
    root, task, _, _, _, upload = imported
    if new_task:
        task.id = uuid4()
        task.result = {}
    archives = get_workspace_paths(root).archives
    before_files, before_rows = _files(archives), _rows()

    def fail(*args, **kwargs):
        raise OSError("injected import failure")

    if failure == "task":
        monkeypatch.setattr(database, "_save_task_row", fail)
    elif failure == "mirrors":
        original = get_artifact_store().import_mirrors

        def partial_mirrors(*args, **kwargs):
            original(*args, **kwargs)
            fail()

        monkeypatch.setattr(get_artifact_store(), "import_mirrors", partial_mirrors)
    elif failure == "index":
        service = archive_sync.get_archive_sync_service()
        original = service._upsert_if_changed

        def partial_index(*args, **kwargs):
            original(*args, **kwargs)
            fail()

        monkeypatch.setattr(service, "_upsert_if_changed", partial_index)
    else:
        connection = database._get_conn()

        class FailCommit:
            def __getattr__(self, name):
                return fail if name == "commit" else getattr(connection, name)

        monkeypatch.setattr(database, "_connection", FailCommit())
    response = upload()
    assert response.status_code == 500
    assert _files(archives) == before_files
    assert _rows() == before_rows
    assert not list((get_workspace_paths(root).backups / "sync-imports").glob("*/journal.json"))


def test_failed_directory_publication_restores_existing_archive(imported, monkeypatch):
    root, _, destination, _, _, upload = imported
    before_files, before_rows = _files(destination), _rows()
    original = archive_sync.os.replace

    def failed_move(source, target):
        if Path(target) == destination and Path(source).name != "previous":
            raise OSError("cannot publish new directory")
        original(source, target)

    monkeypatch.setattr(archive_sync.os, "replace", failed_move)
    assert upload().status_code == 500
    assert _files(destination) == before_files
    assert _rows() == before_rows
    assert not list((get_workspace_paths(root).backups / "sync-imports").glob("*/journal.json"))


def test_workspace_switch_recovers_interrupted_import_before_use(imported, monkeypatch):
    from app.core import logging_setup, workspace_lifecycle

    root, task, destination, incoming, _, _ = imported
    before_files, before_rows = _files(destination), _rows()
    archive_sync.publish_archive(incoming, destination, root, task_id=task.id, import_id=str(uuid4()))
    monkeypatch.setattr(logging_setup, "relocate_log_file", lambda *_: None)
    workspace_lifecycle.reset_workspace_stores(root)
    assert _files(destination) == before_files
    assert _rows() == before_rows


def test_committed_import_keeps_journal_when_backup_cleanup_fails(imported, monkeypatch):
    root, task, destination, _, _, upload = imported
    original = archive_sync.shutil.rmtree

    def cannot_remove_backup(path, *args, **kwargs):
        if Path(path).name == "previous":
            raise PermissionError("backup file is open")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(archive_sync.shutil, "rmtree", cannot_remove_backup)
        response = upload()
        assert response.status_code == 200
        assert (destination / "summary.md").read_text() == "new summary"
        assert database.get_task_store().get_artifact(task.id, "summary.md")["content"] == "new summary"
        assert len(list((get_workspace_paths(root).backups / "sync-imports").glob("*/journal.json"))) == 1
    archive_sync.recover_archive_imports(root)
    assert (destination / "summary.md").read_text() == "new summary"
    assert not list((get_workspace_paths(root).backups / "sync-imports").glob("*/journal.json"))


def test_exception_after_sqlite_commit_keeps_committed_files_and_mirrors(imported, monkeypatch):
    _, task, destination, _, _, upload = imported
    connection = database._get_conn()

    class FailAfterCommit:
        def __getattr__(self, name):
            return getattr(connection, name)

        def commit(self):
            connection.commit()
            raise OSError("interrupted immediately after commit")

    monkeypatch.setattr(database, "_connection", FailAfterCommit())
    assert upload().status_code == 500
    assert (destination / "summary.md").read_text() == "new summary"
    store = database.get_task_store()
    assert store.get_artifact(task.id, "summary.md")["content"] == "new summary"
    assert store.get(task.id).result["remote_sync"]["import_id"]
    assert store.list_events(task.id)[-1]["event_type"] == "remote_archive_imported"
    row = connection.execute("SELECT title FROM archive_sync_index WHERE task_id=?", (str(task.id),)).fetchone()
    assert row["title"] == "New"


@pytest.mark.parametrize("committed", [False, True])
def test_process_termination_recovery_uses_sqlite_commit_marker(imported, committed):
    root, task, destination, incoming, _, _ = imported
    before_files, before_rows = _files(destination), _rows()
    database.close_db()
    script = r'''
import json, os, sys
from pathlib import Path
from app.api.routes import sync
from app.core import archive_sync, database, settings
root, incoming, task_json, committed = sys.argv[1:]
settings._runtime_settings = settings.RuntimeSettings(data_root=root)
database.reset_db_path(Path(root))
task = sync.Task.model_validate_json(task_json)
if committed == "true":
    archive_sync.ArchivePublication.finish = lambda self: os._exit(71)
else:
    service = archive_sync.get_archive_sync_service()
    original = service._upsert_if_changed
    def exit_before_commit(*args, **kwargs):
        original(*args, **kwargs)
        os._exit(70)
    service._upsert_if_changed = exit_before_commit
source = Path(incoming)
sync._commit_import(task, source, json.loads((source / "metadata.json").read_text()),
                    Path(root), "existing", "test-transfer", 123, "test-worker")
'''
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "backend")}
    result = subprocess.run([sys.executable, "-c", script, str(root), str(incoming),
        task.model_dump_json(), str(committed).lower()], env=env, capture_output=True, timeout=30)
    assert result.returncode == (71 if committed else 70), result.stderr.decode(errors="replace")
    assert (destination / "summary.md").read_text() == "new summary"
    database.reset_db_path(root)
    archive_sync.recover_archive_imports(root)
    archive_sync.recover_archive_imports(root)
    if committed:
        store = database.get_task_store()
        assert (destination / "summary.md").read_text() == "new summary"
        assert (destination / "video.mp4").read_bytes() == b"new media"
        assert store.get_artifact(task.id, "summary.md")["content"] == "new summary"
        assert store.get_artifact(task.id, "analysis.json")["content"] == '{"new":true}'
        assert store.get_artifact(task.id, "operator-notes.md")["content"] == "local review"
        assert store.list_events(task.id)[-1]["event_type"] == "remote_archive_imported"
        row = database._get_conn().execute("SELECT title FROM archive_sync_index WHERE task_id=?", (str(task.id),)).fetchone()
        assert row["title"] == "New"
        assert store.get(task.id).result["remote_sync"]["import_id"]
    else:
        assert _files(destination) == before_files
        assert _rows() == before_rows
    assert not list((get_workspace_paths(root).backups / "sync-imports").glob("*/journal.json"))


def test_sync_workspace_guard_supports_unicode_and_rejects_changed_library(imported):
    root, task, _, _, client, _ = imported
    workspace_id = client.get("/api/sync/status").json()["workspace_id"]
    assert workspace_id == str(root.resolve())
    good = {"X-MPP-Workspace-ID": quote(workspace_id, safe="")}
    bad = {"X-MPP-Workspace-ID": quote(str(root / "other"), safe="")}
    for path in ("/api/sync/changes", f"/api/sync/archives/{task.id}/manifest",
                 f"/api/sync/archives/{task.id}/files/summary.md"):
        response = client.get(path, headers=good)
        assert response.status_code == 200
        assert client.get(path, headers=bad).status_code == 409
        if not path.endswith("summary.md"):
            assert response.json()["workspace_id"] == workspace_id
