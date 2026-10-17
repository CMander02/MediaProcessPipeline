from __future__ import annotations

import asyncio
import sys
import threading
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api.routes import filesystem
from app.core import settings
from app.core import workspace_lifecycle


def test_concurrent_get_and_head_share_conversion_without_blocking_health(tmp_path, monkeypatch):
    media = tmp_path / "interview.m4a"
    media.write_bytes(b"audio")
    monkeypatch.setattr(settings, "_runtime_settings", settings.RuntimeSettings(data_root=str(tmp_path)))
    entered, release = threading.Event(), threading.Event()
    calls = []

    def convert(path):
        calls.append(path)
        entered.set()
        assert release.wait(5), "event loop could not service requests during conversion"
        return path, "audio/mp4"

    monkeypatch.setattr(filesystem, "_ensure_browser_playable", convert)
    app = FastAPI()
    app.include_router(filesystem.router, prefix="/api")

    @app.get("/health")
    async def health():
        return {"ok": True}

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            request = asyncio.create_task(client.get("/api/filesystem/media", params={"path": str(media)}))
            head = None
            try:
                assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 2), 3)
                head = asyncio.create_task(client.head("/api/filesystem/media", params={"path": str(media)}))
                response = await asyncio.wait_for(client.get("/health"), 1)
                await asyncio.sleep(0.02)
                assert response.json() == {"ok": True}
                assert not request.done() and not head.done()
                assert calls == [media]
            finally:
                release.set()
                responses = await asyncio.gather(request, *([head] if head else []))
            assert responses[0].content == b"audio"
            assert responses[1].headers["content-length"] == "5"
            assert responses[1].content == b""
            assert filesystem._media_preparations == {}

    asyncio.run(scenario())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_local_playlist_cannot_fetch_private_http_during_browser_conversion(tmp_path):
    requests = []

    class Canary(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = HTTPServer(("localhost", 0), Canary)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        media = tmp_path / "malicious.m4a"
        media.write_text(
            "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\n"
            f"http://localhost:{server.server_port}/private.ts\n#EXT-X-ENDLIST\n",
            encoding="utf-8",
        )
        assert filesystem._ensure_browser_playable(media)[0] == media
        assert requests == []
        assert not media.with_suffix(".browser.mp3").exists()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_real_local_audio_still_transcodes_for_browser(tmp_path):
    media = tmp_path / "正常音频.m4a"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "sine=frequency=1000:duration=0.1", "-c:a", "aac", str(media)],
        check=True, capture_output=True, timeout=15,
    )
    playable, content_type = filesystem._ensure_browser_playable(media)
    assert playable == media.with_suffix(".browser.mp3")
    assert playable.stat().st_size > 0
    assert content_type == "audio/mpeg"


def test_disconnected_viewer_does_not_cancel_shared_conversion(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    path = tmp_path / "sample.ogg"
    calls = []

    def convert(media):
        calls.append(media)
        entered.set()
        assert release.wait(5)
        return media, "audio/ogg"

    monkeypatch.setattr(filesystem, "_ensure_browser_playable", convert)

    async def scenario():
        first = asyncio.create_task(filesystem._prepare_browser_media(path))
        second = None
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 2), 3)
            second = asyncio.create_task(filesystem._prepare_browser_media(path))
            await asyncio.sleep(0)
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            assert not second.done()
        finally:
            release.set()
        assert await second == (path, "audio/ogg")
        assert calls == [path]
        assert filesystem._media_preparations == {}

    asyncio.run(scenario())


def test_last_disconnected_viewer_keeps_conversion_in_workspace_drain(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    path = tmp_path / "sample.m4a"

    def convert(media):
        entered.set()
        assert release.wait(5)
        return media, "audio/mp4"

    monkeypatch.setattr(filesystem, "_ensure_browser_playable", convert)

    async def scenario():
        async def request():
            # The middleware's request activity ends when the viewer disconnects.
            with workspace_lifecycle.workspace_activity():
                return await filesystem._prepare_browser_media(path)

        viewer = asyncio.create_task(request())
        drain = None
        shared = None
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 2), 3)
            shared = filesystem._media_preparations[path]
            viewer.cancel()
            await asyncio.gather(viewer, return_exceptions=True)
            # No task-store access occurs: active work rejects the switch first.
            with pytest.raises(workspace_lifecycle.WorkspaceBusyError):
                with workspace_lifecycle.workspace_change():
                    pytest.fail("Workspace switched before conversion finished")
            drain = asyncio.create_task(workspace_lifecycle.drain_workspace_threads())
            await asyncio.sleep(0)
            assert not drain.done()
            assert not shared.done()
        finally:
            release.set()
            if shared is not None:
                await shared
            if drain is not None:
                await drain
        assert filesystem._media_preparations == {}
        assert not workspace_lifecycle._thread_workers

    asyncio.run(scenario())


def test_failed_shared_conversion_is_removed_and_can_be_retried(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    path = tmp_path / "sample.ogg"
    calls = []

    def convert(media):
        calls.append(media)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
            raise RuntimeError("conversion failed")
        return media, "audio/ogg"

    monkeypatch.setattr(filesystem, "_ensure_browser_playable", convert)

    async def scenario():
        first = asyncio.create_task(filesystem._prepare_browser_media(path))
        second = None
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 2), 3)
            second = asyncio.create_task(filesystem._prepare_browser_media(path))
            await asyncio.sleep(0)
        finally:
            release.set()
        outcomes = await asyncio.gather(first, second, return_exceptions=True)
        assert all(isinstance(error, RuntimeError) for error in outcomes)
        assert len(calls) == 1
        assert filesystem._media_preparations == {}
        assert await filesystem._prepare_browser_media(path) == (path, "audio/ogg")
        assert len(calls) == 2
        assert not workspace_lifecycle._thread_workers

    asyncio.run(scenario())
