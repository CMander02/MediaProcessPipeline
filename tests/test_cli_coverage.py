from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import typer
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.api.routes import tasks as task_routes  # noqa: E402
from app.cli.client import MppClient, MppClientError  # noqa: E402
from app.cli.commands import config_values, configuration  # noqa: E402
from app.cli.commands import operations as operation_commands  # noqa: E402
from app.cli.commands import task as task_commands  # noqa: E402
from app.cli.commands.common import resolve_task_ref  # noqa: E402
from app.cli.context import configure_cli_context  # noqa: E402
from app.cli.daemon import _managed_process_matches  # noqa: E402
from app.cli.main import app  # noqa: E402
from app.cli.output import is_secret_key, redact  # noqa: E402
from app.cli.renderers import (  # noqa: E402
    fmt_ms_timestamp,
    render_chapters,
    render_mindmap_tree,
    render_note_with_image_descriptions,
)
from app.cli.submission import expand_sources, submit_sources, task_options  # noqa: E402
from app.core import database  # noqa: E402
from app.core import settings as settings_module  # noqa: E402
from app.core.settings import RuntimeSettings  # noqa: E402
from app.models import Task, TaskStatus, TaskType  # noqa: E402
from app.services import cleanup as cleanup_module  # noqa: E402

runner = CliRunner()


class FakeTaskApi:
    def __init__(self, tasks: list[dict] | None = None) -> None:
        self.tasks = tasks or []

    def ping(self) -> bool:
        return True

    def list_tasks(self, **kwargs):
        statuses = kwargs.get("statuses")
        items = self.tasks
        if statuses:
            items = [item for item in items if item.get("status") in statuses]
        offset = kwargs.get("offset", 0)
        limit = kwargs.get("limit", 50)
        return items[offset : offset + limit]

    def get_task(self, task_id: str):
        return next(item for item in self.tasks if item["id"] == task_id)


def test_help_exposes_the_documented_command_tree():
    result = runner.invoke(app, ["--skip-version-check", "--plain", "--help"])

    assert result.exit_code == 0
    for command in (
        "server",
        "auth",
        "task",
        "archive",
        "folder",
        "read",
        "search",
        "speaker",
        "stage",
        "provider",
        "model",
        "flow",
        "source",
        "kb",
        "voiceprint",
        "logs",
        "storage",
        "fs",
        "sync",
        "pipeline",
        "status",
        "list",
    ):
        assert command in result.stdout


def test_every_public_leaf_command_renders_help():
    root = typer.main.get_command(app)
    leaves: list[list[str]] = []

    def collect(command, path: list[str]) -> None:
        children = getattr(command, "commands", None)
        if children:
            for name, child in children.items():
                if not getattr(child, "hidden", False):
                    collect(child, [*path, name])
            return
        leaves.append(path)

    collect(root, [])

    assert len(leaves) == 152
    failures = []
    for path in leaves:
        result = runner.invoke(app, ["--skip-version-check", "--plain", *path, "--help"])
        if result.exit_code != 0:
            failures.append((" ".join(path), str(result.exception)))
    assert failures == []


def test_sync_transfer_streams_one_completed_task_without_media_by_default(
    tmp_path: Path,
    monkeypatch,
):
    task_id = str(uuid4())
    task = {
        "id": task_id,
        "task_type": "pipeline",
        "status": "completed",
        "source": "https://example.com/video",
        "options": {},
        "progress": 1.0,
        "result": {"output_dir": "D:/Media/视频任务"},
    }

    class SourceApi:
        def get_task(self, value: str):
            assert value == task_id
            return task

        def export_task_archive(self, value, destination, *, include_media=False):
            assert value == task_id
            assert include_media is False
            destination.write_bytes(b"portable")
            return {
                "size": 8,
                "sha256": "a" * 64,
                "headers": {"x-mpp-archive-name": "%E8%A7%86%E9%A2%91%E4%BB%BB%E5%8A%A1"},
                "folder_json": '{"folder": {"name": "课程"}}',
            }

    captured: dict[str, object] = {}

    class TargetApi:
        def __init__(self, base_url, api_token, timeout):
            captured.update(base_url=base_url, api_token=api_token, timeout=timeout)

        def import_task_archive(self, payload, archive_path, **kwargs):
            captured.update(payload=payload, archive=archive_path.read_bytes(), **kwargs)
            return {"ok": True, "already_synced": False}

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(operation_commands, "client", lambda: SourceApi())
    monkeypatch.setattr(operation_commands, "MppClient", TargetApi)

    result = runner.invoke(
        app,
        [
            "--skip-version-check",
            "--json",
            "--server",
            "http://localhost:18000",
            "sync",
            "transfer",
            task_id,
            "--to",
            "https://target.example",
            "--to-token",
            "target-secret",
        ],
    )

    assert result.exit_code == 0, result.stdout
    output = json.loads(result.stdout)["data"]
    assert output["include_media"] is False
    assert output["archive_name"] == "视频任务"
    assert captured["base_url"] == "https://target.example"
    assert captured["api_token"] == "target-secret"
    assert captured["archive"] == b"portable"
    assert json.loads(captured["folder_json"]) == {"folder": {"name": "课程"}}
    assert captured["closed"] is True


