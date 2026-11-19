from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, unquote
from uuid import uuid4

import httpx
import pytest
from fastapi import APIRouter, FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api.routes.sync import router
from app.core import archive_sync, database, queue, settings
from app.core.library import get_library_service
from app.core.paths import get_workspace_paths
from app.models import Task, TaskStatus, TaskType
from app.services.remote_archive_pull import RemoteArchivePullService
from app.services.remote_archive_upload import RemoteArchiveUploadService

SERVER = "https://coordinator.example"
COLLECTION = {
    "is_collection": True,
    "collection_key": "bilibili:ugc:42",
    "title": "系列课程",
    "owner": {"name": "作者", "face": "https://i0.hdslb.com/avatar.png"},
}


class Workspace:
    def __init__(self, root):
        self.root = root
        self.settings = settings.RuntimeSettings(
            data_root=str(root), remote_sync_enabled=True, remote_server_url=SERVER, remote_api_token="test-token",
        )
        self.index = archive_sync.ArchiveSyncService()
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api")

    @contextmanager
    def active(self):
        previous, previous_index = settings._runtime_settings, archive_sync._sync_service
        settings._runtime_settings, archive_sync._sync_service = self.settings, self.index
        database.reset_db_path(self.root)
        try:
            yield
        finally:
            settings._runtime_settings, archive_sync._sync_service = previous, previous_index
            database.reset_db_path(Path(previous.data_root) if previous else None)

    def client(self, **kwargs):
        async def serve(scope, receive, send):
            with self.active():
                await self.app(scope, receive, send)

        return httpx.AsyncClient(transport=httpx.ASGITransport(app=serve), **kwargs)

    def snapshot(self):
        with self.active():
            return get_library_service().sync_snapshot()


@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "_task_queue", queue.TaskQueue())
    return tuple(Workspace(tmp_path / name) for name in ("SilentField", "Coordinator", "Reader"))


def add_archive(root, name):
    directory = get_workspace_paths(root).archives / name
    directory.mkdir(parents=True)
    task = Task(
        task_type=TaskType.PIPELINE, status=TaskStatus.COMPLETED, source=f"https://example.com/{name}",
        result={"output_dir": str(directory)},
    )
    (directory / "metadata.json").write_text(json.dumps({
        "title": name, "archive_id": str(task.id), "task_id": str(task.id),
        "status": "completed", "source_url": task.source,
    }), encoding="utf-8")
    (directory / "summary.md").write_text(f"# {name}", encoding="utf-8")
    database.get_task_store().save(task)
    archive_sync.get_archive_sync_service().mark_changed(directory)
    archive_sync.get_archive_sync_service().flush_changes()
    key = get_library_service().key_for_task(str(task.id))["content_key"]
    return task, key


@pytest.mark.asyncio
async def test_upload_and_pull_round_trip_preserves_classification(workspaces, monkeypatch):
    desktop, coordinator, reader = workspaces
    library = get_library_service()
    with coordinator.active():
        coordinator_collection_id = library.register_collection(COLLECTION, create=True)["id"]
    with reader.active():
        reader_collection_id = library.register_collection(COLLECTION, create=True)["id"]
        with database.task_write_transaction() as conn:
            conn.execute("UPDATE library_collections SET avatar=? WHERE collection_key=?",
                         (b"cached-avatar", COLLECTION["collection_key"]))

    async def healthy(_server):
        return None

    upload = RemoteArchiveUploadService(settings_getter=lambda: desktop.settings, client_factory=coordinator.client)
    monkeypatch.setattr(upload, "_check_server_health", healthy)
    desktop_pull = RemoteArchivePullService(settings_getter=lambda: desktop.settings, client_factory=coordinator.client)
    reader_pull = RemoteArchivePullService(settings_getter=lambda: reader.settings, client_factory=coordinator.client)

    with desktop.active():
        collection_id = library.register_collection(COLLECTION, create=True)["id"]
        manual_id = library.create("手动文件夹")["id"]
        _, first = add_archive(desktop.root, "first")
        _, second = add_archive(desktop.root, "second")
        _, moved_out = add_archive(desktop.root, "moved-out")
        library.move([first, second], manual_id)
        library.move([moved_out], collection_id)
        library.set_cover(manual_id, first)
        assert await upload.sync_once()
        assert await upload.sync_once()
        assert await upload.sync_once()
        assert await upload.sync_once()

        library.move([moved_out], None)
        assert await upload.sync_once()
        assert not await upload.sync_once()
        before = library.sync_snapshot()
        await desktop_pull.sync_once()
        after = library.sync_snapshot()
        assert after == before
        cursor = database._get_conn().execute("SELECT * FROM remote_sync_cursors").fetchone()
        assert cursor["pushed_library_revision"] == before["library_revision"]
        assert not await upload.sync_once()
        assert not (await desktop_pull.sync_once())["library_changed"]

    with reader.active():
        outcome = await reader_pull.sync_once()
        assert outcome["upserted"] == 3
        assert outcome["library_changed"]
        snapshot = library.sync_snapshot()
        assert database._get_conn().execute("SELECT avatar FROM library_collections").fetchone()[0] == b"cached-avatar"
        assert not (await reader_pull.sync_once())["library_changed"]
        assert library.sync_snapshot() == snapshot

    central = coordinator.snapshot()
    for data, existing_collection_id in (
        (before, collection_id), (central, coordinator_collection_id), (snapshot, reader_collection_id),
    ):
        assert len(data["folders"]) == 2
        folders = {row["name"]: row for row in data["folders"]}
        user_folder = folders["手动文件夹"]["id"]
        assert folders[COLLECTION["title"]]["source_collection_key"] == COLLECTION["collection_key"]
        assert folders[COLLECTION["title"]]["id"] == existing_collection_id
        assert {row["content_key"]: row["folder_id"] for row in data["members"]} == {
            first: user_folder, second: user_folder, moved_out: None,
        }
        assert [(row["folder_id"], row["content_key"]) for row in data["covers"]] == [(user_folder, first)]
        assert data["collections"][0]["owner_name"] == "作者"
        assert "avatar" not in data["collections"][0]
    assert coordinator.snapshot() == central


