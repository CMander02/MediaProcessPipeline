from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.api.routes import pipeline, settings as settings_route, tasks  # noqa: E402
from app.core import database, pipeline as pipeline_core, queue as queue_module, settings as settings_module  # noqa: E402
from app.core.events import EventBus  # noqa: E402
from app.core.settings import RuntimeSettings  # noqa: E402
from app.models import Task, TaskStatus, TaskType  # noqa: E402


class FakeQueue:
    def __init__(self) -> None:
        self.submitted: list[UUID] = []
        self.cancelled: list[UUID] = []
        self.paused: list[UUID] = []
        self.resumed: list[UUID] = []
        self.checkpoint_rerun: list[UUID] = []
        self.deleted: list[UUID] = []

    async def submit(self, task_id: UUID) -> None:
        self.submitted.append(task_id)

    async def cancel(self, task_id: UUID) -> bool:
        self.cancelled.append(task_id)
        return False

    async def pause(self, task_id: UUID) -> bool:
        self.paused.append(task_id)
        return False

    async def resume(self, task_id: UUID, *, force: bool = False) -> bool:
        self.resumed.append(task_id)
        return False

    async def rerun_from_checkpoint(self, task_id: UUID) -> bool:
        self.checkpoint_rerun.append(task_id)
        return False

    async def delete(self, task_id: UUID):
        self.deleted.append(task_id)
        return None


def _client(tmp_path: Path, monkeypatch) -> tuple[TestClient, FakeQueue]:
    settings = RuntimeSettings(
        data_root=str(tmp_path),
        api_token="",
        siliconflow_api_base="https://api.siliconflow.cn",
        siliconflow_api_key="sk-test",
    )
    monkeypatch.setattr(settings_module, "_runtime_settings", settings)
    monkeypatch.setattr(settings_module, "_save_settings_to_file", lambda _settings: None)
    monkeypatch.setattr(settings_route, "get_runtime_settings", lambda: settings)
    monkeypatch.setattr(pipeline, "get_runtime_settings", lambda: settings)
    monkeypatch.setattr(pipeline_core, "get_runtime_settings", lambda: settings)
    database.reset_db_path(tmp_path)

    fake_queue = FakeQueue()
    monkeypatch.setattr(tasks, "get_task_queue", lambda: fake_queue)

    app = FastAPI()
    app.include_router(tasks.router, prefix="/api")
    app.include_router(pipeline.router, prefix="/api")
    app.include_router(settings_route.router, prefix="/api")
    return TestClient(app), fake_queue


def test_backend_api_smoke_triggers_core_boundaries(tmp_path, monkeypatch):
    client, queue = _client(tmp_path, monkeypatch)

    steps = client.get("/api/tasks/steps")
    assert steps.status_code == 200
    assert [step["id"] for step in steps.json()["steps"]][:2] == ["download", "separate"]

    task = client.post(
        "/api/tasks",
        json={
            "task_type": "pipeline",
            "source": str(tmp_path / "demo.mp4"),
            "options": {"force": True},
        },
    )
    assert task.status_code == 200
    task_data = task.json()
    assert task_data["status"] == "queued"
    assert queue.submitted == [UUID(task_data["id"])]
    assert task_data["flow"]["id"] == "url_media_asr"

    listed = client.get("/api/tasks")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == task_data["id"]

    stats = client.get("/api/tasks/stats")
    assert stats.status_code == 200
    assert stats.json()["queued"] == 1

    staged = client.post(
        "/api/pipeline/stage",
        files={"file": ("clip.mp3", b"fake audio", "audio/mpeg")},
    )
    assert staged.status_code == 200
    staged_data = staged.json()
    assert Path(staged_data["path"]).exists()
    assert staged_data["media_type"] == "audio"

    delete_staged = client.delete(f"/api/pipeline/stage/{staged_data['staging_id']}")
    assert delete_staged.status_code == 200
    assert delete_staged.json() == {"deleted": True}