def test_config_replace_dry_run_validates_without_writing(tmp_path: Path):
    source = tmp_path / "settings.json"
    source.write_text("{}", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "--skip-version-check",
            "--json",
            "--no-input",
            "config",
            "replace",
            "--from",
            str(source),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"]["valid"] is True


def test_client_replaces_complete_settings_with_put():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data_root": "D:/Media"})

    api = MppClient("http://localhost:18000")
    api._client.close()
    api._client = httpx.Client(
        base_url=api.base_url,
        transport=httpx.MockTransport(handler),
        headers=api._headers(),
    )
    try:
        result = api.put_settings({"data_root": "D:/Media"})
    finally:
        api.close()

    assert seen == {"method": "PUT", "body": {"data_root": "D:/Media"}}
    assert result["data_root"] == "D:/Media"


def test_non_interactive_destructive_command_requires_confirmation():
    result = runner.invoke(
        app,
        ["--skip-version-check", "--json", "--no-input", "task", "delete", "@last"],
    )

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "confirmation_required"


def test_task_list_json_uses_one_envelope(monkeypatch):
    task_id = str(uuid4())
    fake = FakeTaskApi([{"id": task_id, "status": "paused", "progress": 0.5, "source": "demo.mp4"}])
    monkeypatch.setattr(task_commands, "client", lambda: fake)

    result = runner.invoke(
        app,
        ["--skip-version-check", "--json", "task", "list", "--active"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"][0]["id"] == task_id
    assert payload["meta"]["server"] == "http://localhost:18000"


def test_legacy_command_local_json_uses_shared_envelope(monkeypatch):
    from app.cli.commands import config_values

    monkeypatch.setattr(config_values, "_read_settings", lambda: {"api_token": "abcd..."})

    result = runner.invoke(
        app,
        ["--skip-version-check", "config", "get", "api_token", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"]["key"] == "api_token"


def test_ambiguous_task_prefix_is_a_structured_exit_four():
    shared = "abcdef12"
    fake = FakeTaskApi(
        [
            {"id": f"{shared}-0000-0000-0000-000000000001", "status": "failed"},
            {"id": f"{shared}-0000-0000-0000-000000000002", "status": "completed"},
        ]
    )
    configure_cli_context(output_mode="json")

    with pytest.raises(Exception) as exc_info:
        resolve_task_ref(shared, fake)

    assert getattr(exc_info.value, "exit_code", None) == 4


def test_client_applies_bearer_and_requested_with_headers():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.headers))
        return httpx.Response(200, json={"status": "healthy"})

    api = MppClient("http://localhost:18000", api_token="secret-token")
    api._client.close()
    api._client = httpx.Client(
        base_url=api.base_url,
        transport=httpx.MockTransport(handler),
        headers=api._headers(),
    )
    try:
        assert api.ping() is True
    finally:
        api.close()

    assert seen["authorization"] == "Bearer secret-token"
    assert seen["x-requested-with"] == "mpp-cli"


def test_secret_redaction_covers_nested_provider_data():
    safe = redact(
        {
            "api_token": "token-value",
            "providers": [
                {
                    "api_key": "provider-key",
                    "headers": {"Authorization": "Bearer private"},
                }
            ],
            "hf_proxy": "http://user:password@localhost:1080",
        }
    )

    rendered = json.dumps(safe)
    assert "token-value" not in rendered
    assert "provider-key" not in rendered
    assert "Bearer private" not in rendered
    assert "user:password" not in rendered


@pytest.mark.parametrize("key,value", [
    ("vlm_max_tokens", 4096),
    ("llama_asr_max_new_tokens", 512),
    ("local_llm_max_new_tokens", 4096),
    ("moss_cpp_max_new_tokens", 8192),
    ("llm_token_limit", 100),
    ("total_tokens", 100),
    ("enable_token_timestamps", True),
    ("token_timestamps", False),
    ("youtube_cookies_browser", "chrome"),
    ("youtube_cookies_file", "cookies.txt"),
    ("cookies_from_browser", "firefox"),
])
def test_non_secret_settings_keep_their_values(key, value):
    assert not is_secret_key(key)
    assert redact({key: value}) == {key: value}


@pytest.mark.parametrize("key", [
    "vlm_api_key", "kb_embedding_api_key", "xiaohongshu_cookie", "network_proxy",
    "youtube_proxy", "hf_proxy", "remote_api_token", "api_token", "jina_reader_api_key",
])
def test_credential_settings_are_masked(key):
    assert is_secret_key(key)
    assert redact({key: "sensitive-value"}) == {key: "sens..."}
    assert config_values._mask(key, "sensitive-value") == "sens..."


def test_config_groups_cover_all_runtime_settings():
    assert {key for keys in config_values._CONFIG_GROUPS.values() for key in keys} == set(
        RuntimeSettings.model_fields
    )


def test_config_list_summarizes_complex_values_in_text_only(monkeypatch):
    settings = {
        "service_connections": [],
        "providers": [{"name": "fixture-provider"}, {"name": "second-provider"}],
        "runtime_model_bindings": {"llm": {"provider_id": "fixture-provider"}},
    }
    monkeypatch.setattr(config_values, "_read_settings", lambda: settings)
    args = ["--local", "config", "list", "--group", "providers"]
    human = runner.invoke(app, args)
    assert human.exit_code == 0, human.output
    assert all(item in human.stdout for item in ("[]", "[2 items]", "{1 keys}"))
    assert "fixture-provider" not in human.stdout
    machine = runner.invoke(app, ["--json", *args])
    assert machine.exit_code == 0, machine.output
    assert json.loads(machine.stdout)["data"] == settings
    single = runner.invoke(app, ["--local", "config", "get", "providers"])
    assert single.exit_code == 0, single.output
    assert "fixture-provider" in single.stdout


def test_storage_media_apply_emits_one_json_object(monkeypatch):
    preview = {
        "path": "/archive",
        "policy": "text",
        "entries": [
            {"path": "movie.mp4", "delete": True, "bytes": 200},
            {"path": "reference.wav", "delete": False, "bytes": 100},
        ],
        "reclaimable_bytes": 200,
    }
    applied = {"cleaned": [preview["entries"][0]], "errors": [], "reclaimed_bytes": 200}
    calls = []

    class Api:
        def media_retention(self, path, policy, files=None):
            calls.append((path, policy, files))
            return preview if files is None else applied

    monkeypatch.setattr(operation_commands, "client", Api)
    result = runner.invoke(app, [
        "--json", "--local", "storage", "media", "/archive", "--apply", "--yes",
    ])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"] == {"preview": preview, "applied": applied}
    assert calls == [("/archive", None, None), ("/archive", "text", ["movie.mp4"])]


def test_upgrade_ytdlp_failure_emits_structured_error(monkeypatch):
    from app.services.ingestion import ytdlp_version

    failure = {"ok": False, "output": "upgrade unavailable"}
    monkeypatch.setattr(ytdlp_version, "upgrade", lambda: failure)
    result = runner.invoke(app, ["--json", "upgrade-ytdlp"])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["error"] == {
        "code": "ytdlp_upgrade_failed",
        "message": "upgrade unavailable",
        "detail": failure,
        "retryable": False,
    }


@pytest.mark.parametrize(("mode", "command"), [
    ("--jsonl", ["list", "--watch"]),
    ("--jsonl", ["timeline", "@last", "--follow"]),
    ("--json", ["watch", "@last"]),
    ("--json", ["rerun", "@last", "--wait"]),
])
def test_task_sse_failure_is_reported_as_cli_error(monkeypatch, mode, command):
    task_id = str(uuid4())
    task = {"id": task_id, "status": "processing"}

    class Api(FakeTaskApi):
        def task_timeline(self, task_id, limit):
            return {"events": []}

        def checkpoint_rerun_task(self, task_id):
            return {"task_id": task_id}

        def stream_all_events(self):
            yield {"type": "heartbeat"}
            raise MppClientError("connection_failed", "stream disconnected", exit_code=3)

        def stream_task_events(self, task_id):
            yield from self.stream_all_events()

    monkeypatch.setattr(task_commands, "client", lambda: Api([task]))
    result = runner.invoke(app, [mode, "--local", "task", *command])
    assert result.exit_code == 3, result.output
    assert json.loads(result.stdout.splitlines()[-1])["error"]["code"] == "connection_failed"
    assert "Traceback" not in result.stderr


def test_source_collection_text_lists_selectable_ids_and_preserves_machine_output(monkeypatch):
    source = "https://example.com/collection"
    payload = {
        "title": "合集",
        "items": [{"id": "BV123:p2", "title": "第二集\n标题", "duration": 75, "url": source}],
    }

    class Api:
        def bilibili_collection(self, url):
            assert url == source
            return payload

    monkeypatch.setattr(configuration, "client", Api)
    command = ["--local", "source", "collection", source]
    human = runner.invoke(app, ["--plain", *command])
    assert human.exit_code == 0, human.output
    assert human.stdout == "ID\tDURATION\tTITLE\nBV123:p2\t00:01:15.000\t第二集 标题\n"
    machine = runner.invoke(app, ["--json", *command])
    assert machine.exit_code == 0, machine.output
    assert json.loads(machine.stdout)["data"] == payload
    lines = runner.invoke(app, ["--jsonl", *command])
    assert lines.exit_code == 0, lines.output
    assert json.loads(lines.stdout) == payload


@pytest.mark.parametrize("milliseconds,expected", [
    (0, "00:00:00.000"), (3_661_023, "01:01:01.023"), (-1, "-00:00:00.001"),
])
def test_format_millisecond_timestamp(milliseconds, expected):
    assert fmt_ms_timestamp(milliseconds) == expected


def test_render_mindmap_tree_has_ascii_connectors_in_plain_mode():
    tree = {
        "title": "访谈",
        "children": [
            {"title": "开场", "start": 0, "children": [{"title": "介绍", "start": 1.5}]},
            {"title": "结尾", "start": 62},
        ],
    }
    assert render_mindmap_tree(tree, plain=True).splitlines() == [
        "访谈",
        "|-- 开场\t00:00:00.000",
        "|   `-- 介绍\t00:00:01.500",
        "`-- 结尾\t00:01:02.000",
    ]
    assert "│   └── 介绍" in render_mindmap_tree(tree)


def test_render_chapters_supports_metadata_and_timed_tree_fields():
    assert render_chapters([
        {"title": "Intro", "start_time": 0, "children": [{"title": "Detail", "start_ms": 803}]},
        {"title": "Finale", "start": 125.25},
    ]).splitlines() == [
        "START\tTITLE",
        "00:00:00.000\tIntro",
        "00:00:00.803\t  Detail",
        "00:02:05.250\tFinale",
    ]


def test_render_note_keeps_markdown_and_adds_available_image_descriptions():
    note = "# Note\n\n![Cover](images/01.jpg)\n"
    assert render_note_with_image_descriptions(note, [
        {"index": 1, "text": "A cover.\nSecond line.", "status": "completed"},
        {"index": 2, "status": "skipped", "error": "VLM unavailable"},
    ]) == "# Note\n\n![Cover](images/01.jpg)\n\nImage 1\nA cover.\nSecond line."


def test_new_client_methods_send_api_request_payloads():
    requests = []
    page = {"archives": [{"id": "fixture"}], "total": 1, "page": 2}

    def handler(request):
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, dict(request.url.params), body))
        return httpx.Response(200, json=page)

    api = MppClient(base_url="https://mpp.example")
    api.close()
    with httpx.Client(base_url=api.base_url, transport=httpx.MockTransport(handler)) as transport:
        api._client = transport
        assert api.list_archives_paged(page=2, search="访谈") == page
        assert api.list_archives() == page["archives"]
        api.import_tasks(
            ["https://example.com"], {"force_asr": True},
            target_worker_id="local", folder_id="folder-a", move_existing=True,
            collection_folders=[{"source": "https://example.com", "create_folder": True}],
        )
        api.checkpoint_rerun_task("task-a")
        api.checkpoint_rerun("task-a", from_step="polish", workflow={"polish": False})
        api.checkpoint_rerun("task-a", workflow={})
        api.move_library_members(["source-a"], None)
        api.reconcile_task_speakers("task-a", "SRT")
        api.download_update()
    assert requests == [
        ("GET", "/api/pipeline/archives", {"page": "2", "search": "访谈"}, None),
        ("GET", "/api/pipeline/archives", {"lite": "false"}, None),
        ("POST", "/api/tasks/import", {}, {
            "sources": ["https://example.com"], "options": {"force_asr": True},
            "target_worker_id": "local", "folder_id": "folder-a", "move_existing": True,
            "collection_folders": [{"source": "https://example.com", "create_folder": True}],
        }),
        ("POST", "/api/tasks/task-a/checkpoint-rerun", {}, {}),
        ("POST", "/api/tasks/task-a/checkpoint-rerun", {},
         {"from_step": "polish", "workflow": {"polish": False}}),
        ("POST", "/api/tasks/task-a/checkpoint-rerun", {}, {"workflow": {}}),
        ("POST", "/api/library/folder-members/move", {},
         {"content_keys": ["source-a"], "folder_id": None}),
        ("POST", "/api/tasks/task-a/speakers/reconcile", {}, {"expected_srt": "SRT"}),
        ("POST", "/api/system/update/download", {}, {"asset_name": None}),
    ]


def test_source_expansion_supports_emoji_directories_and_globs(tmp_path: Path):
    folder = tmp_path / "媒体 🎬"
    folder.mkdir()
    first = folder / "第一集.mp4"
    second = folder / "音频 🎧.mp3"
    ignored = folder / "notes.txt"
    first.write_bytes(b"video")
    second.write_bytes(b"audio")
    ignored.write_text("notes", encoding="utf-8")

    expanded = expand_sources([str(folder)], recursive=False)
    globbed = expand_sources([str(folder / "*.mp3")], recursive=False)

    # Directory enumeration order differs between filesystems.
    assert sorted(expanded) == sorted([str(first.resolve()), str(second.resolve())])
    assert globbed == [str(second.resolve())]


def test_common_submit_options_and_conflicts():
    options = task_options(
        skip_separation=True,
        speakers=2,
        hotwords=["Codex", "MPP"],
        assignments=["temperature=0.2", 'labels=["cli","test"]'],
    )

    assert options == {
        "temperature": 0.2,
        "labels": ["cli", "test"],
        "skip_separation": True,
        "num_speakers": 2,
        "hotwords": ["Codex", "MPP"],
    }

    assert task_options(force_asr=True) == {
        "force_asr": True,
    }
    assert task_options(prefer_subtitles=True) == {
        "force_asr": False,
    }


def test_remote_staging_keeps_successes_when_one_upload_fails(tmp_path: Path):
    good = tmp_path / "good.mp3"
    bad = tmp_path / "bad.mp3"
    good.write_bytes(b"good")
    bad.write_bytes(b"bad")

    class FakeUploadApi:
        def capabilities(self):
            return {"local_path_submission": False}

        def stage_file(self, path: Path):
            if path.name == "bad.mp3":
                raise MppClientError("upload_failed", "network interrupted")
            return {"staging_id": "stage-good", "path": "/remote/good.mp3"}

        def create_tasks_batch(self, sources, options, webhook_url):
            return [
                {
                    "id": str(uuid4()),
                    "status": "queued",
                    "source": source,
                    "options": options,
                }
                for source in sources
            ]

        def delete_staged(self, staging_id: str):
            return {"deleted": True, "staging_id": staging_id}

    configure_cli_context(server_url="https://mpp.example", output_mode="text")
    tasks, errors = submit_sources(
        FakeUploadApi(),
        [str(good), str(bad)],
        options={"force_asr": True},
    )

    assert [task["source"] for task in tasks] == ["/remote/good.mp3"]
    assert errors == [
        {"source": str(bad), "code": "upload_failed", "message": "network interrupted"}
    ]


def test_task_list_api_accepts_multiple_statuses_and_offset(tmp_path: Path):
    database.reset_db_path(tmp_path)
    store = database.get_task_store()
    for status in (TaskStatus.COMPLETED, TaskStatus.PAUSED, TaskStatus.QUEUED):
        store.save(Task(task_type=TaskType.PIPELINE, source=f"{status}.mp3", status=status))

    api = FastAPI()
    api.include_router(task_routes.router, prefix="/api")
    response = TestClient(api).get(
        "/api/tasks",
        params={"statuses": "paused,queued", "limit": 1, "offset": 1},
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["status"] in {"paused", "queued"}


def test_cleanup_dry_run_reports_size_and_preserves_data(tmp_path: Path, monkeypatch):
    settings = RuntimeSettings(data_root=str(tmp_path))
    monkeypatch.setattr(settings_module, "_runtime_settings", settings)
    database.reset_db_path(tmp_path)
    cleanup_module._service = None

    output_dir = tmp_path / "failed task"
    output_dir.mkdir()
    (output_dir / "partial.wav").write_bytes(b"12345")
    task = Task(
        task_type=TaskType.PIPELINE,
        source="broken.mp3",
        status=TaskStatus.FAILED,
        result={"output_dir": str(output_dir)},
    )
    database.get_task_store().save(task)

    preview = cleanup_module.get_cleanup_service().cleanup_failed_task(str(task.id), dry_run=True)

    assert output_dir.exists()
    assert preview["candidates"] == [{"path": str(output_dir), "bytes": 5, "reason": "task_failed"}]


def test_cleanup_apply_deletes_failed_task_output(tmp_path: Path, monkeypatch):
    settings = RuntimeSettings(data_root=str(tmp_path))
    monkeypatch.setattr(settings_module, "_runtime_settings", settings)
    database.reset_db_path(tmp_path)
    cleanup_module._service = None

    output_dir = tmp_path / "failed task"
    output_dir.mkdir()
    (output_dir / "partial.wav").write_bytes(b"12345")
    task = Task(
        task_type=TaskType.PIPELINE,
        source="broken.mp3",
        status=TaskStatus.FAILED,
        result={"output_dir": str(output_dir)},
    )
    database.get_task_store().save(task)

    result = cleanup_module.get_cleanup_service().cleanup_failed_task(str(task.id))

    assert not output_dir.exists()
    assert result["cleaned"] == [str(output_dir)]


def test_cleanup_preserves_completed_task_output(tmp_path: Path, monkeypatch):
    settings = RuntimeSettings(data_root=str(tmp_path))
    monkeypatch.setattr(settings_module, "_runtime_settings", settings)
    database.reset_db_path(tmp_path)
    cleanup_module._service = None

    output_dir = tmp_path / "completed task"
    output_dir.mkdir()
    task = Task(
        task_type=TaskType.PIPELINE,
        source="done.mp3",
        status=TaskStatus.COMPLETED,
        result={"output_dir": str(output_dir)},
    )
    database.get_task_store().save(task)

    result = cleanup_module.get_cleanup_service().cleanup_failed_task(str(task.id))

    assert output_dir.exists()
    assert result["cleaned"] == []
    assert "not eligible" in result["errors"][0]["error"]


def test_daemon_stop_verification_requires_the_expected_command(monkeypatch):
    state = {"pid": 123, "create_time": 10.0}
    monkeypatch.setattr(
        "app.cli.daemon._process_info",
        lambda _pid: {
            "pid": 123,
            "running": True,
            "cmdline": ["python", "-m", "unrelated.server"],
            "create_time": 10.0,
        },
    )

    assert _managed_process_matches(state) is False
