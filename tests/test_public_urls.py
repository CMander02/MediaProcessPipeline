from __future__ import annotations

import select
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import public_urls


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http:///missing",
        "http://localhost./canary",
        "http://sub.localhost/canary",
        "http://127.0.0.1/",
        "http://127.0.0.1./",
        "http://127.1/",
        "http://2130706433/",
        "http://0177.0.0.1/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fe80::1%25eth0]/",
        "http://10.0.0.1/",
        "http://169.254.169.254/",
        "http://100.64.0.1/",
        "http://224.0.0.1/",
        "http://[64:ff9b::7f00:1]/",
        "http://user:pass@example.com/",
        "http://example.com:0/",
        "http://example.com:65536/",
        "http://example.com\\@127.0.0.1/",
        "http://example.com\n/",
    ],
)
def test_reject_nonpublic_or_ambiguous_urls(url):
    with pytest.raises(public_urls.UnsafeURLError):
        public_urls.validate_public_http_url(url)


def test_reject_domain_with_any_private_dns_answer(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 80, 0, 0)),
        ],
    )
    with pytest.raises(public_urls.UnsafeURLError):
        public_urls.validate_public_http_url("https://media.example/")


def test_dns_failures_are_closed(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror("unresolvable")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(public_urls.UnsafeURLError):
        public_urls.validate_public_http_url("https://missing.example/")


@pytest.fixture
def http_server(monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(
                (self.path, self.headers.get("Host"), self.headers.get("Authorization"))
            )
            if self.path == "/private-redirect":
                self.send_response(302)
                self.send_header("Location", f"http://localhost.:{self.server.server_port}/canary")
                self.end_headers()
            elif self.path == "/public-redirect":
                self.send_response(302)
                self.send_header("Location", "http://other.example/content")
                self.end_headers()
            elif self.path == "/cookie-redirect":
                self.send_response(302)
                self.send_header("Location", "/cookie-next")
                self.send_header(
                    "Set-Cookie", "session=verified; Path=/; HttpOnly; SameSite=Strict"
                )
                self.end_headers()
            elif self.path == "/cookie-next":
                cookies = self.headers.get("Cookie", "")
                if "initial=present" not in cookies or "session=verified" not in cookies:
                    self.send_response(403)
                    self.end_headers()
                    return
                self.send_response(302)
                self.send_header("Location", "http://other.example/dir/article")
                self.send_header("Set-Cookie", "initial=; Path=/; Max-Age=0")
                self.end_headers()
            elif self.path == "/dir/article":
                if self.headers.get("Cookie"):
                    self.send_response(403)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Set-Cookie", "article=final; Path=/; HttpOnly")
                self.end_headers()
                self.wfile.write(b'<html><body><a href="next">Final article</a></body></html>')
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"PUBLIC_CONTENT")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("localhost", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    real_resolve = socket.getaddrinfo
    real_connect = socket.create_connection
    resolutions = []
    connections = []

    def resolve(host, port, *args, **kwargs):
        if host in {"media.example", "other.example"}:
            resolutions.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]
        return real_resolve(host, port, *args, **kwargs)

    def connect(address, *args, **kwargs):
        connections.append(address)
        assert address[0] == "8.8.8.8", "Unvalidated hostname reached the socket"
        return real_connect(("localhost", server.server_port), *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "create_connection", connect)
    monkeypatch.setattr("app.core.network.runtime_proxy_url", lambda: "")
    yield received, resolutions, connections
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_redirect_private_target_never_connects(http_server):
    received, _, connections = http_server
    with pytest.raises(public_urls.UnsafeURLError):
        public_urls.public_urlopen("http://media.example/private-redirect", timeout=2)
    assert [item[0] for item in received] == ["/private-redirect"]
    assert len(connections) == 1


def test_public_redirect_streaming_preserves_host_and_strips_auth(http_server):
    received, _, connections = http_server
    with public_urls.public_urlopen(
        urllib.request.Request(
            "http://media.example/public-redirect",
            headers={"Authorization": "Bearer demo"},
        ),
        timeout=2,
    ) as response:
        assert response.read(6) == b"PUBLIC"
        assert response.read() == b"_CONTENT"
        assert response.geturl() == "http://other.example/content"
        assert response.status == 200
    assert received == [
        ("/public-redirect", "media.example", "Bearer demo"),
        ("/content", "other.example", None),
    ]
    assert len(connections) == 2


def test_dns_is_pinned_and_rebinding_is_rejected_on_next_request(http_server, monkeypatch):
    received, resolutions, connections = http_server
    with httpx.Client(transport=public_urls.PublicHTTPTransport(), trust_env=False) as client:
        assert client.get("http://media.example/content").text == "PUBLIC_CONTENT"
        assert resolutions == ["media.example"]
        assert connections == [("8.8.8.8", 80)]
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
            ],
        )
        with pytest.raises(public_urls.UnsafeURLError):
            client.get("http://media.example/canary")
    assert len(received) == 1


