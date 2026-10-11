from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.ingestion import ytdlp_version


@pytest.mark.parametrize("launch", [
    ["-X", "utf8", "-m", "app.cli", "--help"],
    ["-m", "uvicorn", "app.main:app", "--host", "localhost", "--port", "18000"],
    ["-m", "app.desktop", "--parent-pid", "123"],
])
def test_restart_keeps_original_interpreter_and_module_arguments(monkeypatch, launch):
    commands = []

    class ImmediateThread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(ytdlp_version.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(ytdlp_version.sys, "orig_argv", ["python", *launch])
    monkeypatch.setattr(ytdlp_version.sys, "argv", [str(ROOT / "backend/app/cli/__main__.py"), "--help"])
    monkeypatch.setattr(ytdlp_version.os, "execv", lambda exe, args: commands.append((exe, args)))
    ytdlp_version.schedule_process_restart(0)
    assert commands == [(sys.executable, [sys.executable, *launch])]
    if "app.cli" in launch:
        monkeypatch.undo()
        # Run the captured invocation, not a mocked import, from the source checkout.
        result = subprocess.run(commands[0][1], cwd=ROOT / "backend", capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