def test_create_task_normalizes_schemeless_bilibili_opus(tmp_path, monkeypatch):
    client, queue = _client(tmp_path, monkeypatch)

    task = client.post(
        "/api/tasks",
        json={
            "task_type": "pipeline",
            "source": "bilibili.com/opus/1220469846869803016?spm_id_from=333.1365.0.0",
        },
    )

    assert task.status_code == 200
    data = task.json()
    assert data["source"] == "https://bilibili.com/opus/1220469846869803016?spm_id_from=333.1365.0.0"
    assert data["platform"] == "bilibili_opus"
    assert data["content_subtype"] == "image_note"
    assert data["flow"]["id"] == "url_image_note"
    assert queue.submitted == [UUID(data["id"])]

    metadata_path = Path(data["result"]["output_dir"]) / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["source_url"] == data["source"]
    assert metadata["platform"] == "bilibili_opus"


def test_create_task_accepts_bare_bilibili_bvid(tmp_path, monkeypatch):
    client, queue = _client(tmp_path, monkeypatch)

    task = client.post(
        "/api/tasks",
        json={"task_type": "pipeline", "source": "BV1XM411M7eD"},
    )

    assert task.status_code == 200
    data = task.json()
    assert data["source"] == "BV1XM411M7eD"
    assert data["platform"] == "bilibili_video"
    assert data["flow"]["id"] == "url_platform_video_subtitle"
    assert queue.submitted == [UUID(data["id"])]


def test_create_task_recovers_malformed_https_bilibili_bvid(tmp_path, monkeypatch):
    client, queue = _client(tmp_path, monkeypatch)

    task = client.post(
        "/api/tasks",
        json={"task_type": "pipeline", "source": "https://BV1XM411M7eD"},
    )

    assert task.status_code == 200
    data = task.json()
    assert data["source"] == "https://BV1XM411M7eD"
    assert data["platform"] == "bilibili_video"
    assert data["flow"]["id"] == "url_platform_video_subtitle"
    assert queue.submitted == [UUID(data["id"])]


def test_backend_settings_and_platform_api_smoke(tmp_path, monkeypatch):
    client, _queue = _client(tmp_path, monkeypatch)

    async def fake_fetch(api_base: str, api_key: str):
        assert api_base == "https://api.siliconflow.cn"
        assert api_key == "sk-test"
        return {
            "data": [
                {"id": "Qwen/Qwen3.5-8B"},
                {"id": "BAAI/bge-reranker-v2-m3"},
            ],
        }

    monkeypatch.setattr(settings_route, "_fetch_siliconflow_models_payload", fake_fetch)

    settings_response = client.get("/api/settings")
    assert settings_response.status_code == 200
    assert settings_response.json()["siliconflow_api_key"].startswith("***")

    model_response = client.get("/api/settings/providers/siliconflow/models")
    assert model_response.status_code == 200
    assert model_response.json()["models"] == [
        {"id": "Qwen/Qwen3.5-8B", "display_name": "Qwen/Qwen3.5-8B", "model_type": "llm"},
        {
            "id": "BAAI/bge-reranker-v2-m3",
            "display_name": "BAAI/bge-reranker-v2-m3",
            "model_type": "rerank",
        },
    ]

    platforms = client.get("/api/pipeline/platforms")
    assert platforms.status_code == 200
    assert {item["id"] for item in platforms.json()["platforms"]} >= {"bilibili", "youtube", "zhihu"}

    update = client.put("/api/pipeline/platforms/youtube", json={"preferred_quality": "1080p"})
    assert update.status_code == 200
    assert update.json() == {"ok": True}


