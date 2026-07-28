from __future__ import annotations

import asyncio
import io
import json
import sys
import zipfile
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.api.routes import tasks  # noqa: E402
from app.core import database  # noqa: E402
from app.core import settings as settings_module  # noqa: E402
from app.core.queue import REMOTE_MIRROR_OPTION, TaskQueue  # noqa: E402
from app.core.settings import RuntimeSettings  # noqa: E402
from app.models import Task, TaskCreate, TaskStatus, TaskType  # noqa: E402
from app.models.task import REMOTE_ARCHIVE_REVISION_OPTION  # noqa: E402
from app.services import remote_sync as remote_sync_module  # noqa: E402
from app.services.remote_sync import RemoteSyncService, _RemoteConfig  # noqa: E402


class _FakeRemoteQueue:
    def __init__(
        self,
        store,
        output_dir: Path | None = None,
        *,
        revision_updates: int = 0,
    ) -> None:
        self.store = store
        self.output_dir = output_dir
        self.revision_updates = revision_updates
        self.submitted: list[tuple[UUID, str]] = []
        self.cancelled: list[UUID] = []
        self.released: list[tuple[UUID, str]] = []

    async def submit_remote_claim(self, task_id: UUID, worker_id: str) -> None:
        self.submitted.append((task_id, worker_id))
        if self.output_dir is not None:
            task = self.store.get(task_id)
            assert task is not None
            task.status = TaskStatus.COMPLETED
            task.completed_at = task.updated_at
            task.result = {"output_dir": str(self.output_dir)}
            self.store.save(task)
            for _ in range(self.revision_updates):
                self.store.update_status(
                    task_id,
                    TaskStatus.COMPLETED,
                    progress=1.0,
                )

    async def cancel(self, task_id: UUID) -> bool:
        self.cancelled.append(task_id)
        return True

    def release_remote_claim(self, task_id: UUID, worker_id: str) -> bool:
        self.released.append((task_id, worker_id))
        return True


def _runtime(tmp_path: Path, **updates) -> RuntimeSettings:
    values = {
        "data_root": str(tmp_path),
        "remote_sync_enabled": True,
        "remote_server_url": "https://coordinator.example",
        "remote_worker_id": "desktop-test",
        "remote_worker_name": "Desktop Test",
        "remote_sync_interval_sec": 5,
    }
    values.update(updates)
    return RuntimeSettings(**values)


def _async_client(transport: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://coordinator.example/api/",
        transport=transport,
        headers={"X-Requested-With": "test"},
    )


