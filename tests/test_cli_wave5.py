from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api.routes import pipeline as pipeline_routes  # noqa: E402
from app.api.routes import sync as sync_routes  # noqa: E402
from app.api.routes import tasks as task_routes  # noqa: E402
from app.cli.client import MppClient, MppClientError  # noqa: E402
from app.cli.commands import configuration, execution, operations, system, task  # noqa: E402
from app.cli.context import CliContext, get_cli_context  # noqa: E402
from app.cli.main import app  # noqa: E402
from app.core import archive_sync, database, pipeline, queue, settings  # noqa: E402
from app.core.library import get_library_service  # noqa: E402
from app.core.paths import get_workspace_paths  # noqa: E402
from app.models import Task, TaskStatus, TaskType  # noqa: E402
from app.services import remote_archive_upload  # noqa: E402
from app.services.ingestion.platform.bilibili.login import BilibiliLoginService  # noqa: E402

RAW = "1\n00:00:00,000 --> 00:00:02,000\nA transcript that survives the rerun.\n"
ANALYSIS_FILES = (
    "summary.md",
    "summary.json",
    "mindmap.md",
    "mindmap.json",
    "detail.md",
    "analysis.json",
    "content_selection.json",
)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    runtime = settings.RuntimeSettings(data_root=str(tmp_path))
    monkeypatch.setattr(settings, "_runtime_settings", runtime)
    monkeypatch.setattr(archive_sync, "_sync_service", archive_sync.ArchiveSyncService())
    monkeypatch.setattr(queue, "_task_queue", queue.TaskQueue())
    database.reset_db_path(tmp_path)
    try:
        yield runtime
    finally:
        database.reset_db_path()


def invoke(*args, input=None):
    return CliRunner().invoke(
        app,
        ["--skip-version-check", *args],
        input=input,
        env={"MPP_SERVER_URL": None, "MPP_API_TOKEN": None},
    )


def data(result):
    assert result.exit_code == 0, (result.output, result.exception)
    return json.loads(result.stdout)["data"]


def api_for(*routers):
    application = FastAPI()
    for router in routers:
        application.include_router(router, prefix="/api")
    return TestClient(application)


def make_task(workspace, *, status=TaskStatus.COMPLETED, name="archive", **kwargs):
    directory = get_workspace_paths(workspace.data_root).archives / name
    directory.mkdir(parents=True)
    record = Task(
        task_type=TaskType.PIPELINE,
        source=f"https://example.test/{name}",
        status=status,
        result={"output_dir": str(directory)},
        **kwargs,
    )
    (directory / "metadata.json").write_text(
        json.dumps(
            {
                "title": name,
                "media_type": "video",
                "task_id": str(record.id),
                "status": record.status,
                "source_url": record.source,
            }
        ),
        encoding="utf-8",
    )
    database.get_task_store().save(record)
    return record, directory