def test_task_timeline_returns_persisted_events(tmp_path, monkeypatch):
    client, _queue = _client(tmp_path, monkeypatch)
    task_response = client.post(
        "/api/tasks",
        json={"task_type": "pipeline", "source": "https://example.com/video.mp4", "options": {}},
    )
    task_id = task_response.json()["id"]

    database.get_task_store().add_event(
        task_id,
        "asr.api_fallback.selected",
        stage="asr",
        step_id="transcribe",
        level="info",
        message="已选择 API ASR fallback",
        data={"provider": "siliconflow"},
    )

    response = client.get(f"/api/tasks/{task_id}/timeline")

    assert response.status_code == 200
    events = response.json()["events"]
    assert events[-1]["event_type"] == "asr.api_fallback.selected"
    assert events[-1]["stage"] == "asr"
    assert events[-1]["data"]["provider"] == "siliconflow"


def test_archive_thumbnail_uses_first_image_note_image(tmp_path, monkeypatch):
    client, _queue = _client(tmp_path, monkeypatch)
    archive_dir = tmp_path / "image-note"
    image_dir = archive_dir / "images"
    image_dir.mkdir(parents=True)
    (archive_dir / "metadata.json").write_text(
        json.dumps({"title": "小红书图文", "content_subtype": "image_note"}, ensure_ascii=False),
        encoding="utf-8",
    )
    first_image = image_dir / "00.jpg"
    first_image.write_bytes(b"first-image")
    (image_dir / "01.jpg").write_bytes(b"second-image")

    response = client.get("/api/pipeline/archives/thumbnail", params={"path": str(archive_dir)})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.content == b"first-image"


def test_archive_thumbnail_caches_low_res_first_image(tmp_path, monkeypatch):
    Image = pytest.importorskip("PIL.Image")

    client, _queue = _client(tmp_path, monkeypatch)
    archive_dir = tmp_path / "image-note-real"
    image_dir = archive_dir / "images"
    image_dir.mkdir(parents=True)
    (archive_dir / "metadata.json").write_text(
        json.dumps({"title": "Bili opus", "platform": "bilibili_opus", "content_subtype": "image_note"}),
        encoding="utf-8",
    )
    first_image = image_dir / "00.png"
    Image.new("RGB", (1200, 800), color=(220, 10, 20)).save(first_image)

    response = client.get("/api/pipeline/archives/thumbnail", params={"path": str(archive_dir)})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert (archive_dir / "thumbnail.jpg").exists()
    assert response.content == (archive_dir / "thumbnail.jpg").read_bytes()


