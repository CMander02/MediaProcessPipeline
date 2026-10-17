from __future__ import annotations

import asyncio
import json
import multiprocessing
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api.routes import pipeline, tasks
from app.core import archive_sync, database, settings
from app.core import queue as queue_module
from app.core.archive_index import source_key
from app.core.paths import get_workspace_paths
from app.core.task_import import import_task
from app.models import Task, TaskStatus, TaskType
from app.services.ingestion.platform import source_urls

VIDEO = "https://www.bilibili.com/video/BV1DK4y1b7bY"


class FakeQueue:
    def __init__(self):
        self.submitted = []
        self.fail = False
        self.active_task_ids = set()

    def get_queue_snapshot(self):
        return self.submitted

    async def submit(self, task_id):
        if self.fail:
            raise RuntimeError("notification interrupted")
        self.submitted.append(task_id)


@pytest.fixture
def api(tmp_path, monkeypatch):
    runtime = settings.RuntimeSettings(data_root=str(tmp_path), allow_remote_filesystem=True)
    monkeypatch.setattr(settings, "_runtime_settings", runtime)
    database.reset_db_path(tmp_path)
    database.init_db(tmp_path)
    sync = archive_sync.ArchiveSyncService()
    monkeypatch.setattr(archive_sync, "_sync_service", sync)
    fake_queue = FakeQueue()
    monkeypatch.setattr(tasks, "get_task_queue", lambda: fake_queue)
    app = FastAPI()
    app.include_router(tasks.router, prefix="/api")
    app.include_router(pipeline.router, prefix="/api")
    with TestClient(app) as client:
        yield client, fake_queue, tmp_path, sync
    database.close_db()


def submit(client, sources):
    response = client.post("/api/tasks/import", json={"sources": sources})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def existing(status, source=VIDEO):
    task = Task(task_type=TaskType.PIPELINE, source=source, status=status)
    database.get_task_store().save(task)
    return task


def archive_for_task(api, task, source=VIDEO, *, extra=None):
    _client, _queue, root, sync = api
    directory = get_workspace_paths(root).archives / str(uuid4())
    directory.mkdir()
    archive_id = str(uuid4())
    metadata = {
        "archive_id": archive_id,
        "task_id": str(task.id),
        "source_url": source,
        "title": "历史归档",
        "status": str(task.status),
        "extra": extra or {},
    }
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    task.result = {"output_dir": str(directory)}
    database.get_task_store().save(task)
    sync.reconcile()
    return directory, archive_id


def test_selected_items_batch_duplicates_and_bilibili_parts(api):
    client, queue, root, _sync = api
    sources = [VIDEO + "?spm_id_from=share", VIDEO + "?p=01", VIDEO + "?p=2"]
    items = submit(client, sources)
    assert [item["source"] for item in items] == sources
    assert [item["outcome"] for item in items] == ["created", "reused", "created"]
    assert items[1]["reason"] == "duplicate_in_request"
    assert items[0]["task"]["id"] == items[1]["task"]["id"]
    assert items[2]["task"]["id"] != items[0]["task"]["id"]
    assert items[2]["content_key"] == "bilibili:BV1DK4y1b7bY:p2"
    assert len(queue.submitted) == database.get_task_store().count() == 2
    assert len(list(get_workspace_paths(root).archives.iterdir())) == 2
    assert all(item["outcome"] == "reused" for item in submit(client, sources))
    assert len(queue.submitted) == 2


@pytest.mark.parametrize(
    "status,outcome",
    [
        (TaskStatus.COMPLETED, "reused"),
        (TaskStatus.PENDING, "reused"),
        (TaskStatus.QUEUED, "reused"),
        (TaskStatus.PROCESSING, "reused"),
        (TaskStatus.PAUSED, "skipped"),
        (TaskStatus.FAILED, "skipped"),
        (TaskStatus.CANCELLED, "skipped"),
    ],
)
def test_existing_task_state_is_visible_before_archiving(api, status, outcome):
    client, queue, _root, _sync = api
    task = existing(status)
    item = submit(client, [VIDEO])[0]
    assert item["outcome"] == outcome
    assert item["task"]["id"] == str(task.id)
    assert item["match"]["status"] == status
    assert item["reason"] == ("needs_action" if outcome == "skipped" else None)
    assert not queue.submitted
    lookup = client.post("/api/pipeline/archives/lookup", json={"sources": [VIDEO]}).json()
    assert lookup["matches"][VIDEO][0]["status"] == status


