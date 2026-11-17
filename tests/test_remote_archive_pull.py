from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.api.routes import sync as sync_route  # noqa: E402
from app.core import database  # noqa: E402
from app.core import settings as settings_module  # noqa: E402
from app.core.archive_sync import get_archive_sync_service, recover_archive_imports  # noqa: E402
from app.core.events import get_event_bus  # noqa: E402
from app.core.settings import RuntimeSettings  # noqa: E402
from app.models import Task, TaskStatus, TaskType  # noqa: E402
from app.services import remote_archive_pull as pull  # noqa: E402
from app.services.remote_archive_upload import RemoteArchiveUploadService, _normalized_server_url  # noqa: E402


class RemoteServer:
    def __init__(self):
        self.app = FastAPI()
        self.workspace = "/remote/资料库"
        self.instance_id = None
        self.archives = {}
        self.changes = []
        self.library = {"library_revision": 0, "folders": [], "members": []}
        self.requests = []
        self.corrupt = None
        self.manifest_extra = None

        @self.app.middleware("http")
        async def authenticate(request: Request, call_next):
            self.requests.append((request.url.path, request.url.query))
            if request.headers.get("authorization") != "Bearer secret":
                return Response(status_code=401)
            if request.url.path != "/api/sync/status":
                if request.headers.get("x-mpp-workspace-id") != quote(self.workspace, safe=""):
                    return Response(status_code=409)
            return await call_next(request)

        @self.app.get("/api/sync/status")
        async def status():
            return {
                "workspace_id": self.workspace,
                "revision": len(self.changes),
                "instance_id": self.instance_id,
            }

        @self.app.get("/api/sync/changes")
        async def changes(cursor: int):
            page = self.changes[cursor : cursor + 1]
            return {
                "workspace_id": self.workspace,
                "changes": page,
                "next_cursor": page[-1]["revision"] if page else cursor,
                "has_more": cursor + len(page) < len(self.changes),
                "library_revision": self.library["library_revision"],
                "server_revision": len(self.changes),
            }

        @self.app.get("/api/sync/archives/{archive_id}/manifest")
        async def manifest(archive_id: str):
            if archive_id not in self.archives:
                raise HTTPException(404, "Archive not found")
            files = [
                {
                    "relative_path": path,
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
                for path, content in self.archives[archive_id].items()
            ]
            if self.manifest_extra:
                files.append(self.manifest_extra)
            return {"workspace_id": self.workspace, "archive_id": archive_id, "files": files}

        @self.app.get("/api/sync/archives/{archive_id}/files/{path:path}")
        async def file(archive_id: str, path: str):
            content = self.archives[archive_id][path]
            return Response(content=b"tampered" if path == self.corrupt else content)

        @self.app.get("/api/sync/library")
        async def library():
            return self.library

    def upsert(self, archive_id=None, *, summary=b"# First\n", title="远程归档"):
        archive_id = archive_id or str(uuid4())
        self.archives[archive_id] = {
            "metadata.json": json.dumps(
                {
                    "title": title,
                    "archive_id": archive_id,
                    "task_id": archive_id,
                    "source_url": "https://example.com/video",
                    "status": "completed",
                },
                ensure_ascii=False,
            ).encode(),
            "summary.md": summary,
            "transcript.srt": b"1\n00:00:00,000 --> 00:00:01,000\nHello\n",
            "cover.png": b"cover",
            "images/one.png": b"image",
            "descriptions/one.md": b"description",
        }
        self.changes.append(
            {"revision": len(self.changes) + 1, "archive_id": archive_id, "operation": "upsert"}
        )
        return archive_id

    def delete(self, archive_id):
        del self.archives[archive_id]
        self.changes.append(
            {"revision": len(self.changes) + 1, "archive_id": archive_id, "operation": "delete"}
        )

    def client(self, **kwargs):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), **kwargs)


@pytest.fixture
def replica(tmp_path, monkeypatch):
    runtime = RuntimeSettings(
        data_root=str(tmp_path / "local"),
        remote_sync_enabled=True,
        remote_server_url="https://remote.example/api/",
        remote_api_token="secret",
    )
    monkeypatch.setattr(settings_module, "_runtime_settings", runtime)
    database.reset_db_path(Path(runtime.data_root))
    database.init_db()
    remote = RemoteServer()
    service = pull.RemoteArchivePullService(
        settings_getter=lambda: runtime, client_factory=remote.client
    )
    try:
        yield runtime, remote, service
    finally:
        database.reset_db_path()