def test_main_app_lifespan_auth_and_task_submission_smoke(tmp_path, monkeypatch):
    from app import main as app_main
    from app.services.ingestion import ytdlp_version

    settings = RuntimeSettings(data_root=str(tmp_path), api_token="", max_download_concurrency=1)
    monkeypatch.setattr(settings_module, "_runtime_settings", settings)
    monkeypatch.setattr(settings_module, "_save_settings_to_file", lambda _settings: None)
    monkeypatch.setattr(app_main, "get_runtime_settings", lambda: settings)
    monkeypatch.setattr(pipeline_core, "get_runtime_settings", lambda: settings)
    monkeypatch.setattr(ytdlp_version, "warn_if_stale", lambda: None)
    database.reset_db_path(tmp_path)

    class LifecycleQueue(FakeQueue):
        def __init__(self) -> None:
            super().__init__()
            self.pipeline_fn = None
            self.started = False
            self.stopped = False

        def set_pipeline(self, fn) -> None:
            self.pipeline_fn = fn

        async def start(self) -> None:
            self.started = True

        async def stop(self) -> None:
            self.stopped = True

    queue = LifecycleQueue()
    monkeypatch.setattr(app_main, "get_task_queue", lambda: queue)
    monkeypatch.setattr(tasks, "get_task_queue", lambda: queue)

    with TestClient(app_main.app) as client:
        assert queue.started is True
        assert queue.pipeline_fn is app_main.process_task

        blocked = client.post(
            "/api/tasks",
            json={"task_type": "pipeline", "source": str(tmp_path / "demo.mp4")},
        )
        assert blocked.status_code == 403

        created = client.post(
            "/api/tasks",
            json={"task_type": "pipeline", "source": str(tmp_path / "demo.mp4")},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert created.status_code == 200
        assert queue.submitted == [UUID(created.json()["id"])]

    assert queue.stopped is True


def test_main_app_lifespan_auto_updates_ytdlp_when_enabled(tmp_path, monkeypatch):
    from app import main as app_main
    from app.services.ingestion import ytdlp_version

    settings = RuntimeSettings(
        data_root=str(tmp_path),
        api_token="",
        max_download_concurrency=1,
        ytdlp_auto_update=True,
    )
    monkeypatch.setattr(settings_module, "_runtime_settings", settings)
    monkeypatch.setattr(settings_module, "_save_settings_to_file", lambda _settings: None)
    monkeypatch.setattr(app_main, "get_runtime_settings", lambda: settings)
    monkeypatch.setattr(pipeline_core, "get_runtime_settings", lambda: settings)
    database.reset_db_path(tmp_path)

    auto_update_calls: list[bool] = []
    monkeypatch.setattr(ytdlp_version, "auto_update_on_startup", lambda enabled: auto_update_calls.append(enabled))
    monkeypatch.setattr(ytdlp_version, "warn_if_stale", lambda: (_ for _ in ()).throw(AssertionError("warn_if_stale should not run")))

    class LifecycleQueue(FakeQueue):
        def __init__(self) -> None:
            super().__init__()
            self.started = False
            self.stopped = False

        def set_pipeline(self, fn) -> None:
            self.pipeline_fn = fn

        async def start(self) -> None:
            self.started = True

        async def stop(self) -> None:
            self.stopped = True

    queue = LifecycleQueue()
    monkeypatch.setattr(app_main, "get_task_queue", lambda: queue)

    with TestClient(app_main.app):
        assert queue.started is True
        assert auto_update_calls == [True]

    assert queue.stopped is True


def test_ytdlp_settings_endpoints_report_status_and_schedule_restart(tmp_path, monkeypatch):
    from app.services.ingestion import ytdlp_version

    client, _queue = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ytdlp_version,
        "check_version",
        lambda: ytdlp_version.YtdlpVersionInfo(
            installed="2026.03.17",
            latest="2026.07.09",
            age_days=0,
            is_stale=True,
        ),
    )
    monkeypatch.setattr(
        ytdlp_version,
        "upgrade",
        lambda: {
            "ok": True,
            "old": "2026.03.17",
            "new": "2026.07.09",
            "output": "",
            "restart_recommended": True,
        },
    )
    scheduled: list[float] = []
    monkeypatch.setattr(ytdlp_version, "schedule_process_restart", lambda delay: scheduled.append(delay))

    status = client.get("/api/settings/ytdlp")
    assert status.status_code == 200
    assert status.json() == {
        "installed": "2026.03.17",
        "latest": "2026.07.09",
        "age_days": 0,
        "is_stale": True,
        "auto_update": False,
    }

    upgrade = client.post("/api/settings/ytdlp/upgrade", headers={"X-Requested-With": "fetch"})
    assert upgrade.status_code == 200
    assert upgrade.json()["restart_scheduled"] is True
    assert scheduled == [1.0]