def _zip_bytes(files: dict[str, str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, content in files.items():
            archive.writestr(filename, content)
    return output.getvalue()


def test_remote_worker_name_defaults_to_hostname(tmp_path, monkeypatch):
    monkeypatch.setattr(remote_sync_module.socket, "gethostname", lambda: "desktop-host")

    config = _RemoteConfig.from_settings(
        _runtime(tmp_path, remote_worker_name="", remote_worker_id="")
    )

    assert config.worker_name == "desktop-host"


@pytest.mark.asyncio
async def test_remote_sync_service_start_and_stop_manage_runner(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    service = RemoteSyncService(settings_getter=lambda: runtime)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_run():
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setattr(service, "_run", fake_run)

    await service.start()
    await started.wait()
    await service.stop()

    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_forward_task_uses_default_executor_and_saves_safe_mirror(
    tmp_path,
    monkeypatch,
):
    runtime = _runtime(tmp_path, default_task_executor="exe")
    database.reset_db_path(tmp_path)
    store = database.get_task_store()
    requests: list[dict] = []
    task_id = uuid4()

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads((await request.aread()).decode("utf-8"))
        if request.url.path.endswith("/workers/register"):
            return httpx.Response(
                200,
                json={"worker": {"id": "desktop-test"}},
            )
        requests.append(payload)
        return httpx.Response(
            200,
            json={
                "id": str(task_id),
                "task_type": "pipeline",
                "status": "queued",
                "source": payload["source"],
                "options": payload["options"],
                "origin_client": payload["origin_client"],
                "requested_executor": payload["requested_executor"],
                "result": {"output_dir": "/srv/mpp/task"},
            },
        )

    transport = httpx.MockTransport(handler)
    service = RemoteSyncService(
        settings_getter=lambda: runtime,
        store_getter=lambda: store,
    )
    monkeypatch.setattr(service, "_make_client", lambda _config: _async_client(transport))

    task = await service.forward_task(
        TaskCreate(task_type=TaskType.PIPELINE, source="https://example.com/video")
    )

    assert requests[0]["origin_client"] == "exe"
    assert requests[0]["requested_executor"] == "exe"
    assert task.id == task_id
    assert task.options[REMOTE_MIRROR_OPTION] is True
    assert "output_dir" not in (task.result or {})
    assert task.result["remote_sync"]["remote_output_dir"] == "/srv/mpp/task"
    assert store.get(task_id) == task
    database.reset_db_path()


@pytest.mark.asyncio
async def test_forward_local_file_targets_only_registered_exe(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, default_task_executor="exe")
    media_path = tmp_path / "local-video.mp4"
    media_path.write_bytes(b"video")
    database.reset_db_path(tmp_path)
    store = database.get_task_store()
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads((await request.aread()).decode("utf-8"))
        if request.url.path.endswith("/workers/register"):
            return httpx.Response(
                200,
                json={"worker": {"id": "desktop-test"}},
            )
        requests.append(payload)
        return httpx.Response(
            200,
            json={
                "task_type": "pipeline",
                "status": "queued",
                "source": payload["source"],
                "options": payload["options"],
                "origin_client": "exe",
                "requested_executor": "exe",
                "assigned_executor": "desktop-test",
            },
        )

    service = RemoteSyncService(
        settings_getter=lambda: runtime,
        store_getter=lambda: store,
    )
    monkeypatch.setattr(
        service,
        "_make_client",
        lambda _config: _async_client(httpx.MockTransport(handler)),
    )

    task = await service.forward_task(
        TaskCreate(task_type=TaskType.PIPELINE, source=str(media_path))
    )

    assert requests[0]["requested_executor"] == "exe"
    assert requests[0]["options"]["_mpp_preferred_worker_id"] == "desktop-test"
    assert task.assigned_executor == "desktop-test"
    with pytest.raises(
        remote_sync_module.RemoteSyncConfigurationError,
        match="只能指派给 EXE",
    ):
        await service.forward_task(
            TaskCreate(
                task_type=TaskType.PIPELINE,
                source=str(media_path),
                requested_executor="server",
            )
        )
    database.reset_db_path()


@pytest.mark.parametrize("use_local_source", [False, True], ids=["url", "local-file"])
@pytest.mark.asyncio
async def test_claimed_task_uploads_archive_and_preserves_uuid(
    tmp_path,
    use_local_source,
):
    runtime = _runtime(tmp_path)
    database.reset_db_path(tmp_path)
    store = database.get_task_store()
    archive_dir = tmp_path / "local-result"
    archive_dir.mkdir()
    (archive_dir / "metadata.json").write_text(
        json.dumps({"title": "Local result"}),
        encoding="utf-8",
    )
    (archive_dir / "summary.md").write_text("# Done", encoding="utf-8")
    queue = _FakeRemoteQueue(store, archive_dir, revision_updates=12)
    task_id = uuid4()
    upload_requests = 0
    local_media = tmp_path / "claimed-local.mp4"
    local_media.write_bytes(b"video")
    source = str(local_media) if use_local_source else "https://example.com/video"

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal upload_requests
        if request.url.path.endswith("/archive"):
            upload_requests += 1
            body = await request.aread()
            assert b"metadata.json" in body
            return httpx.Response(
                200,
                json={
                    "task": {
                        "id": str(task_id),
                        "sync_revision": 3,
                    },
                    "already_completed": False,
                },
            )
        return httpx.Response(500)

    service = RemoteSyncService(
        settings_getter=lambda: runtime,
        store_getter=lambda: store,
        queue_getter=lambda: queue,
    )
    claim = {
        "task": {
            "id": str(task_id),
            "task_type": "pipeline",
            "status": "processing",
            "source": source,
            "options": {"_mpp_preferred_worker_id": "desktop-test"},
            "origin_client": "apk",
            "requested_executor": "exe",
            "assigned_executor": "desktop-test",
        },
        "lease_token": "lease-token-1234567890",
        "lease_expires_at": "2099-01-01T00:00:00+00:00",
    }

    async with _async_client(httpx.MockTransport(handler)) as client:
        await service._process_claim(
            client,
            _RemoteConfig.from_settings(runtime),
            "desktop-test",
            claim,
        )

    saved = store.get(task_id)
    assert upload_requests == 1
    assert queue.submitted == [(task_id, "desktop-test")]
    assert queue.released == [(task_id, "desktop-test")]
    assert saved is not None
    assert saved.sync_revision == 3
    assert saved.source == source
    assert saved.result["output_dir"] == str(archive_dir)
    assert "_mpp_preferred_worker_id" not in saved.options
    assert saved.options[REMOTE_ARCHIVE_REVISION_OPTION] == 3
    database.reset_db_path()


@pytest.mark.asyncio
async def test_claimed_missing_local_file_is_reported_without_queueing(tmp_path):
    runtime = _runtime(tmp_path)
    database.reset_db_path(tmp_path)
    store = database.get_task_store()
    queue = _FakeRemoteQueue(store)
    task_id = uuid4()
    failures: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/fail"):
            failures.append(json.loads((await request.aread()).decode("utf-8")))
            return httpx.Response(200, json={"task": {"id": str(task_id)}})
        return httpx.Response(500)

    service = RemoteSyncService(
        settings_getter=lambda: runtime,
        store_getter=lambda: store,
        queue_getter=lambda: queue,
    )
    claim = {
        "task": {
            "id": str(task_id),
            "task_type": "pipeline",
            "status": "processing",
            "source": str(tmp_path / "missing.mp4"),
            "origin_client": "exe",
            "requested_executor": "exe",
            "assigned_executor": "desktop-test",
        },
        "lease_token": "lease-token-1234567890",
        "lease_expires_at": "2099-01-01T00:00:00+00:00",
    }

    async with _async_client(httpx.MockTransport(handler)) as client:
        await service._process_claim(
            client,
            _RemoteConfig.from_settings(runtime),
            "desktop-test",
            claim,
        )

    assert len(failures) == 1
    assert "可读取" in failures[0]["error"]
    assert queue.submitted == []
    assert store.get(task_id) is None
    database.reset_db_path()


@pytest.mark.asyncio
async def test_claimed_server_staged_input_is_downloaded_atomically_before_queue(
    tmp_path,
):
    runtime = _runtime(tmp_path)
    database.reset_db_path(tmp_path)
    store = database.get_task_store()
    archive_dir = tmp_path / "local-result"
    archive_dir.mkdir()
    (archive_dir / "metadata.json").write_text(
        json.dumps({"title": "Local result"}),
        encoding="utf-8",
    )
    source_seen: list[Path] = []
    source_bytes: list[bytes] = []

    class InspectingQueue(_FakeRemoteQueue):
        async def submit_remote_claim(self, task_id: UUID, worker_id: str) -> None:
            task = self.store.get(task_id)
            assert task is not None
            source = Path(task.source)
            source_seen.append(source)
            source_bytes.append(source.read_bytes())
            assert not list(source.parent.glob("*.part"))
            await super().submit_remote_claim(task_id, worker_id)

    queue = InspectingQueue(store, archive_dir)
    task_id = uuid4()
    media = b"media uploaded through coordinator"
    input_requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal input_requests
        if request.method == "GET" and request.url.path.endswith("/input"):
            input_requests += 1
            assert request.headers["X-MPP-Lease-Token"] == "lease-token-1234567890"
            return httpx.Response(200, content=media)
        if request.url.path.endswith("/heartbeat"):
            return httpx.Response(200, json={"worker": {"id": "desktop-test"}})
        if request.method == "GET" and request.url.path.endswith("/tasks"):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/archive"):
            return httpx.Response(
                200,
                json={
                    "task": {"id": str(task_id), "sync_revision": 4},
                    "already_completed": False,
                },
            )
        return httpx.Response(500)

    service = RemoteSyncService(
        settings_getter=lambda: runtime,
        store_getter=lambda: store,
        queue_getter=lambda: queue,
    )
    claim = {
        "task": {
            "id": str(task_id),
            "task_type": "pipeline",
            "status": "processing",
            "source": "/srv/mpp/data/_staging/upload/phone.mp4",
            "origin_client": "apk",
            "requested_executor": "exe",
            "assigned_executor": "desktop-test",
        },
        "input": {"filename": "../phone.mp4", "size": len(media)},
        "lease_token": "lease-token-1234567890",
        "lease_expires_at": "2099-01-01T00:00:00+00:00",
    }

    async with _async_client(httpx.MockTransport(handler)) as client:
        await service._process_claim(
            client,
            _RemoteConfig.from_settings(runtime),
            "desktop-test",
            claim,
        )

    assert input_requests == 1
    assert source_bytes == [media]
    assert source_seen[0].name == "phone.mp4"
    assert source_seen[0].is_relative_to(tmp_path / "_staging")
    assert not source_seen[0].exists()
    assert queue.submitted == [(task_id, "desktop-test")]
    assert queue.released == [(task_id, "desktop-test")]
    database.reset_db_path()


@pytest.mark.asyncio
async def test_long_claim_keeps_heartbeat_and_remote_mirror_running(
    tmp_path,
    monkeypatch,
):
    runtime = _runtime(tmp_path)
    database.reset_db_path(tmp_path)
    store = database.get_task_store()
    archive_dir = tmp_path / "long-result"
    archive_dir.mkdir()
    (archive_dir / "metadata.json").write_text("{}", encoding="utf-8")
    queue = _FakeRemoteQueue(store)
    task_id = uuid4()
    heartbeat_seen = asyncio.Event()
    mirror_seen = asyncio.Event()
    mirror_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/heartbeat"):
            heartbeat_seen.set()
            return httpx.Response(200, json={"worker": {"id": "desktop-test"}})
        if request.url.path.endswith("/archive"):
            return httpx.Response(
                200,
                json={
                    "task": {"id": str(task_id), "sync_revision": 2},
                    "already_completed": False,
                },
            )
        return httpx.Response(500)

    service = RemoteSyncService(
        settings_getter=lambda: runtime,
        store_getter=lambda: store,
        queue_getter=lambda: queue,
    )

    async def record_mirror(_client, _config):
        nonlocal mirror_calls
        mirror_calls += 1
        if mirror_calls >= 2:
            mirror_seen.set()

    async def finish_after_guards(task_id_arg, lease_lost):
        assert task_id_arg == task_id
        await asyncio.wait_for(
            asyncio.gather(heartbeat_seen.wait(), mirror_seen.wait()),
            timeout=2,
        )
        assert not lease_lost.is_set()
        task = store.get(task_id)
        assert task is not None
        task.status = TaskStatus.COMPLETED
        task.result = {"output_dir": str(archive_dir)}
        store.save(task)
        return task

    monkeypatch.setattr(service, "_mirror_remote_tasks", record_mirror)
    monkeypatch.setattr(service, "_wait_for_local_terminal", finish_after_guards)
    config = _RemoteConfig(
        api_base="https://coordinator.example/api/",
        api_token="",
        worker_id="desktop-test",
        worker_name="Desktop Test",
        interval_sec=0.01,
        upload_results=True,
        download_results=True,
        include_media=False,
    )
    claim = {
        "task": {
            "id": str(task_id),
            "task_type": "pipeline",
            "status": "processing",
            "source": "https://example.com/long-video",
            "origin_client": "apk",
            "requested_executor": "exe",
            "assigned_executor": "desktop-test",
        },
        "lease_token": "lease-token-1234567890",
        "lease_expires_at": "2099-01-01T00:00:00+00:00",
    }

    async with _async_client(httpx.MockTransport(handler)) as client:
        await service._process_claim(client, config, "desktop-test", claim)

    assert heartbeat_seen.is_set()
    assert mirror_calls >= 2
    assert queue.submitted == [(task_id, "desktop-test")]
    assert queue.released == [(task_id, "desktop-test")]
    database.reset_db_path()


@pytest.mark.asyncio
async def test_completed_remote_archive_download_is_not_repeated(tmp_path):
    runtime = _runtime(tmp_path, remote_sync_download_results=True)
    database.reset_db_path(tmp_path)
    store = database.get_task_store()
    task_id = uuid4()
    archive_downloads = 0
    archive_bytes = _zip_bytes(
        {
            "metadata.json": json.dumps(
                {
                    "title": "Remote archive",
                    "extra": {
                        "downloaded_image_paths": [
                            "/srv/mpp/result/images/00.jpg",
                        ],
                    },
                }
            ),
            "summary.md": "# Remote",
            "images/00.jpg": "jpeg-data",
            "analysis.json": json.dumps(
                {
                    "image_descriptions": [
                        {
                            "index": 0,
                            "image_path": "/srv/mpp/result/images/00.jpg",
                        },
                    ],
                }
            ),
        }
    )
    remote_task = Task(
        id=task_id,
        task_type=TaskType.PIPELINE,
        status=TaskStatus.COMPLETED,
        source="https://example.com/video",
        requested_executor="server",
        assigned_executor="server",
        sync_revision=10,
        result={
            "output_dir": "/srv/mpp/result",
            "metadata": {
                "title": "Remote archive",
                "extra": {
                    "downloaded_image_paths": [
                        "/srv/mpp/result/images/00.jpg",
                    ],
                },
            },
            "image_descriptions": [
                {
                    "index": 0,
                    "image_path": "/srv/mpp/result/images/00.jpg",
                    "text": "图片说明",
                },
            ],
        },
    )
    stale_mirror = remote_task.model_copy(deep=True)
    stale_mirror.status = TaskStatus.PROCESSING
    stale_mirror.sync_revision = 2
    stale_mirror.options = {REMOTE_MIRROR_OPTION: True}
    stale_mirror.result = None
    store.save_remote_mirror(stale_mirror)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal archive_downloads
        if request.url.path.endswith("/archive"):
            archive_downloads += 1
            return httpx.Response(
                200,
                content=archive_bytes,
                headers={"content-type": "application/zip"},
            )
        return httpx.Response(500)

    service = RemoteSyncService(
        settings_getter=lambda: runtime,
        store_getter=lambda: store,
    )
    config = _RemoteConfig.from_settings(runtime)
    async with _async_client(httpx.MockTransport(handler)) as client:
        await service._mirror_remote_task(client, config, remote_task)
        await service._mirror_remote_task(client, config, remote_task)

    saved = store.get(task_id)
    assert archive_downloads == 1
    assert saved is not None
    assert saved.sync_revision == 10
    local_output = Path(saved.result["output_dir"])
    assert local_output == tmp_path / f"remote-{task_id}"
    assert (local_output / "summary.md").read_text(encoding="utf-8") == "# Remote"
    expected_image = str((local_output / "images" / "00.jpg").resolve())
    metadata = json.loads((local_output / "metadata.json").read_text(encoding="utf-8"))
    analysis = json.loads((local_output / "analysis.json").read_text(encoding="utf-8"))
    assert metadata["extra"]["downloaded_image_paths"] == [expected_image]
    assert analysis["image_descriptions"][0]["image_path"] == expected_image
    assert saved.result["image_descriptions"][0]["image_path"] == expected_image
    assert saved.options[REMOTE_ARCHIVE_REVISION_OPTION] == 10
    database.reset_db_path()


@pytest.mark.asyncio
async def test_reenabling_archive_download_fetches_revision_skipped_while_disabled(
    tmp_path,
):
    database.reset_db_path(tmp_path)
    store = database.get_task_store()
    task_id = uuid4()
    local_output = tmp_path / f"remote-{task_id}"
    local_output.mkdir()
    (local_output / "metadata.json").write_text(
        json.dumps({"title": "Old archive"}),
        encoding="utf-8",
    )
    (local_output / "summary.md").write_text("# Old", encoding="utf-8")

    remote_task = Task(
        id=task_id,
        task_type=TaskType.PIPELINE,
        status=TaskStatus.COMPLETED,
        source="https://example.com/video",
        sync_revision=5,
        result={
            "output_dir": "/srv/mpp/result",
            "metadata": {"title": "New archive"},
        },
    )
    local_task = remote_task.model_copy(deep=True)
    local_task.sync_revision = 4
    local_task.options = {
        REMOTE_MIRROR_OPTION: True,
        REMOTE_ARCHIVE_REVISION_OPTION: 2,
    }
    local_task.result = {
        "output_dir": str(local_output),
        "metadata": {"title": "Old archive"},
    }
    store.save_remote_mirror(local_task)

    archive_downloads = 0
    archive_bytes = _zip_bytes(
        {
            "metadata.json": json.dumps({"title": "New archive"}),
            "summary.md": "# New",
        }
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal archive_downloads
        if request.url.path.endswith("/archive"):
            archive_downloads += 1
            return httpx.Response(
                200,
                content=archive_bytes,
                headers={"content-type": "application/zip"},
            )
        return httpx.Response(500)

    service = RemoteSyncService(
        settings_getter=lambda: _runtime(tmp_path),
        store_getter=lambda: store,
    )
    download_disabled = _RemoteConfig.from_settings(
        _runtime(tmp_path, remote_sync_download_results=False)
    )
    download_enabled = _RemoteConfig.from_settings(
        _runtime(tmp_path, remote_sync_download_results=True)
    )
    async with _async_client(httpx.MockTransport(handler)) as client:
        await service._mirror_remote_task(client, download_disabled, remote_task)
        skipped = store.get(task_id)
        assert skipped is not None
        assert skipped.sync_revision == 5
        assert skipped.options[REMOTE_ARCHIVE_REVISION_OPTION] == 2
        assert (local_output / "summary.md").read_text(encoding="utf-8") == "# Old"
        assert archive_downloads == 0

        await service._mirror_remote_task(client, download_enabled, remote_task)

    saved = store.get(task_id)
    assert saved is not None
    assert archive_downloads == 1
    assert saved.sync_revision == 5
    assert saved.options[REMOTE_ARCHIVE_REVISION_OPTION] == 5
    assert (local_output / "summary.md").read_text(encoding="utf-8") == "# New"
    database.reset_db_path()


@pytest.mark.asyncio
async def test_remote_task_mirroring_fetches_every_page(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    service = RemoteSyncService(settings_getter=lambda: runtime)
    tasks_payload = [
        Task(
            task_type=TaskType.PIPELINE,
            status=TaskStatus.QUEUED,
            source=f"https://example.com/{index}",
        ).model_dump(mode="json")
        for index in range(450)
    ]
    poison_id = UUID(tasks_payload[10]["id"])
    requested_offsets: list[int] = []
    mirrored_ids: list[UUID] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset", "0"))
        limit = int(request.url.params.get("limit", "50"))
        requested_offsets.append(offset)
        return httpx.Response(
            200,
            json=tasks_payload[offset : offset + limit],
        )

    async def record_mirror(_client, _config, remote_task):
        if remote_task.id == poison_id:
            raise OSError("damaged remote archive")
        mirrored_ids.append(remote_task.id)

    monkeypatch.setattr(service, "_mirror_remote_task", record_mirror)
    async with _async_client(httpx.MockTransport(handler)) as client:
        await service._mirror_remote_tasks(
            client,
            _RemoteConfig.from_settings(runtime),
        )

    assert requested_offsets == [0, 200, 400]
    assert len(mirrored_ids) == 449
    assert len(set(mirrored_ids)) == 449
    assert poison_id not in mirrored_ids


@pytest.mark.asyncio
async def test_queue_requires_explicit_remote_claim_grant(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(settings_module, "_runtime_settings", runtime)
    database.reset_db_path(tmp_path)
    store = database.get_task_store()
    task = Task(
        task_type=TaskType.PIPELINE,
        status=TaskStatus.PROCESSING,
        source="https://example.com/video",
        options={REMOTE_MIRROR_OPTION: True},
        requested_executor="exe",
        assigned_executor="desktop-test",
    )
    store.save(task)
    queue = TaskQueue()

    with pytest.raises(ValueError, match="not assigned"):
        await queue.submit_remote_claim(task.id, "another-worker")
    await queue.submit_remote_claim(task.id, "desktop-test")

    assert queue.get_queue_snapshot() == [task.id]
    assert queue.release_remote_claim(task.id, "desktop-test") is True
    assert queue._is_locally_runnable(store.get(task.id)) is False
    database.reset_db_path()


def test_task_and_batch_forwarding_preserve_default_executor(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, default_task_executor="exe")
    monkeypatch.setattr(settings_module, "_runtime_settings", runtime)
    database.reset_db_path(tmp_path)
    forwarded: list[TaskCreate] = []
    local_path = tmp_path / "local.mp4"
    local_path.write_bytes(b"video")

    class FakeService:
        async def forward_task(self, request: TaskCreate) -> Task:
            if (
                not request.source.startswith(("http://", "https://"))
                and request.requested_executor == "server"
            ):
                raise remote_sync_module.RemoteSyncConfigurationError(
                    "本地文件只能指派给 EXE"
                )
            forwarded.append(request)
            return Task(
                task_type=request.task_type,
                status=TaskStatus.QUEUED,
                source=request.source,
                options={REMOTE_MIRROR_OPTION: True},
                requested_executor=request.requested_executor,
                origin_client="exe",
            )

    monkeypatch.setattr(
        remote_sync_module,
        "get_remote_sync_service",
        lambda: FakeService(),
    )
    app = FastAPI()
    app.include_router(tasks.router, prefix="/api")

    with TestClient(app) as client:
        single = client.post(
            "/api/tasks",
            json={
                "task_type": "pipeline",
                "source": "https://example.com/single",
            },
        )
        batch = client.post(
            "/api/tasks/batch",
            json={
                "task_type": "pipeline",
                "sources": [
                    "https://example.com/one",
                    str(local_path),
                ],
            },
        )
        local_file = client.post(
            "/api/tasks",
            json={
                "task_type": "pipeline",
                "source": str(local_path),
            },
        )
        server_local_file = client.post(
            "/api/tasks",
            json={
                "task_type": "pipeline",
                "source": str(local_path),
                "requested_executor": "server",
            },
        )

    assert single.status_code == 200
    assert batch.status_code == 200
    assert local_file.status_code == 200
    assert server_local_file.status_code == 400
    assert [request.requested_executor for request in forwarded] == [
        "exe",
        "exe",
        "exe",
        "exe",
    ]
    database.reset_db_path()