def local_archive(archive_id):
    return Path(get_archive_sync_service()._active_record(archive_id)["archive_path"])


@pytest.mark.asyncio
async def test_pull_creates_updates_restarts_merges_folders_and_deletes(replica):
    runtime, remote, service = replica
    archive_id = remote.upsert()
    events = get_event_bus().subscribe_global()
    try:
        first = await service.sync_once()
        directory = local_archive(archive_id)
        assert first["upserted"] == 1
        assert directory.parent == Path(runtime.data_root) / "archives"
        assert (directory / "summary.md").read_bytes() == b"# First\n"
        assert (directory / "images/one.png").read_bytes() == b"image"
        task = database.get_task_store().get(UUID(archive_id))
        assert task.status == TaskStatus.COMPLETED
        assert task.options["read_only"] is True
        assert task.options["_remote_pull"]["workspace_id"] == remote.workspace
        assert (
            database.get_task_store().get_artifact(task.id, "summary.md")["content"] == "# First\n"
        )
        assert (await events.get()).event_type == "library_changed"
        initial_revision = get_archive_sync_service().current_revision()
        restarted = pull.RemoteArchivePullService(
            settings_getter=lambda: runtime, client_factory=remote.client
        )
        database.close_db()
        remote.requests.clear()
        repeat = await restarted.sync_once()
        assert repeat["cursor"] == 1
        assert ("/api/sync/changes", "cursor=1") in remote.requests
        assert not any("/files/" in path for path, _ in remote.requests)
        assert get_archive_sync_service().current_revision() == initial_revision

        folder_id = str(uuid4())
        remote.library = {
            "library_revision": 1,
            "folders": [
                {
                    "id": folder_id,
                    "name": "课程",
                    "created_at": "2026-01-01",
                    "updated_at": "2026-01-01",
                }
            ],
            "members": [{"content_key": "url:https://example.com/video", "folder_id": folder_id}],
        }
        remote.upsert(archive_id, summary=b"# Second\n")
        second = await restarted.sync_once()
        assert second["upserted"] == 1 and second["library_changed"]
        assert directory == local_archive(archive_id)
        assert (directory / "summary.md").read_bytes() == b"# Second\n"
        conn = database._get_conn()
        folder = conn.execute("SELECT * FROM library_folders").fetchone()
        assert folder["name"] == "课程" and folder["id"] != folder_id
        member = conn.execute("SELECT * FROM library_folder_members").fetchone()
        assert member["folder_id"] == folder["id"]
        local_library_revision = conn.execute("SELECT revision FROM library_meta").fetchone()[0]
        await restarted.sync_once()
        assert (
            conn.execute("SELECT revision FROM library_meta").fetchone()[0]
            == local_library_revision
        )

        remote.delete(archive_id)
        remote.library = {"library_revision": 2, "folders": [], "members": []}
        deleted = await restarted.sync_once()
        assert deleted["deleted"] == 1
        assert not directory.exists()
        assert get_archive_sync_service()._active_record(archive_id) is None
        assert database.get_task_store().get(UUID(archive_id)) is None
        assert (
            conn.execute(
                "SELECT deleted FROM archive_sync_index WHERE archive_id=?", (archive_id,)
            ).fetchone()[0]
            == 1
        )
        assert not conn.execute("SELECT * FROM library_folders").fetchall()
        assert not conn.execute("SELECT * FROM library_folder_members").fetchall()
    finally:
        await get_event_bus().unsubscribe_global(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("local_file", ["video.mp4", "personal.txt"])
async def test_pull_preserves_local_files_during_update_and_remote_delete(replica, local_file):
    runtime, remote, service = replica
    archive_id = remote.upsert()
    await service.sync_once()
    directory = local_archive(archive_id)
    (directory / local_file).write_bytes(b"local original")
    remote.upsert(archive_id, summary=b"Updated text")
    await service.sync_once()
    assert (directory / local_file).read_bytes() == b"local original"
    assert (directory / "summary.md").read_bytes() == b"Updated text"
    remote.delete(archive_id)
    outcome = await service.sync_once()
    assert outcome["deleted"] == 0
    assert (directory / local_file).read_bytes() == b"local original"
    assert get_archive_sync_service()._active_record(archive_id)
    before = get_archive_sync_service().current_revision()
    get_archive_sync_service().reconcile()
    assert get_archive_sync_service().current_revision() == before


@pytest.mark.asyncio
async def test_pull_rejects_tampered_files_and_retries_from_last_success(replica):
    _, remote, service = replica
    first = remote.upsert()
    remote.corrupt = "summary.md"
    with pytest.raises(ValueError, match="SHA-256|declared size"):
        await service.sync_once()
    assert pull._cursor("https://remote.example", remote.workspace) == (0, -1)
    assert get_archive_sync_service()._active_record(first) is None
    assert not database.get_task_store().list()
    remote.corrupt = None
    await service.sync_once()
    second = remote.upsert(title="第二个归档")
    remote.corrupt = "summary.md"
    with pytest.raises(ValueError):
        await service.sync_once()
    assert pull._cursor("https://remote.example", remote.workspace)[0] == 1
    assert get_archive_sync_service()._active_record(second) is None
    remote.corrupt = None
    assert (await service.sync_once())["cursor"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["video.mp4", "../outside.txt", "images/run.exe", "C:/unsafe.txt"])
async def test_pull_rejects_unsafe_or_media_manifest_entries(replica, path):
    _, remote, service = replica
    remote.upsert()
    remote.manifest_extra = {"relative_path": path, "size": 0, "sha256": "0" * 64}
    with pytest.raises(ValueError):
        await service.sync_once()
    assert pull._cursor("https://remote.example", remote.workspace)[0] == 0


@pytest.mark.asyncio
async def test_pull_skips_identical_uploaded_archive_without_claiming_ownership(replica):
    runtime, remote, service = replica
    archive_id = remote.upsert()
    directory = Path(runtime.data_root) / "original"
    directory.mkdir()
    for name, data in remote.archives[archive_id].items():
        target = directory / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(data)
    task = Task(
        id=UUID(archive_id),
        source="local.mp4",
        task_type=TaskType.PIPELINE,
        status=TaskStatus.COMPLETED,
        result={"output_dir": str(directory)},
    )
    database.get_task_store().save(task)
    get_archive_sync_service().reconcile()
    outcome = await service.sync_once()
    assert outcome["skipped"] == 1 and outcome["upserted"] == 0
    assert not any("/files/" in path for path, _ in remote.requests)
    remote.delete(archive_id)
    await service.sync_once()
    assert directory.is_dir()
    assert not database.get_task_store().get(task.id).options.get("_remote_pull")


@pytest.mark.asyncio
async def test_pull_does_not_overwrite_active_local_archive(replica):
    _, remote, service = replica
    archive_id = remote.upsert()
    await service.sync_once()
    task = database.get_task_store().get(UUID(archive_id))
    task.status = TaskStatus.PROCESSING
    database.get_task_store().save(task)
    remote.upsert(archive_id, summary=b"replacement")
    with pytest.raises(ValueError, match="active task"):
        await service.sync_once()
    assert (local_archive(archive_id) / "summary.md").read_bytes() == b"# First\n"
    assert database.get_task_store().get(task.id).status == TaskStatus.PROCESSING
    assert pull._cursor("https://remote.example", remote.workspace)[0] == 1


@pytest.mark.asyncio
async def test_pull_rolls_back_publication_and_replays_after_sqlite_failure(replica, monkeypatch):
    runtime, remote, service = replica
    archive_id = remote.upsert()
    await service.sync_once()
    directory = local_archive(archive_id)
    remote.upsert(archive_id, summary=b"replacement")
    sync = get_archive_sync_service()
    original = sync._upsert_if_changed

    def fail(*args, **kwargs):
        raise RuntimeError("database write failed")

    monkeypatch.setattr(sync, "_upsert_if_changed", fail)
    with pytest.raises(RuntimeError, match="database write"):
        await service.sync_once()
    assert (directory / "summary.md").read_bytes() == b"# First\n"
    assert pull._cursor("https://remote.example", remote.workspace)[0] == 1
    recover_archive_imports(runtime.data_root)
    monkeypatch.setattr(sync, "_upsert_if_changed", original)
    await service.sync_once()
    assert (directory / "summary.md").read_bytes() == b"replacement"


@pytest.mark.asyncio
async def test_pull_guards_self_workspace_but_allows_remote_matching_path(replica):
    runtime, remote, _ = replica
    remote.workspace = str(Path(runtime.data_root).resolve())
    archive_id = remote.upsert()
    runtime.remote_server_url = "http://127.0.0.1:18000"
    service = pull.RemoteArchivePullService(
        settings_getter=lambda: runtime, client_factory=remote.client
    )
    assert (await service.sync_once())["reason"] == "self_workspace"
    assert len(remote.requests) == 1
    runtime.remote_server_url = "https://remote.example"
    assert (await service.sync_once())["upserted"] == 1
    assert local_archive(archive_id).is_dir()


@pytest.mark.asyncio
async def test_pull_recognizes_self_instance_through_public_proxy_hostname(replica):
    runtime, remote, service = replica
    remote.workspace = str(Path(runtime.data_root).resolve())
    remote.instance_id = get_archive_sync_service().instance_id
    archive_id = remote.upsert()
    assert (await service.sync_once())["reason"] == "self_workspace"
    assert len(remote.requests) == 1
    remote.instance_id = str(uuid4())
    runtime.remote_server_url = "http://127.0.0.1:18000"
    assert (await service.sync_once())["upserted"] == 1
    assert local_archive(archive_id).is_dir()


@pytest.mark.asyncio
async def test_pull_is_serialized_and_manual_route_triggers_same_service(replica, monkeypatch):
    _, remote, service = replica
    remote.upsert()
    monkeypatch.setattr(pull, "_service", service)
    app = FastAPI()
    app.include_router(sync_route.router, prefix="/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as local:
        results = await asyncio.gather(local.post("/api/sync/pull"), local.post("/api/sync/pull"))
    assert all(response.status_code == 200 for response in results)
    assert sum(response.json()["upserted"] for response in results) == 1
    assert len(database.get_task_store().list()) == 1


@pytest.mark.asyncio
async def test_upload_ignores_pulled_records(replica, monkeypatch):
    runtime, remote, service = replica
    remote.upsert()
    await service.sync_once()
    upload = RemoteArchiveUploadService(settings_getter=lambda: runtime)

    async def fail(*_args):
        raise AssertionError("Pulled record must not be re-uploaded")

    monkeypatch.setattr(upload, "_check_server_health", fail)
    assert await upload.sync_once() is False


@pytest.mark.asyncio
async def test_pull_advances_past_historical_upsert_deleted_on_a_later_page(replica):
    _, remote, service = replica
    deleted_id = remote.upsert()
    remote.delete(deleted_id)
    live_id = remote.upsert(title="当前归档")
    outcome = await service.sync_once()
    assert outcome["cursor"] == 3 and outcome["upserted"] == 1
    assert get_archive_sync_service()._active_record(deleted_id) is None
    assert local_archive(live_id).is_dir()


@pytest.mark.asyncio
async def test_pull_keeps_cursor_on_manifest_404_without_later_tombstone(replica):
    _, remote, service = replica
    missing_id = remote.upsert()
    del remote.archives[missing_id]
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await service.sync_once()
    assert caught.value.response.status_code == 404
    assert pull._cursor("https://remote.example", remote.workspace)[0] == 0


@pytest.mark.asyncio
async def test_pulled_read_only_records_reject_local_execution_through_real_routes(
    replica, monkeypatch
):
    from app.api.routes import tasks
    from app.core import queue as queue_module

    _, remote, service = replica
    archive_id = remote.upsert()
    await service.sync_once()
    task_queue = queue_module.TaskQueue()
    monkeypatch.setattr(queue_module, "_task_queue", task_queue)
    app = FastAPI()
    app.include_router(tasks.router, prefix="/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as local:
        for action in ("full-rerun", "checkpoint-rerun", "resume"):
            response = await local.post(f"/api/tasks/{archive_id}/{action}")
            assert response.status_code == 409
            assert "只读" in response.json()["detail"]
        assert (await local.get(f"/api/tasks/{archive_id}")).status_code == 200
    with pytest.raises(ValueError, match="read-only"):
        await task_queue.submit(UUID(archive_id))
    assert database.get_task_store().get(UUID(archive_id)).status == TaskStatus.COMPLETED
    assert task_queue.pending_count == 0
    assert (local_archive(archive_id) / "summary.md").read_bytes() == b"# First\n"


@pytest.mark.parametrize(
    "url",
    [
        "http://:18000",
        "https://user:pass@example.com",
        "https://example.com?x=1",
        "https://example.com/#fragment",
    ],
)
def test_server_url_rejects_invalid_identity(url):
    with pytest.raises(ValueError):
        _normalized_server_url(url)


def test_server_url_identity_is_normalized():
    assert _normalized_server_url(" HTTPS://EXAMPLE.COM:443/api/ ") == "https://example.com"
    assert (
        _normalized_server_url("http://EXAMPLE.COM:80/prefix/api/") == "http://example.com/prefix"
    )
    assert _normalized_server_url("http://[::1]:18000/api/") == "http://[::1]:18000"