def test_redirect_disabled_returns_urllib_http_error(http_server):
    with pytest.raises(urllib.error.HTTPError) as caught:
        public_urls.public_urlopen("http://media.example/public-redirect", follow_redirects=False)
    try:
        assert caught.value.code == 302
        assert caught.value.headers["Location"] == "http://other.example/content"
    finally:
        caught.value.close()


def test_https_direct_transport_pins_ip_and_keeps_tls_name(monkeypatch):
    sent = []
    delegates = []

    class RecordingTransport(httpx.BaseTransport):
        def __init__(self, **kwargs):
            delegates.append(kwargs)

        def handle_request(self, request):
            sent.append(request)
            return httpx.Response(200, content=b"ok")

    monkeypatch.setattr(httpx, "HTTPTransport", RecordingTransport)
    monkeypatch.setattr(
        public_urls, "resolve_public_url", lambda url: ("media.example", 443, ("8.8.8.8",))
    )
    with httpx.Client(transport=public_urls.PublicHTTPTransport(), trust_env=False) as client:
        assert client.get("https://media.example/content").text == "ok"
    assert str(sent[0].url) == "https://8.8.8.8/content"
    assert sent[0].headers["Host"] == "media.example"
    assert sent[0].extensions["sni_hostname"] == "media.example"
    assert delegates[0]["proxy"] is None


def test_fake_ip_dns_uses_real_public_addresses(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.12", 443)),
        ],
    )
    resolved = []
    monkeypatch.setattr(
        public_urls, "_resolve_fake_ip", lambda host: resolved.append(host) or ("8.8.8.8",)
    )
    assert public_urls.resolve_public_url("https://www.bilibili.com/video/BV1XM411M7eD")[2] == (
        "8.8.8.8",
    )
    assert resolved == ["www.bilibili.com"]


@pytest.mark.parametrize("results", [("198.18.0.12", "127.0.0.1"), ("127.0.0.1",)])
def test_private_dns_never_uses_public_dns_fallback(monkeypatch, results):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in results
        ],
    )

    def unexpected(host):
        raise AssertionError("Private DNS must not use fallback")

    monkeypatch.setattr(public_urls, "_resolve_fake_ip", unexpected)
    with pytest.raises(public_urls.UnsafeURLError):
        public_urls.validate_public_http_url("https://private.example/")
    with pytest.raises(public_urls.UnsafeURLError):
        public_urls.validate_public_http_url("https://198.18.0.12/")


def test_public_dns_fallback_still_rejects_private_answers(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.12", 443)),
        ],
    )
    monkeypatch.setattr(public_urls, "_resolve_fake_ip", lambda host: ("8.8.8.8", "10.1.2.3"))
    with pytest.raises(public_urls.UnsafeURLError):
        public_urls.validate_public_http_url("https://rebind.example/")


def test_ytdlp_uses_safe_handler_and_blocks_private_redirect(http_server):
    from app.services.ingestion.public_ytdlp import public_youtube_dl
    from yt_dlp.networking.exceptions import RequestError

    received, _, _ = http_server
    with public_youtube_dl({"quiet": True, "proxy": ""}) as ydl:
        with ydl.urlopen("http://media.example/content") as response:
            assert response.read() == b"PUBLIC_CONTENT"
        with pytest.raises(RequestError, match="public address"):
            ydl.urlopen("http://media.example/private-redirect")
        assert set(ydl._request_director.handlers) == {"Public"}
    assert [item[0] for item in received] == ["/content", "/private-redirect"]


def test_ytdlp_refuses_external_network_downloader(tmp_path):
    from app.services.ingestion.public_ytdlp import public_youtube_dl

    with public_youtube_dl({"quiet": True, "proxy": ""}) as ydl:
        with pytest.raises(public_urls.UnsafeURLError, match="external downloader"):
            ydl.dl(
                str(tmp_path / "output.mp4"),
                {
                    "url": "rtmp://media.example/stream",
                    "protocol": "rtmp",
                    "ext": "mp4",
                },
            )


