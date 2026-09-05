from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import database, settings
from app.core.workspace_lifecycle import (
    WorkspaceBusyError,
    drain_workspace_threads,
    run_in_thread,
    workspace_activity,
)
from app.models import Task, TaskStatus, TaskType
from app.services.kb import store as kb
from app.services.voiceprint import store as voice


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "old"
    root.mkdir()
    monkeypatch.setattr(
        settings, "_runtime_settings", settings.RuntimeSettings(data_root=str(root))
    )
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "config.json")
    database.reset_db_path(root)
    kb.reset_kb_store()
    voice.reset_voiceprint_store()
    settings._save_settings_to_file(settings.get_runtime_settings())
    yield root, tmp_path / "new"
    database.close_db()
    kb.reset_kb_store()
    voice.reset_voiceprint_store()


def test_switch_reopens_all_three_stores(workspace):
    old, new = workspace
    task = Task(task_type=TaskType.PIPELINE, source="old", status=TaskStatus.COMPLETED)
    database.get_task_store().save(task)
    old_kb = kb.get_kb_store()
    old_kb.chunk_count()
    old_voice = voice.get_voiceprint_store()
    person = old_voice.create_person("保留人物")
    settings.patch_runtime_settings({"data_root": str(new)})
    assert database.get_task_store().count() == 0
    assert kb.get_kb_store()._db_path == new / "kb.db"
    assert kb.get_kb_store().chunk_count() == 0
    assert voice.get_voiceprint_store().db_path == new / "voiceprints" / "library.db"
    assert voice.get_voiceprint_store().get_person(person.id) is None
    assert old_kb._conn is None
    assert old_voice._conn is None
    settings.patch_runtime_settings({"data_root": str(old)})
    assert database.get_task_store().get(task.id)
    assert voice.get_voiceprint_store().get_person(person.id).name == "保留人物"


def test_invalid_database_rolls_back_config_and_live_store(workspace):
    old, new = workspace
    new.mkdir()
    (new / "tasks.db").write_bytes(b"invalid database")
    with pytest.raises(Exception, match="database"):
        settings.patch_runtime_settings({"data_root": str(new)})
    assert settings.get_runtime_settings().data_root == str(old)
    assert json.loads(settings.SETTINGS_FILE.read_text(encoding="utf-8"))["data_root"] == str(old)
    assert database._get_db_path() == old / "tasks.db"
    assert database.get_task_store().count() == 0


@pytest.mark.parametrize(
    "status", [TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.PROCESSING, TaskStatus.PAUSED]
)
def test_active_task_blocks_switch(workspace, status):
    old, new = workspace
    database.get_task_store().save(
        Task(task_type=TaskType.PIPELINE, source="active", status=status)
    )
    with pytest.raises(WorkspaceBusyError):
        settings.patch_runtime_settings({"data_root": str(new)})
    assert settings.get_runtime_settings().data_root == str(old)


def test_background_operation_blocks_switch(workspace):
    old, new = workspace
    with workspace_activity():
        with pytest.raises(WorkspaceBusyError):
            settings.patch_runtime_settings({"data_root": str(new)})
    settings.patch_runtime_settings({"data_root": str(new)})


@pytest.mark.asyncio
async def test_cancel_drains_actual_thread_before_resources_are_released(workspace):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def worker():
        started.set()
        release.wait(timeout=5)
        database.get_task_store().count()
        finished.set()

    task = asyncio.create_task(run_in_thread(worker))
    await asyncio.to_thread(started.wait, 5)
    with pytest.raises(WorkspaceBusyError):
        settings.patch_runtime_settings({"data_root": str(workspace[1])})
    task.cancel()
    draining = asyncio.create_task(drain_workspace_threads())
    await asyncio.sleep(0)
    assert not task.done()
    assert not draining.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await draining
    assert finished.is_set()


def test_api_and_offline_core_share_busy_switch_behavior(workspace):
    from app.api.routes.settings import router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    old, new = workspace
    app = FastAPI()
    app.include_router(router, prefix="/api")
    with TestClient(app) as client, workspace_activity():
        response = client.patch("/api/settings", json={"data_root": str(new)})
        assert response.status_code == 409
        with pytest.raises(WorkspaceBusyError):
            settings.patch_runtime_settings({"data_root": str(new)})
    assert settings.get_runtime_settings().data_root == str(old)
    with TestClient(app) as client:
        response = client.patch("/api/settings", json={"data_root": str(new)})
        assert response.status_code == 200
    assert database._get_db_path() == new / "tasks.db"
