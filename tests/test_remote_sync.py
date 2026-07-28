from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.api.routes import pipeline, sync, tasks  # noqa: E402
from app.core import archive_sync, database  # noqa: E402
from app.core import settings as settings_module  # noqa: E402
from app.core.archive_sync import safe_extract_zip  # noqa: E402
from app.core.queue import TaskQueue  # noqa: E402
from app.core.settings import RuntimeSettings  # noqa: E402
from app.models import Task, TaskStatus, TaskType  # noqa: E402
from app.models.task import PREFERRED_WORKER_OPTION  # noqa: E402


class FakeQueue:
    def __init__(self) -> None:
        self.submitted: list[UUID] = []

    async def submit(self, task_id: UUID) -> None:
        self.submitted.append(task_id)


@pytest.fixture
def sync_client(tmp_path: Path, monkeypatch):
    runtime = RuntimeSettings(data_root=str(tmp_path), api_token="")
    monkeypatch.setattr(settings_module, "_runtime_settings", runtime)
    monkeypatch.setattr(settings_module, "_save_settings_to_file", lambda _settings: None)
    database.reset_db_path(tmp_path)
    fake_queue = FakeQueue()
    monkeypatch.setattr(tasks, "get_task_queue", lambda: fake_queue)

    app = FastAPI()
    app.include_router(tasks.router, prefix="/api")
    app.include_router(sync.router, prefix="/api")
    with TestClient(app) as client:
        yield client, fake_queue, tmp_path
    database.reset_db_path()


def _task(
    *,
    executor: str = "exe",
    status: TaskStatus = TaskStatus.QUEUED,
    result: dict | None = None,
) -> Task:
    return Task(
        id=uuid4(),
        task_type=TaskType.PIPELINE,
        status=status,
        source="https://example.com/media",
        requested_executor=executor,
        assigned_executor="server" if executor == "server" else None,
        origin_client="test",
        result=result,
    )