@pytest.mark.parametrize(
    ("stage", "status", "retained"),
    [
        ("speaker_review", TaskStatus.COMPLETED, []),
        ("polish", TaskStatus.PAUSED, ["speaker_review"]),
        ("analyze", TaskStatus.CANCELLED, ["speaker_review", "polish"]),
    ],
)
def test_checkpoint_rerun_preserves_prerequisites_and_runs_postprocess(
    workspace,
    monkeypatch,
    stage,
    status,
    retained,
):
    record, directory = make_task(
        workspace,
        status=status,
        options={"workflow": {"diarize": False, "summary": True}},
        completed_steps=[
            "download",
            "separate",
            "transcribe",
            "speaker_review",
            "polish",
            "analyze",
            "archive",
        ],
    )
    store = database.get_task_store()
    for name in (
        *ANALYSIS_FILES,
        "speaker_review.json",
        "speaker_review.checkpoint.json",
        "transcript.srt",
        "transcript_polished.srt",
        "transcript_polished.md",
        "user.md",
    ):
        content = RAW if name.endswith(".srt") else "{}"
        (directory / name).write_text(content, encoding="utf-8")
        store.save_artifact(record.id, name, content)

    async def forbidden(*args):
        pytest.fail("Scoped rerun entered download or transcription")

    processed = []

    async def postprocess(ctx):
        processed.append((ctx.transcript, ctx.done, ctx.task.current_step))

    async def source_context(*args):
        return {"title": directory.name}

    monkeypatch.setattr(pipeline, "prepare_download", forbidden)
    monkeypatch.setattr(pipeline, "transcribe", forbidden)
    monkeypatch.setattr(pipeline, "postprocess", postprocess)
    monkeypatch.setattr(pipeline, "_prepare_source_context", source_context)
    with api_for(task_routes.router) as api:
        response = api.post(
            f"/api/tasks/{record.id}/checkpoint-rerun",
            json={"from_step": stage, "workflow": {"summary": False}},
        )
    assert response.status_code == 200, response.text
    assert queue.get_task_queue().get_queue_snapshot() == [record.id]
    updated = store.get(record.id)
    assert updated.status == TaskStatus.QUEUED
    assert updated.current_step == stage
    assert updated.completed_steps == ["download", "separate", "transcribe", *retained]
    assert updated.options["workflow"]["summary"] is False
    assert updated.options["workflow"]["diarize"] is False
    removed = list(ANALYSIS_FILES)
    if stage != "analyze":
        removed.extend(("transcript_polished.srt", "transcript_polished.md"))
    if stage == "speaker_review":
        removed.extend(("speaker_review.json", "speaker_review.checkpoint.json"))
    for name in removed:
        assert not (directory / name).exists()
        assert store.get_artifact(record.id, name) is None
    for name in {
        "transcript.srt",
        "transcript_polished.srt",
        "speaker_review.json",
        "user.md",
    } - set(removed):
        assert (directory / name).is_file()
        assert store.get_artifact(record.id, name) is not None
    assert (directory / "transcript.srt").read_text() == RAW
    asyncio.run(pipeline.run_pipeline(updated))
    assert processed == [
        ("A transcript that survives the rerun.", set(updated.completed_steps), stage),
    ]


def test_checkpoint_rerun_platform_subtitle_and_empty_body(workspace):
    record, directory = make_task(workspace, name="caption")
    (directory / "subtitles").mkdir()
    (directory / "subtitles" / "en.srt").write_text(RAW, encoding="utf-8")
    legacy, legacy_dir = make_task(workspace, name="legacy")
    (legacy_dir / "transcript.srt").write_text(RAW, encoding="utf-8")
    (legacy_dir / "summary.md").write_text("legacy summary", encoding="utf-8")
    invalid, invalid_dir = make_task(workspace, name="invalid")
    (invalid_dir / "summary.md").write_text("leave this alone", encoding="utf-8")
    with api_for(task_routes.router) as api:
        assert (
            api.post(
                f"/api/tasks/{record.id}/checkpoint-rerun", json={"from_step": "polish"}
            ).status_code
            == 200
        )
        assert api.post(f"/api/tasks/{legacy.id}/checkpoint-rerun").status_code == 200
        assert (
            api.post(
                f"/api/tasks/{invalid.id}/checkpoint-rerun", json={"from_step": "analyze"}
            ).status_code
            == 400
        )
    assert "survives the rerun" in (directory / "transcript.srt").read_text()
    assert (legacy_dir / "summary.md").read_text() == "legacy summary"
    assert (invalid_dir / "summary.md").read_text() == "leave this alone"


@pytest.mark.parametrize("has_metadata", [False, True])
def test_checkpoint_checks_prerequisites_before_removing_outputs(workspace, has_metadata):
    record, directory = make_task(workspace)
    if not has_metadata:
        (directory / "metadata.json").unlink()
    (directory / "transcript.srt").write_text(RAW)
    (directory / "summary.md").write_text("Existing summary")
    with api_for(task_routes.router) as api:
        response = api.post(
            f"/api/tasks/{record.id}/checkpoint-rerun",
            json={"from_step": "analyze"},
        )
    assert response.status_code == 400
    assert ("polished transcript" if has_metadata else "metadata") in response.json()["detail"]
    assert database.get_task_store().get(record.id).status == TaskStatus.COMPLETED
    assert (directory / "summary.md").read_text() == "Existing summary"


