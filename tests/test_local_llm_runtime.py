import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.analysis import local_llm_runtime as module


@pytest.fixture
def runtime(monkeypatch):
    instance = module.LocalLlamaCppRuntime()
    instance._base_url = "http://localhost:19010"
    instance._process = SimpleNamespace(poll=lambda: None)
    clock = [0.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(
        monotonic=lambda: clock[0],
        sleep=lambda delay: clock.__setitem__(0, clock[0] + delay),
    ))
    return instance


def health_client(monkeypatch, handler):
    client_type = httpx.Client
    clients = []

    def create(**kwargs):
        # A system proxy can otherwise intercept localhost requests on Windows.
        assert kwargs["trust_env"] is False
        client = client_type(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(module.httpx, "Client", create)
    return clients


def test_health_reuses_direct_client_until_ready(monkeypatch, runtime):
    responses = iter([503, 200])
    requests = []

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(next(responses), json={"status": "loading"})

    clients = health_client(monkeypatch, respond)
    runtime._wait_until_ready(5)
    assert requests == ["http://localhost:19010/health"] * 2
    assert len(clients) == 1
    assert clients[0].is_closed


@pytest.mark.parametrize("status", [301, 401, 403, 404, 503])
def test_health_requires_200_and_reports_response(monkeypatch, runtime, status):
    clients = health_client(
        monkeypatch, lambda request: httpx.Response(status, text="server loading"),
    )
    with pytest.raises(module.LocalLlamaCppStartupError, match=f"HTTP {status}: server loading"):
        runtime._wait_until_ready(1)
    assert clients[0].is_closed


def test_health_timeout_reports_empty_exception_type(monkeypatch, runtime):
    def timeout(request):
        raise httpx.ReadTimeout("", request=request)

    clients = health_client(monkeypatch, timeout)
    with pytest.raises(module.LocalLlamaCppStartupError, match="ReadTimeout"):
        runtime._wait_until_ready(1)
    assert clients[0].is_closed


@pytest.fixture
def startup(monkeypatch, tmp_path):
    config = {"device": "cpu", "parallel": 8, "ctx": 4096}
    for key in ("binary_path", "model_path"):
        path = tmp_path / key
        path.write_bytes(b"test")
        config[key] = str(path)
    logs = tmp_path / "日志 🎧"
    monkeypatch.setattr(module, "get_workspace_paths", lambda: SimpleNamespace(logs=logs))
    runtime = module.LocalLlamaCppRuntime()
    monkeypatch.setattr(runtime, "_free_port", lambda: 19010)
    return runtime, config, logs


class Process:
    def __init__(self):
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout):
        return self.returncode


def test_start_retains_native_log_and_closes_it_on_stop(monkeypatch, startup, caplog):
    runtime, config, logs = startup
    spawned = []
    process = Process()

    def spawn(args, **kwargs):
        spawned.append((args, kwargs))
        kwargs["stdout"].write("slot 1: 原生输出\n".encode())
        kwargs["stdout"].flush()
        return process

    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    monkeypatch.setattr(runtime, "_wait_until_ready", lambda timeout: None)
    with caplog.at_level(logging.INFO, logger=module.__name__):
        assert runtime.ensure(config) == "http://localhost:19010"
        assert runtime.ensure(config) == "http://localhost:19010"
        runtime.stop()
    assert len(spawned) == 1
    args, kwargs = spawned[0]
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert args[args.index("-np") + 1] == "8"
    assert args[args.index("-c") + 1] == str(4096 * 8)
    assert kwargs["stderr"] == module.subprocess.STDOUT
    assert kwargs["stdout"].closed
    assert process.terminated
    paths = list(logs.glob("llama_cpp_*.log"))
    assert len(paths) == 1
    assert paths[0].read_text(encoding="utf-8") == "slot 1: 原生输出\n"
    assert str(paths[0]).replace("\\", "\\\\") in caplog.text
    assert "原生输出" not in caplog.text
    assert runtime._process is None


def test_spawn_failure_closes_log_and_exposes_path(monkeypatch, startup, caplog):
    runtime, config, logs = startup
    handles = []

    def spawn(args, **kwargs):
        handles.append(kwargs["stdout"])
        raise OSError("process launch denied")

    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    with pytest.raises(
        module.LocalLlamaCppStartupError, match="OSError: process launch denied",
    ) as exc:
        runtime.ensure(config)
    assert handles[0].closed
    assert runtime._process is None
    assert str(next(logs.glob("llama_cpp_*.log"))) in str(exc.value)
    assert "llama_cpp.startup_failed" in caplog.text


def test_startup_health_failure_stops_process_and_closes_log(monkeypatch, startup):
    runtime, config, logs = startup
    process = Process()
    handles = []

    def spawn(args, **kwargs):
        handles.append(kwargs["stdout"])
        return process

    def fail(timeout):
        raise module.LocalLlamaCppStartupError("HTTP 503: loading")

    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    monkeypatch.setattr(runtime, "_wait_until_ready", fail)
    with pytest.raises(module.LocalLlamaCppStartupError, match="HTTP 503: loading"):
        runtime.ensure(config)
    assert handles[0].closed
    assert process.terminated
    assert runtime._base_url == ""
    assert runtime._signature is None


def test_process_exit_reports_code_and_closes_health_client(monkeypatch, runtime):
    runtime._process = SimpleNamespace(poll=lambda: 7)
    clients = health_client(monkeypatch, lambda request: pytest.fail("process already exited"))
    with pytest.raises(module.LocalLlamaCppStartupError, match="exit code 7"):
        runtime._wait_until_ready(5)
    assert clients[0].is_closed


@pytest.mark.parametrize("key", ["binary_path", "model_path", "mmproj_path"])
def test_missing_startup_files_use_startup_error(startup, key):
    runtime, config, _ = startup
    config[key] = "missing-file"
    with pytest.raises(module.LocalLlamaCppStartupError):
        runtime.ensure(config)
    assert runtime._process is None
