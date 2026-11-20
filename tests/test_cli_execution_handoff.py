"""Root command registration dispatches through the shared CLI context."""

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.cli.client import MppClientError
from app.cli.commands import execution
from app.cli.context import get_cli_context
from app.cli.main import app


@pytest.mark.parametrize("status,exit_code", [("completed", 0), ("failed", 1)])
def test_direct_command_preserves_json_context_and_exit_code(tmp_path, monkeypatch, status, exit_code):
    source = tmp_path / "素材🎧.wav"
    source.write_bytes(b"fixture")
    calls = []

    def direct(path, options, quiet):
        calls.append((path, options, quiet, get_cli_context().output_mode))
        return {"id": "fixture", "status": status, "result": {"output_dir": str(tmp_path)}}

    monkeypatch.setattr(execution, "_run_direct", direct)
    result = CliRunner().invoke(
        app,
        ["--skip-version-check", "--json", "run", str(source), "--direct", "--no-sep"],
    )
    assert result.exit_code == exit_code, result.output
    payload = json.loads(result.stdout)
    assert payload["data"][0]["status"] == status
    assert calls[0][1]["skip_separation"] is True
    assert calls[0][3] == "json"


def test_direct_conflicting_upload_returns_usage_error_before_execution(tmp_path, monkeypatch):
    source = tmp_path / "素材🎧.wav"
    source.write_bytes(b"fixture")
    calls = []
    monkeypatch.setattr(execution, "_run_direct", lambda *args, **kwargs: calls.append(args))
    result = CliRunner().invoke(
        app,
        ["--skip-version-check", "--json", "run", str(source), "--direct", "--upload", "always"],
    )
    assert result.exit_code == 2
    assert "direct_option_conflict" in result.output
    assert calls == []


@pytest.mark.parametrize(
    "command,opts,default_remote,default_worker,global_opts,env,expected",
    [
        ("run", ["--detach", "-W", "silentfield"], False, "", [], {},
         ("https://coordinator.example", "remote-secret", "exe-silentfield")),
        ("submit", [], True, "exe-silentfield", [], {},
         ("https://coordinator.example", "remote-secret", "exe-silentfield")),
        ("submit", ["--local", "-W", "local"], True, "exe-silentfield", [], {},
         ("http://localhost:18000", "", "local")),
        ("submit", ["--remote"], False, "", ["--token", "explicit-token"], {},
         ("https://coordinator.example", "explicit-token", None)),
        ("submit", ["--remote", "-W", "future-node"], True, "", ["--server", "https://explicit.example"], {},
         ("https://explicit.example", "", "future-node")),
        ("submit", [], True, "", [], {"MPP_SERVER_URL": "https://env.example"},
         ("https://env.example", "", None)),
    ],
)
def test_submission_routes(command, opts, default_remote, default_worker, global_opts, env, expected, monkeypatch):
    from app.core import settings

    monkeypatch.setattr(settings, "_runtime_settings", settings.RuntimeSettings(
        remote_server_url="https://coordinator.example",
        remote_api_token="remote-secret",
        cli_use_remote_server=default_remote,
        cli_default_worker_id=default_worker,
    ))
    calls = []

    class Api:
        def capabilities(self):
            return {}

        def execution_nodes(self):
            return {"nodes": [{"node_id": "exe-silentfield", "display_name": "SilentField"}]}

        def create_tasks_batch(self, sources, options, webhook_url, target_worker_id=None):
            ctx = get_cli_context()
            calls.append((ctx.server_url, ctx.api_token, target_worker_id))
            return [{"id": "task", "status": "queued", "source": sources[0]}]

    monkeypatch.setattr(execution, "_require_daemon", lambda **kwargs: Api())
    result = CliRunner().invoke(
        app, ["--skip-version-check", "--json", *global_opts, command, "https://example.com/video", *opts],
        env={"MPP_SERVER_URL": None, "MPP_API_TOKEN": None, **env},
    )
    assert result.exit_code == 0, result.output
    assert calls == [expected]


@pytest.mark.parametrize("option", [["--target-worker", "local"], ["--remote"]])
def test_direct_rejects_node_routing(option):
    result = CliRunner().invoke(app, [
        "--skip-version-check", "--json", "run", "https://example.com/video", "--direct", *option,
    ])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "direct_option_conflict"