@pytest.mark.asyncio
async def test_task_archive_export_and_repeated_import_preserve_folder(workspaces):
    source, target, _ = workspaces
    with source.active():
        task, key = add_archive(source.root, "transfer")
        library = get_library_service()
        folder_id = library.create("固定封面")["id"]
        library.move([key], folder_id)
        library.set_cover(folder_id, key)

    async with source.client(base_url=SERVER) as client:
        response = await client.get(f"/api/sync/tasks/{task.id}/archive")
        assert response.status_code == 200
    folder_json = unquote(response.headers["x-mpp-folder-meta"])
    fields = {"task_json": task.model_dump_json(), "folder_json": folder_json, "archive_name": "transfer"}
    files = {"archive": ("archive.zip", response.content, "application/zip")}
    async with target.client(base_url=SERVER) as client:
        first = await client.post("/api/sync/import", data=fields, files=files)
        assert first.status_code == 200, first.text
        snapshot = target.snapshot()
        second = await client.post("/api/sync/import", data=fields, files=files)
        assert second.json()["already_synced"]
    assert snapshot == target.snapshot()
    assert snapshot["folders"][0]["id"] == folder_id
    assert snapshot["members"][0]["folder_id"] == folder_id
    assert snapshot["covers"][0]["content_key"] == snapshot["members"][0]["content_key"] == key


@pytest.mark.asyncio
async def test_legacy_coordinator_skips_library_push_and_self_target_skips_upload(workspaces, monkeypatch):
    desktop, legacy, _ = workspaces
    legacy_router = APIRouter()
    legacy_router.routes.extend(
        route for route in router.routes if route.path != "/sync/library" or "POST" not in route.methods
    )
    legacy.app = FastAPI()
    legacy.app.include_router(legacy_router, prefix="/api")

    async def healthy(_server):
        return None

    upload = RemoteArchiveUploadService(settings_getter=lambda: desktop.settings, client_factory=legacy.client)
    monkeypatch.setattr(upload, "_check_server_health", healthy)
    with desktop.active():
        task, key = add_archive(desktop.root, "legacy")
        library = get_library_service()
        library.move([key], library.create("local")["id"])
        self_upload = RemoteArchiveUploadService(settings_getter=lambda: desktop.settings, client_factory=desktop.client)
        monkeypatch.setattr(self_upload, "_check_server_health", healthy)
        assert not await self_upload.sync_once()
        assert "remote_sync" not in (database.get_task_store().get(task.id).result or {})
        assert await upload.sync_once()
        assert not await upload.sync_once()
        assert not database._get_conn().execute("SELECT * FROM remote_sync_cursors").fetchall()
    assert legacy.snapshot()["archives"][0]["content_key"] == key
    assert legacy.snapshot()["folders"] == []


