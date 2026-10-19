from pathlib import Path
import json
import sys
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core import database, remote_execution as execution, settings as settings_module
from app.core.settings import RuntimeSettings
from app.models import Task, TaskStatus, TaskType
from app.api.routes import execution as route, sync
from app.core.archive_sync import build_archive_zip


@pytest.fixture
def setup(tmp_path, monkeypatch):
    runtime = RuntimeSettings(data_root=str(tmp_path), remote_execution_enabled=True,
        remote_worker_id="desktop", remote_worker_name="Desktop")
    monkeypatch.setattr(settings_module, "_runtime_settings", runtime)
    monkeypatch.setattr(execution, "_last_seen", 0)
    database.reset_db_path(tmp_path)
    app = FastAPI()
    app.include_router(route.router, prefix="/api")
    app.include_router(sync.router, prefix="/api")
    yield runtime, database.get_task_store(), TestClient(app)
    database.close_db()


def waiting(store, source="https://example.com/video"):
    task = Task(task_type=TaskType.PIPELINE, status=TaskStatus.QUEUED, source=source)
    store.save(task)
    assert execution.defer(task)
    return task


def test_claim_is_exclusive_and_oldest_first(setup):
    _, store, _ = setup
    first = waiting(store)
    waiting(store)
    assigned = execution.poll("desktop", capacity=True)["task"]
    assert assigned["id"] == str(first.id)
    assert store.get(first.id).status == TaskStatus.PROCESSING
    assert execution.poll("desktop", capacity=True)["task"]["id"] == str(first.id)
    with pytest.raises(ValueError):
        execution.poll("another", capacity=True)


def test_remote_archive_name_fits_linux_utf8_component():
    name = sync._safe_archive_name("长中文标题🎥" * 40)
    assert len(name.encode("utf-8")) <= 200
    assert name == ("长中文标题🎥" * 40)[:len(name)]
    assert sync._safe_archive_name("a/b:c") == "a_b_c"


def test_worker_presence_keeps_busy_worker_priority_and_offline_falls_back(setup, monkeypatch):
    _, store, _ = setup
    monkeypatch.setattr(execution.time, "time", lambda: 1000)
    task = waiting(store)
    monkeypatch.setattr(execution.time, "time", lambda: 1100)
    execution.poll("desktop", capacity=False)
    monkeypatch.setattr(execution.time, "time", lambda: 1200)
    assert execution.fallbacks() == []
    monkeypatch.setattr(execution.time, "time", lambda: 1230)
    assert execution.fallbacks() == [task.id]
    assert not execution.defer(store.get(task.id))
    assert execution.poll("desktop", capacity=True) == {}


@pytest.mark.asyncio
async def test_server_restart_allows_reconnection_before_cloud_fallback(setup, monkeypatch):
    from app.services.remote_task_worker import RemoteTaskWorker
    _, store, _ = setup
    monkeypatch.setattr(execution.time, "time", lambda: 1000)
    task = waiting(store)
    monkeypatch.setattr(execution.time, "time", lambda: 1200)
    service = RemoteTaskWorker()
    await service.start()
    try:
        assert execution.fallbacks() == []
        assert store.get(task.id).status == TaskStatus.QUEUED
    finally:
        await service.stop()
    monkeypatch.setattr(execution.time, "time", lambda: 1330)
    assert execution.fallbacks() == [task.id]


def test_lost_heartbeat_never_starts_second_copy(setup, monkeypatch):
    _, store, _ = setup
    monkeypatch.setattr(execution.time, "time", lambda: 1000)
    task = waiting(store)
    execution.poll("desktop", capacity=True)
    monkeypatch.setattr(execution.time, "time", lambda: 2000)
    assert execution.fallbacks() == []
    assert store.get(task.id).status == TaskStatus.PROCESSING
    assert execution.defer(store.get(task.id))


def test_progress_cancel_and_retry_fence(setup):
    _, store, _ = setup
    task = waiting(store)
    claim = execution.poll("desktop", capacity=True)["task"]
    attempt = claim["options"][execution.KEY]["attempt"]
    response = execution.poll("desktop", {"id": str(task.id), "attempt": attempt,
        "status": "processing", "progress": 0.6, "current_step": "transcribe", "message": "60%",
        "completed_steps": ["download", "transcribe"]})
    assert response["command"] == "processing"
    assert store.get(task.id).progress == 0.6
    store.update_status(task.id, TaskStatus.CANCELLED)
    assert execution.poll("desktop", {"id": str(task.id), "attempt": attempt})["command"] == "cancelled"
    with pytest.raises(HTTPException):
        execution.require_owner(store.get(task.id), "desktop", attempt)
    store.update_status(task.id, TaskStatus.QUEUED)
    assert execution.defer(store.get(task.id))
    assert store.get(task.id).completed_steps == []
    new = execution.poll("desktop", capacity=True)["task"]["options"][execution.KEY]["attempt"]
    assert new != attempt
    with pytest.raises(HTTPException):
        execution.require_owner(store.get(task.id), "desktop", attempt)


def test_only_new_or_downloaded_pipeline_tasks_defer(setup, tmp_path):
    runtime, store, _ = setup
    task = Task(task_type=TaskType.PIPELINE, source="https://example.com", completed_steps=["download", "transcribe"])
    assert not execution.defer(task)
    task.completed_steps = ["download"]
    assert execution.defer(task)
    task.completed_steps = []
    runtime.remote_execution_enabled = False
    assert not execution.defer(task)
    runtime.remote_execution_enabled = True
    task.source = str(tmp_path.parent / "outside.mp4")
    assert not execution.defer(task)


