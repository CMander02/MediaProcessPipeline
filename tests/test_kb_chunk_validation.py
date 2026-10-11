from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api.routes import settings as settings_route
from app.core import settings
from app.services.kb.indexer import _sliding_window


@pytest.mark.parametrize("size,overlap", [(0, 0), (-1, 0), (10, -1), (10, 10), (10, 20)])
def test_invalid_windows_fail_before_indexing(size, overlap):
    with pytest.raises(ValidationError):
        settings.RuntimeSettings(kb_chunk_size_chars=size, kb_chunk_overlap_chars=overlap)
    # Also protect direct callers and old in-memory settings from unbounded loops.
    with pytest.raises(ValueError):
        _sliding_window("a" * 100, size, overlap)


@pytest.mark.parametrize("size,overlap,expected", [
    (4, 0, ["abcd", "efgh", "ij"]),
    (4, 1, ["abcd", "defg", "ghij"]),
    (4, 3, ["abcd", "bcde", "cdef", "defg", "efgh", "fghi", "ghij"]),
])
def test_valid_windows_progress_and_keep_the_tail(size, overlap, expected):
    assert _sliding_window("abcdefghij", size, overlap) == expected


def test_settings_patch_rejects_invalid_window_without_saving(tmp_path, monkeypatch):
    rt = settings.RuntimeSettings(data_root=str(tmp_path))
    monkeypatch.setattr(settings, "_runtime_settings", rt)
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(settings, "SETTINGS_FILE", config_path)
    settings._save_settings_to_file(rt)
    before = config_path.read_bytes()
    app = FastAPI()
    app.include_router(settings_route.router, prefix="/api")
    with TestClient(app) as client:
        response = client.patch("/api/settings", json={"kb_chunk_overlap_chars": 400})
        assert response.status_code == 422
        assert "kb_chunk_overlap_chars" in response.text
        assert settings.get_runtime_settings() is rt
        assert config_path.read_bytes() == before
