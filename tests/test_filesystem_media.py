from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.api.routes.filesystem import router  # noqa: E402
from app.core import settings as settings_module  # noqa: E402
from app.core.settings import RuntimeSettings  # noqa: E402


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(
        settings_module,
        "_runtime_settings",
        RuntimeSettings(data_root=str(tmp_path)),
    )
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_media_endpoint_streams_full_file_inline(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    media = tmp_path / "sample.mp3"
    media.write_bytes(b"0123456789")

    response = client.get("/api/filesystem/media", params={"path": str(media)})

    assert response.status_code == 200
    assert response.content == b"0123456789"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-length"] == "10"
    assert response.headers["content-disposition"] == "inline"


def test_media_endpoint_supports_head_and_byte_ranges(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    media = tmp_path / "sample.mp3"
    media.write_bytes(b"0123456789")

    head = client.head(
        "/api/filesystem/media",
        params={"path": str(media)},
        headers={"Range": "bytes=2-5"},
    )
    response = client.get(
        "/api/filesystem/media",
        params={"path": str(media)},
        headers={"Range": "bytes=2-5"},
    )

    assert head.status_code == 206
    assert head.headers["content-range"] == "bytes 2-5/10"
    assert head.headers["content-length"] == "4"
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert response.headers["content-length"] == "4"


def test_media_endpoint_supports_suffix_ranges(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    media = tmp_path / "sample.mp3"
    media.write_bytes(b"0123456789")

    response = client.get(
        "/api/filesystem/media",
        params={"path": str(media)},
        headers={"Range": "bytes=-4"},
    )

    assert response.status_code == 206
    assert response.content == b"6789"
    assert response.headers["content-range"] == "bytes 6-9/10"


def test_media_endpoint_rejects_unsatisfiable_ranges(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    media = tmp_path / "sample.mp3"
    media.write_bytes(b"0123456789")

    response = client.get(
        "/api/filesystem/media",
        params={"path": str(media)},
        headers={"Range": "bytes=99-100"},
    )

    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */10"
