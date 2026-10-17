from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.api.routes import filesystem as filesystem_route  # noqa: E402
from app.core import settings as settings_module  # noqa: E402
from app.core.settings import RuntimeSettings  # noqa: E402


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(
        settings_module,
        "_runtime_settings",
        RuntimeSettings(data_root=str(tmp_path), allow_remote_filesystem=True),
    )
    app = FastAPI()
    app.include_router(filesystem_route.router, prefix="/api")
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


def test_open_folder_endpoint_opens_data_root_child(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    opened: list[list[str]] = []

    monkeypatch.setattr(filesystem_route.subprocess, "Popen", lambda args: opened.append(list(args)))

    response = client.post("/api/filesystem/open-folder", json={"path": str(archive_dir)})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert opened


def test_open_folder_endpoint_rejects_outside_data_root(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    outside = tmp_path.parent / "outside-folder"
    outside.mkdir(exist_ok=True)

    response = client.post("/api/filesystem/open-folder", json={"path": str(outside)})

    assert response.status_code == 403


def test_browser_transcode_publishes_only_complete_cache(tmp_path, monkeypatch):
    media = tmp_path / 'recording.m4a'
    media.write_bytes(b'source')
    cache = media.with_suffix('.browser.mp3')
    def run(args, **kwargs):
        assert not cache.exists()
        Path(args[-1]).write_bytes(b'complete')
    monkeypatch.setattr(filesystem_route.subprocess, 'run', run)
    result, _ = filesystem_route._ensure_browser_playable(media)
    assert result == cache and cache.read_bytes() == b'complete'
    assert not list(tmp_path.glob('.browser-*.tmp'))


def test_failed_browser_transcode_cannot_poison_cache(tmp_path, monkeypatch):
    media = tmp_path / 'recording.m4a'
    media.write_bytes(b'source')
    def run(args, **kwargs):
        Path(args[-1]).write_bytes(b'partial')
        raise filesystem_route.subprocess.TimeoutExpired(args, 300)
    monkeypatch.setattr(filesystem_route.subprocess, 'run', run)
    result, _ = filesystem_route._ensure_browser_playable(media)
    assert result == media
    assert not media.with_suffix('.browser.mp3').exists()
    assert not list(tmp_path.glob('.browser-*.tmp'))


def test_old_truncated_browser_cache_is_regenerated(tmp_path, monkeypatch):
    from app.services.recognition.chunking import ASRChunker

    media = tmp_path / 'recording.m4a'
    media.write_bytes(b'source')
    cache = media.with_suffix('.browser.mp3')
    cache.write_bytes(b'old-incomplete-audio')
    monkeypatch.setattr(ASRChunker, 'probe_duration',
                        lambda self, path: 5448.48 if str(path).endswith('.m4a') else 4860.68)
    def run(args, **kwargs):
        Path(args[-1]).write_bytes(b'full-audio')
    monkeypatch.setattr(filesystem_route.subprocess, 'run', run)
    result, _ = filesystem_route._ensure_browser_playable(media)
    assert result == cache
    assert cache.read_bytes() == b'full-audio'


def test_matching_browser_cache_is_probed_once_and_reused(tmp_path, monkeypatch):
    from app.services.recognition.chunking import ASRChunker

    media = tmp_path / 'recording.m4a'
    media.write_bytes(b'source')
    cache = media.with_suffix('.browser.mp3')
    cache.write_bytes(b'complete')
    probes = []
    def probe(self, path):
        probes.append(path)
        return 90.01 if str(path).endswith('.mp3') else 90.0
    monkeypatch.setattr(ASRChunker, 'probe_duration', probe)
    for _ in range(3):
        assert filesystem_route._ensure_browser_playable(media)[0] == cache
    assert len(probes) == 2
    assert cache.read_bytes() == b'complete'


def test_resume_ignores_browser_playback_cache(tmp_path):
    from app.core.pipeline_steps.context import PipelineContext
    cache = tmp_path / 'a.browser.mp3'
    cache.write_bytes(b'partial')
    ctx = PipelineContext(task=None, rt=None, task_dir=tmp_path)
    assert not ctx.restore_audio_paths()
    original = tmp_path / 'a.m4a'
    original.write_bytes(b'source')
    assert ctx.restore_audio_paths()
    assert ctx.audio_path == str(original)


def test_editor_write_rejects_stale_baseline_without_losing_new_content(tmp_path, monkeypatch):
    from app.core.artifacts import ArtifactStore
    client = _client(tmp_path, monkeypatch)
    path = tmp_path / "transcript_polished.srt"
    path.write_text("newer edit", encoding="utf-8")
    monkeypatch.setattr(filesystem_route, "_artifact_owner", lambda *args: (None, tmp_path, path.name))
    monkeypatch.setattr(filesystem_route, "get_artifact_store", lambda: ArtifactStore())
    response = client.post("/api/filesystem/write", json={
        "path": str(path), "content": "my draft", "expected_content": "old edit",
    })
    assert response.status_code == 409
    assert path.read_text(encoding="utf-8") == "newer edit"
