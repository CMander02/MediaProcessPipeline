from __future__ import annotations

import io
import shutil
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.api.routes import pipeline as pipeline_route  # noqa: E402
from app.core.settings import RuntimeSettings  # noqa: E402
from app.services.archiving import thumbnails  # noqa: E402


def _pipeline_client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(
        pipeline_route,
        "get_runtime_settings",
        lambda: RuntimeSettings(data_root=str(tmp_path)),
    )
    app = FastAPI()
    app.include_router(pipeline_route.router, prefix="/api")
    return TestClient(app)


def _archive_image(tmp_path: Path, *, size: tuple[int, int] = (1600, 900)) -> Path:
    Image = pytest.importorskip("PIL.Image")
    archive_dir = tmp_path / "archive"
    images_dir = archive_dir / "images"
    images_dir.mkdir(parents=True)
    (archive_dir / "metadata.json").write_text("{}", encoding="utf-8")
    source = images_dir / "source.png"
    Image.new("RGBA", size, color=(20, 120, 240, 128)).save(source, "PNG")
    return source


def test_image_thumbnail_uses_ffmpeg_when_pillow_cannot_decode(tmp_path, monkeypatch):
    calls: list[str] = []

    def fake_pillow(data, thumb_path, *, size, quality):
        calls.append("pillow")
        return False

    def fake_ffmpeg(data, thumb_path, *, size):
        calls.append("ffmpeg")
        thumb_path.write_bytes(b"jpeg-thumbnail")
        return True

    monkeypatch.setattr(thumbnails, "_create_pillow_thumbnail_from_bytes", fake_pillow)
    monkeypatch.setattr(thumbnails, "_create_ffmpeg_thumbnail_from_bytes", fake_ffmpeg)

    result = thumbnails.create_image_thumbnail_from_bytes(b"source-image", tmp_path)

    assert calls == ["pillow", "ffmpeg"]
    assert result == tmp_path / "thumbnail.jpg"
    assert result.read_bytes() == b"jpeg-thumbnail"


def test_ffmpeg_image_thumbnail_fallback_produces_jpeg(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is not installed")
    Image = pytest.importorskip("PIL.Image")
    source = io.BytesIO()
    Image.new("RGB", (1200, 800), color=(10, 80, 220)).save(source, "PNG")
    monkeypatch.setattr(
        thumbnails,
        "_create_pillow_thumbnail_from_bytes",
        lambda *_args, **_kwargs: False,
    )

    result = thumbnails.create_image_thumbnail_from_bytes(source.getvalue(), tmp_path)

    assert result == tmp_path / "thumbnail.jpg"
    with Image.open(result) as image:
        assert image.format == "JPEG"
        assert image.width <= 480
        assert image.height <= 480


@pytest.mark.asyncio
async def test_archive_thumbnail_offloads_image_conversion(tmp_path, monkeypatch):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    (archive_dir / "cover.jpg").write_bytes(b"source-image")
    settings = RuntimeSettings(data_root=str(tmp_path))
    offloaded: list[object] = []

    def fake_create_image_thumbnail(source, destination):
        thumb_path = destination / "thumbnail.jpg"
        thumb_path.write_bytes(b"jpeg-thumbnail")
        return thumb_path

    async def fake_to_thread(function, *args, **kwargs):
        offloaded.append(function)
        return function(*args, **kwargs)

    monkeypatch.setattr(pipeline_route, "get_runtime_settings", lambda: settings)
    monkeypatch.setattr(pipeline_route, "create_image_thumbnail", fake_create_image_thumbnail)
    monkeypatch.setattr(pipeline_route.asyncio, "to_thread", fake_to_thread)

    response = await pipeline_route.archive_thumbnail(str(archive_dir))

    assert isinstance(response, FileResponse)
    assert offloaded == [fake_create_image_thumbnail]
    assert Path(response.path).read_bytes() == b"jpeg-thumbnail"


def test_archive_image_endpoint_returns_bounded_jpeg_with_cache_headers(
    tmp_path,
    monkeypatch,
):
    Image = pytest.importorskip("PIL.Image")
    source = _archive_image(tmp_path)
    client = _pipeline_client(tmp_path, monkeypatch)

    response = client.get(
        "/api/pipeline/archives/image",
        params={"path": str(source), "max_edge": 512},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, max-age=86400"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["vary"] == "Authorization"
    assert response.headers["etag"].startswith('"')
    with Image.open(io.BytesIO(response.content)) as resized:
        assert resized.format == "JPEG"
        assert resized.width == 512
        assert resized.height == 288
        assert resized.mode == "RGB"

    cached = client.get(
        "/api/pipeline/archives/image",
        params={"path": str(source), "max_edge": 512},
        headers={"If-None-Match": response.headers["etag"]},
    )
    assert cached.status_code == 304
    assert cached.content == b""


def test_archive_image_endpoint_rejects_paths_outside_data_root(
    tmp_path,
    monkeypatch,
):
    Image = pytest.importorskip("PIL.Image")
    outside = tmp_path.parent / "outside-archive-image.png"
    Image.new("RGB", (32, 32), color="red").save(outside, "PNG")
    client = _pipeline_client(tmp_path, monkeypatch)

    response = client.get(
        "/api/pipeline/archives/image",
        params={"path": str(outside)},
    )

    assert response.status_code == 403


def test_archive_image_endpoint_requires_archive_marker(tmp_path, monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    folder = tmp_path / "unregistered"
    folder.mkdir()
    source = folder / "image.png"
    Image.new("RGB", (32, 32), color="red").save(source, "PNG")
    client = _pipeline_client(tmp_path, monkeypatch)

    response = client.get(
        "/api/pipeline/archives/image",
        params={"path": str(source)},
    )

    assert response.status_code == 403


def test_archive_image_endpoint_rejects_invalid_content_and_parameters(
    tmp_path,
    monkeypatch,
):
    archive_dir = tmp_path / "archive"
    images_dir = archive_dir / "images"
    images_dir.mkdir(parents=True)
    (archive_dir / "metadata.json").write_text("{}", encoding="utf-8")
    invalid = images_dir / "broken.png"
    invalid.write_bytes(b"not-an-image")
    client = _pipeline_client(tmp_path, monkeypatch)

    invalid_content = client.get(
        "/api/pipeline/archives/image",
        params={"path": str(invalid)},
    )
    invalid_edge = client.get(
        "/api/pipeline/archives/image",
        params={"path": str(invalid), "max_edge": 128},
    )
    invalid_quality = client.get(
        "/api/pipeline/archives/image",
        params={"path": str(invalid), "quality": 99},
    )

    assert invalid_content.status_code == 415
    assert invalid_edge.status_code == 422
    assert invalid_quality.status_code == 422


def test_archive_image_endpoint_enforces_source_size_limit(tmp_path, monkeypatch):
    source = _archive_image(tmp_path, size=(64, 64))
    client = _pipeline_client(tmp_path, monkeypatch)
    monkeypatch.setattr(thumbnails, "MAX_ARCHIVE_IMAGE_SOURCE_BYTES", 8)

    response = client.get(
        "/api/pipeline/archives/image",
        params={"path": str(source)},
    )

    assert response.status_code == 413