def test_display_name_ambiguity_uses_candidate_ids(monkeypatch):
    from app.core import settings

    monkeypatch.setattr(settings, "_runtime_settings", settings.RuntimeSettings())

    class Api:
        def execution_nodes(self):
            return {"nodes": [
                {"node_id": "exe-a", "display_name": "SilentField"},
                {"node_id": "exe-b", "display_name": "silentfield"},
            ]}

    monkeypatch.setattr(execution, "_require_daemon", lambda **kwargs: Api())
    result = CliRunner().invoke(app, [
        "--skip-version-check", "--json", "submit", "https://example.com/video", "-W", "silentfield",
    ])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["detail"]["candidate_node_ids"] == ["exe-a", "exe-b"]


def test_server_nodes_and_task_queries_use_default_coordinator(monkeypatch):
    from app.cli.commands import operations
    from app.cli.commands import common
    from app.core import settings

    monkeypatch.setattr(settings, "_runtime_settings", settings.RuntimeSettings(
        remote_server_url="https://coordinator.example", cli_use_remote_server=True,
    ))
    payload = {"enabled": True, "preferred_worker_id": "exe-a", "offline_policy": "wait", "nodes": [
        {"node_id": "exe-a", "display_name": "SilentField", "online": True,
         "busy": False, "platform": "windows", "compute_devices": ["cpu", "cuda"]},
    ]}
    servers = []

    class Api:
        def execution_nodes(self):
            return payload

        def ping(self):
            return True

        def task_stats(self):
            return {"queued": 1}

        def list_tasks(self, **kwargs):
            return []

    def connect():
        servers.append(get_cli_context().server_url)
        return Api()

    monkeypatch.setattr(operations, "client", connect)
    monkeypatch.setattr(common, "client", connect)
    runner = CliRunner()
    args = ["--skip-version-check"]
    env = {"MPP_SERVER_URL": None}
    nodes = runner.invoke(app, [*args, "server", "nodes", "--json"], env=env)
    assert nodes.exit_code == 0, nodes.output
    assert json.loads(nodes.stdout)["data"] == payload
    table = runner.invoke(app, [*args, "server", "nodes"], env=env)
    assert table.exit_code == 0, table.output
    assert "exe-a\tSilentField\tTrue\tFalse\twindows\tcpu, cuda" in table.stdout
    status = runner.invoke(app, [*args, "--json", "status"], env=env)
    assert status.exit_code == 0, status.output
    assert json.loads(status.stdout)["data"]["stats"]["queued"] == 1
    assert servers == ["https://coordinator.example"] * 3


@pytest.mark.parametrize("group", ["remote", "security"])
def test_config_masks_remote_api_token(group, monkeypatch):
    from app.cli.commands import config_values

    class Api:
        def ping(self):
            return True

        def get_settings(self):
            return {"remote_api_token": "the-complete-token", "cli_use_remote_server": True}

    monkeypatch.setattr(config_values, "_get_client", Api)
    result = CliRunner().invoke(app, [
        "--skip-version-check", "config", "list", "--group", group, "--json",
    ])
    assert result.exit_code == 0, result.output
    assert "the-complete-token" not in result.stdout
    assert json.loads(result.stdout)["data"]["remote_api_token"] == "the-..."


@pytest.mark.parametrize(
    "global_opts,command,default_remote,server_kind",
    [
        (["--remote"], ["status"], False, "remote"),
        (["--local"], ["status"], True, "local"),
        (["--remote"], ["server", "status"], False, "remote"),
        (["--local"], ["server", "nodes"], True, "local"),
        (["--local"], ["sync", "status"], True, "local"),
        (["--remote"], ["sync", "status"], False, "remote"),
        (["--local"], ["storage", "usage"], True, "local"),
        (["--local"], ["server", "nodes", "--remote"], True, "remote"),
        (["--remote"], ["server", "nodes", "--local"], False, "local"),
    ],
)
def test_global_daemon_routing(monkeypatch, global_opts, command, default_remote, server_kind):
    from app.cli.commands import common, operations
    from app.core import settings

    local = ("http://localhost:18000", "local-secret")
    remote = ("https://coordinator.example", "remote-secret")
    monkeypatch.setattr(settings, "_runtime_settings", settings.RuntimeSettings(
        cli_use_remote_server=default_remote,
        api_token=local[1],
        remote_server_url=remote[0],
        remote_api_token=remote[1],
    ))
    calls = []

    class Api:
        def ping(self):
            return True

        def task_stats(self):
            return {"queued": 1}

        def list_tasks(self, **kwargs):
            return []

        def execution_nodes(self):
            return {"nodes": []}

        def sync_changes(self, cursor, limit):
            return {"server_revision": 12}

        def disk_usage(self):
            return {"total_size": 1}

    def connect():
        ctx = get_cli_context()
        calls.append((ctx.server_url, ctx.api_token))
        return Api()

    def daemon_status(server, token):
        calls.append((server, token))
        return {"online": True}

    monkeypatch.setattr(common, "client", connect)
    monkeypatch.setattr(operations, "client", connect)
    monkeypatch.setattr(operations, "daemon_status", daemon_status)
    result = CliRunner().invoke(
        app, ["--json", *global_opts, *command],
        env={"MPP_SERVER_URL": None, "MPP_API_TOKEN": None},
    )
    assert result.exit_code == 0, result.output
    expected = remote if server_kind == "remote" else local
    assert calls == [expected]
    assert json.loads(result.stdout)["meta"]["server"] == expected[0]


