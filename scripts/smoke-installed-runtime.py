#!/usr/bin/env python3
"""Exercise a staged desktop runtime with an isolated writable user tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_snapshot(root: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(root).as_posix(): (path.stat().st_size, _sha256(path))
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("localhost", 0))
        return int(listener.getsockname()[1])


def _request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, bytes]:
    data = None
    request_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers.update(
            {
                "Content-Type": "application/json",
                "X-Requested-With": "MediaProcessPipeline",
            }
        )
    request = urllib.request.Request(
        url,
        data=data,
        headers=request_headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def _wait_for_health(
    base_url: str,
    process: subprocess.Popen[bytes],
    *,
    session_token: str,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error = "backend did not answer"
    while time.monotonic() < deadline:
        returncode = process.poll()
        if returncode is not None:
            raise RuntimeError(f"backend exited before health was ready ({returncode})")
        try:
            status, body = _request(
                f"{base_url}/health",
                headers={"X-MPP-Desktop-Session": session_token},
                timeout=1.0,
            )
            if status == 200:
                payload = json.loads(body)
                if isinstance(payload, dict):
                    return payload
                last_error = "health response was not an object"
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(0.2)
    raise TimeoutError(f"backend health timed out after {timeout:.1f}s: {last_error}")


def _runtime_environment(runtime_root: Path, user_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    for key in list(environment):
        upper = key.upper()
        if (
            upper.startswith("MPP_")
            or upper.startswith("UV_")
            or upper in {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}
        ):
            environment.pop(key, None)
    cache_dir = user_root / "cache"
    environment.update(
        {
            "MPP_PROJECT_ROOT": str(runtime_root),
            "MPP_CONFIG_FILE": str(user_root / "config" / "config.json"),
            "MPP_LOG_DIR": str(user_root / "logs"),
            "MPP_CACHE_DIR": str(cache_dir),
            "MPP_WEB_DIST_DIR": str(runtime_root / "web" / "dist"),
            "MPP_DATA_ROOT": str(user_root / "data"),
            "UV_PROJECT_ENVIRONMENT": str(user_root / "runtime" / ".venv"),
            "UV_CACHE_DIR": str(cache_dir / "uv"),
            "UV_PYTHON_INSTALL_DIR": str(user_root / "runtime" / "python"),
            "UV_MANAGED_PYTHON": "1",
            "HF_HOME": str(cache_dir / "huggingface"),
            "TORCH_HOME": str(cache_dir / "torch"),
            "PLAYWRIGHT_BROWSERS_PATH": str(cache_dir / "ms-playwright"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
            "MPP_SKIP_VERSION_CHECK": "1",
            "MPP_PLAIN_OUTPUT": "1",
            "NO_COLOR": "1",
            "NO_PROXY": "localhost,127.0.0.1",
            "no_proxy": "localhost,127.0.0.1",
        }
    )
    return environment


def _bundled_uv_path(runtime_root: Path) -> Path:
    manifest = json.loads(
        (runtime_root / "runtime-manifest.json").read_text(encoding="utf-8")
    )
    uv_path = str((manifest.get("uv") or {}).get("path") or "")
    if uv_path not in {"bin/uv", "bin/uv.exe"}:
        raise RuntimeError(f"runtime manifest has an invalid uv path: {uv_path!r}")
    bundled_uv = runtime_root / uv_path
    if not bundled_uv.is_file():
        raise RuntimeError(f"bundled uv is missing: {bundled_uv}")
    return bundled_uv


def _venv_python(user_root: Path) -> Path:
    if os.name == "nt":
        return user_root / "runtime" / ".venv" / "Scripts" / "python.exe"
    return user_root / "runtime" / ".venv" / "bin" / "python"


def _log_tail(path: Path, *, limit: int = 4000) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return content[-limit:]


def smoke_runtime(
    runtime_root: Path,
    *,
    python_executable: Path,
    user_root: Path,
    timeout: float,
    host_python: bool = False,
) -> dict[str, Any]:
    runtime_root = runtime_root.resolve()
    user_root = user_root.resolve()
    version = (runtime_root / "VERSION").read_text(encoding="utf-8").strip()
    if not version:
        raise RuntimeError("staged VERSION is empty")
    before = _tree_snapshot(runtime_root)

    for relative in ("config", "runtime", "cache", "logs", "updates", "state", "data"):
        (user_root / relative).mkdir(parents=True, exist_ok=True)
    config_file = user_root / "config" / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "data_root": str(user_root / "data"),
                "kb_enabled": False,
                "playwright_enabled": False,
                "remote_sync_enabled": False,
                "ytdlp_auto_update": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    port = _available_port()
    base_url = f"http://localhost:{port}"
    output_file = user_root / "logs" / "installed-smoke-process.log"
    environment = _runtime_environment(runtime_root, user_root)
    environment["MPP_APP_VERSION"] = version
    session_token = secrets.token_urlsafe(32)
    environment["MPP_DESKTOP_SESSION_TOKEN"] = session_token
    selected_python = python_executable
    launcher = "host-python"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    if not host_python:
        launcher = "bundled-uv"
        bundled_uv = _bundled_uv_path(runtime_root)
        with output_file.open("wb") as output:
            sync = subprocess.run(
                [
                    str(bundled_uv),
                    "sync",
                    "--frozen",
                    "--project",
                    str(runtime_root),
                ],
                cwd=runtime_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=max(timeout, 300.0),
                check=False,
            )
            output.flush()
        if sync.returncode:
            raise RuntimeError(
                f"bundled uv failed to initialize the user environment ({sync.returncode}): "
                f"{_log_tail(output_file)}"
            )
        selected_python = _venv_python(user_root)
        if not selected_python.is_file():
            raise RuntimeError(
                f"bundled uv did not create the expected environment: {selected_python}"
            )

    command = [
        str(selected_python),
        "-u",
        "-m",
        "app.cli",
        "serve",
        "--host",
        "localhost",
        "--port",
        str(port),
    ]

    with output_file.open("ab") as output:
        process = subprocess.Popen(
            command,
            cwd=runtime_root / "backend",
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            health = _wait_for_health(
                base_url,
                process,
                session_token=session_token,
                timeout=timeout,
            )
            if health.get("version") != version:
                raise RuntimeError(
                    f"health version mismatch: {health.get('version')} != {version}"
                )
            if health.get("service") != "Media Process Pipeline":
                raise RuntimeError(f"unexpected health service: {health.get('service')}")
            if health.get("product") != "com.mpp.backend":
                raise RuntimeError(f"unexpected health product: {health.get('product')}")
            if health.get("protocol") != 1:
                raise RuntimeError(f"unexpected health protocol: {health.get('protocol')}")

            status, homepage = _request(f"{base_url}/")
            expected_homepage = (runtime_root / "web" / "dist" / "index.html").read_bytes()
            if status != 200 or homepage != expected_homepage:
                raise RuntimeError("staged Web index was not served byte-for-byte")

            status, response = _request(
                f"{base_url}/api/settings",
                method="PATCH",
                payload={"default_task_executor": "exe"},
            )
            patched = json.loads(response)
            if status != 200 or patched.get("default_task_executor") != "exe":
                raise RuntimeError("settings PATCH did not persist through the staged backend")
            persisted = json.loads(config_file.read_text(encoding="utf-8"))
            if persisted.get("default_task_executor") != "exe":
                raise RuntimeError("settings were not written to the isolated user config")

            database = user_root / "data" / "tasks.db"
            if not database.is_file():
                raise RuntimeError("tasks.db was not created in the isolated user data root")
            if not any((user_root / "logs").glob("mpp_*.log")):
                raise RuntimeError("backend log was not created in the isolated user log directory")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)

    after = _tree_snapshot(runtime_root)
    if after != before:
        added = sorted(set(after) - set(before))
        removed = sorted(set(before) - set(after))
        changed = sorted(
            path for path in set(before) & set(after) if before[path] != after[path]
        )
        raise RuntimeError(
            "read-only runtime changed during smoke: "
            f"added={added}, removed={removed}, changed={changed}"
        )

    return {
        "version": version,
        "python": str(selected_python),
        "launcher": launcher,
        "runtimeRoot": str(runtime_root),
        "userRoot": str(user_root),
        "health": health,
        "runtimeFiles": len(before),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=Path("web/src-tauri/resources/runtime"),
    )
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument(
        "--host-python",
        action="store_true",
        help="Skip bundled uv initialization and use --python for a fast layout-only smoke.",
    )
    parser.add_argument("--user-root", type=Path)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    temporary: tempfile.TemporaryDirectory[str] | None = None
    try:
        if args.user_root is None:
            temporary = tempfile.TemporaryDirectory(prefix="mpp-installed-smoke-")
            user_root = Path(temporary.name) / "用户 数据"
        else:
            user_root = args.user_root
        result = smoke_runtime(
            args.runtime_root,
            python_executable=args.python.resolve(),
            user_root=user_root,
            timeout=args.timeout,
            host_python=args.host_python,
        )
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(
                "[PASS] installed runtime smoke: "
                f"v{result['version']} ({result['runtimeFiles']} immutable files)"
            )
        return 0
    except Exception as exc:
        print(f"[FAIL] installed runtime smoke: {exc}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
