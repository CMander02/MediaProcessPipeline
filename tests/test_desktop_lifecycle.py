from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import desktop  # noqa: E402


def test_shutdown_command_and_parent_eof_request_graceful_shutdown():
    for command in (b"shutdown\n", b""):
        pipe, writer = os.pipe()
        try:
            if command:
                os.write(writer, command)
            os.close(writer)
            server = SimpleNamespace(should_exit=False)
            finished = threading.Event()
            finished.set()
            desktop.watch_parent(server, pipe, finished)
            assert server.should_exit
        finally:
            os.close(pipe)


def test_parent_disconnect_stops_stalled_native_workers(monkeypatch):
    exits = []
    monkeypatch.setattr(desktop.os, "_exit", exits.append)
    monkeypatch.setattr(desktop, "wait_for_parent", lambda _pipe: None)
    server = SimpleNamespace(should_exit=False)
    finished = SimpleNamespace(wait=lambda timeout: False)
    desktop.watch_parent(server, 0, finished)
    assert server.should_exit
    assert exits == [1]


def test_numpy_import_completes_while_parent_pipe_is_idle():
    code = """
import sys, threading
from app.desktop import wait_for_parent
threading.Thread(target=wait_for_parent, args=(sys.stdin.fileno(),), daemon=True).start()
import numpy
print('numpy ready', flush=True)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1] / "backend",
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    ready = threading.Event()
    output = []

    def read_output():
        output.append(process.stdout.readline().strip())
        ready.set()

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        assert ready.wait(10), "NumPy import blocked on the parent pipe"
        assert output == ["numpy ready"]
    finally:
        process.communicate("shutdown\n", timeout=10)
        reader.join(timeout=1)
    assert process.returncode == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object lifecycle")
def test_windows_job_reclaims_backend_children():
    code = """
import subprocess, sys
from app.cli.serve import _setup_win32_job_object
_setup_win32_job_object()
assert getattr(_setup_win32_job_object, '_handle', None)
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
print(child.pid, flush=True)
sys.stdin.readline()
"""
    parent = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1] / "backend",
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    child_pid = None
    try:
        child_pid = int(parent.stdout.readline())
        assert psutil.pid_exists(child_pid)
        parent.communicate("shutdown\n", timeout=10)
        deadline = time.monotonic() + 5
        while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert parent.returncode == 0
        assert not psutil.pid_exists(child_pid)
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.communicate(timeout=5)
        if child_pid and psutil.pid_exists(child_pid):
            psutil.Process(child_pid).kill()