def test_checkpoint_recovers_selected_legacy_subtitle_over_empty_raw(workspace):
    record, directory = make_task(workspace)
    path = directory / "metadata.json"
    metadata = json.loads(path.read_text())
    metadata["extra"] = {
        "subtitle_tracks": [
            {"lang": "en", "filename": "transcript.en.srt", "type": "cc", "polished": False},
            {"lang": "zh", "filename": "transcript.zh.srt", "type": "cc", "polished": True},
        ]
    }
    path.write_text(json.dumps(metadata))
    (directory / "transcript.srt").touch()
    (directory / "transcript.en.srt").write_text(RAW)
    (directory / "transcript.zh.srt").write_text(
        json.dumps(
            {
                "events": [{"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "选定字幕"}]}],
            }
        )
    )
    with api_for(task_routes.router) as api:
        response = api.post(
            f"/api/tasks/{record.id}/checkpoint-rerun",
            json={"from_step": "polish"},
        )
    assert response.status_code == 200, response.text
    assert "选定字幕" in (directory / "transcript.srt").read_text()


def test_checkpoint_worker_recovers_interrupted_cleanup(workspace, monkeypatch):
    record, directory = make_task(workspace)
    store = database.get_task_store()
    (directory / "transcript.srt").write_text(RAW)
    for filename in ("summary.md", "summary.json"):
        (directory / filename).write_text("{}")
        store.save_artifact(record.id, filename, "{}")
    unlink = Path.unlink

    def interrupt(path, *args, **kwargs):
        if path == directory / "summary.json":
            raise OSError("simulated interruption")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as attempt, api_for(task_routes.router) as api:
        attempt.setattr(Path, "unlink", interrupt)
        response = api.post(
            f"/api/tasks/{record.id}/checkpoint-rerun",
            json={"from_step": "polish"},
        )
    assert response.status_code == 200, response.text
    assert not (directory / "summary.md").exists()
    assert (directory / "summary.json").exists()

    async def postprocess(ctx):
        assert (directory / "transcript.srt").read_text() == RAW
        for filename in ("summary.md", "summary.json"):
            assert not (directory / filename).exists()
            assert store.get_artifact(record.id, filename) is None

    async def source_context(*args):
        return {"title": directory.name}

    monkeypatch.setattr(pipeline, "postprocess", postprocess)
    monkeypatch.setattr(pipeline, "_prepare_source_context", source_context)
    asyncio.run(pipeline.run_pipeline(store.get(record.id)))
    assert not store.get(record.id).result.get("_checkpoint_cleanup_pending")


@pytest.mark.parametrize("command", ["run", "submit"])
def test_deduplicated_collection_submission_waits_for_created_only(workspace, monkeypatch, command):
    urls = [f"https://www.bilibili.com/video/BVabcdefghij?p={i}" for i in range(1, 7)]
    ids = [str(uuid4()) for _ in urls]
    captured = {}
    waited = []

    class Api:
        def capabilities(self):
            return {"local_path_submission": True}

        def bilibili_collection(self, source):
            assert source == urls[0]
            return {
                "is_collection": True,
                "items": [{"id": f"episode-{i}", "url": url} for i, url in enumerate(urls, 1)],
            }

        def list_library_folders(self):
            return {"folders": [{"id": "course-id", "name": "Course"}]}

        def import_tasks(self, sources, options, **kwargs):
            captured.update(sources=sources, options=options, **kwargs)
            return {
                "items": [
                    {
                        "outcome": "created",
                        "task": {"id": ids[0], "source": sources[0], "status": "queued"},
                    },
                    {
                        "outcome": "reused",
                        "task": {"id": ids[1], "source": sources[1], "status": "processing"},
                    },
                    {
                        "outcome": "skipped",
                        "match": {"task_id": ids[2], "status": "failed"},
                        "reason": "needs_action",
                    },
                    {"outcome": "reused", "match": {"archive_id": ids[4], "status": "completed"}},
                ]
            }

        def get_task(self, task_id):
            assert task_id == ids[0]
            waited.append(task_id)
            return {"id": task_id, "status": "completed", "source": urls[0]}

    monkeypatch.setattr(execution, "_require_daemon", lambda **_: Api())
    flags = ["--wait"] if command == "submit" else []
    result = invoke(
        "--json",
        command,
        urls[0],
        "--folder",
        "Course",
        "--episodes",
        "1-3,5",
        "--collection-folder",
        "--move-existing",
        "--only-transcribe",
        "--no-diarize",
        "--webhook",
        "https://example.test/callback",
        *flags,
    )
    results = data(result)
    assert waited == ids[:1]
    assert [item.get("outcome") for item in results] == [None, "reused", "skipped", "reused"]
    assert results[-1]["id"] == ids[4]
    assert captured == {
        "sources": [urls[i] for i in (0, 1, 2, 4)],
        "options": {
            "workflow": dict.fromkeys(
                ("polish", "analyze", "summary", "mindmap", "detail", "diarize"),
                False,
            )
        },
        "webhook_url": "https://example.test/callback",
        "folder_id": "course-id",
        "collection_folders": [{"source": urls[0], "create_folder": True}],
        "move_existing": True,
    }


def test_single_submit_and_unowned_staging_cleanup(workspace, tmp_path, monkeypatch):
    sources = [tmp_path / name for name in ("own.mp3", "reuse.mp3", "skip.mp3", "bad.mp3")]
    for source in sources:
        source.touch()
    task_id = str(uuid4())
    deleted = []

    class Api:
        def capabilities(self):
            return {}

        def stage_file(self, path):
            return {"staging_id": path.name, "path": f"/staged/{path.name}"}

        def delete_staged(self, staging_id):
            deleted.append(staging_id)

        def import_tasks(self, sources, options):
            return {
                "items": [
                    {
                        "outcome": "created",
                        "task": {"id": task_id, "source": sources[0], "status": "queued"},
                    },
                    *[
                        {"outcome": outcome, "reason": reason, "error": error}
                        for outcome, reason, error in (
                            ("reused", None, None),
                            ("skipped", "needs_action", None),
                            ("failed", None, "server rejected source"),
                        )
                    ][: len(sources) - 1],
                ]
            }

    monkeypatch.setattr(execution, "_require_daemon", lambda **_: Api())
    single = invoke("submit", str(sources[0]))
    assert single.exit_code == 0, single.exception
    assert single.stdout == f"{task_id}\n"
    assert deleted == []
    batch = invoke("submit", *(str(source) for source in sources))
    assert batch.exit_code == 5
    assert "reused" in batch.stdout and "skipped" in batch.stdout
    assert deleted == ["reuse.mp3", "skip.mp3", "bad.mp3"]


def test_import_http_payload_preserves_webhook_and_omits_unspecified_folder(monkeypatch):
    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"items": []})

    api = MppClient(base_url="https://example.test")
    with httpx.Client(
        base_url="https://example.test", transport=httpx.MockTransport(respond)
    ) as http:
        monkeypatch.setattr(api, "_client", http)
        api.import_tasks(["https://example.test/a"], webhook_url="https://example.test/hook")
        api.import_tasks(["https://example.test/b"], folder_id="xyz")
    assert "folder_id" not in seen[0]
    assert seen[0]["webhook_url"] == "https://example.test/hook"
    assert seen[1]["folder_id"] == "xyz"


