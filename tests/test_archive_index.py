from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core import archive_sync, database, settings
from app.core.archive_index import index_fields
from app.core.artifacts import ArtifactStore


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "_runtime_settings", settings.RuntimeSettings(data_root=str(tmp_path))
    )
    database.reset_db_path(tmp_path)
    service = archive_sync.ArchiveSyncService()
    monkeypatch.setattr(archive_sync, "_sync_service", service)
    yield tmp_path, service
    database.close_db()


def archive(root, title):
    directory = root / title
    directory.mkdir()
    identity = str(uuid4())
    (directory / "metadata.json").write_text(
        json.dumps({"archive_id": identity, "title": title, "duration_seconds": 10}),
        encoding="utf-8",
    )
    return directory, identity


def test_5000_rows_page_filter_order_and_no_filesystem_scan(library, monkeypatch):
    root, service = library
    conn = database._get_conn()
    fields = list(index_fields({}))
    for i in range(5000):
        item = {
            "archive_id": str(UUID(int=i + 1)),
            "title": f"项目 {i:05d}",
            "created_at": "2026-09-05T00:00:00Z",
            "metadata": {"platform": "youtube" if i % 2 else "bilibili"},
            "has_video": bool(i % 2),
            "has_audio": not i % 2,
            "has_image": False,
            "processing": False,
            "path": str(root / str(i)),
        }
        query_fields = index_fields(item)
        conn.execute(
            "INSERT INTO archive_sync_index(archive_id,archive_path,revision,fingerprint,snapshot,updated_at,"
            + ",".join(fields)
            + ") VALUES (?,?,1,'',?,'now',"
            + ",".join("?" for _ in fields)
            + ")",
            (item["archive_id"], str(i), json.dumps(item), *query_fields.values()),
        )
    conn.commit()
    monkeypatch.setattr(service, "reconcile", lambda: pytest.fail("unexpected full scan"))
    monkeypatch.setattr(service, "_index_directory", lambda *a: pytest.fail("unexpected file read"))
    seen = []
    for page in range(1, 180):
        result = service.list_page(page=page, page_size=28)
        assert result["total"] == 5000
        seen.extend(item["archive_id"] for item in result["archives"])
    assert len(seen) == len(set(seen)) == 5000
    assert seen == [str(UUID(int=i + 1)) for i in range(5000)]
    assert service.list_page(source="youtube", media="video")["total"] == 2500
    assert service.list_page(media="audio")["total"] == 2500
    assert service.list_page(search="项目 0000")["total"] == 10
    last = service.list_page(page=999, page_size=28)
    assert last["page"] == 179
    assert len(last["archives"]) == 16


def test_app_write_updates_one_archive_and_idle_sync_reads_no_files(library, monkeypatch):
    root, service = library
    first, first_id = archive(root, "first")
    archive(root, "second")
    service.reconcile()
    revision = service.current_revision()
    walked = []
    original = service._iter_sync_files
    monkeypatch.setattr(
        service,
        "_iter_sync_files",
        lambda path, **kw: (walked.append(path), original(path, **kw))[1],
    )
    ArtifactStore().write(None, first, "summary.md", "updated text")
    result = service.changes(revision, 100)
    assert [item["archive_id"] for item in result["changes"]] == [first_id]
    assert walked == [first]
    walked.clear()
    assert service.changes(result["next_cursor"], 100)["changes"] == []
    assert walked == []


def test_ordering_source_aliases_and_literal_search(library):
    root, service = library
    for title, platform, created, published in [
        ("Zulu_100%", "yt", "2026-01-01", "2026-03-01"),
        ("Alpha", "youtube", "2026-02-01", "2026-01-01"),
        ("Other", "bili", "2026-03-01", "2026-02-01"),
    ]:
        directory, identity = archive(root, title)
        (directory / "metadata.json").write_text(json.dumps({
            "archive_id": identity, "title": title, "platform": platform,
            "created_at": created, "upload_date": published,
        }), encoding="utf-8")
    service.reconcile()
    titles = lambda **query: [item["title"] for item in service.list_page(**query)["archives"]]
    assert titles(source="youtube", sort="title_asc") == ["Alpha", "Zulu_100%"]
    assert titles(sort="published_desc") == ["Zulu_100%", "Other", "Alpha"]
    assert titles(search="_100%") == ["Zulu_100%"]