def test_remote_config_commands_and_bare_config_routing(monkeypatch):
    from app.cli.commands import config_values
    from app.core import settings

    monkeypatch.setattr(settings, "_runtime_settings", settings.RuntimeSettings(
        remote_server_url="https://coordinator.example",
        remote_api_token="remote-secret",
        cli_use_remote_server=True,
    ))
    calls = []

    class Api:
        def ping(self):
            return True

        def get_settings(self):
            return {"vlm_max_tokens": 4096}

        def patch_settings(self, payload):
            assert payload == {"vlm_max_tokens": 1024}
            return payload

    def connect():
        ctx = get_cli_context()
        calls.append((ctx.server_url, ctx.api_token, ctx.remote_preference))
        return Api()

    monkeypatch.setattr(config_values, "_get_client", connect)
    runner = CliRunner()
    env = {"MPP_SERVER_URL": None, "MPP_API_TOKEN": None}
    for command in (["list"], ["get", "vlm_max_tokens"], ["set", "vlm_max_tokens", "1024"]):
        result = runner.invoke(app, ["--remote", "--json", "config", *command], env=env)
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["meta"]["server"] == "https://coordinator.example"
    result = runner.invoke(app, ["config"], env=env)
    assert result.exit_code == 0, result.output
    assert calls == [
        *[("https://coordinator.example", "remote-secret", True)] * 3,
        ("http://localhost:18000", "", None),
    ]


def test_unreachable_remote_config_does_not_write_local_settings(monkeypatch):
    from app.cli.commands import config_values
    from app.core import settings

    original = settings.RuntimeSettings(remote_server_url="https://coordinator.example")
    monkeypatch.setattr(settings, "_runtime_settings", original)
    writes = []

    class Api:
        def ping(self):
            return False

        def patch_settings(self, payload):
            raise MppClientError("connection_failed", "offline", retryable=True, exit_code=3)

    monkeypatch.setattr(config_values, "_get_client", Api)
    monkeypatch.setattr(settings, "patch_runtime_settings", writes.append)
    result = CliRunner().invoke(
        app, ["--remote", "--json", "config", "set", "vlm_max_tokens", "1024"],
        env={"MPP_SERVER_URL": None, "MPP_API_TOKEN": None},
    )
    assert result.exit_code == 3, result.output
    assert json.loads(result.stdout)["error"]["code"] == "connection_failed"
    assert writes == []


