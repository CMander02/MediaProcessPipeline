"""Fresh installs use platform user storage; configured libraries stay explicit."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import user_paths
from app.core.config import Settings
from app.core.configuration.models import RuntimeSettings


@pytest.mark.parametrize(
    "platform,variable,value,expected",
    [
        ("darwin", None, None, "Library/Application Support/MediaProcessPipeline"),
        ("win32", None, None, "AppData/Local/MediaProcessPipeline"),
        ("win32", "LOCALAPPDATA", "custom-local", "custom-local/MediaProcessPipeline"),
        ("linux", None, None, ".local/share/MediaProcessPipeline"),
        ("linux", "XDG_DATA_HOME", "custom-data", "custom-data/MediaProcessPipeline"),
        ("linux", "XDG_DATA_HOME", "relative", ".local/share/MediaProcessPipeline"),
    ],
)
def test_platform_defaults(monkeypatch, tmp_path, platform, variable, value, expected):
    monkeypatch.setattr(user_paths.sys, "platform", platform)
    monkeypatch.setattr(user_paths.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    if variable:
        monkeypatch.setenv(variable, value if value == "relative" else str(tmp_path / value))
    target = tmp_path / expected
    assert user_paths.default_data_root() == target
    assert RuntimeSettings().data_root == str(target)
    assert Settings(_env_file=None).data_root == target
    assert not target.exists()


def test_explicit_library_location_is_preserved(tmp_path):
    target = tmp_path / "Existing Library 🎧"
    assert RuntimeSettings(data_root=str(target)).data_root == str(target)
    assert Settings(_env_file=None, data_root=target).data_root == target