def _zip_bytes(files: dict[str, bytes | str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content.encode("utf-8") if isinstance(content, str) else content)
    return output.getvalue()


def _register_and_claim(client: TestClient, worker_id: str = "exe-test") -> dict:
    registered = client.post(
        "/api/workers/register",
        json={
            "worker_id": worker_id,
            "name": "Test EXE",
            "capabilities": {"gpu": True},
        },
    )
    assert registered.status_code == 200
    claimed = client.post(
        f"/api/workers/{worker_id}/claim",
        json={"lease_seconds": 900},
    )
    assert claimed.status_code == 200
    assert claimed.json()["task"] is not None
    return claimed.json()


def _lease_state(task_id: UUID | str) -> str | None:
    row = database._get_conn().execute(
        "SELECT state FROM task_leases WHERE task_id = ?",
        (str(task_id),),
    ).fetchone()
    return str(row["state"]) if row else None


def test_existing_database_is_migrated_with_sync_defaults(tmp_path):
    db_path = tmp_path / "tasks.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            task_type TEXT NOT NULL,
            status TEXT NOT NULL,
            source TEXT NOT NULL,
            options TEXT NOT NULL DEFAULT '{}',
            progress REAL NOT NULL DEFAULT 0,
            message TEXT,
            result TEXT,
            error TEXT,
            webhook_url TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            current_step TEXT,
            steps TEXT NOT NULL DEFAULT '[]',
            completed_steps TEXT NOT NULL DEFAULT '[]',
            flow TEXT,
            platform TEXT,
            uploader_id TEXT,
            content_subtype TEXT
        );
        """
    )
    task_id = str(uuid4())
    now = datetime.now().isoformat()
    conn.execute(
        """
        INSERT INTO tasks
            (id, task_type, status, source, options, created_at, updated_at, steps, completed_steps)
        VALUES (?, 'pipeline', 'completed', 'legacy.mp4', '{}', ?, ?, '[]', '[]')
        """,
        (task_id, now, now),
    )
    conn.commit()
    conn.close()

    database.reset_db_path(tmp_path)
    task = database.get_task_store().get(UUID(task_id))
    assert task is not None
    assert task.origin_client == "legacy"
    assert task.requested_executor == "server"
    assert task.assigned_executor is None
    assert task.sync_revision == 0

    columns = {
        row["name"]
        for row in database._get_conn().execute("PRAGMA table_info(tasks)").fetchall()
    }
    assert {"origin_client", "requested_executor", "assigned_executor", "sync_revision"} <= columns
    assert database._get_conn().execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='task_leases'"
    ).fetchone()
    database.reset_db_path()


def test_task_api_accepts_origin_and_requested_executor(sync_client):
    client, queue, _tmp_path = sync_client
    response = client.post(
        "/api/tasks",
        json={
            "task_type": "pipeline",
            "source": "https://example.com/video",
            "origin_client": "apk",
            "requested_executor": "exe",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["origin_client"] == "apk"
    assert payload["requested_executor"] == "exe"
    assert payload["assigned_executor"] is None
    assert payload["message"] == "等待 EXE 处理..."
    assert queue.submitted == [UUID(payload["id"])]


@pytest.mark.asyncio
async def test_local_queue_only_enqueues_server_tasks(tmp_path, monkeypatch):
    runtime = RuntimeSettings(data_root=str(tmp_path), max_download_concurrency=1)
    monkeypatch.setattr(settings_module, "_runtime_settings", runtime)
    database.reset_db_path(tmp_path)
    store = database.get_task_store()
    server_task = _task(executor="server")
    exe_task = _task(executor="exe")
    store.save(server_task)
    store.save(exe_task)

    queue = TaskQueue()
    await queue.submit(server_task.id)
    await queue.submit(exe_task.id)

    assert queue.get_queue_snapshot() == [server_task.id]
    saved_exe = store.get(exe_task.id)
    assert saved_exe is not None
    assert saved_exe.status == TaskStatus.QUEUED
    assert saved_exe.message == "等待 EXE 处理..."
    database.reset_db_path()


@pytest.mark.asyncio
async def test_local_queue_preserves_preferred_exe_assignment(tmp_path, monkeypatch):
    runtime = RuntimeSettings(data_root=str(tmp_path), max_download_concurrency=1)
    monkeypatch.setattr(settings_module, "_runtime_settings", runtime)
    database.reset_db_path(tmp_path)
    task = _task()
    task.assigned_executor = "desktop-a"
    task.options[PREFERRED_WORKER_OPTION] = "desktop-a"
    database.get_task_store().save(task)

    queue = TaskQueue()
    await queue.submit(task.id)

    saved = database.get_task_store().get(task.id)
    assert saved is not None
    assert saved.assigned_executor == "desktop-a"
    assert queue.get_queue_snapshot() == []
    database.reset_db_path()


def test_workers_list_includes_server_and_online_exe(sync_client):
    client, _queue, _tmp_path = sync_client
    client.post(
        "/api/workers/register",
        json={"worker_id": "desktop-a", "name": "Desktop A", "capabilities": {"vram_gb": 16}},
    )
    response = client.get("/api/workers")
    assert response.status_code == 200
    workers = response.json()["workers"]
    assert workers[0]["id"] == "server"
    assert workers[0]["online"] is True
    desktop = next(worker for worker in workers if worker["id"] == "desktop-a")
    assert desktop["executor"] == "exe"
    assert desktop["online"] is True
    assert desktop["capabilities"]["vram_gb"] == 16


def test_claimed_worker_downloads_only_its_server_staged_input(sync_client):
    client, _queue, tmp_path = sync_client
    staged_dir = tmp_path / "_staging" / uuid4().hex
    staged_dir.mkdir(parents=True)
    media = staged_dir / "手机 上传.mp4"
    media.write_bytes(b"server-staged-media")
    task = _task()
    task.source = str(media)
    database.get_task_store().save(task)

    claim = _register_and_claim(client)

    assert claim["task"]["id"] == str(task.id)
    assert claim["input"] == {
        "filename": media.name,
        "size": len(b"server-staged-media"),
    }
    missing_token = client.get(
        f"/api/workers/exe-test/tasks/{task.id}/input",
    )
    assert missing_token.status_code == 422
    wrong_token = client.get(
        f"/api/workers/exe-test/tasks/{task.id}/input",
        headers={"X-MPP-Lease-Token": "another-lease-token-123"},
    )
    assert wrong_token.status_code == 409

    downloaded = client.get(
        f"/api/workers/exe-test/tasks/{task.id}/input",
        headers={"X-MPP-Lease-Token": claim["lease_token"]},
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"server-staged-media"
    assert downloaded.headers["cache-control"] == "private, no-store"
    assert downloaded.headers["x-mpp-input-size"] == str(len(downloaded.content))


def test_claimed_input_rejects_files_outside_staging_and_over_limit(
    sync_client,
    monkeypatch,
):
    client, _queue, tmp_path = sync_client
    outside = tmp_path / "private.mp4"
    outside.write_bytes(b"private")
    outside_task = _task()
    outside_task.source = str(outside)
    database.get_task_store().save(outside_task)
    outside_claim = _register_and_claim(client)
    assert "input" not in outside_claim
    outside_download = client.get(
        f"/api/workers/exe-test/tasks/{outside_task.id}/input",
        headers={"X-MPP-Lease-Token": outside_claim["lease_token"]},
    )
    assert outside_download.status_code == 403

    database.get_task_store().fail_remote_task(
        outside_task.id,
        "exe-test",
        outside_claim["lease_token"],
        "test cleanup",
    )
    staged_dir = tmp_path / "_staging" / uuid4().hex
    staged_dir.mkdir(parents=True)
    oversized = staged_dir / "oversized.mp4"
    oversized.write_bytes(b"12345")
    oversized_task = _task()
    oversized_task.source = str(oversized)
    database.get_task_store().save(oversized_task)
    monkeypatch.setattr(sync, "MAX_UPLOAD_BYTES", 4)
    oversized_claim = _register_and_claim(client)
    assert "input" not in oversized_claim
    oversized_download = client.get(
        f"/api/workers/exe-test/tasks/{oversized_task.id}/input",
        headers={"X-MPP-Lease-Token": oversized_claim["lease_token"]},
    )
    assert oversized_download.status_code == 413


def test_staging_sweep_retains_input_for_offline_exe_task(
    sync_client,
):
    _client, _queue, tmp_path = sync_client
    staging_root = tmp_path / "_staging"
    active_dir = staging_root / uuid4().hex
    stale_dir = staging_root / uuid4().hex
    active_dir.mkdir(parents=True)
    stale_dir.mkdir()
    active_source = active_dir / "waiting.mp4"
    active_source.write_bytes(b"waiting")
    (stale_dir / "abandoned.mp4").write_bytes(b"abandoned")
    os.utime(active_dir, (1, 1))
    os.utime(stale_dir, (1, 1))

    task = _task()
    task.source = str(active_source)
    database.get_task_store().save(task)

    removed = pipeline.sweep_stale_staging(max_age_hours=0)

    assert removed == 1
    assert active_source.is_file()
    assert not stale_dir.exists()


def test_concurrent_claim_has_one_winner(tmp_path, monkeypatch):
    runtime = RuntimeSettings(data_root=str(tmp_path))
    monkeypatch.setattr(settings_module, "_runtime_settings", runtime)
    database.reset_db_path(tmp_path)
    store = database.get_task_store()
    task = _task()
    store.save(task)
    store.register_worker(worker_id="exe-a", name="A")
    store.register_worker(worker_id="exe-b", name="B")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda worker_id: store.claim_remote_task(worker_id),
                ["exe-a", "exe-b"],
            )
        )

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    claimed = store.get(task.id)
    assert claimed is not None
    assert claimed.status == TaskStatus.PROCESSING
    assert claimed.assigned_executor in {"exe-a", "exe-b"}
    database.reset_db_path()


def test_concurrent_upload_reservation_has_one_winner(tmp_path, monkeypatch):
    runtime = RuntimeSettings(data_root=str(tmp_path))
    monkeypatch.setattr(settings_module, "_runtime_settings", runtime)
    database.reset_db_path(tmp_path)
    store = database.get_task_store()
    task = _task()
    store.save(task)
    store.register_worker(worker_id="exe-a", name="A")
    claim = store.claim_remote_task("exe-a")
    assert claim is not None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _index: store.begin_remote_upload(
                    task.id,
                    "exe-a",
                    claim["lease_token"],
                ),
                range(2),
            )
        )

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert winners[0]["state"] == "uploading"
    assert _lease_state(task.id) == "uploading"
    assert store.release_remote_upload(task.id, "exe-a", claim["lease_token"]) is True
    assert _lease_state(task.id) == "active"
    database.reset_db_path()


def test_preferred_worker_is_the_only_worker_allowed_to_claim(sync_client):
    client, _queue, _tmp_path = sync_client
    created = client.post(
        "/api/tasks",
        json={
            "task_type": "pipeline",
            "source": "https://example.com/video",
            "requested_executor": "exe",
            "options": {PREFERRED_WORKER_OPTION: "desktop-a"},
        },
    )
    assert created.status_code == 200
    assert created.json()["assigned_executor"] == "desktop-a"

    client.post(
        "/api/workers/register",
        json={"worker_id": "desktop-b", "name": "Desktop B"},
    )
    rejected = client.post(
        "/api/workers/desktop-b/claim",
        json={"lease_seconds": 900},
    )
    assert rejected.status_code == 200
    assert rejected.json()["task"] is None

    client.post(
        "/api/workers/register",
        json={"worker_id": "desktop-a", "name": "Desktop A"},
    )
    claimed = client.post(
        "/api/workers/desktop-a/claim",
        json={"lease_seconds": 900},
    )
    assert claimed.status_code == 200
    assert claimed.json()["task"]["id"] == created.json()["id"]


def test_preferred_worker_survives_lease_expiry(tmp_path, monkeypatch):
    runtime = RuntimeSettings(data_root=str(tmp_path))
    monkeypatch.setattr(settings_module, "_runtime_settings", runtime)
    database.reset_db_path(tmp_path)
    store = database.get_task_store()
    task = _task()
    task.options[PREFERRED_WORKER_OPTION] = "exe-a"
    task.assigned_executor = "exe-a"
    store.save(task)
    store.register_worker(worker_id="exe-a", name="A")
    store.register_worker(worker_id="exe-b", name="B")
    first = store.claim_remote_task("exe-a")
    assert first is not None

    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    database._get_conn().execute(
        "UPDATE task_leases SET lease_expires_at = ? WHERE task_id = ?",
        (expired, str(task.id)),
    )
    database._get_conn().commit()

    assert store.claim_remote_task("exe-b") is None
    second = store.claim_remote_task("exe-a")
    assert second is not None
    assert second["attempt"] == 2
    database.reset_db_path()


def test_expired_lease_can_be_reclaimed(tmp_path, monkeypatch):
    runtime = RuntimeSettings(data_root=str(tmp_path))
    monkeypatch.setattr(settings_module, "_runtime_settings", runtime)
    database.reset_db_path(tmp_path)
    store = database.get_task_store()
    task = _task()
    store.save(task)
    store.register_worker(worker_id="exe-a", name="A")
    store.register_worker(worker_id="exe-b", name="B")
    first = store.claim_remote_task("exe-a")
    assert first is not None

    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    database._get_conn().execute(
        "UPDATE task_leases SET lease_expires_at = ? WHERE task_id = ?",
        (expired, str(task.id)),
    )
    database._get_conn().commit()

    second = store.claim_remote_task("exe-b")
    assert second is not None
    assert second["task"].assigned_executor == "exe-b"
    assert second["attempt"] == 2
    assert store.get_remote_lease(task.id, "exe-a", first["lease_token"]) is None
    database.reset_db_path()


def test_repeated_worker_crashes_move_task_to_failed_and_unblock_queue(
    tmp_path,
    monkeypatch,
):
    runtime = RuntimeSettings(data_root=str(tmp_path))
    monkeypatch.setattr(settings_module, "_runtime_settings", runtime)
    database.reset_db_path(tmp_path)
    store = database.get_task_store()
    poison = _task()
    following = _task()
    following.created_at = poison.created_at + timedelta(seconds=1)
    store.save(poison)
    store.save(following)
    store.register_worker(worker_id="exe-a", name="A")

    for expected_attempt in (1, 2, 3):
        claim = store.claim_remote_task("exe-a")
        assert claim is not None
        assert claim["task"].id == poison.id
        assert claim["attempt"] == expected_attempt
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        database._get_conn().execute(
            "UPDATE task_leases SET lease_expires_at = ? WHERE task_id = ?",
            (expired, str(poison.id)),
        )
        database._get_conn().commit()

    next_claim = store.claim_remote_task("exe-a")

    assert next_claim is not None
    assert next_claim["task"].id == following.id
    failed = store.get(poison.id)
    assert failed is not None
    assert failed.status == TaskStatus.FAILED
    assert "多次中断" in failed.message
    database.reset_db_path()


@pytest.mark.parametrize("lease_phase", ["uploading", "finalizing"])
def test_expired_upload_phase_can_be_reclaimed(tmp_path, monkeypatch, lease_phase):
    runtime = RuntimeSettings(data_root=str(tmp_path))
    monkeypatch.setattr(settings_module, "_runtime_settings", runtime)
    database.reset_db_path(tmp_path)
    store = database.get_task_store()
    task = _task()
    store.save(task)
    store.register_worker(worker_id="exe-a", name="A")
    store.register_worker(worker_id="exe-b", name="B")
    first = store.claim_remote_task("exe-a")
    assert first is not None
    assert store.begin_remote_upload(
        task.id,
        "exe-a",
        first["lease_token"],
    )
    if lease_phase == "finalizing":
        assert store.begin_remote_finalization(
            task.id,
            "exe-a",
            first["lease_token"],
        )

    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    database._get_conn().execute(
        "UPDATE task_leases SET lease_expires_at = ? WHERE task_id = ?",
        (expired, str(task.id)),
    )
    database._get_conn().commit()

    second = store.claim_remote_task("exe-b")
    assert second is not None
    assert second["attempt"] == 2
    assert second["task"].assigned_executor == "exe-b"
    assert store.get_remote_lease(task.id, "exe-a", first["lease_token"]) is None
    database.reset_db_path()


@pytest.mark.parametrize(
    ("control", "lease_phase", "expected_status", "expected_lease_state"),
    [
        ("cancel", "active", "cancelled", "cancelled"),
        ("cancel", "uploading", "cancelled", "cancelled"),
        ("pause", "active", "paused", "paused"),
        ("pause", "uploading", "paused", "paused"),
    ],
)
def test_cancel_or_pause_terminates_mutable_remote_lease(
    sync_client,
    monkeypatch,
    control,
    lease_phase,
    expected_status,
    expected_lease_state,
):
    client, _queue, _tmp_path = sync_client
    created = client.post(
        "/api/tasks",
        json={
            "task_type": "pipeline",
            "source": "https://example.com/video",
            "requested_executor": "exe",
        },
    ).json()
    claim = _register_and_claim(client)
    if lease_phase == "uploading":
        assert database.get_task_store().begin_remote_upload(
            UUID(created["id"]),
            "exe-test",
            claim["lease_token"],
        )

    control_queue = TaskQueue()
    monkeypatch.setattr(tasks, "get_task_queue", lambda: control_queue)
    response = client.post(f"/api/tasks/{created['id']}/{control}")
    assert response.status_code == 200, response.text
    assert client.get(f"/api/tasks/{created['id']}").json()["status"] == expected_status
    assert _lease_state(created["id"]) == expected_lease_state

    renewed = client.post(
        f"/api/workers/exe-test/tasks/{created['id']}/lease/renew",
        json={"lease_token": claim["lease_token"], "lease_seconds": 900},
    )
    assert renewed.status_code == 409


def test_lease_renewal_requires_processing_task(tmp_path, monkeypatch):
    runtime = RuntimeSettings(data_root=str(tmp_path))
    monkeypatch.setattr(settings_module, "_runtime_settings", runtime)
    database.reset_db_path(tmp_path)
    store = database.get_task_store()
    task = _task()
    store.save(task)
    store.register_worker(worker_id="exe-a", name="A")
    claim = store.claim_remote_task("exe-a")
    assert claim is not None

    database._get_conn().execute(
        "UPDATE tasks SET status = 'cancelled' WHERE id = ?",
        (str(task.id),),
    )
    database._get_conn().commit()

    assert store.renew_remote_lease(
        task.id,
        "exe-a",
        claim["lease_token"],
    ) is None
    assert _lease_state(task.id) == "active"
    database.reset_db_path()


def test_complete_remote_task_requires_finalizing_lease(tmp_path, monkeypatch):
    runtime = RuntimeSettings(data_root=str(tmp_path))
    monkeypatch.setattr(settings_module, "_runtime_settings", runtime)
    database.reset_db_path(tmp_path)
    store = database.get_task_store()
    task = _task()
    store.save(task)
    store.register_worker(worker_id="exe-a", name="A")
    claim = store.claim_remote_task("exe-a")
    assert claim is not None

    completed, _already_completed = store.complete_remote_task(
        task.id,
        "exe-a",
        claim["lease_token"],
        {},
    )
    assert completed is None
    assert store.begin_remote_upload(task.id, "exe-a", claim["lease_token"])
    completed, _already_completed = store.complete_remote_task(
        task.id,
        "exe-a",
        claim["lease_token"],
        {},
    )
    assert completed is None
    assert store.begin_remote_finalization(task.id, "exe-a", claim["lease_token"])
    completed, already_completed = store.complete_remote_task(
        task.id,
        "exe-a",
        claim["lease_token"],
        {},
    )
    assert completed is not None
    assert completed.status == TaskStatus.COMPLETED
    assert already_completed is False
    database.reset_db_path()


def test_archive_upload_finishes_task_and_is_idempotent(sync_client):
    client, _queue, _tmp_path = sync_client
    created = client.post(
        "/api/tasks",
        json={
            "task_type": "pipeline",
            "source": "https://example.com/video",
            "origin_client": "apk",
            "requested_executor": "exe",
        },
    ).json()
    claim = _register_and_claim(client)
    assert claim["task"]["id"] == created["id"]

    archive_bytes = _zip_bytes(
        {
            "metadata.json": json.dumps(
                {
                    "title": "Remote result",
                    "status": "processing",
                    "extra": {
                        "downloaded_image_paths": [
                            "D:/MPP/local-result/images/00.jpg",
                        ],
                    },
                }
            ),
            "summary.md": "# Summary\nDone\n",
            "images/00.jpg": b"jpeg-data",
            "analysis.json": json.dumps(
                {
                    "image_descriptions": [
                        {
                            "index": 0,
                            "image_path": "D:/MPP/local-result/images/00.jpg",
                        },
                    ],
                }
            ),
        }
    )
    portable_result = {
        "image_descriptions": [
            {
                "index": 0,
                "image_path": "images/00.jpg",
                "text": "图片说明",
            },
        ],
        "analysis": {
            "image_descriptions": [
                {
                    "index": 0,
                    "image_path": "images/00.jpg",
                },
            ],
        },
        "warnings": [{"code": "test-warning"}],
        "output_dir": "D:/MPP/local-result",
        "remote_sync": {"api_token": "must-not-merge"},
    }
    endpoint = f"/api/workers/exe-test/tasks/{created['id']}/archive"
    completed = client.post(
        endpoint,
        data={
            "lease_token": claim["lease_token"],
            "result_json": json.dumps(portable_result, ensure_ascii=False),
        },
        files={"archive": ("result.zip", archive_bytes, "application/zip")},
    )
    assert completed.status_code == 200, completed.text
    payload = completed.json()
    assert payload["already_completed"] is False
    assert payload["task"]["status"] == "completed"
    assert payload["task"]["assigned_executor"] == "exe-test"
    assert payload["task"]["sync_revision"] >= 2

    output_dir = Path(payload["task"]["result"]["output_dir"])
    assert (output_dir / "summary.md").read_text(encoding="utf-8") == "# Summary\nDone\n"
    metadata = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "completed"
    assert metadata["task_id"] == created["id"]
    expected_image = str((output_dir / "images" / "00.jpg").resolve())
    assert metadata["extra"]["downloaded_image_paths"] == [expected_image]
    analysis = json.loads((output_dir / "analysis.json").read_text(encoding="utf-8"))
    assert analysis["image_descriptions"][0]["image_path"] == expected_image
    result = payload["task"]["result"]
    assert result["image_descriptions"][0]["image_path"] == expected_image
    assert result["analysis"]["image_descriptions"][0]["image_path"] == expected_image
    assert result["warnings"] == [{"code": "test-warning"}]
    assert "api_token" not in json.dumps(result)

    repeated = client.post(
        endpoint,
        data={"lease_token": claim["lease_token"]},
        files={"archive": ("result.zip", archive_bytes, "application/zip")},
    )
    assert repeated.status_code == 200
    assert repeated.json()["already_completed"] is True


def test_cancel_during_archive_upload_prevents_publish(sync_client, monkeypatch):
    client, _queue, _tmp_path = sync_client
    created = client.post(
        "/api/tasks",
        json={
            "task_type": "pipeline",
            "source": "https://example.com/video",
            "requested_executor": "exe",
        },
    ).json()
    claim = _register_and_claim(client)
    task_id = UUID(created["id"])
    output_dir = Path(created["result"]["output_dir"])
    archive_bytes = _zip_bytes(
        {
            "metadata.json": json.dumps({"title": "Cancelled upload"}),
            "summary.md": "# This must not be published",
        }
    )
    original_stream_upload = sync.stream_upload_to_path
    control_queue = TaskQueue()

    async def cancel_after_upload_stream(upload, destination, **kwargs):
        streamed = await original_stream_upload(upload, destination, **kwargs)
        assert await control_queue.cancel(task_id)
        return streamed

    monkeypatch.setattr(sync, "stream_upload_to_path", cancel_after_upload_stream)
    response = client.post(
        f"/api/workers/exe-test/tasks/{task_id}/archive",
        data={"lease_token": claim["lease_token"]},
        files={"archive": ("result.zip", archive_bytes, "application/zip")},
    )

    assert response.status_code == 409
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == "cancelled"
    assert _lease_state(task_id) == "cancelled"
    assert not (output_dir / "summary.md").exists()


def test_archive_upload_rejects_path_traversal(sync_client):
    client, _queue, tmp_path = sync_client
    created = client.post(
        "/api/tasks",
        json={
            "task_type": "pipeline",
            "source": "https://example.com/video",
            "requested_executor": "exe",
        },
    ).json()
    claim = _register_and_claim(client)
    archive_bytes = _zip_bytes(
        {
            "metadata.json": "{}",
            "../escaped.txt": "blocked",
        }
    )
    response = client.post(
        f"/api/workers/exe-test/tasks/{created['id']}/archive",
        data={"lease_token": claim["lease_token"]},
        files={"archive": ("unsafe.zip", archive_bytes, "application/zip")},
    )
    assert response.status_code == 400
    assert not (tmp_path.parent / "escaped.txt").exists()
    task = client.get(f"/api/tasks/{created['id']}").json()
    assert task["status"] == "processing"
    assert _lease_state(created["id"]) == "active"


def test_safe_extract_rejects_symlink(tmp_path):
    zip_path = tmp_path / "symlink.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("metadata.json", "{}")
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (0o120777 << 16)
        archive.writestr(link, "../outside")

    with pytest.raises(ValueError, match="symbolic links"):
        safe_extract_zip(zip_path, tmp_path / "extract")


def test_safe_extract_limits_structured_json_size(tmp_path, monkeypatch):
    monkeypatch.setattr(archive_sync, "MAX_STRUCTURED_JSON_BYTES", 8)
    zip_path = tmp_path / "large-json.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("metadata.json", '{"title":"too large"}')

    with pytest.raises(ValueError, match="structured JSON member is too large"):
        safe_extract_zip(zip_path, tmp_path / "extract")


def test_sync_storage_sweep_recovers_backup_and_removes_stale_staging(tmp_path):
    staging = tmp_path / "_remote_sync" / "abandoned-upload"
    staging.mkdir(parents=True)
    (staging / "archive.zip").write_bytes(b"partial")
    os.utime(staging, (1, 1))

    backup = tmp_path / f".recovered.sync-backup-{'a' * 32}"
    backup.mkdir()
    (backup / "metadata.json").write_text("{}", encoding="utf-8")

    stale_backup = tmp_path / f".current.sync-backup-{'b' * 32}"
    stale_backup.mkdir()
    os.utime(stale_backup, (1, 1))
    current = tmp_path / "current"
    current.mkdir()

    result = archive_sync.sweep_stale_sync_storage(
        tmp_path,
        max_age_seconds=0,
    )

    assert result == {"removed": 2, "restored": 1}
    assert not staging.exists()
    assert not stale_backup.exists()
    assert (tmp_path / "recovered" / "metadata.json").is_file()


def test_archive_download_excludes_media_by_default(sync_client):
    client, _queue, tmp_path = sync_client
    archive_dir = tmp_path / "completed"
    (archive_dir / "images").mkdir(parents=True)
    (archive_dir / "metadata.json").write_text(
        json.dumps({"title": "Portable"}),
        encoding="utf-8",
    )
    (archive_dir / "summary.md").write_text("# Portable", encoding="utf-8")
    (archive_dir / "images" / "00.jpg").write_bytes(b"image")
    (archive_dir / "video.mp4").write_bytes(b"video")

    task = _task(
        executor="server",
        status=TaskStatus.COMPLETED,
        result={
            "output_dir": str(archive_dir),
            "metadata": {"title": "Portable"},
        },
    )
    database.get_task_store().save(task)

    without_media = client.get(f"/api/sync/tasks/{task.id}/archive")
    assert without_media.status_code == 200
    with zipfile.ZipFile(io.BytesIO(without_media.content)) as archive:
        names = set(archive.namelist())
        assert "metadata.json" in names
        assert "summary.md" in names
        assert "images/00.jpg" in names
        assert "video.mp4" not in names
        manifest = json.loads(archive.read("_sync_manifest.json"))
        assert manifest["include_media"] is False

    with_media = client.get(
        f"/api/sync/tasks/{task.id}/archive",
        params={"include_media": "true"},
    )
    assert with_media.status_code == 200
    with zipfile.ZipFile(io.BytesIO(with_media.content)) as archive:
        assert "video.mp4" in archive.namelist()
