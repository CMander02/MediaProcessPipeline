"""Runtime path contract for source, portable, and installed executions.

Read-only application resources are resolved from ``MPP_PROJECT_ROOT`` or the
source checkout. User-writable locations can be supplied independently by the
desktop launcher. When an installed launcher supplies ``MPP_CONFIG_FILE`` but
no data directory, the default data root lives beside the user configuration
tree instead of assuming that a ``D:`` drive exists.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_APP_DIRECTORY_NAME = "MediaProcessPipeline"
_SOURCE_DATA_ROOT = "D:/Video/MediaProcessPipeline"


@dataclass(frozen=True)
class RuntimePaths:
    """Resolved application resource and writable paths."""

    project_root: Path
    backend_dir: Path
    user_root: Path
    config_file: Path
    log_dir: Path
    cache_dir: Path
    web_dist_dir: Path
    default_data_root: Path
    installed_mode: bool


_PERCENT_VARIABLE = re.compile(r"%([^%]+)%")
_DOLLAR_VARIABLE = re.compile(r"\$(\w+)|\$\{([^}]+)\}")


def _environment_value(environment: Mapping[str, str], name: str) -> str | None:
    if name in environment:
        return environment[name]
    if os.name == "nt":
        upper_name = name.upper()
        for key, value in environment.items():
            if key.upper() == upper_name:
                return value
    return None


def _expand_from_environment(raw: str | Path, environment: Mapping[str, str]) -> str:
    value = str(raw)

    def replace_percent(match: re.Match[str]) -> str:
        replacement = _environment_value(environment, match.group(1))
        return replacement if replacement is not None else match.group(0)

    def replace_dollar(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        replacement = _environment_value(environment, name)
        return replacement if replacement is not None else match.group(0)

    value = _PERCENT_VARIABLE.sub(replace_percent, value)
    value = _DOLLAR_VARIABLE.sub(replace_dollar, value)
    if value == "~" or value.startswith("~/") or value.startswith("~\\"):
        home = (
            _environment_value(environment, "HOME")
            or _environment_value(environment, "USERPROFILE")
        )
        if home is None:
            home_drive = _environment_value(environment, "HOMEDRIVE")
            home_path = _environment_value(environment, "HOMEPATH")
            if home_drive and home_path:
                home = f"{home_drive}{home_path}"
        if home is not None:
            value = f"{home}{value[1:]}"
    return value


def _absolute_path(
    raw: str | Path,
    *,
    base: Path,
    environment: Mapping[str, str],
) -> Path:
    """Expand a configured path and make relative values stable against cwd changes."""

    candidate = Path(_expand_from_environment(raw, environment))
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()


def _source_project_root(module_path: Path | None = None) -> Path:
    path = (module_path or Path(__file__)).resolve()
    try:
        # <project>/backend/app/core/paths.py
        return path.parents[3]
    except IndexError as exc:
        raise RuntimeError(f"Cannot resolve project root relative to {path}") from exc


def _installed_user_root(
    environment: Mapping[str, str],
    *,
    project_root: Path,
    config_file: Path,
) -> Path:
    local_app_data = environment.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return (
            _absolute_path(
                local_app_data,
                base=project_root,
                environment=environment,
            )
            / _APP_DIRECTORY_NAME
        )

    # This also gives deterministic behavior in tests and non-Windows packaging.
    config_parent = config_file.parent
    if config_parent.name.casefold() == "config":
        return config_parent.parent
    return config_parent


def resolve_runtime_paths(
    environ: Mapping[str, str] | None = None,
    *,
    module_path: Path | None = None,
    platform_name: str | None = None,
) -> RuntimePaths:
    """Resolve application paths without reading or writing the filesystem."""

    environment = os.environ if environ is None else environ
    current_platform = os.name if platform_name is None else platform_name
    source_root = _source_project_root(module_path)
    project_value = environment.get("MPP_PROJECT_ROOT", "").strip()
    project_root = (
        _absolute_path(
            project_value,
            base=source_root,
            environment=environment,
        )
        if project_value
        else source_root
    )

    config_value = environment.get("MPP_CONFIG_FILE", "").strip()
    installed_mode = bool(config_value)
    config_file = (
        _absolute_path(
            config_value,
            base=project_root,
            environment=environment,
        )
        if config_value
        else (project_root / "config.json").resolve()
    )
    user_root = _installed_user_root(
        environment,
        project_root=project_root,
        config_file=config_file,
    )

    data_value = environment.get("MPP_DATA_ROOT", "").strip()
    if data_value:
        default_data_root = _absolute_path(
            data_value,
            base=user_root if installed_mode else project_root,
            environment=environment,
        )
    elif installed_mode:
        default_data_root = (user_root / "data").resolve()
    elif current_platform == "nt":
        default_data_root = _absolute_path(
            _SOURCE_DATA_ROOT,
            base=project_root,
            environment=environment,
        )
    else:
        xdg_data_home = environment.get("XDG_DATA_HOME", "").strip()
        default_data_root = (
            _absolute_path(
                Path(xdg_data_home) / _APP_DIRECTORY_NAME,
                base=project_root,
                environment=environment,
            )
            if xdg_data_home
            else (project_root / "data").resolve()
        )

    log_value = environment.get("MPP_LOG_DIR", "").strip()
    log_dir = (
        _absolute_path(
            log_value,
            base=project_root,
            environment=environment,
        )
        if log_value
        else (
            (user_root / "logs").resolve()
            if installed_mode
            else (project_root / "logs").resolve()
        )
    )

    cache_value = environment.get("MPP_CACHE_DIR", "").strip()
    cache_dir = (
        _absolute_path(
            cache_value,
            base=project_root,
            environment=environment,
        )
        if cache_value
        else (
            (user_root / "cache").resolve()
            if installed_mode
            else (default_data_root / ".cache").resolve()
        )
    )

    web_value = environment.get("MPP_WEB_DIST_DIR", "").strip()
    web_dist_dir = (
        _absolute_path(
            web_value,
            base=project_root,
            environment=environment,
        )
        if web_value
        else (project_root / "web" / "dist").resolve()
    )

    return RuntimePaths(
        project_root=project_root,
        backend_dir=(project_root / "backend").resolve(),
        user_root=user_root,
        config_file=config_file,
        log_dir=log_dir,
        cache_dir=cache_dir,
        web_dist_dir=web_dist_dir,
        default_data_root=default_data_root,
        installed_mode=installed_mode,
    )


def default_data_root() -> str:
    """Return the process-default data directory for ``RuntimeSettings``."""

    return str(resolve_runtime_paths().default_data_root)


def resolve_data_root(
    value: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
    module_path: Path | None = None,
    platform_name: str | None = None,
) -> Path:
    """Resolve persisted data-root values against a stable execution root."""

    environment = os.environ if environ is None else environ
    paths = resolve_runtime_paths(
        environment,
        module_path=module_path,
        platform_name=platform_name,
    )
    base = paths.user_root if paths.installed_mode else paths.project_root
    return _absolute_path(value, base=base, environment=environment)


def runtime_cache_dir(*, data_root: str | Path | None = None) -> Path:
    """Return the cache root, preserving the legacy data-root cache fallback."""

    environment = os.environ
    if environment.get("MPP_CACHE_DIR", "").strip():
        return resolve_runtime_paths(environment).cache_dir
    if data_root is not None:
        return (resolve_data_root(data_root) / ".cache").resolve()
    return resolve_runtime_paths(environment).cache_dir


def validate_web_dist(web_dist_dir: Path, *, required: bool) -> Path | None:
    """Validate a packaged frontend without mutating application state."""

    if web_dist_dir.is_dir() and (web_dist_dir / "index.html").is_file():
        return web_dist_dir
    if required:
        raise RuntimeError(
            f"Web frontend distribution is unavailable or incomplete: {web_dist_dir}"
        )
    return None


__all__ = [
    "RuntimePaths",
    "default_data_root",
    "resolve_data_root",
    "resolve_runtime_paths",
    "runtime_cache_dir",
    "validate_web_dist",
]