def test_external_changes_are_published_by_explicit_reconciliation(library):
    root, service = library
    directory, archive_id = archive(root, "external")
    service.reconcile()
    before = service.list_page()["archives"][0]
    revision = service.current_revision()
    (directory / "metadata.json").write_text(
        json.dumps({"archive_id": archive_id, "title": "changed"}), encoding="utf-8"
    )
    assert service.changes(revision, 10)["changes"] == []
    service.reconcile()
    result = service.changes(revision, 10)
    assert result["changes"][0]["archive"]["title"] == "changed"
    assert service.list_page()["archives"][0]["created_at"] == before["created_at"]
    assert service.index_status()["last_reconciled_at"]
    revision = service.current_revision()
    service.reconcile()
    assert service.current_revision() == revision


def test_manifest_only_visits_its_target(library, monkeypatch):
    root, service = library
    first, identity = archive(root, "first")
    archive(root, "second")
    service.reconcile()
    original = service._iter_sync_files

    def visit(path, **kwargs):
        assert path == first
        return original(path, **kwargs)

    monkeypatch.setattr(service, "_iter_sync_files", visit)
    result = service.manifest(identity)
    assert result["archive_id"] == identity
    assert result["files"][0]["sha256"]


def test_initial_id_assignment_leaves_no_dirty_scan(library, monkeypatch):
    root, service = library
    directory, _ = archive(root, "without-id")
    (directory / "metadata.json").write_text('{"title":"first index"}', encoding="utf-8")
    service.reconcile()
    monkeypatch.setattr(service, "_index_directory", lambda *args: pytest.fail("already indexed"))
    assert service.changes(service.current_revision(), 10)["changes"] == []


def test_failed_index_write_rolls_back_revision_and_retries(library):
    import sqlite3

    root, service = library
    directory, identity = archive(root, "transaction")
    service.reconcile()
    revision = service.current_revision()
    conn = database._get_conn()
    conn.execute("""CREATE TRIGGER reject_title BEFORE UPDATE OF title ON archive_sync_index
                    WHEN NEW.title='reject' BEGIN SELECT RAISE(ABORT,'reject title'); END""")
    ArtifactStore().write(
        None, directory, "metadata.json", json.dumps({"archive_id": identity, "title": "reject"})
    )
    with pytest.raises(sqlite3.IntegrityError, match="reject title"):
        service.flush_changes()
    assert not conn.in_transaction
    assert service.current_revision() == revision
    conn.execute("DROP TRIGGER reject_title")
    service.flush_changes()
    assert service.current_revision() == revision + 1
    assert service.list_page()["archives"][0]["title"] == "reject"


def test_task_status_change_updates_processing_priority(library):
    from app.models import Task, TaskStatus, TaskType

    root, service = library
    directory, _ = archive(root, "task")
    task = Task(
        task_type=TaskType.PIPELINE,
        source="source",
        status=TaskStatus.COMPLETED,
        result={"output_dir": str(directory)},
    )
    store = database.get_task_store()
    store.save(task)
    assert not service.list_page()["archives"][0]["processing"]
    store.update_status(task.id, TaskStatus.PAUSED)
    assert service.list_page()["archives"][0]["processing"]


def test_api_keeps_legacy_shape_and_adds_pagination(library):
    from app.api.routes.pipeline import router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    root, service = library
    archive(root, "one")
    archive(root, "two")
    service.reconcile()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    with TestClient(app) as client:
        legacy = client.get("/api/pipeline/archives?lite=true").json()
        assert set(legacy) == {"archives"}
        assert len(legacy["archives"]) == 2
        result = client.get("/api/pipeline/archives?page=2&page_size=1").json()
        assert result["total"] == 2
        assert result["page"] == 2
        assert len(result["archives"]) == 1
        assert result["workspace_id"] == str(root)
        assert client.get("/api/pipeline/archives?page=0").status_code == 422