def test_uploaded_source_requires_matching_attempt_and_managed_path(setup, tmp_path):
    _, store, client = setup
    media = tmp_path / "手机🎥.mp4"
    media.write_bytes(b"media-content")
    task = waiting(store, str(media))
    claim = client.post("/api/execution/poll", json={"worker_id": "desktop", "capacity": True}).json()["task"]
    params = {"worker_id": "desktop", "attempt": claim["options"][execution.KEY]["attempt"]}
    assert client.get(f"/api/execution/tasks/{task.id}/source", params=params).content == b"media-content"
    params["attempt"] = "old"
    assert client.get(f"/api/execution/tasks/{task.id}/source", params=params).status_code == 409


def test_paused_task_does_not_block_next_claim(setup):
    _, store, _ = setup
    first = waiting(store)
    second = waiting(store)
    execution.poll("desktop", capacity=True)
    store.update_status(first.id, TaskStatus.PAUSED)
    assert execution.poll("desktop", capacity=True)["task"]["id"] == str(second.id)


def test_result_import_fenced_preserves_server_identity(setup, tmp_path):
    _, store, client = setup
    task = waiting(store)
    claim = execution.poll("desktop", capacity=True)["task"]
    attempt = claim["options"][execution.KEY]["attempt"]
    archive = tmp_path / "result"
    archive.mkdir()
    title = "长中文标题🎥" * 40
    (archive / "metadata.json").write_text(json.dumps({"title": title, "task_id": str(task.id)}))
    (archive / "summary.md").write_text("# Result")
    package = tmp_path / "result.zip"
    build_archive_zip(archive, package, include_media=True)
    completed = Task(id=task.id, task_type=TaskType.PIPELINE, source="C:/worker/private.mp4",
        status=TaskStatus.COMPLETED, options={execution.ORIGIN: {"attempt": attempt}})
    data = {"task_json": completed.model_dump_json(), "archive_name": title, "worker_id": "desktop"}
    def upload():
        with package.open("rb") as f:
            return client.post("/api/sync/import", data=data, files={"archive": ("result.zip", f, "application/zip")})
    assert upload().status_code == 409
    data["execution_id"] = "wrong"
    assert upload().status_code == 409
    data["execution_id"] = attempt
    response = upload()
    assert response.status_code == 200, response.text
    result = store.get(task.id)
    assert result.status == TaskStatus.COMPLETED
    assert result.source == task.source
    assert result.options[execution.KEY]["attempt"] == attempt
    assert execution.ORIGIN not in result.options
    destination = Path(result.result["output_dir"])
    assert len(destination.name.encode("utf-8")) <= 200
    assert json.loads((destination / "metadata.json").read_text(encoding="utf-8"))["title"] == title
    assert upload().status_code == 200


@pytest.mark.asyncio
async def test_worker_reconnects_before_restarting_and_obeys_cancel(setup, monkeypatch):
    from app.services import remote_task_worker as worker_module
    import httpx
    runtime, store, _ = setup
    runtime.remote_server_url = "https://cloud.example"
    runtime.remote_execution_enabled = False
    local = Task(task_type=TaskType.PIPELINE, source="https://example.com/video",
        status=TaskStatus.QUEUED, options={execution.ORIGIN: {
            "server_url": runtime.remote_server_url, "attempt": "attempt-1"}})
    store.save(local)
    commands = []
    class Queue:
        active_task_ids = set()
        pending_count = 0
        def get_queue_snapshot(self): return []
        async def submit(self, task_id): commands.append(("submit", task_id))
        async def cancel(self, task_id): commands.append(("cancel", task_id))
        async def pause(self, task_id): commands.append(("pause", task_id))
    queue = Queue()
    monkeypatch.setattr(worker_module, "get_task_queue", lambda: queue)
    monkeypatch.setattr(worker_module, "httpx_client_kwargs", lambda _: {"trust_env": False})
    reply = {"command": "processing"}
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=reply)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(worker_module.httpx, "AsyncClient", lambda **kwargs:
        original_client(**kwargs, transport=httpx.MockTransport(handle)))
    worker = worker_module.RemoteTaskWorker()
    await worker.tick(runtime)
    assert requests[0]["state"]["attempt"] == "attempt-1"
    assert commands == [("submit", local.id)]
    queue.active_task_ids = {local.id}
    await worker.tick(runtime)
    assert len(commands) == 1
    reply["command"] = "cancelled"
    await worker.tick(runtime)
    assert commands[-1] == ("cancel", local.id)
    assert store.get(local.id).options[execution.ORIGIN]["settled"] is True


@pytest.mark.asyncio
async def test_upload_keeps_heartbeat_alive_until_result_commits(setup, monkeypatch):
    import asyncio
    import httpx
    from app.services import remote_task_worker as worker_module
    runtime, store, _ = setup
    finish = asyncio.Event()
    calls = []
    class Uploader:
        async def _upload(self, *args):
            await finish.wait()
            calls.append("uploaded")
    class Client:
        async def post(self, url, json):
            calls.append(json)
            finish.set()
            return httpx.Response(200, json={"command": "processing"}, request=httpx.Request("POST", url))
    original_wait = asyncio.wait
    async def quick_wait(tasks, **kwargs):
        return await original_wait(tasks, timeout=0)
    monkeypatch.setattr(worker_module.asyncio, "wait", quick_wait)
    worker = worker_module.RemoteTaskWorker()
    await worker.upload_result(Uploader(), None, None, "https://cloud.example", runtime,
        store, Client(), {"id": "task", "attempt": "attempt"})
    assert calls[0]["state"]["attempt"] == "attempt"
    assert calls[0]["capacity"] is False
    assert calls[-1] == "uploaded"
