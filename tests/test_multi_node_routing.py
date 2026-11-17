from pathlib import Path
import json
import sys
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.api.routes import execution as execution_route, settings as settings_route, tasks
from app.core import database, remote_execution as execution, settings as settings_module
from app.core.settings import RuntimeSettings
from app.models import Task, TaskStatus, TaskType


@pytest.fixture
def setup(tmp_path, monkeypatch):
    runtime = RuntimeSettings(data_root=str(tmp_path), remote_execution_enabled=True,
                              api_token="server-token")
    monkeypatch.setattr(settings_module, "_runtime_settings", runtime)
    monkeypatch.setattr(execution, "_server_started_at", 0)
    monkeypatch.setattr(execution, "host_capabilities", lambda: {
        "cuda_available": False, "mps_available": False, "metal_available": False,
        "cpu_count": 2, "memory_bytes": 4 * 1024**3,
    })
    database.reset_db_path(tmp_path)
    app = FastAPI()
    app.include_router(execution_route.router, prefix="/api")
    app.include_router(tasks.router, prefix="/api")
    try:
        with TestClient(app) as client:
            yield runtime, database.get_task_store(), client
    finally:
        database.reset_db_path()


def waiting(store, target=None):
    task = Task(task_type=TaskType.PIPELINE, status=TaskStatus.QUEUED,
                source="https://example.com/video", options=(
                    {execution.KEY: {"target_worker_id": target}} if target else {}))
    store.save(task)
    assert execution.defer(task)
    return task


def poll(client, worker_id="desktop", **fields):
    return client.post("/api/execution/poll", headers={"Authorization": "Bearer server-token"},
                       json={"worker_id": worker_id, **fields})


def test_authenticated_multi_node_enrollment_and_persistent_hardware(setup, monkeypatch):
    runtime, _, client = setup
    monkeypatch.setattr(execution.time, "time", lambda: 1000)
    assert poll(client, "windows", worker_name="台式机", platform="windows", capacity=True,
                compute_capabilities={"cuda_available": True, "cpu_count": 16}).status_code == 200
    assert poll(client, "mac", worker_name="Mac", platform="macos", capacity=False,
                compute_capabilities={"metal_available": True, "mps_available": True}).status_code == 200
    assert poll(client, "windows", capacity=True).status_code == 200
    database.reset_db_path(Path(runtime.data_root))
    nodes = {node["node_id"]: node for node in client.get("/api/execution/nodes").json()["nodes"]}
    assert set(nodes) == {"local", "windows", "mac"}
    assert nodes["windows"]["display_name"] == "台式机"
    assert nodes["windows"]["platform"] == "windows"
    assert nodes["windows"]["compute_devices"] == ["cpu", "cuda"]
    assert nodes["windows"]["capabilities"]["cpu_count"] == 16
    assert not nodes["windows"]["busy"]
    assert nodes["mac"]["busy"]
    assert nodes["mac"]["compute_devices"] == ["cpu", "mps", "metal"]
    assert nodes["mac"]["last_heartbeat_at"] == 1000
    monkeypatch.setattr(execution.time, "time", lambda: 1046)
    nodes = client.get("/api/execution/nodes").json()["nodes"]
    assert nodes[0]["node_id"] == "local" and nodes[0]["online"]
    assert all(not node["online"] for node in nodes[1:])


def test_open_registration_requires_nonempty_server_token_and_reserves_local(setup):
    runtime, _, client = setup
    assert client.post("/api/execution/poll", json={"worker_id": "desktop"}).status_code == 401
    assert client.post("/api/execution/poll", headers={"Authorization": "Bearer wrong"},
                       json={"worker_id": "desktop"}).status_code == 401
    runtime.api_token = ""
    assert poll(client).status_code == 401
    runtime.api_token = "server-token"
    assert poll(client, "local").status_code == 400
    assert client.get("/api/execution/nodes").json()["nodes"][0]["node_id"] == "local"


def test_targeted_claim_is_exclusive_and_preferred_is_default(setup):
    runtime, store, client = setup
    runtime.remote_preferred_worker_id = "windows"
    targeted = waiting(store, "mac")
    default = waiting(store)
    assert poll(client, "other", capacity=True).json() == {}
    assert poll(client, "windows", capacity=True).json()["task"]["id"] == str(default.id)
    assert poll(client, "mac", capacity=True).json()["task"]["id"] == str(targeted.id)
    assert poll(client, "other", capacity=True).json() == {}
    assert {node["node_id"]: node for node in client.get("/api/execution/nodes").json()["nodes"]}["mac"]["busy"]