@pytest.mark.parametrize("remote_has_folder", [True, False])
def test_folder_resolved_on_each_daemon_for_split_batch(
    workspace,
    tmp_path,
    monkeypatch,
    remote_has_folder,
):
    workspace.cli_use_remote_server = True
    workspace.remote_server_url = "https://coordinator.example"
    workspace.cli_local_files_stay_local = True
    file = tmp_path / "local.wav"
    file.touch()
    calls = []

    class Api:
        def __init__(self, is_local):
            self.folder = "local-folder" if is_local else "remote-folder"

        def capabilities(self):
            return {}

        def list_library_folders(self):
            if self.folder == "remote-folder" and not remote_has_folder:
                return {"folders": []}
            return {"folders": [{"id": self.folder, "name": "Shared name"}]}

        def import_tasks(self, sources, options, **kwargs):
            calls.append((sources, kwargs))
            assert kwargs["folder_id"] == self.folder
            return {
                "items": [
                    {
                        "outcome": "created",
                        "task": {
                            "id": str(uuid4()),
                            "status": "queued",
                            "source": sources[0],
                        },
                    }
                ]
            }

    monkeypatch.setattr(execution, "_require_daemon", lambda **_: Api(get_cli_context().is_local))
    result = invoke(
        "--json", "submit", "https://example.test/a", str(file), "--folder", "Shared name"
    )
    if not remote_has_folder:
        assert result.exit_code == 4 and calls == []
        return
    result = data(result)
    assert [item["source"] for item in result] == ["https://example.test/a", str(file)]
    assert calls == [
        ([str(file)], {"folder_id": "local-folder", "target_worker_id": "local"}),
        (["https://example.test/a"], {"folder_id": "remote-folder"}),
    ]


