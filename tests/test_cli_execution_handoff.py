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