def test_wait_policy_never_falls_back_and_server_restart_grace(setup, monkeypatch):
    runtime, store, client = setup
    runtime.remote_execution_offline_policy = "wait"
    monkeypatch.setattr(execution.time, "time", lambda: 1000)
    task = waiting(store, "offline")
    monkeypatch.setattr(execution.time, "time", lambda: 10000)
    assert execution.fallbacks() == []
    assert poll(client, "other", capacity=True).json() == {}
    assert store.get(task.id).status == TaskStatus.QUEUED
    runtime.remote_execution_offline_policy = "fallback_server"
    execution.server_started()
    assert execution.fallbacks() == []
    monkeypatch.setattr(execution.time, "time", lambda: 10121)
    assert execution.fallbacks() == [task.id]


def test_fallback_any_online_transfers_after_timeout_and_keeps_attempt(setup, monkeypatch):
    runtime, store, client = setup
    runtime.remote_preferred_worker_id = "windows"
    runtime.remote_execution_offline_policy = "fallback_any_online"
    monkeypatch.setattr(execution.time, "time", lambda: 1000)
    task = waiting(store)
    assert poll(client, "mac", capacity=True).json() == {}
    monkeypatch.setattr(execution.time, "time", lambda: 1121)
    assert execution.fallbacks() == []
    claim = poll(client, "mac", capacity=True).json()["task"]
    attempt = claim["options"][execution.KEY]["attempt"]
    monkeypatch.setattr(execution.time, "time", lambda: 5000)
    assert execution.fallbacks() == []
    assert poll(client, "windows", capacity=True).json() == {}
    assert poll(client, "mac", capacity=True).json()["task"]["options"][execution.KEY]["attempt"] == attempt
    runtime.remote_execution_enabled = False
    assert execution.defer(store.get(task.id))


def test_online_busy_preferred_node_keeps_priority(setup, monkeypatch):
    runtime, store, client = setup
    runtime.remote_execution_offline_policy = "fallback_any_online"
    monkeypatch.setattr(execution.time, "time", lambda: 1000)
    task = waiting(store, "windows")
    monkeypatch.setattr(execution.time, "time", lambda: 1100)
    poll(client, "windows", capacity=False)
    monkeypatch.setattr(execution.time, "time", lambda: 1200)
    assert poll(client, "mac", capacity=True).json() == {}
    assert store.get(task.id).status == TaskStatus.QUEUED
    monkeypatch.setattr(execution.time, "time", lambda: 1221)
    assert poll(client, "mac", capacity=True).json()["task"]["id"] == str(task.id)


def test_local_route_runs_without_remote_claim_and_legacy_whitelist(setup):
    runtime, store, client = setup
    local = Task(task_type=TaskType.PIPELINE, source="https://example.com/video",
                 status=TaskStatus.QUEUED, options={execution.KEY: {"target_worker_id": "local"}})
    store.save(local)
    assert not execution.defer(local)
    assert execution.fallbacks() == []
    assert poll(client, capacity=True).json() == {}
    runtime.remote_worker_id = "legacy"
    runtime.remote_preferred_worker_id = "new"
    assert poll(client, "new", capacity=True).status_code == 400
    explicit = waiting(store, "legacy")
    assert poll(client, "legacy", capacity=True).json()["task"]["id"] == str(explicit.id)
    runtime.api_token = ""
    assert client.post("/api/execution/poll", json={"worker_id": "legacy"}).status_code == 200