def test_retry_and_bulk_task_actions(workspace, monkeypatch):
    statuses = ["paused", "failed", "cancelled", "queued"]
    tasks = {str(uuid4()): {"status": state} for state in statuses}
    for task_id, record in tasks.items():
        record.update(id=task_id, source="test.wav")
    paused, failed, cancelled, queued = tasks
    calls = []

    class Api:
        def ping(self):
            return True

        def get_task(self, task_id):
            return tasks[task_id]

        def list_tasks(self, *, statuses, **kwargs):
            return [item for item in tasks.values() if item["status"] in statuses]

        def resume_task(self, task_id):
            calls.append(("resume", task_id))

        def checkpoint_rerun(self, task_id, **kwargs):
            calls.append(("rerun", task_id, kwargs))
            return {"task_id": task_id}

        def create_task(self, source, **kwargs):
            calls.append(("create", source))
            return {"id": "new-id"}

        def task_action(self, task_id, action):
            calls.append((action, task_id))
            return {"task_id": task_id}

        def delete_task(self, task_id):
            calls.append(("delete", task_id))
            return {"task_id": task_id}

    api = Api()
    monkeypatch.setattr(execution, "_require_daemon", lambda **_: api)
    monkeypatch.setattr(
        execution, "_wait_for_task", lambda task_id, _: {"id": task_id, "status": "completed"}
    )
    monkeypatch.setattr(task, "client", lambda: api)
    for ref in (paused, cancelled):
        assert data(invoke("--json", "retry", ref))["id"] == ref
    assert [item["id"] for item in data(invoke("--json", "retry", "--all-failed"))] == [failed]
    assert data(invoke("--json", "retry", failed, "--new"))["id"] == "new-id"
    data(invoke("--json", "task", "rerun", failed, "--from", "analyze"))
    data(invoke("--json", "task", "resume", "--all-paused"))
    data(invoke("--json", "task", "cancel", "--all-active"))
    data(invoke("--json", "task", "delete", "--status", "failed", "--yes"))
    assert calls == [
        ("resume", paused),
        ("rerun", cancelled, {}),
        ("rerun", failed, {}),
        ("create", "test.wav"),
        ("rerun", failed, {"from_step": "analyze"}),
        ("resume", paused),
        ("cancel", paused),
        ("cancel", queued),
        ("delete", failed),
    ]


def test_retry_all_failed_reports_conflict_and_continues(workspace, monkeypatch):
    ids = [str(uuid4()) for _ in range(3)]
    attempts = []

    class Api:
        def list_tasks(self, **kwargs):
            return [{"id": task_id} for task_id in ids]

        def get_task(self, task_id):
            return {"id": task_id, "status": "failed"}

        def checkpoint_rerun(self, task_id):
            attempts.append(task_id)
            if task_id == ids[1]:
                raise MppClientError("state_conflict", "State changed", exit_code=4)

    monkeypatch.setattr(execution, "_require_daemon", lambda **_: Api())
    result = invoke("--json", "retry", "--all-failed")
    assert attempts == ids
    assert result.exit_code == 5, result.exception
    result_data = json.loads(result.stdout)["data"]
    assert result_data["applied"] == [{"task_id": ids[0]}, {"task_id": ids[2]}]
    assert result_data["errors"][0]["task_id"] == ids[1]


def test_bilibili_generate_and_terminal_qr_login(workspace, monkeypatch):
    url = "https://passport.bilibili.com/qrcode?key=test-only"

    def respond(_):
        return httpx.Response(200, json={"code": 0, "data": {"url": url, "qrcode_key": "fixture"}})

    login = BilibiliLoginService()
    monkeypatch.setattr(
        login, "_client", lambda: httpx.Client(transport=httpx.MockTransport(respond))
    )
    qr = login.generate()
    assert qr["url"] == url
    assert qr["image"].startswith("data:image/svg+xml;base64,")
    polled = []
    states = iter(["waiting", "scanned", "success"])
    sleeps = []

    class Api:
        def bilibili_qr_generate(self):
            return qr

        def bilibili_qr_poll(self, session_id):
            polled.append(session_id)
            return {"state": next(states)}

    monkeypatch.setattr(configuration, "client", Api)
    monkeypatch.setattr(
        configuration,
        "time",
        SimpleNamespace(
            monotonic=lambda: sum(sleeps),
            sleep=sleeps.append,
        ),
    )
    result = invoke("source", "auth", "login", "bilibili", "--timeout", "10")
    assert result.exit_code == 0, result.exception
    assert url in result.stderr and "█" in result.stderr
    assert "login succeeded" in result.stdout
    assert polled == [qr["session_id"]] * 3 and sleeps == [2.0, 2.0]