@pytest.mark.asyncio
async def test_push_aligns_archive_and_cover_to_coordinator_content_key(workspaces):
    _, coordinator, _ = workspaces
    with coordinator.active():
        task, key = add_archive(coordinator.root, "canonical")
    folder = str(uuid4())
    at = "2026-11-19T10:00:00Z"
    payload = {
        "folders": [{"id": folder, "name": "分类", "created_at": at, "updated_at": at}],
        "members": [{"content_key": "archive:old-key", "archive_id": str(task.id), "folder_id": folder,
                     "added_at": at, "origin": "manual"}],
        "covers": [{"folder_id": folder, "content_key": "archive:old-key", "updated_at": at}],
    }
    async with coordinator.client(base_url=SERVER) as client:
        wrong = await client.post("/api/sync/library", headers={"X-MPP-Workspace-ID": "/wrong"}, json=payload)
        assert wrong.status_code == 409
        headers = {"X-MPP-Workspace-ID": quote(str(coordinator.root), safe="")}
        first = await client.post("/api/sync/library", headers=headers, json=payload)
        assert first.status_code == 200, first.text
        assert first.json()["folder_id_map"] == {folder: folder}
        second = await client.post("/api/sync/library", headers=headers, json=payload)
        assert second.json() == first.json()
        cover_at = "2026-11-20T10:00:00Z"
        payload["members"] = []
        payload["covers"][0].update(archive_id=str(task.id), updated_at=cover_at)
        cover_only = await client.post("/api/sync/library", headers=headers, json=payload)
        assert cover_only.json()["library_revision"] == first.json()["library_revision"] + 1
    snapshot = coordinator.snapshot()
    assert [row["content_key"] for row in snapshot["members"]] == [key]
    assert snapshot["covers"] == [{"folder_id": folder, "content_key": key, "updated_at": cover_at}]


@pytest.mark.asyncio
async def test_pull_after_lost_push_response_reuses_original_folder(workspaces, monkeypatch):
    desktop, coordinator, _ = workspaces
    upload = RemoteArchiveUploadService(settings_getter=lambda: desktop.settings, client_factory=coordinator.client)
    pull = RemoteArchivePullService(settings_getter=lambda: desktop.settings, client_factory=coordinator.client)
    original_post = httpx.AsyncClient.post

    async def lose_response(client, url, **kwargs):
        response = await original_post(client, url, **kwargs)
        response.raise_for_status()
        raise httpx.ReadError("Response lost after coordinator committed", request=response.request)

    with desktop.active():
        library = get_library_service()
        folder = library.create("离线课程")
        with monkeypatch.context() as patch:
            patch.setattr(httpx.AsyncClient, "post", lose_response)
            with pytest.raises(httpx.ReadError):
                await upload.sync_once()
        assert not (await pull.sync_once())["library_changed"]
        assert library.sync_snapshot()["folders"] == [folder]
        assert await upload.sync_once()
        assert not await upload.sync_once()

    snapshot = coordinator.snapshot()
    assert snapshot["folders"] == [folder]
    assert snapshot["library_revision"] == 1


def test_pull_aligns_archive_alias_and_preserves_pending_manual_move(workspaces):
    _, coordinator, reader = workspaces
    library = get_library_service()
    with reader.active():
        task, key = add_archive(reader.root, "alias")
    folder_id, old_key, at = str(uuid4()), "archive:old-key", "2026-01-01T00:00:00Z"

    from app.services.remote_archive_pull import RemoteLibrary, _merge_library

    snapshot = RemoteLibrary.model_validate({
        "library_revision": 1,
        "folders": [{"id": folder_id, "name": "分类", "created_at": at, "updated_at": at}],
        "members": [{"content_key": old_key, "folder_id": folder_id, "origin": "manual", "added_at": at}],
        "archives": [{"archive_id": str(task.id), "content_key": old_key, "folder_id": folder_id}],
        "covers": [{"folder_id": folder_id, "content_key": old_key, "updated_at": at}],
    })
    with reader.active():
        assert _merge_library(SERVER, str(coordinator.root), snapshot)
        synced = library.sync_snapshot()
        assert any(member["content_key"] == key and member["folder_id"] == synced["folders"][0]["id"]
                   for member in synced["members"])
        assert synced["covers"][0]["content_key"] == key
        library.move([key], None)
        snapshot.library_revision += 1
        assert not _merge_library(SERVER, str(coordinator.root), snapshot)
        assert library.sync_snapshot()["covers"] == []
        assert any(member["content_key"] == key and member["folder_id"] is None and member["origin"] == "manual"
                   for member in library.sync_snapshot()["members"])