@pytest.mark.parametrize(
    "command,global_opts,options,stay_local,base_url,target,uploaded",
    [
        ("submit", [], [], {}, "http://localhost:18000", "local", False),
        ("run", [], ["--detach"], {}, "http://localhost:18000", "local", False),
        ("submit", [], ["--remote"], {}, "https://coordinator.example", "exe-silentfield", True),
        ("submit", [], ["-W", "silentfield"], {},
         "https://coordinator.example", "exe-silentfield", True),
        ("submit", [], [], {"cli_local_files_stay_local": False},
         "https://coordinator.example", "exe-silentfield", True),
        ("submit", [], ["--upload", "always"], {},
         "https://coordinator.example", "exe-silentfield", True),
        ("submit", ["--remote"], [], {}, "https://coordinator.example", "exe-silentfield", True),
        ("submit", ["--local"], ["--remote"], {},
         "https://coordinator.example", "exe-silentfield", True),
        ("submit", ["--server", "https://explicit.example"], [], {},
         "https://explicit.example", "exe-silentfield", True),
    ],
)
def test_local_submission_policy(
    monkeypatch, tmp_path, command, global_opts, options, stay_local, base_url, target, uploaded,
):
    from app.core import settings

    source = tmp_path / "素材🎧.wav"
    source.write_bytes(b"fixture")
    monkeypatch.setattr(settings, "_runtime_settings", settings.RuntimeSettings(
        remote_server_url="https://coordinator.example",
        remote_api_token="remote-secret",
        api_token="local-secret",
        cli_use_remote_server=True,
        cli_default_worker_id="exe-silentfield",
        **stay_local,
    ))
    connections, uploads, batches = [], [], []

    class Api:
        def capabilities(self):
            return {"local_path_submission": False}

        def execution_nodes(self):
            return {"nodes": [{"node_id": "exe-silentfield", "display_name": "SilentField"}]}

        def stage_file(self, path):
            uploads.append(path)
            return {"path": "/remote/staged.wav"}

        def create_tasks_batch(self, sources, options, webhook_url, target_worker_id):
            batches.append((sources, target_worker_id))
            return [{"id": "fixture", "status": "queued", "source": sources[0]}]

    def connect(**kwargs):
        ctx = get_cli_context()
        connections.append((ctx.server_url, ctx.api_token))
        return Api()

    monkeypatch.setattr(execution, "_require_daemon", connect)
    result = CliRunner().invoke(
        app, ["--json", *global_opts, command, str(tmp_path / "*.wav"), *options],
        env={"MPP_SERVER_URL": None, "MPP_API_TOKEN": None},
    )
    assert result.exit_code == 0, result.output
    tokens = {
        "http://localhost:18000": "local-secret",
        "https://coordinator.example": "remote-secret",
    }
    assert connections == [(base_url, tokens.get(base_url, ""))]
    assert uploads == ([source.resolve()] if uploaded else [])
    assert batches == [(["/remote/staged.wav" if uploaded else str(source.resolve())], target)]


@pytest.mark.parametrize("command,options,remote_failure", [
    ("submit", [], False),
    ("submit", ["--wait"], False),
    ("run", ["--detach"], False),
    ("run", [], False),
    ("submit", [], True),
])
def test_mixed_batch_routes_and_waits_in_source_order(
    monkeypatch, tmp_path, command, options, remote_failure,
):
    from app.core import settings

    source = tmp_path / "素材🎧.wav"
    source.write_bytes(b"fixture")
    source_list = tmp_path / "sources.txt"
    sources = ["https://example.com/video?list[]=1", str(source.resolve()), "https://example.com/2"]
    source_list.write_text("\n".join(sources), encoding="utf-8")
    local = "http://localhost:18000"
    remote = "https://coordinator.example"
    monkeypatch.setattr(settings, "_runtime_settings", settings.RuntimeSettings(
        remote_server_url=remote,
        remote_api_token="remote-secret",
        cli_use_remote_server=True,
        cli_default_worker_id="exe-silentfield",
    ))
    calls, waits = [], []

    class Api:
        def __init__(self):
            self.url = get_cli_context().server_url
            self.tasks = {}

        def capabilities(self):
            return {}

        def execution_nodes(self):
            return {"nodes": []}

        def create_tasks_batch(self, sources, options, webhook_url, target_worker_id):
            ctx = get_cli_context()
            assert ctx.server_url == self.url
            calls.append((self.url, ctx.api_token, sources, target_worker_id))
            if self.url == remote and remote_failure:
                raise MppClientError("invalid_request", "Server rejected URL", exit_code=2)
            self.tasks = {
                item: {"id": item, "source": item, "status": "queued"} for item in sources
            }
            return list(self.tasks.values())

        def get_task(self, task_id):
            waits.append((self.url, task_id))
            return {**self.tasks[task_id], "status": "completed"}

    monkeypatch.setattr(execution, "_require_daemon", lambda **kwargs: Api())
    result = CliRunner().invoke(
        app, ["--json", command, "--from-file", str(source_list), *options],
        env={"MPP_SERVER_URL": None, "MPP_API_TOKEN": None},
    )
    assert calls == [
        (local, "", [sources[1]], "local"),
        (remote, "remote-secret", [sources[0], sources[2]], "exe-silentfield"),
    ]
    payload = json.loads(result.stdout)
    if remote_failure:
        assert result.exit_code == 2, result.output
        assert payload["error"]["code"] == "invalid_request"
        assert payload["error"]["detail"]["local_tasks"] == [
            {"id": sources[1], "source": sources[1]},
        ]
        return
    assert result.exit_code == 0, result.output
    assert [task["source"] for task in payload["data"]] == sources
    assert payload["meta"]["server"] == remote
    waiting = options == ["--wait"] or (command == "run" and not options)
    assert waits == ([(remote, sources[0]), (local, sources[1]), (remote, sources[2])]
                     if waiting else [])
