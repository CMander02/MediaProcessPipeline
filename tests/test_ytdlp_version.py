"""Source priority, version ordering, and startup update regression tests."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.ingestion import ytdlp_version as service


@pytest.mark.parametrize(
    "installed,latest,stale",
    [
        ("2026.03.17", "2026.3.17", False),
        ("2026.8.19", "2026.7.4", False),
        ("2026.7.4", "2026.8.19", True),
        ("2026.8.19.dev1", "2026.8.19", True),
        (None, "2026.8.19", False),
    ],
)
def test_version_order(monkeypatch, installed, latest, stale):
    monkeypatch.setattr(service, "_installed_version", lambda: installed)
    monkeypatch.setattr(service, "_latest_from_source", lambda index: latest)
    assert service.check_version().is_stale is stale


@pytest.mark.parametrize("failed_sources", [0, 1, 2, 3])
def test_check_source_fallback(monkeypatch, failed_sources):
    calls = []
    monkeypatch.setattr(service, "_installed_version", lambda: "2026.3.17")

    def resolve(index):
        calls.append(index)
        if len(calls) <= failed_sources:
            raise subprocess.TimeoutExpired("uv", 20)
        return "2026.8.19"

    monkeypatch.setattr(service, "_latest_from_source", resolve)
    info = service.check_version()
    assert calls == [index for _, index in service._SOURCES][: min(failed_sources + 1, 3)]
    assert bool(info.check_error) is (failed_sources == 3)
    assert info.latest == (None if failed_sources == 3 else "2026.8.19")


def test_resolver_uses_current_python_and_default_config(monkeypatch):
    calls = []
    monkeypatch.setattr(service.shutil, "which", lambda name: "uv")

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, "yt-dlp==2026.08.19\n", "")

    monkeypatch.setattr(service.subprocess, "run", run)
    monkeypatch.setenv("UV_DEFAULT_INDEX", "https://custom.example/simple")
    monkeypatch.setenv("UV_INDEX", "https://extra.example/simple")
    assert service._latest_from_source(service._SOURCES[0][1]) == "2026.8.19"
    mirror_cmd, mirror_kwargs = calls[-1]
    assert mirror_cmd[mirror_cmd.index("--python") + 1] == sys.executable
    assert "--no-config" in mirror_cmd
    assert "UV_INDEX" not in mirror_kwargs["env"]
    service._latest_from_source(None)
    default_cmd, default_kwargs = calls[-1]
    assert "--default-index" not in default_cmd
    assert "--no-config" not in default_cmd
    assert default_kwargs["env"]["UV_DEFAULT_INDEX"] == "https://custom.example/simple"
    assert default_kwargs["env"]["UV_INDEX"] == "https://extra.example/simple"


@pytest.mark.parametrize("failed_sources", [0, 1, 2, 3])
def test_upgrade_fallback_and_minimum_version(monkeypatch, failed_sources):
    installed = "2026.7.4"
    calls = []
    monkeypatch.setattr(service, "_installed_version", lambda: installed)
    monkeypatch.setattr(service.shutil, "which", lambda name: "uv")

    def run(cmd, timeout):
        nonlocal installed
        calls.append(cmd)
        if len(calls) <= failed_sources:
            return subprocess.CompletedProcess(cmd, 1, "", "offline")
        installed = "2026.8.19"
        return subprocess.CompletedProcess(cmd, 0, "updated", "")

    monkeypatch.setattr(service, "_run_upgrade_command", run)
    result = service.upgrade(target="2026.8.19")
    assert result["ok"] is (failed_sources < 3)
    assert len(calls) == min(failed_sources + 1, 3)
    assert all("yt-dlp>=2026.8.19" in cmd for cmd in calls)
    if len(calls) == 3:
        assert "--default-index" not in calls[-1]
    assert not service._upgrade_lock.locked()


def test_unchanged_install_does_not_restart(monkeypatch):
    monkeypatch.setattr(service, "_installed_version", lambda: "2026.8.19")
    monkeypatch.setattr(
        service,
        "_run_upgrade_command",
        lambda cmd, timeout: subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    assert service.upgrade()["restart_recommended"] is False


@pytest.mark.parametrize(
    "installed,latest,stale,update",
    [
        ("2026.8.19", "2026.7.4", False, False),
        ("2026.8.19", "2026.8.19", False, False),
        ("2026.7.4", "2026.8.19", True, True),
        ("2026.7.4", None, False, False),
        (None, None, False, False),
        (None, "2026.8.19", False, True),
    ],
)
def test_startup_only_updates_known_new_release(monkeypatch, installed, latest, stale, update):
    calls = []
    monkeypatch.setattr(
        service, "check_version", lambda: service.YtdlpVersionInfo(installed, latest, None, stale)
    )
    monkeypatch.setattr(service, "upgrade", lambda **kwargs: calls.append(kwargs) or {"ok": True})
    service.auto_update_on_startup(True)
    assert calls == ([{"target": latest}] if update else [])
    calls.clear()
    service.auto_update_on_startup(False)
    assert calls == []


def test_default_auto_update_preserves_explicit_opt_out():
    from app.core.settings import RuntimeSettings

    assert RuntimeSettings().ytdlp_auto_update is True
    assert RuntimeSettings(ytdlp_auto_update=False).ytdlp_auto_update is False


def test_pip_fallback_keeps_source_order(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda name: None)
    commands = service._upgrade_commands("2026.8.19")
    assert commands[0][-2:] == ["--index-url", service._SOURCES[0][1]]
    assert commands[1][-2:] == ["--index-url", service._SOURCES[1][1]]
    assert "--index-url" not in commands[2]
    assert commands[2][:3] == [sys.executable, "-m", "pip"]


@pytest.mark.asyncio
async def test_api_does_not_restart_after_unchanged_update(monkeypatch):
    from app.api.routes.settings import upgrade_ytdlp
    from fastapi import BackgroundTasks

    monkeypatch.setattr(service, "upgrade", lambda: {"ok": True, "restart_recommended": False})
    background = BackgroundTasks()
    result = await upgrade_ytdlp(background)
    assert result["restart_scheduled"] is False
    assert background.tasks == []