@pytest.mark.parametrize("use_proxy", [False, True])
def test_https_uses_original_certificate_name_with_pinned_ip(tmp_path, monkeypatch, use_proxy):
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("openssl is required to create a temporary TLS test certificate")
    cert, key = tmp_path / "server.pem", tmp_path / "key.pem"
    config = tmp_path / "openssl.cnf"
    config.write_text("[req]\ndistinguished_name=dn\n[dn]\n", encoding="utf-8")
    subprocess.run(
        [
            openssl,
            "req",
            "-config",
            str(config),
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=media.example",
            "-addext",
            "subjectAltName=DNS:media.example",
        ],
        capture_output=True,
        check=True,
    )
    names, hosts, tunnels = [], [], []
    server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ssl.load_cert_chain(cert, key)
    server_ssl.set_servername_callback(lambda _socket, name, _context: names.append(name))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hosts.append(self.headers.get("Host"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"VERIFIED_TLS")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("localhost", 0), Handler)
    server.socket = server_ssl.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    real_connect, real_resolve = socket.create_connection, socket.getaddrinfo

    class Proxy(BaseHTTPRequestHandler):
        def do_CONNECT(self):
            tunnels.append(self.path)
            upstream = real_connect(("localhost", server.server_port), timeout=3)
            self.send_response(200)
            self.end_headers()
            try:
                while True:
                    readable, _, _ = select.select([self.connection, upstream], [], [], 3)
                    if not readable:
                        break
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        (upstream if source is self.connection else self.connection).sendall(data)
            finally:
                upstream.close()
                self.close_connection = True

        def log_message(self, *args):
            pass

    proxy = ThreadingHTTPServer(("localhost", 0), Proxy)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()

    def resolve(host, port, *args, **kwargs):
        if host == "media.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]
        return real_resolve(host, port, *args, **kwargs)

    def connect(address, *args, **kwargs):
        if address[0] == "8.8.8.8":
            address = ("localhost", server.server_port)
        return real_connect(address, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "create_connection", connect)
    trust = ssl.create_default_context(cafile=str(cert))
    try:
        with httpx.Client(
            transport=public_urls.PublicHTTPTransport(
                proxy=f"http://localhost:{proxy.server_port}" if use_proxy else None,
                verify=trust,
            ),
            trust_env=False,
        ) as client:
            assert client.get("https://media.example/content").content == b"VERIFIED_TLS"
        assert names == ["media.example"]
        assert hosts == ["media.example"]
        assert tunnels == (["8.8.8.8:443"] if use_proxy else [])
    finally:
        server.shutdown()
        proxy.shutdown()
        server.server_close()
        proxy.server_close()
        thread.join(timeout=3)
        proxy_thread.join(timeout=3)


def test_background_browser_uses_public_transport_for_navigation(http_server):
    playwright = pytest.importorskip("playwright.sync_api")
    from app.services.ingestion.public_browser import protect_browser_context

    received, _, _ = http_server
    with playwright.sync_playwright() as session:
        if not Path(session.chromium.executable_path).exists():
            pytest.skip("Playwright Chromium is not installed")
        browser = session.chromium.launch(headless=True)
        try:
            context = protect_browser_context(browser.new_context(service_workers="block"))
            page = context.new_page()
            page.goto("http://media.example/content")
            assert "PUBLIC_CONTENT" in page.content()
            with pytest.raises(playwright.Error, match="ERR_BLOCKED_BY_CLIENT"):
                page.goto("http://media.example/private-redirect")
            assert "/canary" not in [item[0] for item in received]
            context.close()
        finally:
            browser.close()


def test_background_browser_redirect_preserves_url_origin_and_cookies(http_server):
    playwright = pytest.importorskip("playwright.sync_api")
    from app.services.ingestion.public_browser import goto_public_page, protect_browser_context

    received, _, _ = http_server
    with playwright.sync_playwright() as session:
        if not Path(session.chromium.executable_path).exists():
            pytest.skip("Playwright Chromium is not installed")
        browser = session.chromium.launch(headless=True)
        try:
            context = protect_browser_context(browser.new_context(service_workers="block"))
            context.add_cookies(
                [{"name": "initial", "value": "present", "domain": "media.example", "path": "/"}]
            )
            page = context.new_page()
            goto_public_page(page, "http://media.example/cookie-redirect")
            assert page.url == "http://other.example/dir/article"
            assert page.evaluate("document.baseURI") == "http://other.example/dir/article"
            assert page.evaluate("location.origin") == "http://other.example"
            assert page.locator("a").evaluate("link => link.href") == "http://other.example/dir/next"
            cookies = {item["name"]: item for item in context.cookies()}
            assert "initial" not in cookies
            assert cookies["session"]["domain"] == "media.example"
            assert cookies["session"]["httpOnly"] is True
            assert cookies["session"]["sameSite"] == "Strict"
            assert cookies["article"]["domain"] == "other.example"
            assert cookies["article"]["httpOnly"] is True
            assert [item[0] for item in received] == [
                "/cookie-redirect", "/cookie-next", "/dir/article"
            ]
            context.close()
        finally:
            browser.close()


