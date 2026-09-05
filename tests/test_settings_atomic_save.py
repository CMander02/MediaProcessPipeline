from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import atomic_file, settings


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    rt = settings.RuntimeSettings(data_root=str(tmp_path), max_download_concurrency=2)
    monkeypatch.setattr(settings, "SETTINGS_FILE", path)
    monkeypatch.setattr(settings, "_runtime_settings", rt)
    settings._save_settings_to_file(rt)
    return path, rt


@pytest.mark.parametrize("operation", ["fsync", "replace"])
@pytest.mark.parametrize("replace_all", [False, True])
def test_failure_keeps_memory_and_original_file(config, monkeypatch, operation, replace_all):
    path, rt = config
    before = path.read_bytes()

    def fail(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(atomic_file.os, operation, fail)
    with pytest.raises(OSError, match="disk failure"):
        if replace_all:
            settings.update_runtime_settings(rt.model_copy(update={"max_download_concurrency": 4}))
        else:
            settings.patch_runtime_settings({"max_download_concurrency": 4})
    assert settings.get_runtime_settings() is rt
    assert path.read_bytes() == before
    assert list(path.parent.glob("*.tmp")) == []


def test_concurrent_patches_preserve_each_others_fields_and_reload(config):
    path, rt = config
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(settings.patch_runtime_settings, update) for update in (
            {"max_download_concurrency": 4}, {"ytdlp_auto_update": True},
        )]
        for future in futures:
            future.result()
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["max_download_concurrency"] == 4
    assert persisted["ytdlp_auto_update"] is True
    reloaded = settings._load_settings_from_file()
    assert reloaded.max_download_concurrency == 4
    assert reloaded.ytdlp_auto_update is True