@pytest.mark.parametrize("endpoint", ["", "/batch", "/import"])
@pytest.mark.parametrize("target", ["local", "mac"])
def test_task_entrypoints_persist_target_and_discard_forged_lease(setup, monkeypatch, endpoint, target):
    from app.core import task_import
    _, store, client = setup
    queue = Mock(submit=AsyncMock())
    monkeypatch.setattr(tasks, "get_task_queue", lambda: queue)
    monkeypatch.setattr(tasks, "_validate_public_http_url", lambda _: None)
    monkeypatch.setattr(task_import, "detect_import_collection", lambda _: None)
    body = {"task_type": "pipeline", "target_worker_id": target,
            "options": {execution.KEY: {"attempt": "forged", "worker_id": "forged"}}}
    body["source" if not endpoint else "sources"] = (
        "https://example.com/video" if not endpoint else ["https://example.com/video"])
    response = client.post("/api/tasks" + endpoint, json=body)
    assert response.status_code == 200, response.text
    if endpoint == "/import":
        task = response.json()["items"][0]["task"]
    elif endpoint == "/batch":
        task = response.json()[0]
    else:
        task = response.json()
    assert task["options"][execution.KEY] == {"target_worker_id": target}
    assert len(store.list()) == 1
    queue.submit.assert_awaited_once()


def test_poll_validates_state_at_network_boundary(setup):
    _, _, client = setup
    assert poll(client, state={"id": "invalid", "attempt": "lease"}).status_code == 422
    assert poll(client, state={"id": "00000000-0000-0000-0000-000000000000", "attempt": "lease",
                               "progress": "invalid"}).status_code == 422


def test_rejected_attempt_does_not_publish_progress_event(setup, monkeypatch):
    _, store, client = setup
    task = waiting(store, "desktop")
    claim = poll(client, capacity=True).json()["task"]
    bus = Mock(publish=AsyncMock())
    monkeypatch.setattr(execution_route, "get_event_bus", lambda: bus)
    response = poll(client, state={"id": str(task.id), "attempt": "expired", "progress": 0.8})
    assert response.json() == {"command": "cancel"}
    assert store.get(task.id).progress == 0
    bus.publish.assert_not_awaited()
    assert poll(client, state={"id": str(task.id),
                              "attempt": claim["options"][execution.KEY]["attempt"],
                              "progress": 0.6}).json() == {"command": "processing"}
    assert store.get(task.id).progress == 0.6
    bus.publish.assert_awaited_once()


def test_remote_token_is_masked_and_restored(setup):
    runtime, _, _ = setup
    runtime.remote_api_token = "private-token"
    assert settings_route._mask_settings(runtime)["remote_api_token"] == "*" * len(runtime.remote_api_token)
    assert settings_route._restore_secrets({"remote_api_token": "*" * len(runtime.remote_api_token)}) == {}


def test_node_ids_are_normalized_at_request_and_settings_boundaries(setup):
    from app.models import TaskCreate, TaskBatchCreate
    _, _, client = setup
    assert RuntimeSettings(remote_worker_id=" desktop ", remote_preferred_worker_id=" mac ").remote_worker_id == "desktop"
    assert RuntimeSettings(remote_preferred_worker_id=" mac ").remote_preferred_worker_id == "mac"
    assert TaskCreate(task_type="pipeline", source="url", target_worker_id=" mac ").target_worker_id == "mac"
    assert TaskBatchCreate(sources=["url"], target_worker_id=" local ").target_worker_id == "local"
    assert poll(client, "   ").status_code == 422
    assert client.post("/api/tasks", json={"task_type": "pipeline", "source": "url",
                                         "target_worker_id": "  "}).status_code == 422


@pytest.mark.asyncio
async def test_worker_reports_stable_identity_hardware_and_name(setup, monkeypatch):
    from app.services import remote_task_worker as worker_module
    runtime, _, _ = setup
    runtime.remote_server_url = "https://server.example"
    runtime.remote_worker_name = "Portable Mac"
    monkeypatch.setattr(worker_module, "get_task_queue", lambda: Mock(active_task_ids=set(), pending_count=0))
    monkeypatch.setattr(worker_module, "httpx_client_kwargs", lambda _: {"trust_env": False})
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={})
    original_client = httpx.AsyncClient
    monkeypatch.setattr(worker_module.httpx, "AsyncClient", lambda **kwargs:
        original_client(**kwargs, transport=httpx.MockTransport(handle)))
    await worker_module.RemoteTaskWorker().tick(runtime)
    await worker_module.RemoteTaskWorker().tick(runtime)
    assert requests[0]["worker_id"] == requests[1]["worker_id"]
    assert requests[0]["worker_id"].startswith("desktop-")
    assert requests[0]["worker_name"] == "Portable Mac"
    assert requests[0]["platform"] in {"windows", "macos", "linux"}
    assert requests[0]["compute_capabilities"]["cpu_count"] == 2