@pytest.mark.asyncio
async def test_real_task_queue_hands_downloaded_task_to_gpu_worker(tmp_path, monkeypatch):
    settings = RuntimeSettings(
        data_root=str(tmp_path),
        max_download_concurrency=1,
        pipeline_overlap=True,
    )
    monkeypatch.setattr(settings_module, "_runtime_settings", settings)
    monkeypatch.setattr(queue_module, "_flush_gpu_models", lambda: None)
    database.reset_db_path(tmp_path)

    store = database.get_task_store()
    task = Task(
        id=uuid4(),
        task_type=TaskType.PIPELINE,
        status=TaskStatus.PENDING,
        source="demo.mp4",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    store.save(task)

    task_queue = queue_module.TaskQueue()
    calls: list[tuple[UUID, bool]] = []
    done = asyncio.Event()

    async def fake_pipeline(task_id: UUID, download_worker_call: bool) -> None:
        calls.append((task_id, download_worker_call))
        if download_worker_call:
            await task_queue.advance_to_gpu(task_id)
            return
        store.update_status(task_id, TaskStatus.COMPLETED, completed_at=datetime.now())
        done.set()

    task_queue.set_pipeline(fake_pipeline)
    await task_queue.start()
    try:
        await task_queue.submit(task.id)
        await asyncio.wait_for(done.wait(), timeout=5)
    finally:
        await task_queue.stop()

    assert calls == [(task.id, True), (task.id, False)]
    assert store.get(task.id).status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_real_task_queue_pause_and_resume_queued_task(tmp_path, monkeypatch):
    settings = RuntimeSettings(data_root=str(tmp_path), max_download_concurrency=1)
    monkeypatch.setattr(settings_module, "_runtime_settings", settings)
    monkeypatch.setattr(queue_module, "_flush_gpu_models", lambda: None)
    database.reset_db_path(tmp_path)

    store = database.get_task_store()
    task = Task(
        id=uuid4(),
        task_type=TaskType.PIPELINE,
        status=TaskStatus.PENDING,
        source="demo.mp4",
    )
    store.save(task)

    task_queue = queue_module.TaskQueue()
    await task_queue.submit(task.id)

    assert task_queue.get_queue_snapshot() == [task.id]
    assert await task_queue.pause(task.id) is True
    assert store.get(task.id).status == TaskStatus.PAUSED
    assert task_queue.get_queue_snapshot() == []

    assert await task_queue.resume(task.id) is True
    assert store.get(task.id).status == TaskStatus.QUEUED
    assert task_queue.get_queue_snapshot() == [task.id]


@pytest.mark.asyncio
async def test_real_task_queue_resume_failed_task_from_checkpoint(tmp_path, monkeypatch):
    settings = RuntimeSettings(data_root=str(tmp_path), max_download_concurrency=1)
    monkeypatch.setattr(settings_module, "_runtime_settings", settings)
    monkeypatch.setattr(queue_module, "_flush_gpu_models", lambda: None)
    database.reset_db_path(tmp_path)

    store = database.get_task_store()
    output_dir = tmp_path / "failed-output"
    output_dir.mkdir()
    (output_dir / "metadata.json").write_text(json.dumps({"status": "failed"}), encoding="utf-8")
    task = Task(
        id=uuid4(),
        task_type=TaskType.PIPELINE,
        status=TaskStatus.FAILED,
        source="https://example.com/video.mp4",
        completed_steps=["download"],
        result={"output_dir": str(output_dir)},
        completed_at=datetime.now(),
    )
    store.save(task)

    task_queue = queue_module.TaskQueue()

    assert await task_queue.resume(task.id) is True
    saved = store.get(task.id)
    assert saved.status == TaskStatus.QUEUED
    assert saved.completed_at is None
    assert task_queue.get_queue_snapshot() == [task.id]
    assert json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))["status"] == "queued"


@pytest.mark.asyncio
async def test_real_task_queue_checkpoint_rerun_completed_image_note(tmp_path, monkeypatch):
    settings = RuntimeSettings(data_root=str(tmp_path), max_download_concurrency=1)
    monkeypatch.setattr(settings_module, "_runtime_settings", settings)
    monkeypatch.setattr(queue_module, "_flush_gpu_models", lambda: None)
    database.reset_db_path(tmp_path)

    store = database.get_task_store()
    output_dir = tmp_path / "image-note-output"
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True)
    (images_dir / "00.jpg").write_bytes(b"fake image")
    (output_dir / "metadata.json").write_text(
        json.dumps({"status": "completed", "title": "谈谈沐神最近在交大的演讲", "content_subtype": "image_note"}),
        encoding="utf-8",
    )
    task = Task(
        id=uuid4(),
        task_type=TaskType.PIPELINE,
        status=TaskStatus.COMPLETED,
        source="https://example.com/note",
        steps=["download", "separate", "transcribe", "voiceprint", "polish", "analyze", "archive"],
        completed_steps=["download", "separate", "transcribe", "voiceprint", "polish", "analyze", "archive"],
        result={
            "output_dir": str(output_dir),
            "metadata": {"content_subtype": "image_note"},
            "image_descriptions": [{"index": 0, "image_path": str(images_dir / "00.jpg"), "text": ""}],
        },
        content_subtype="image_note",
        completed_at=datetime.now(),
    )
    store.save(task)

    task_queue = queue_module.TaskQueue()

    assert await task_queue.rerun_from_checkpoint(task.id) is True
    saved = store.get(task.id)
    assert saved.status == TaskStatus.QUEUED
    assert saved.completed_at is None
    assert saved.completed_steps == ["download", "separate", "transcribe", "voiceprint", "polish"]
    assert saved.current_step == "analyze"
    assert task_queue.get_queue_snapshot() == [task.id]
    assert json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))["status"] == "queued"


