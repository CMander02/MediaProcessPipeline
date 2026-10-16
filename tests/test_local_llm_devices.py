"""Check local llama.cpp acceleration across supported desktop platforms."""
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.analysis import local_llm_runtime as module


@pytest.mark.parametrize(
    "system,machine,nvidia,device,layers,expected_layers",
    [
        ("darwin", "arm64", False, "auto", -1, "-1"),
        ("darwin", "arm64", False, "cpu", -1, "0"),
        ("darwin", "x86_64", False, "auto", -1, "0"),
        ("win32", "AMD64", True, "auto", 99, "99"),
        ("linux", "x86_64", False, "auto", 99, "0"),
    ],
)
def test_desktop_acceleration(
    monkeypatch, system, machine, nvidia, device, layers, expected_layers
):
    monkeypatch.setattr(module.sys, "platform", system)
    monkeypatch.setattr(module.platform, "machine", lambda: machine)
    monkeypatch.setattr(module.LocalLlamaCppRuntime, "_has_nvidia_gpu", lambda: nvidia)
    args = module.LocalLlamaCppRuntime._device_args({"device": device, "n_gpu_layers": layers})
    assert args[args.index("-ngl") + 1] == expected_layers


def test_local_model_health_bypasses_system_proxy(monkeypatch):
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *args):
            pass

    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(key, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    server = HTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        runtime = module.LocalLlamaCppRuntime()
        runtime._base_url = f"http://localhost:{server.server_port}"
        runtime._wait_until_ready(1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