def test_sync_push_endpoint_flushes_archives_and_library_and_reuploads_explicitly(
    workspace,
    monkeypatch,
):
    workspace.remote_server_url = "https://coordinator.example"
    first, first_dir = make_task(workspace, name="first")
    second, _ = make_task(workspace, name="second")
    first_id, second_id = str(first.id), str(second.id)
    workspace_id = "other-workspace"
    remote_instance = str(uuid4())
    uploads = []
    library_calls = []
    health_calls = []

    async def health(server):
        health_calls.append(server)

    def respond(request):
        if request.url.path == "/api/sync/status":
            return httpx.Response(
                200,
                json={
                    "workspace_id": workspace_id,
                    "revision": 0,
                    "instance_id": remote_instance,
                },
            )
        assert request.headers["x-mpp-workspace-id"] == workspace_id
        if request.url.path == "/api/sync/import":
            content = request.read()
            uploads.append(content)
            return httpx.Response(
                200, json={"sync": {"archive_sha256": "fixture", "worker_id": "test"}}
            )
        assert request.url.path == "/api/sync/library"
        payload = json.loads(request.content)
        library_calls.append(payload)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "folder_id_map": {folder["id"]: folder["id"] for folder in payload["folders"]},
            },
        )

    service = remote_archive_upload.RemoteArchiveUploadService(
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(respond),
            **kwargs,
        ),
    )
    monkeypatch.setattr(service, "_check_server_health", health)
    monkeypatch.setattr(remote_archive_upload, "_service", service)
    with api_for(sync_routes.router) as api:
        assert api.post("/api/sync/push").json() == {
            "enabled": False,
            "uploaded": 0,
            "library_pushed": False,
        }
        assert uploads == []
        workspace.remote_sync_enabled = True
        url = workspace.remote_server_url
        workspace.remote_server_url = ""
        assert api.post("/api/sync/push").status_code == 400
        workspace.remote_server_url = url
        workspace.remote_sync_upload_results = False
        get_library_service().create("Course")
        status = api.get("/api/sync/status").json()
        assert status["remote_sync_enabled"] and status["remote_server_configured"]
        assert status["remote_sync_upload_results"] is False

        response = api.post("/api/sync/push")
        assert response.status_code == 200, response.text
        outcome = response.json()
        assert outcome == {
            "enabled": True,
            "uploaded": 2,
            "library_pushed": True,
            "task_ids": [second_id, first_id],
        }
        assert len(uploads) == 2 and len(library_calls) == 1
        assert api.post("/api/sync/push").json() == {
            "enabled": True,
            "uploaded": 0,
            "library_pushed": False,
            "task_ids": [],
        }

        (first_dir / "summary.md").write_text("An edited summary", encoding="utf-8")
        result = api.post("/api/sync/push", json={"task_ids": [first_id, first_id]})
        assert result.status_code == 200, result.text
        assert result.json()["task_ids"] == [first_id]
        assert len(uploads) == 3 and uploads[-1] != uploads[1]
        assert len(health_calls) == 3

        remote_instance = archive_sync.get_archive_sync_service().instance_id
        result = api.post("/api/sync/push", json={"task_ids": [first_id]})
        assert result.json()["uploaded"] == 0
        assert len(uploads) == 3


def test_sync_cli_status_pull_and_push(workspace, monkeypatch):
    record, _ = make_task(workspace)
    calls = []

    class Api:
        def sync_status_info(self):
            return {
                "workspace_id": "fixture",
                "revision": 8,
                "indexing": False,
                "remote_sync_enabled": True,
            }

        def sync_pull(self):
            return {"enabled": True, "upserted": 3, "deleted": 1, "skipped": 0}

        def get_task(self, task_id):
            assert task_id == str(record.id)
            return record.model_dump(mode="json")

        def sync_push(self, payload=None):
            calls.append(payload)
            return {"enabled": True, "uploaded": 1, "library_pushed": False}

    monkeypatch.setattr(operations, "client", Api)
    status = invoke("sync", "status")
    assert status.exit_code == 0, status.exception
    assert all(field in status.stdout for field in ("workspace_id", "revision", "indexing"))
    assert data(invoke("--json", "sync", "pull"))["upserted"] == 3
    assert data(invoke("--json", "sync", "push", str(record.id)))["uploaded"] == 1
    data(invoke("--json", "sync", "push", "--all"))
    assert calls == [{"task_ids": [str(record.id)]}, None]


