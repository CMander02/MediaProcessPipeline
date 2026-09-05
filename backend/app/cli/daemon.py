"""Persistent local daemon process management for CLI commands."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.cli.client import MppClient
from app.cli.context import DEFAULT_SERVER_URL, normalize_server_url


def _state_path() -> Path:
    from app.core.paths import get_workspace_paths
    paths = get_workspace_paths()
    paths.state.mkdir(parents=True, exist_ok=True)
    return paths.state / ".mpp-daemon.json"


def _read_state() -> dict[str, Any] | None:
    path = _state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_state(data: dict[str, Any]) -> None:
    path = _state_path()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _remove_state() -> None:
    try:
        _state_path().unlink(missing_ok=True)
    except OSError:
        pass


def _process_info(pid: int) -> dict[str, Any] | None:
    try:
        import psutil

        process = psutil.Process(pid)
        return {
            "pid": pid,
            "running": process.is_running() and process.status() != psutil.STATUS_ZOMBIE,
            "cmdline": process.cmdline(),
            "create_time": process.create_time(),
        }
    except Exception:
        try:
            os.kill(pid, 0)
            return {"pid": pid, "running": True, "cmdline": [], "create_time": None}
        except (OSError, ValueError):
            return None


def _managed_process_matches(state: dict[str, Any]) -> bool:
    try:
        pid = int(state["pid"])
    except (KeyError, TypeError, ValueError):
        return False
    info = _process_info(pid)
    if not info or not info.get("running"):
        return False
    cmdline = " ".join(str(part) for part in info.get("cmdline") or [])
    if not cmdline or not ("app.cli" in cmdline and "serve" in cmdline):
        return False
    expected_time = state.get("create_time")
    actual_time = info.get("create_time")
    if expected_time and actual_time and abs(float(expected_time) - float(actual_time)) > 2.0:
        return False
    return True


def daemon_status(server_url: str = DEFAULT_SERVER_URL, api_token: str = "") -> dict[str, Any]:
    url = normalize_server_url(server_url)
    state = _read_state()
    managed = bool(state and _managed_process_matches(state))
    client = MppClient(url, timeout=3.0, api_token=api_token)
    try:
        health = client.health()
        online = True
    except Exception:
        health = None
        online = False
    finally:
        client.close()
    if state and not managed and not online:
        _remove_state()
        state = None
    return {
        "online": online,
        "managed": managed,
        "pid": state.get("pid") if state else None,
        "server": url,
        "started_at": state.get("started_at") if state else None,
        "stdout_log": state.get("stdout_log") if state else None,
        "stderr_log": state.get("stderr_log") if state else None,
        "health": health,
    }


def start_daemon(
    server_url: str = DEFAULT_SERVER_URL,
    *,
    api_token: str = "",
    timeout: float = 20.0,
) -> dict[str, Any]:
    url = normalize_server_url(server_url)
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError(f"Automatic daemon start only supports localhost: {url}")
    existing = daemon_status(url, api_token)
    if existing["online"]:
        return existing

    host = "localhost"
    port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
    state_path = _state_path()
    from app.core.paths import get_workspace_paths
    log_dir = get_workspace_paths().logs
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "mpp-daemon-stdout.log"
    stderr_path = log_dir / "mpp-daemon-stderr.log"
    backend_dir = Path(__file__).resolve().parents[2]
    command = [
        sys.executable,
        "-m",
        "app.cli",
        "serve",
        "--host",
        host,
        "--port",
        str(port),
    ]
    creationflags = 0
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0
        )
    else:
        kwargs["start_new_session"] = True

    with stdout_path.open("ab") as stdout_handle, stderr_path.open("ab") as stderr_handle:
        process = subprocess.Popen(
            command,
            cwd=backend_dir,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=creationflags,
            **kwargs,
        )

    info = _process_info(process.pid) or {}
    state = {
        "pid": process.pid,
        "create_time": info.get("create_time"),
        "started_at": datetime.now().isoformat(),
        "server": url,
        "command": command,
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
    }
    _write_state(state)

    deadline = time.monotonic() + timeout
    client = MppClient(url, timeout=2.0, api_token=api_token)
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            if client.ping():
                return daemon_status(url, api_token)
            time.sleep(0.25)
    finally:
        client.close()

    try:
        process.terminate()
    except OSError:
        pass
    _remove_state()
    raise RuntimeError(f"Daemon failed to start within {timeout:.0f}s. See {stderr_path}")


def stop_daemon(
    server_url: str = DEFAULT_SERVER_URL, *, api_token: str = "", timeout: float = 10.0
) -> dict[str, Any]:
    state = _read_state()
    if not state or not _managed_process_matches(state):
        raise RuntimeError("No verified CLI-managed daemon process is running.")
    pid = int(state["pid"])
    try:
        import psutil

        parent = psutil.Process(pid)
        processes = parent.children(recursive=True)
        processes.append(parent)
        for process in reversed(processes):
            try:
                process.terminate()
            except psutil.Error:
                pass
        _, alive = psutil.wait_procs(processes, timeout=timeout)
        for process in alive:
            try:
                process.kill()
            except psutil.Error:
                pass
    except ImportError:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            os.kill(pid, signal.SIGTERM)

    _remove_state()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = daemon_status(server_url, api_token)
        if not status["online"]:
            return status
        time.sleep(0.25)
    return daemon_status(server_url, api_token)