@pytest.mark.asyncio
async def test_real_task_queue_delete_running_task_removes_record_and_output_dir(tmp_path, monkeypatch):
    settings = RuntimeSettings(
        data_root=str(tmp_path),
        max_download_concurrency=1,
        pipeline_overlap=True,
    )
    monkeypatch.setattr(settings_module, "_runtime_settings", settings)
    monkeypatch.setattr(queue_module, "_flush_gpu_models", lambda: None)
    database.reset_db_path(tmp_path)

    store = database.get_task_store()
    output_dir = tmp_path / "running-output"
    output_dir.mkdir()
    (output_dir / "metadata.json").write_text(json.dumps({"status": "processing"}), encoding="utf-8")
    task = Task(
        id=uuid4(),
        task_type=TaskType.PIPELINE,
        status=TaskStatus.PENDING,
        source="demo.mp4",
        result={"output_dir": str(output_dir)},
    )
    store.save(task)

    task_queue = queue_module.TaskQueue()
    started = asyncio.Event()

    async def fake_pipeline(_task_id: UUID, _download_worker_call: bool) -> None:
        started.set()
        await asyncio.sleep(30)

    task_queue.set_pipeline(fake_pipeline)
    await task_queue.start()
    try:
        await task_queue.submit(task.id)
        await asyncio.wait_for(started.wait(), timeout=5)
        result = await task_queue.delete(task.id)
    finally:
        await task_queue.stop()

    assert result is not None
    assert store.get(task.id) is None
    assert not output_dir.exists()


@pytest.mark.asyncio
async def test_process_task_dispatches_pipeline_and_publishes_completion(tmp_path, monkeypatch):
    settings = RuntimeSettings(data_root=str(tmp_path))
    monkeypatch.setattr(settings_module, "_runtime_settings", settings)
    monkeypatch.setattr(pipeline_core, "get_runtime_settings", lambda: settings)
    database.reset_db_path(tmp_path)

    store = database.get_task_store()
    task = Task(
        id=uuid4(),
        task_type=TaskType.PIPELINE,
        status=TaskStatus.QUEUED,
        source="demo.mp4",
    )
    store.save(task)

    event_bus = EventBus()
    monkeypatch.setattr(pipeline_core, "get_event_bus", lambda: event_bus)
    monkeypatch.setattr("app.services.analysis.llm.offload_local_llm", lambda: None)

    async def fake_run_pipeline(task: Task, _download_worker_call: bool = False) -> None:
        assert _download_worker_call is False
        task.result = {"output_dir": str(tmp_path / "demo-output")}

    monkeypatch.setattr(pipeline_core, "run_pipeline", fake_run_pipeline)

    await pipeline_core.process_task(task.id, _download_worker_call=False)

    saved = store.get(task.id)
    assert saved.status == TaskStatus.COMPLETED
    assert saved.result == {"output_dir": str(tmp_path / "demo-output")}
    assert [event.event_type for event in event_bus.get_recent_log()] == ["processing", "completed"]
