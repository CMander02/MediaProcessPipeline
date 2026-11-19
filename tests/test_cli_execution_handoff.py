"""Root command registration dispatches through the shared CLI context."""

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
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