def test_device_settings_node_preference_and_offline_fallback(workspace, monkeypatch):
    updates = []

    class Api:
        def ping(self):
            return True

        def capabilities(self):
            return {
                "compute_capabilities": {
                    "cuda": False,
                    "mps": True,
                    "metal": True,
                    "onnx_providers": ["CPUExecutionProvider"],
                }
            }

        def get_settings(self):
            return {"uvr_device": "cpu", "remote_api_token": "secret-fixture"}

        def patch_settings(self, fields):
            updates.append(fields)
            return {**fields, "remote_api_token": "secret-fixture"}

        def execution_nodes(self):
            return {"nodes": [{"node_id": "worker-id", "display_name": "Office"}]}

    monkeypatch.setattr(configuration, "client", Api)
    monkeypatch.setattr(operations, "client", Api)
    listing = data(invoke("--json", "device", "list"))
    assert listing["compute_capabilities"]["mps"] is True
    assert listing["devices"]["uvr_device"] == "cpu"
    assert "secret-fixture" not in str(listing)
    assert data(invoke("--json", "device", "set", "asr", "cuda")) == {
        "sherpa_device": "cuda",
        "llama_asr_device": "cuda",
    }
    assert len(data(invoke("--json", "device", "set", "all", "cpu"))) == 7
    assert data(invoke("--json", "server", "prefer", "office", "--offline-policy", "wait")) == {
        "remote_preferred_worker_id": "worker-id",
        "remote_execution_offline_policy": "wait",
    }
    assert data(invoke("--json", "server", "prefer", "auto")) == {"remote_preferred_worker_id": ""}
    assert invoke("server", "prefer", "unknown").exit_code == 4

    class Offline(Api):
        def ping(self):
            return False

    def patch_local(fields):
        updates.append(("local", fields))
        return settings.RuntimeSettings(**fields)

    monkeypatch.setattr(configuration, "client", Offline)
    monkeypatch.setattr(settings, "patch_runtime_settings", patch_local)
    assert data(invoke("--json", "device", "set", "aligner", "metal")) == {
        "aligner_device": "metal"
    }
    assert updates[-1] == ("local", {"aligner_device": "metal"})


def test_system_and_update_cli_render_actual_contract(workspace, monkeypatch):
    calls = []

    class Api:
        def get_system_status(self):
            return {
                "version": "0.1.0",
                "pid": 10,
                "uptime_seconds": 30,
                "gpu": {
                    "devices": [
                        {
                            "index": 0,
                            "name": "fixture-GPU",
                            "memory_used_mb": 128,
                            "memory_total_mb": 1024,
                        }
                    ],
                    "mpp_reserved_mb": 64,
                },
                "models": [{"kind": "ASR", "name": "fixture-model"}],
                "workers": {
                    "gpu": {"busy": True},
                    "downloads": {"active": 2, "slots": 3},
                    "waiting": 4,
                    "paused": 0,
                },
                "disk": {"path": "/data", "total": 2**31, "used": 2**30, "free": 2**30},
                "errors": {
                    "count": 1,
                    "recent": [{"event": "fixture.error", "message": "failure"}],
                },
                "failed_tasks_24h": 2,
            }

        def check_update(self, *, force):
            calls.append(("check", force))
            return {
                "current_version": "0.1.0",
                "latest_version": "0.2.0",
                "update_available": True,
                "release_url": "https://example.test/release",
                "assets": [{"name": "mpp.tar.gz", "size": 50, "recommended": True}],
            }

        def download_update(self, asset):
            calls.append(("download", asset))
            return {"path": "/data/mpp.tar.gz", "sha256": "a" * 64, "instructions": "Unpack it."}

    monkeypatch.setattr(system, "client", Api)
    status = invoke("system", "status")
    assert status.exit_code == 0, status.exception
    for expected in (
        "30s",
        "128/1024",
        "fixture-model",
        "2/3",
        "1.00 GiB free",
        "fixture.error",
        "Failed tasks (24h)\t2",
        "Queued tasks\t4",
    ):
        assert expected in status.stdout
    check = invoke("update", "check", "--force")
    assert check.exit_code == 0, check.exception
    for expected in ("0.1.0", "0.2.0", "update_available", "release", "mpp.tar.gz"):
        assert expected in check.stdout
    download = invoke("update", "download", "--asset", "mpp.tar.gz")
    assert download.exit_code == 0, download.exception
    assert "/data/mpp.tar.gz" in download.stdout and "a" * 64 in download.stdout
    assert "Unpack it." in download.stdout
    assert calls == [("check", True), ("download", "mpp.tar.gz")]


