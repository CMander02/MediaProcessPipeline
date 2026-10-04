"""Updating yt-dlp restarts the backend only when no task is running or queued."""

import asyncio
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.api.routes import settings as settings_route  # noqa: E402
from app.core import queue as queue_module  # noqa: E402
from app.core import settings as settings_module  # noqa: E402
from app.core.settings import RuntimeSettings  # noqa: E402
from app.services.ingestion import ytdlp_version  # noqa: E402


class FakeQueue:
    def __init__(self, active: int = 0, pending: int = 0):
        self.active_task_ids = {f"task-{index}" for index in range(active)}
        self.pending_count = pending


class DrainingQueue:
    """Busy for the first few checks, then idle."""

    def __init__(self, busy_checks: int):
        self.checks = 0
        self.busy_checks = busy_checks
        self.pending_count = 0

    @property
    def active_task_ids(self) -> set[str]:
        self.checks += 1
        return {"task"} if self.checks <= self.busy_checks else set()


def _client(tmp_path, monkeypatch, queue, *, upgrade_ok: bool = True) -> TestClient:
    settings = RuntimeSettings(data_root=str(tmp_path), api_token="")
    monkeypatch.setattr(settings_module, "_runtime_settings", settings)
    monkeypatch.setattr(settings_module, "_save_settings_to_file", lambda _settings: None)
    monkeypatch.setattr(settings_route, "get_runtime_settings", lambda: settings)
    monkeypatch.setattr(queue_module, "get_task_queue", lambda: queue)
    monkeypatch.setattr(
        ytdlp_version,
        "upgrade",
        lambda: {
            "ok": upgrade_ok,
            "old": "2026.03.17",
            "new": "2026.07.09",
            "output": "",
            "restart_recommended": upgrade_ok,
        },
    )
    app = FastAPI()
    app.include_router(settings_route.router, prefix="/api")
    return TestClient(app)


def _upgrade(client: TestClient) -> dict:
    response = client.post("/api/settings/ytdlp/upgrade", headers={"X-Requested-With": "fetch"})
    assert response.status_code == 200
    return response.json()


def test_restarts_right_away_when_the_queue_is_idle(tmp_path, monkeypatch):
    restarts: list[float] = []
    monkeypatch.setattr(ytdlp_version, "schedule_process_restart", lambda delay: restarts.append(delay))
    waits: list[bool] = []
    monkeypatch.setattr(settings_route, "_restart_when_idle", lambda: waits.append(True))

    result = _upgrade(_client(tmp_path, monkeypatch, FakeQueue()))

    assert result["restart_scheduled"] is True
    assert result["restart_after_tasks"] == 0
    assert restarts == [1.0]
    assert waits == []


def test_waits_for_running_and_queued_tasks_before_restarting(tmp_path, monkeypatch):
    restarts: list[float] = []
    monkeypatch.setattr(ytdlp_version, "schedule_process_restart", lambda delay: restarts.append(delay))
    waits: list[bool] = []
    monkeypatch.setattr(settings_route, "_restart_when_idle", lambda: waits.append(True))

    result = _upgrade(_client(tmp_path, monkeypatch, FakeQueue(active=2, pending=1)))

    assert result["restart_scheduled"] is True
    assert result["restart_after_tasks"] == 3
    assert restarts == []
    assert waits == [True]


def test_no_restart_when_the_upgrade_fails(tmp_path, monkeypatch):
    restarts: list[float] = []
    monkeypatch.setattr(ytdlp_version, "schedule_process_restart", lambda delay: restarts.append(delay))

    result = _upgrade(_client(tmp_path, monkeypatch, FakeQueue(active=1), upgrade_ok=False))

    assert result["restart_scheduled"] is False
    assert result["restart_after_tasks"] == 0
    assert restarts == []


def test_the_waiting_restart_fires_once_the_queue_drains(monkeypatch):
    queue = DrainingQueue(busy_checks=2)
    monkeypatch.setattr(queue_module, "get_task_queue", lambda: queue)
    restarts: list[float] = []
    monkeypatch.setattr(ytdlp_version, "schedule_process_restart", lambda delay: restarts.append(delay))

    asyncio.run(settings_route._wait_until_idle_then_restart(poll_sec=0))

    assert queue.checks == 3
    assert restarts == [1.0]
