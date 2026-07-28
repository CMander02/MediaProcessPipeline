from __future__ import annotations

import errno
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.cli.serve import _bind_desktop_loopback_sockets  # noqa: E402


def _raw_health_request(family: socket.AddressFamily, address: tuple) -> bytes:
    with socket.socket(family, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect(address)
        client.sendall(
            b"GET /health HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Connection: close\r\n\r\n"
        )
        chunks: list[bytes] = []
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)


def test_desktop_loopback_reserves_ipv4_and_ipv6_on_one_port() -> None:
    listeners = _bind_desktop_loopback_sockets(0)
    try:
        assert [listener.family for listener in listeners] == [
            socket.AF_INET,
            socket.AF_INET6,
        ]
        ports = {int(listener.getsockname()[1]) for listener in listeners}
        assert len(ports) == 1
        port = ports.pop()

        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
        with socket.create_connection(("::1", port), timeout=1):
            pass

        with pytest.raises(OSError):
            _bind_desktop_loopback_sockets(port)
    finally:
        for listener in listeners:
            listener.close()


def test_desktop_loopback_supports_hosts_with_ipv6_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_socket = socket.socket

    def ipv4_only_socket(
        family: socket.AddressFamily = socket.AF_INET,
        type: socket.SocketKind = socket.SOCK_STREAM,
        proto: int = 0,
        fileno: int | None = None,
    ) -> socket.socket:
        if family == socket.AF_INET6:
            raise OSError(errno.EAFNOSUPPORT, "IPv6 is disabled")
        return original_socket(family, type, proto, fileno)

    monkeypatch.setattr(socket, "socket", ipv4_only_socket)
    listeners = _bind_desktop_loopback_sockets(0)
    try:
        assert len(listeners) == 1
        assert listeners[0].family == socket.AF_INET
    finally:
        for listener in listeners:
            listener.close()


def test_uvicorn_serves_the_same_health_app_on_both_loopback_families() -> None:
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    listeners = _bind_desktop_loopback_sockets(0)
    port = int(listeners[0].getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="localhost",
            port=port,
            lifespan="off",
            log_level="error",
        )
    )
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": listeners},
        daemon=True,
    )
    thread.start()
    try:
        deadline = time.monotonic() + 3
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started is True

        ipv4 = _raw_health_request(
            socket.AF_INET,
            ("127.0.0.1", port),
        )
        ipv6 = _raw_health_request(
            socket.AF_INET6,
            ("::1", port, 0, 0),
        )
        for response in (ipv4, ipv6):
            assert response.startswith(b"HTTP/1.1 200 OK\r\n")
            assert b'{"status":"healthy"}' in response
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        for listener in listeners:
            listener.close()
    assert thread.is_alive() is False