def test_platform_cookies_are_saved_on_daemon_without_echo(workspace, monkeypatch, tmp_path):
    from app.api.routes import settings as routes

    def persist(updates):
        for key, value in updates.items():
            setattr(workspace, key, value)
        return workspace

    monkeypatch.setattr(settings, "patch_runtime_settings", persist)
    monkeypatch.setattr(routes, "patch_runtime_settings", persist)
    with api_for(pipeline_routes.router, routes.router) as http:
        server = MppClient()
        monkeypatch.setattr(server, "_client", http)
        monkeypatch.setattr(configuration, "client", lambda: server)
        for platform, cookie in (
            ("bilibili", "SESSDATA=bili-fixture; bili_jct=csrf-fixture; DedeUserID=123"),
            ("xiaohongshu", "a1=xhs-fixture"),
            ("twitter", "auth_token=twitter-fixture; ct0=csrf-fixture"),
        ):
            result = invoke("--json", "source", "auth", "set-cookie", platform, input=cookie)
            assert data(result)["configured"]
            assert "fixture" not in result.output
        youtube = tmp_path / "cookies.txt"
        youtube.write_text("# Netscape HTTP Cookie File\n# youtube-fixture\n")
        result = invoke("source", "auth", "set-cookie", "youtube", "--from", str(youtube))
        assert result.exit_code == 0, result.output
        assert "youtube-fixture" not in result.output
        assert Path(workspace.youtube_cookies_file).read_text() == youtube.read_text()
        assert (
            invoke("source", "auth", "set-cookie", "youtube", "--browser", "firefox").exit_code == 0
        )
    assert workspace.bilibili_sessdata == "bili-fixture"
    assert workspace.xiaohongshu_cookie == "a1=xhs-fixture"
    storage_state = json.loads(Path(workspace.twitter_storage_state_path).read_text())
    assert storage_state["cookies"][0]["value"] == "twitter-fixture"
    assert "twitter-fixture" not in workspace.platform_configs
    assert workspace.youtube_cookies_file == "" and workspace.youtube_cookies_browser == "firefox"


@pytest.mark.parametrize(
    ("provider_type", "arguments"),
    [
        ("codex_oauth", ["login"]),
        ("agy_oauth", []),
        ("kimi_oauth", ["login"]),
        ("qoder_oauth", ["login"]),
    ],
)
def test_provider_login_verifies_daemon_and_syncs(
    workspace, monkeypatch, tmp_path, provider_type, arguments,
):
    from app.services.analysis import coding_plan_cli

    calls = []
    executable = tmp_path / "Provider Tools" / "provider-cli"

    class Api:
        def get_settings(self):
            return {
                "providers": [
                    {"id": "oauth", "provider_type": provider_type, "cli_path": str(executable)}
                ]
            }

        def provider_oauth_status(self, provider_id):
            calls.append(("status", provider_id))
            return {"authenticated": True}

        def sync_provider_models(self, provider_id):
            calls.append(("sync", provider_id))
            return {"models": [{"model_id": "fixture"}]}

    monkeypatch.setattr(configuration, "client", Api)
    monkeypatch.setattr(CliContext, "interactive", property(lambda _: True))
    monkeypatch.setattr(
        configuration, "sys", SimpleNamespace(stdout=SimpleNamespace(isatty=lambda: True))
    )
    monkeypatch.setattr(
        coding_plan_cli, "resolve_coding_plan_executable", lambda *args: executable
    )

    def login(command, *, check):
        calls.append(("login", command, check))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(configuration, "subprocess", SimpleNamespace(run=login))
    assert data(invoke("--json", "provider", "login", "oauth"))["authenticated"]
    assert calls == [
        ("login", [str(executable), *arguments], False),
        ("status", "oauth"),
        ("sync", "oauth"),
    ]
    calls.clear()
    remote = invoke("--json", "--server", "https://remote.example", "provider", "login", "oauth")
    assert data(remote)["authenticated"]
    assert calls == [("status", "oauth"), ("sync", "oauth")]
