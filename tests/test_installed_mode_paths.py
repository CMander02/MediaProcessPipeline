from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core import settings as core_settings  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.core.paths import (  # noqa: E402
    resolve_data_root,
    resolve_runtime_paths,
    runtime_cache_dir,
    validate_web_dist,
)
from app.core.settings import RuntimeSettings, SettingsLoadError  # noqa: E402


def _module_path(project_root: Path) -> Path:
    return project_root / "backend" / "app" / "core" / "paths.py"


def test_source_paths_keep_repository_layout(tmp_path: Path) -> None:
    project_root = tmp_path / "source checkout"
    paths = resolve_runtime_paths({}, module_path=_module_path(project_root))

    assert paths.project_root == project_root.resolve()
    assert paths.backend_dir == (project_root / "backend").resolve()
    assert paths.config_file == (project_root / "config.json").resolve()
    assert paths.log_dir == (project_root / "logs").resolve()
    assert paths.web_dist_dir == (project_root / "web" / "dist").resolve()
    assert paths.installed_mode is False


def test_installed_paths_prefer_explicit_environment_and_are_absolute(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "只读 runtime"
    environment = {
        "MPP_PROJECT_ROOT": str(project_root),
        "MPP_CONFIG_FILE": "用户 配置/config.json",
        "MPP_LOG_DIR": "用户 数据/logs",
        "MPP_CACHE_DIR": "用户 数据/cache",
        "MPP_WEB_DIST_DIR": "静态 页面/dist",
        "MPP_DATA_ROOT": "任务 数据",
    }

    paths = resolve_runtime_paths(
        environment,
        module_path=_module_path(tmp_path / "ignored source"),
    )

    assert paths.installed_mode is True
    assert paths.config_file == (project_root / "用户 配置" / "config.json").resolve()
    assert paths.log_dir == (project_root / "用户 数据" / "logs").resolve()
    assert paths.cache_dir == (project_root / "用户 数据" / "cache").resolve()
    assert paths.web_dist_dir == (project_root / "静态 页面" / "dist").resolve()
    assert paths.default_data_root == (
        project_root / "用户 配置" / "任务 数据"
    ).resolve()
    assert all(
        path.is_absolute()
        for path in (
            paths.project_root,
            paths.config_file,
            paths.log_dir,
            paths.cache_dir,
            paths.web_dist_dir,
            paths.default_data_root,
        )
    )


def test_installed_default_uses_local_app_data_without_d_drive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "runtime"
    local_app_data = tmp_path / "Local App Data"
    config_file = local_app_data / "MediaProcessPipeline" / "config" / "config.json"
    paths = resolve_runtime_paths(
        {
            "MPP_PROJECT_ROOT": str(project_root),
            "MPP_CONFIG_FILE": str(config_file),
            "LOCALAPPDATA": str(local_app_data),
        },
        module_path=_module_path(tmp_path / "ignored"),
    )

    app_root = local_app_data / "MediaProcessPipeline"
    assert paths.default_data_root == (app_root / "data").resolve()
    assert paths.log_dir == (app_root / "logs").resolve()
    assert paths.cache_dir == (app_root / "cache").resolve()
    assert "D:" not in str(paths.default_data_root)

    monkeypatch.setenv("MPP_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("MPP_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.delenv("MPP_DATA_ROOT", raising=False)
    assert RuntimeSettings().data_root == str((app_root / "data").resolve())


def test_explicit_paths_do_not_depend_on_process_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "runtime root"
    environment = {
        "MPP_PROJECT_ROOT": str(project_root),
        "MPP_CONFIG_FILE": "config/config.json",
        "MPP_DATA_ROOT": "data",
    }
    first = resolve_runtime_paths(environment, module_path=_module_path(tmp_path / "source"))

    other_cwd = tmp_path / "unrelated cwd"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)
    second = resolve_runtime_paths(environment, module_path=_module_path(tmp_path / "source"))

    assert second == first
    assert second.config_file == (project_root / "config" / "config.json").resolve()
    assert second.default_data_root == (project_root / "data").resolve()


def test_mapping_controls_variable_and_home_expansion(tmp_path: Path) -> None:
    project_root = tmp_path / "runtime"
    mapped_home = tmp_path / "mapping home"
    environment = {
        "MPP_PROJECT_ROOT": str(project_root),
        "MPP_CONFIG_FILE": "$MAPPED_HOME/config/config.json",
        "MPP_DATA_ROOT": "~/任务",
        "MAPPED_HOME": str(mapped_home),
        "HOME": str(mapped_home),
        "USERPROFILE": str(mapped_home),
    }

    paths = resolve_runtime_paths(
        environment,
        module_path=_module_path(tmp_path / "source"),
    )

    assert paths.config_file == (mapped_home / "config" / "config.json").resolve()
    assert paths.default_data_root == (mapped_home / "任务").resolve()


def test_relative_data_root_uses_stable_mode_specific_base(tmp_path: Path) -> None:
    project_root = tmp_path / "source"
    source_environment = {"MPP_PROJECT_ROOT": str(project_root)}
    assert resolve_data_root(
        "relative/data",
        environ=source_environment,
        module_path=_module_path(project_root),
    ) == (project_root / "relative" / "data").resolve()

    config_file = tmp_path / "user root" / "config.json"
    installed_environment = {
        "MPP_PROJECT_ROOT": str(project_root),
        "MPP_CONFIG_FILE": str(config_file),
    }
    assert resolve_data_root(
        "relative/data",
        environ=installed_environment,
        module_path=_module_path(project_root),
    ) == (config_file.parent / "relative" / "data").resolve()


def test_non_windows_source_default_uses_stable_project_data(tmp_path: Path) -> None:
    project_root = tmp_path / "source"
    paths = resolve_runtime_paths(
        {},
        module_path=_module_path(project_root),
        platform_name="posix",
    )

    assert paths.default_data_root == (project_root / "data").resolve()


def test_non_windows_source_default_honors_xdg_data_home(tmp_path: Path) -> None:
    project_root = tmp_path / "source"
    xdg_data_home = tmp_path / "xdg"
    paths = resolve_runtime_paths(
        {"XDG_DATA_HOME": str(xdg_data_home)},
        module_path=_module_path(project_root),
        platform_name="posix",
    )

    assert paths.default_data_root == (
        xdg_data_home / "MediaProcessPipeline"
    ).resolve()


def test_static_env_file_is_resolved_from_project_root_for_any_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "runtime root"
    project_root.mkdir()
    (project_root / ".env").write_text("DEBUG=true\n", encoding="utf-8")
    unrelated = tmp_path / "unrelated cwd"
    unrelated.mkdir()
    monkeypatch.setenv("MPP_PROJECT_ROOT", str(project_root))
    monkeypatch.delenv("DEBUG", raising=False)
    monkeypatch.chdir(unrelated)
    get_settings.cache_clear()

    try:
        assert get_settings().debug is True
    finally:
        get_settings.cache_clear()


def test_runtime_settings_and_static_settings_use_mpp_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "任务 数据"
    monkeypatch.setenv("MPP_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MPP_CONFIG_FILE", str(tmp_path / "config" / "config.json"))

    assert RuntimeSettings().data_root == str(data_root.resolve())
    assert Settings(_env_file=None).data_root == data_root.resolve()


def test_cache_override_wins_over_legacy_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_root = tmp_path / "cache override"
    monkeypatch.setenv("MPP_CACHE_DIR", str(cache_root))

    assert runtime_cache_dir(data_root=tmp_path / "legacy data") == cache_root.resolve()


def test_explicit_corrupt_settings_file_fails_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_file = tmp_path / "config.json"
    settings_file.write_text("{ invalid", encoding="utf-8")
    monkeypatch.setenv("MPP_CONFIG_FILE", str(settings_file))
    monkeypatch.setattr(core_settings, "SETTINGS_FILE", settings_file)

    with pytest.raises(SettingsLoadError, match="explicit settings file"):
        core_settings._load_settings_from_file()


def test_missing_explicit_settings_file_uses_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_file = tmp_path / "missing.json"
    monkeypatch.setenv("MPP_CONFIG_FILE", str(settings_file))
    monkeypatch.setattr(core_settings, "SETTINGS_FILE", settings_file)

    loaded = core_settings._load_settings_from_file()

    assert isinstance(loaded, RuntimeSettings)
    assert not settings_file.exists()


def test_explicit_unreadable_settings_file_fails_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_file = tmp_path / "config.json"
    settings_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MPP_CONFIG_FILE", str(settings_file))
    monkeypatch.setattr(core_settings, "SETTINGS_FILE", settings_file)
    real_read_text = Path.read_text

    def fail_read(path: Path, *args, **kwargs) -> str:
        if path == settings_file:
            raise PermissionError("access denied")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_read)
    with pytest.raises(SettingsLoadError, match="explicit settings file"):
        core_settings._load_settings_from_file()