def test_background_browser_rejects_cross_origin_resource_redirect(http_server):
    playwright = pytest.importorskip("playwright.sync_api")
    from app.services.ingestion.public_browser import protect_browser_context

    with playwright.sync_playwright() as session:
        if not Path(session.chromium.executable_path).exists():
            pytest.skip("Playwright Chromium is not installed")
        browser = session.chromium.launch(headless=True)
        try:
            context = protect_browser_context(browser.new_context(service_workers="block"))
            page = context.new_page()
            page.goto("http://media.example/content")
            assert page.evaluate(
                "fetch('/public-redirect').then(() => 'loaded', () => 'blocked')"
            ) == "blocked"
            context.close()
        finally:
            browser.close()


def test_browser_api_resources_cannot_bypass_private_check(http_server):
    from app.services.ingestion.public_browser import fetch_browser_resource

    class Context:
        def cookies(self):
            return []

    received, _, _ = http_server
    assert (
        fetch_browser_resource(
            Context(),
            "http://media.example/content",
            headers={},
            timeout=2000,
        ).content
        == b"PUBLIC_CONTENT"
    )
    with pytest.raises(public_urls.UnsafeURLError):
        fetch_browser_resource(
            Context(), "http://media.example/private-redirect", headers={}, timeout=2000
        )
    assert "/canary" not in [item[0] for item in received]


@pytest.mark.parametrize("endpoint", ["task", "batch", "probe", "download"])
def test_api_rejects_dns_private_media_before_running_work(tmp_path, monkeypatch, endpoint):
    from app.api.routes import pipeline, tasks
    from app.core import settings
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        settings, "_runtime_settings", settings.RuntimeSettings(data_root=str(tmp_path))
    )
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
        ],
    )
    app = FastAPI()
    app.include_router(tasks.router, prefix="/api")
    app.include_router(pipeline.router, prefix="/api")
    with TestClient(app) as client:
        url = "http://private.example/canary"
        if endpoint == "task":
            response = client.post("/api/tasks", json={"task_type": "pipeline", "source": url})
        elif endpoint == "batch":
            response = client.post("/api/tasks/batch", json={"sources": [url]})
        elif endpoint == "probe":
            response = client.get("/api/pipeline/probe", params={"url": url})
        else:
            response = client.post("/api/pipeline/download", json={"url": url})
    assert response.status_code == 400
    assert not list(tmp_path.iterdir())


def test_thumbnail_fetch_cannot_follow_private_redirect(tmp_path, http_server):
    from app.api.routes.pipeline import _cache_remote_thumbnail

    received, _, _ = http_server
    assert _cache_remote_thumbnail("http://media.example/private-redirect", tmp_path) is None
    assert [item[0] for item in received] == ["/private-redirect"]
    assert not list(tmp_path.iterdir())


def test_defuddle_receives_downloaded_html_on_stdin(monkeypatch):
    from types import SimpleNamespace

    from app.services.ingestion.platform.webpage import api

    if not shutil.which("node") or not api._bundled_defuddle_cli():
        pytest.skip("Bundled Defuddle and node are required for local parsing")
    source = "<html><head><title>Safe article</title></head><body><article>"
    source += "<h1>Safe article</h1><p>" + "This paragraph contains useful article content. " * 30
    source += "</p></article></body></html>"
    monkeypatch.setattr(
        api,
        "get_runtime_settings",
        lambda: SimpleNamespace(
            defuddle_enabled=True,
            web_scrape_timeout_sec=10,
        ),
    )
    monkeypatch.setattr(
        api, "_get_text_with_retries", lambda *args, **kwargs: (source, "text/html")
    )
    run = subprocess.run
    calls = []

    def record(command, **kwargs):
        calls.append((command, kwargs.get("input")))
        return run(command, **kwargs)

    monkeypatch.setattr(api.subprocess, "run", record)
    assert "useful article content" in api._run_defuddle_markdown("https://media.example/article")
    assert calls[0][0][-3:] == ["parse", "-", "--md"]
    assert calls[0][1] == source