def test_completed_has_priority_over_active_and_failed(api):
    client, _queue, _root, _sync = api
    done = existing(TaskStatus.COMPLETED)
    existing(TaskStatus.QUEUED)
    existing(TaskStatus.FAILED)
    item = submit(client, [VIDEO])[0]
    assert item["task"]["id"] == str(done.id)
    assert item["outcome"] == "reused"


def test_selected_part_survives_older_metadata_missing_page(api):
    client, _queue, _root, _sync = api
    part_two = existing(TaskStatus.COMPLETED, VIDEO + "?p=2")
    part_two.result = {"metadata": {"source_url": VIDEO}}
    database.get_task_store().save(part_two)
    items = submit(client, [VIDEO, VIDEO + "?p=2"])
    assert [item["outcome"] for item in items] == ["created", "reused"]
    assert items[1]["task"]["id"] == str(part_two.id)


def test_standalone_archive_without_task_can_be_reused(api):
    client, queue, root, sync = api
    directory = get_workspace_paths(root).archives / "standalone"
    directory.mkdir()
    archive_id = str(uuid4())
    (directory / "metadata.json").write_text(
        json.dumps(
            {
                "archive_id": archive_id,
                "source_url": VIDEO,
                "title": "已导入",
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )
    sync.reconcile()
    item = submit(client, [VIDEO])[0]
    assert item["outcome"] == "reused"
    assert item["task"] is None
    assert item["match"]["archive_id"] == archive_id
    assert item["match"]["path"] == str(directory)
    assert not queue.submitted
    assert database.get_task_store().count() == 0


def test_download_only_archive_does_not_skip_pipeline_processing(api):
    client, queue, _root, _sync = api
    downloaded = Task(task_type=TaskType.INGESTION, source=VIDEO, status=TaskStatus.COMPLETED)
    archive_for_task(api, downloaded)
    lookup = client.post("/api/pipeline/archives/lookup", json={"sources": [VIDEO]}).json()
    assert VIDEO not in lookup["matches"]
    item = submit(client, [VIDEO])[0]
    assert item["outcome"] == "created"
    assert item["task"]["id"] != str(downloaded.id)
    assert len(queue.submitted) == 1


@pytest.mark.parametrize(
    "status,outcome",
    [
        (TaskStatus.COMPLETED, "created"),
        (TaskStatus.QUEUED, "reused"),
    ],
)
def test_deleted_archive_does_not_reuse_finished_task_but_keeps_active_task(api, status, outcome):
    client, _queue, _root, sync = api
    old = existing(status)
    directory, archive_id = archive_for_task(api, old)
    (directory / "metadata.json").unlink()
    directory.rmdir()
    sync.reconcile()
    assert (
        database._get_conn()
        .execute(
            "SELECT deleted FROM archive_sync_index WHERE archive_id=?",
            (archive_id,),
        )
        .fetchone()[0]
        == 1
    )
    item = submit(client, [VIDEO])[0]
    assert item["outcome"] == outcome
    assert (item["task"]["id"] == str(old.id)) == (outcome == "reused")


def test_archive_source_is_available_when_owner_has_only_local_source(api):
    client, _queue, root, _sync = api
    old = existing(TaskStatus.COMPLETED, str(root / "local-video.mp4"))
    directory, _archive_id = archive_for_task(api, old)
    item = submit(client, [VIDEO])[0]
    assert item["outcome"] == "reused"
    assert item["task"]["id"] == str(old.id)
    assert item["match"]["path"] == str(directory)


def test_conflicting_archive_source_is_not_bound_to_wrong_task(api):
    client, _queue, _root, _sync = api
    other_source = "https://www.bilibili.com/video/BV1Ab411c7Xy"
    old = existing(TaskStatus.COMPLETED, other_source)
    directory, _archive_id = archive_for_task(api, old)
    items = submit(client, [VIDEO, other_source])
    assert items[0]["outcome"] == "reused"
    assert items[0]["task"] is None and items[0]["match"]["task_id"] is None
    assert items[0]["match"]["path"] == str(directory)
    assert items[1]["outcome"] == "created"
    assert items[1]["task"]["id"] != str(old.id)


def test_archive_missing_page_keeps_the_tasks_selected_part(api):
    client, _queue, _root, _sync = api
    old = existing(TaskStatus.COMPLETED, VIDEO + "?p=2")
    archive_for_task(api, old)
    items = submit(client, [VIDEO, VIDEO + "?p=2"])
    assert [item["outcome"] for item in items] == ["created", "reused"]
    assert items[1]["task"]["id"] == str(old.id)


def test_historical_av_can_use_stored_bvid_without_network_scan(api, monkeypatch):
    client, _queue, _root, _sync = api
    historical = existing(TaskStatus.COMPLETED, "https://www.bilibili.com/video/av123?p=2")
    archive_for_task(
        api, historical, historical.source, extra={"bvid": "BV1DK4y1b7bY", "page_number": 2}
    )

    def no_network(*_args, **_kwargs):
        raise AssertionError("historical aliases must not trigger network scans")

    monkeypatch.setattr(source_urls, "urllib_urlopen", no_network)
    item = submit(client, [VIDEO + "?p=2"])[0]
    assert item["outcome"] == "reused"
    assert item["task"]["id"] == str(historical.id)
    assert item["match"]["path"]


def test_historical_av_selected_page_survives_canonical_metadata_without_page(api):
    client, _queue, _root, _sync = api
    historical = existing(TaskStatus.COMPLETED, "https://www.bilibili.com/video/av123?p=2")
    historical.result = {
        "metadata": {"source_url": VIDEO, "extra": {"bvid": "BV1DK4y1b7bY", "page_number": 2}},
    }
    database.get_task_store().save(historical)
    items = submit(client, [VIDEO, VIDEO + "?p=2"])
    assert [item["outcome"] for item in items] == ["created", "reused"]
    assert items[1]["task"]["id"] == str(historical.id)
    assert items[1]["content_key"] == "bilibili:BV1DK4y1b7bY:p2"


@pytest.mark.parametrize("query", ["aid=999", "bvid=BV1Ab411c7Xy", "bvid=BV1Ab411c7Xy&aid=999"])
def test_bilibili_video_path_takes_priority_over_query_aliases(api, monkeypatch, query):
    client, _queue, _root, _sync = api

    def no_network(*_args, **_kwargs):
        raise AssertionError("a valid BV path must not resolve a query aid")

    monkeypatch.setattr(source_urls, "urllib_urlopen", no_network)
    item = submit(client, [VIDEO + "?" + query])[0]
    assert item["outcome"] == "created"
    assert item["task"]["source"] == VIDEO


def test_query_only_bilibili_ids_still_resolve(api, monkeypatch):
    client, _queue, _root, _sync = api
    original = source_urls._extract_bilibili_bvid
    monkeypatch.setattr(
        source_urls,
        "_extract_bilibili_bvid",
        lambda source: "BV1DK4y1b7bY" if source.endswith("/av123") else original(source),
    )
    sources = [
        "https://www.bilibili.com/video/?aid=123",
        "https://www.bilibili.com/video/?bvid=BV1DK4y1b7bY",
    ]
    items = submit(client, sources)
    assert [item["outcome"] for item in items] == ["created", "reused"]
    assert items[0]["task"]["id"] == items[1]["task"]["id"]


def test_bilibili_short_and_av_aliases_resolve_outside_transaction(api, monkeypatch):
    client, _queue, _root, _sync = api
    original_bvid = source_urls._extract_bilibili_bvid

    def resolve_short(_source):
        assert not database._get_conn().in_transaction
        return VIDEO + "?p=2"

    def resolve_bvid(source):
        assert not database._get_conn().in_transaction
        return "BV1DK4y1b7bY" if "/av123" in source else original_bvid(source)

    monkeypatch.setattr(source_urls, "_resolve_bilibili_short_url", resolve_short)
    monkeypatch.setattr(source_urls, "_extract_bilibili_bvid", resolve_bvid)
    items = submit(client, ["https://b23.tv/short", "https://www.bilibili.com/video/av123?p=2"])
    assert [item["outcome"] for item in items] == ["created", "reused"]
    assert items[0]["task"]["id"] == items[1]["task"]["id"]


def test_normalization_errors_are_not_reported_as_unimported(api, monkeypatch):
    client, queue, _root, _sync = api
    monkeypatch.setattr(source_urls, "_resolve_bilibili_short_url", lambda _source: None)
    failed_source = "https://b23.tv/unavailable"
    item = submit(client, [failed_source])[0]
    assert item["outcome"] == "failed" and item["error"]
    lookup = client.post("/api/pipeline/archives/lookup", json={"sources": [failed_source]}).json()
    assert lookup["errors"][failed_source]
    assert not lookup["matches"]
    assert not queue.submitted


def test_generic_queries_remain_distinct_and_partial_validation_keeps_success(api):
    client, queue, _root, _sync = api
    items = submit(
        client,
        [
            "https://example.com/watch?id=1",
            "http://localhost/private",
            "https://example.com/watch?id=2",
        ],
    )
    assert [item["outcome"] for item in items] == ["created", "failed", "created"]
    assert items[0]["content_key"] != items[2]["content_key"]
    assert len(queue.submitted) == 2
    assert source_key("https://example.com/" + VIDEO.rsplit("/", 1)[-1]) != source_key(VIDEO)


def test_local_permissions_and_staged_upload_exception(api):
    client, queue, root, _sync = api
    settings._runtime_settings.allow_remote_filesystem = False
    assert submit(client, [str(root / "private.mp4")])[0]["outcome"] == "failed"
    stage = get_workspace_paths(root).temporary("staging") / ("a" * 32) / "🎤.mp3"
    stage.parent.mkdir(parents=True)
    stage.write_bytes(b"fixture")
    item = submit(client, [str(stage)])[0]
    assert item["outcome"] == "created"
    assert item['content_key'] == f"archive:{item['task']['id']}"
    assert len(queue.submitted) == 1


def test_failed_initialization_rolls_back_and_cleans_only_new_directory(api, monkeypatch):
    client, queue, root, _sync = api
    original_write = tasks.atomic_write_text

    def reject(_path, _content):
        raise OSError("disk full")

    monkeypatch.setattr(tasks, "atomic_write_text", reject)
    item = submit(client, [VIDEO])[0]
    assert item["outcome"] == "failed" and "disk full" in item["error"]
    assert not database._get_conn().in_transaction
    assert database.get_task_store().count() == 0
    assert not list(get_workspace_paths(root).archives.iterdir())
    monkeypatch.setattr(tasks, "atomic_write_text", original_write)
    assert submit(client, [VIDEO])[0]["outcome"] == "created"
    assert len(queue.submitted) == 1


def test_notification_error_is_visible_and_keeps_same_retryable_task(api, monkeypatch):
    client, queue, _root, _sync = api
    queue.fail = True
    item = submit(client, [VIDEO])[0]
    task_id = UUID(item["task"]["id"])
    assert item["reason"] == "queue_notification_failed"
    assert database.get_task_store().get(task_id).status == TaskStatus.FAILED
    repeated = submit(client, [VIDEO])[0]
    assert repeated["task"]["id"] == str(task_id)
    assert repeated["outcome"] == "skipped" and repeated["reason"] == "needs_action"
    assert database.get_task_store().count() == 1

    resumed = queue_module.TaskQueue()
    monkeypatch.setattr(tasks, "get_task_queue", lambda: resumed)
    response = client.post(f"/api/tasks/{task_id}/resume")
    assert response.status_code == 200
    assert resumed.get_queue_snapshot() == [task_id]


def test_error_after_enqueue_does_not_fail_or_replace_the_queued_task(api, monkeypatch):
    client, queue, _root, _sync = api

    async def enqueue_then_fail(task_id):
        queue.submitted.append(task_id)
        raise RuntimeError("logging failed after enqueue")

    monkeypatch.setattr(queue, "submit", enqueue_then_fail)
    item = submit(client, [VIDEO])[0]
    assert item["outcome"] == "created"
    assert item["task"]["status"] == "queued"
    assert submit(client, [VIDEO])[0]["outcome"] == "reused"
    assert len(queue.submitted) == 1


def test_commit_before_notification_is_durable_and_startup_restores_it(api, monkeypatch):
    client, _queue, root, _sync = api
    task = Task(task_type=TaskType.PIPELINE, source=VIDEO, status=TaskStatus.QUEUED)

    def initialize(candidate):
        directory = get_workspace_paths(root).archives / str(candidate.id)
        directory.mkdir()
        candidate.result = {"output_dir": str(directory)}
        (directory / "metadata.json").write_text(
            json.dumps({"source_url": VIDEO, "status": "queued"}), encoding="utf-8"
        )

    class Interrupted(BaseException):
        pass

    with pytest.raises(Interrupted):
        imported = import_task(task, initialize)
        assert imported.outcome == "created"
        raise Interrupted("process stopped before queue notification")
    task_id = task.id
    assert database.get_task_store().get(task_id).status == TaskStatus.QUEUED
    assert submit(client, [VIDEO])[0]["task"]["id"] == str(task_id)
    assert database.get_task_store().count() == 1

    async def hold_worker(*args, **kwargs):
        await asyncio.Event().wait()

    recovered = queue_module.TaskQueue()
    monkeypatch.setattr(recovered, "_download_worker", hold_worker)
    monkeypatch.setattr(recovered, "_gpu_worker", hold_worker)

    async def recover():
        await recovered.start()
        try:
            assert recovered.pending_count == 1
            assert recovered._download_queue.get_nowait() == task_id
        finally:
            await recovered.stop()

    asyncio.run(recover())


def _concurrent_import(root, barrier, results):
    """A separate process owns its own connection and its own Python DB lock."""
    settings._runtime_settings = settings.RuntimeSettings(data_root=root)
    database.reset_db_path(Path(root))
    database.init_db(Path(root))
    task = Task(task_type=TaskType.PIPELINE, source=VIDEO, status=TaskStatus.QUEUED)

    def initialize(candidate):
        directory = get_workspace_paths(root).archives / str(candidate.id)
        directory.mkdir()
        candidate.result = {"output_dir": str(directory)}
        (directory / "metadata.json").write_text(
            json.dumps({"source_url": VIDEO, "status": "queued"}), encoding="utf-8"
        )

    try:
        barrier.wait(timeout=30)
        item = import_task(task, initialize)
        results.put((item.outcome, str(item.task.id)))
    finally:
        database.close_db()


def test_two_processes_create_only_one_task(api):
    _client, _queue, root, _sync = api
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(3)
    results = context.Queue()
    processes = [
        context.Process(target=_concurrent_import, args=(str(root), barrier, results))
        for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        barrier.wait(timeout=30)
        received = [results.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(timeout=30)
            assert process.exitcode == 0
        assert sorted(outcome for outcome, _task_id in received) == ["created", "reused"]
        assert len({task_id for _outcome, task_id in received}) == 1
        assert database.get_task_store().count() == 1
        assert len(list(get_workspace_paths(root).archives.iterdir())) == 1
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        results.close()


def test_legacy_endpoints_keep_unconditional_creation_and_limits(api):
    client, queue, _root, _sync = api
    first = client.post("/api/tasks", json={"task_type": "pipeline", "source": VIDEO}).json()
    response = client.post("/api/tasks/batch", json={"sources": [VIDEO, VIDEO]})
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert len({first["id"], *(task["id"] for task in response.json())}) == 3
    assert len(queue.submitted) == 3
    assert client.post("/api/tasks/import", json={"sources": [VIDEO] * 101}).status_code == 422