def test_web_dist_validation_is_fail_closed_when_required(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    complete = tmp_path / "complete"
    complete.mkdir()
    (complete / "index.html").write_text("<html></html>", encoding="utf-8")

    assert validate_web_dist(missing, required=False) is None
    with pytest.raises(RuntimeError, match="distribution"):
        validate_web_dist(missing, required=True)
    with pytest.raises(RuntimeError, match="distribution"):
        validate_web_dist(incomplete, required=True)
    assert validate_web_dist(complete, required=True) == complete


def test_settings_save_is_atomic_and_updates_memory_after_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_file = tmp_path / "用户 配置" / "config.json"
    original = RuntimeSettings(data_root=str(tmp_path / "old data"), kb_enabled=True)
    monkeypatch.setattr(core_settings, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(core_settings, "_runtime_settings", original)
    fsync_calls = 0
    real_fsync = core_settings.os.fsync

    def record_fsync(descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        real_fsync(descriptor)

    monkeypatch.setattr(core_settings.os, "fsync", record_fsync)

    updated = core_settings.patch_runtime_settings({"kb_enabled": False})

    assert fsync_calls == (1 if os.name == "nt" else 2)
    assert updated.kb_enabled is False
    assert core_settings._runtime_settings is updated
    persisted = json.loads(settings_file.read_text(encoding="utf-8"))
    assert persisted["kb_enabled"] is False
    assert not list(settings_file.parent.glob(f".{settings_file.name}.*.tmp"))


def test_settings_replace_failure_preserves_file_and_in_memory_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_file = tmp_path / "config" / "config.json"
    settings_file.parent.mkdir(parents=True)
    original = RuntimeSettings(data_root=str(tmp_path / "old data"), kb_enabled=True)
    original_payload = json.dumps(original.model_dump(), ensure_ascii=False)
    settings_file.write_text(original_payload, encoding="utf-8")
    monkeypatch.setattr(core_settings, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(core_settings, "_runtime_settings", original)

    def fail_replace(_source: str | os.PathLike[str], _destination: str | os.PathLike[str]) -> None:
        raise PermissionError("destination is read-only")

    monkeypatch.setattr(core_settings.os, "replace", fail_replace)

    with pytest.raises(PermissionError, match="read-only"):
        core_settings.patch_runtime_settings({"kb_enabled": False})

    assert core_settings._runtime_settings is original
    assert core_settings._runtime_settings.kb_enabled is True
    assert settings_file.read_text(encoding="utf-8") == original_payload
    assert not list(settings_file.parent.glob(f".{settings_file.name}.*.tmp"))


def test_settings_cleanup_failure_does_not_mask_replace_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_file = tmp_path / "config" / "config.json"
    original = RuntimeSettings(data_root=str(tmp_path / "old data"))
    monkeypatch.setattr(core_settings, "SETTINGS_FILE", settings_file)

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise PermissionError("replace failed")

    def fail_unlink(_path: Path, *, missing_ok: bool = False) -> None:
        raise OSError("cleanup failed")

    monkeypatch.setattr(core_settings.os, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_unlink)

    with pytest.raises(PermissionError, match="replace failed"):
        core_settings._save_settings_to_file(original)


def test_posix_parent_directory_is_fsynced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_file = tmp_path / "config.json"
    calls: list[tuple[str, int | Path]] = []
    monkeypatch.setattr(core_settings.os, "name", "posix")
    monkeypatch.setattr(
        core_settings.os,
        "open",
        lambda path, _flags: calls.append(("open", path)) or 42,
    )
    monkeypatch.setattr(
        core_settings.os,
        "fsync",
        lambda descriptor: calls.append(("fsync", descriptor)),
    )
    monkeypatch.setattr(
        core_settings.os,
        "close",
        lambda descriptor: calls.append(("close", descriptor)),
    )

    core_settings._fsync_parent_directory(settings_file)

    assert calls == [
        ("open", settings_file.parent),
        ("fsync", 42),
        ("close", 42),
    ]
