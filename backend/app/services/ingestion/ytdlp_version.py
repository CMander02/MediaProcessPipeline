"""Check and update yt-dlp through TUNA, USTC, then package-manager defaults."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)

# Try each source independently; the final attempt inherits package-manager config.
_SOURCES = (
    ("清华源", "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"),
    ("中科大源", "https://mirrors.ustc.edu.cn/pypi/simple"),
    ("系统默认源", None),
)
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_CHECK_TIMEOUT = 20
_upgrade_lock = threading.Lock()


@dataclass
class YtdlpVersionInfo:
    installed: str | None
    latest: str | None
    age_days: int | None
    is_stale: bool
    source: str | None = None
    check_error: str | None = None


def _installed_version() -> str | None:
    # yt_dlp doesn't expose __version__ at module level; use dist metadata.
    try:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _ver

        try:
            return _ver("yt-dlp")
        except PackageNotFoundError:
            return None
    except Exception:
        return None


def _command_environment(cmd: list[str]) -> dict[str, str]:
    env = os.environ.copy()
    if "--default-index" in cmd or "--index-url" in cmd:
        # Explicit mirror attempts must not be overridden by an extra index.
        for key in (
            "UV_INDEX",
            "UV_DEFAULT_INDEX",
            "UV_INDEX_URL",
            "UV_EXTRA_INDEX_URL",
            "PIP_INDEX_URL",
            "PIP_EXTRA_INDEX_URL",
            "UV_CONFIG_FILE",
        ):
            env.pop(key, None)
        if "--index-url" in cmd:
            env["PIP_CONFIG_FILE"] = os.devnull
    env.setdefault("UV_HTTP_TIMEOUT", "10")
    env.setdefault("UV_HTTP_RETRIES", "0")
    return env


def _latest_from_source(index: str | None) -> str:
    """Let the resolver select a stable release compatible with this Python."""
    uv = shutil.which("uv")
    if uv:
        cmd = [
            uv,
            "pip",
            "compile",
            "-",
            "--python",
            sys.executable,
            "--no-deps",
            "--upgrade",
            "--prerelease",
            "disallow",
            "--no-header",
            "--no-annotate",
            "--quiet",
            "--project",
            str(_PROJECT_ROOT),
        ]
        if index:
            cmd += ["--no-config", "--default-index", index]
        proc = subprocess.run(
            cmd,
            input="yt-dlp\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_CHECK_TIMEOUT,
            env=_command_environment(cmd),
            cwd=_PROJECT_ROOT,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if line.startswith("yt-dlp=="):
                    return str(Version(line.split("==", 1)[1].split(";", 1)[0].strip()))
    else:
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--dry-run",
            "--ignore-installed",
            "--no-deps",
            "--only-binary=:all:",
            "--quiet",
            "--report",
            "-",
            "yt-dlp",
        ]
        if index:
            cmd += ["--index-url", index]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_CHECK_TIMEOUT,
            env=_command_environment(cmd),
            cwd=_PROJECT_ROOT,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            for item in data.get("install", []):
                metadata = item["metadata"]
                if metadata["name"].replace("_", "-").lower() == "yt-dlp":
                    version = Version(metadata["version"])
                    if not version.is_prerelease and not version.is_devrelease:
                        return str(version)
    raise RuntimeError("package resolver did not return a compatible yt-dlp release")


def _is_newer(latest: str, installed: str) -> bool:
    try:
        return Version(latest) > Version(installed)
    except InvalidVersion:
        return False


def check_version(stale_days: int = 30) -> YtdlpVersionInfo:
    """Check sources in priority order. Keep the legacy age field for API clients."""
    installed = _installed_version()
    failures = []
    for source, index in _SOURCES:
        try:
            latest = _latest_from_source(index)
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, RuntimeError) as exc:
            logger.info(
                "yt-dlp check via %s failed (%s); trying next source", source, type(exc).__name__
            )
            failures.append(source)
            continue
        return YtdlpVersionInfo(
            installed,
            latest,
            None,
            bool(installed and _is_newer(latest, installed)),
            source,
        )
    return YtdlpVersionInfo(
        installed, None, None, False, check_error="版本检查失败：" + "、".join(failures)
    )


def warn_if_stale() -> YtdlpVersionInfo | None:
    """Log a warning if yt-dlp is behind the PyPI latest. Returns the version info."""
    try:
        info = check_version()
    except Exception as e:
        logger.debug(f"yt-dlp version check failed: {e}")
        return None

    if not info.installed:
        return info
    if info.is_stale:
        logger.warning(
            f"yt-dlp is out of date: installed={info.installed} latest={info.latest}. "
            f"YouTube extraction may fail. Run `mpp upgrade-ytdlp` or "
            f"`POST /api/settings/ytdlp/upgrade` to update."
        )
    else:
        logger.info(f"yt-dlp version: {info.installed} (latest: {info.latest or 'unknown'})")
    return info


def _upgrade_commands(minimum: str | None = None) -> list[list[str]]:
    # A lower bound prevents a lagging mirror from downgrading the installation.
    requirement = f"yt-dlp>={minimum}" if minimum else "yt-dlp"
    uv = shutil.which("uv")
    commands = []
    for _source, index in _SOURCES:
        if uv:
            cmd = [
                uv,
                "sync",
                "--project",
                str(_PROJECT_ROOT),
                "--python",
                sys.executable,
                "--inexact",
                "--upgrade-package",
                requirement,
                "--prerelease",
                "disallow",
            ]
            if index:
                cmd += ["--default-index", index]
        else:
            cmd = [sys.executable, "-m", "pip", "install", "-U", "--quiet", requirement]
            if index:
                cmd += ["--index-url", index]
        commands.append(cmd)
    return commands


def _run_upgrade_command(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        env=_command_environment(cmd),
        cwd=_PROJECT_ROOT,
    )


def _result(
    *,
    ok: bool,
    old: str | None,
    new: str | None,
    output: str,
    command: list[str] | None = None,
    restart_recommended: bool = False,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "old": old,
        "new": new,
        "output": output.strip()[-2000:],
        "command": command or [],
        "restart_recommended": restart_recommended,
    }


def upgrade(timeout: float = 180, *, target: str | None = None) -> dict[str, Any]:
    """Upgrade yt-dlp in the Python environment used by this process.

    Returns {ok, old, new, output}. Caller should reload yt_dlp module or restart
    the daemon to actually use the new version.
    """
    if not _upgrade_lock.acquire(blocking=False):
        return _result(
            ok=False,
            old=_installed_version(),
            new=None,
            output="yt-dlp upgrade is already running",
        )

    try:
        old = _installed_version()
        failures: list[str] = []
        minimum = old
        if target and (not old or _is_newer(target, old)):
            minimum = target
        commands = _upgrade_commands(minimum)
        for (source, _index), cmd in zip(_SOURCES, commands):
            try:
                proc = _run_upgrade_command(cmd, timeout)
            except (subprocess.SubprocessError, OSError) as exc:
                failures.append(f"{source}: {type(exc).__name__}")
                continue

            output = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode != 0:
                failures.append(f"{source}: " + (output.strip() or f"exit {proc.returncode}"))
                continue

            new = _installed_version()
            if not new or (minimum and _is_newer(minimum, new)):
                failures.append(f"{source}: expected >= {minimum}, installed {new}")
                continue
            logger.info("yt-dlp update via %s: %s -> %s", source, old, new)
            loaded = getattr(sys.modules.get("yt_dlp.version"), "__version__", None)
            return _result(
                ok=True,
                old=old,
                new=new,
                output=output,
                command=cmd,
                restart_recommended=(new != old or bool(loaded and _is_newer(new, loaded))),
            )

        return _result(
            ok=False,
            old=old,
            new=old,
            output="\n\n".join(failures),
            command=commands[-1],
        )
    finally:
        _upgrade_lock.release()


def auto_update_on_startup(enabled: bool) -> dict[str, Any] | None:
    """Upgrade yt-dlp during daemon startup when the runtime setting enables it."""
    if not enabled:
        return None

    try:
        info = check_version()
    except Exception as e:
        logger.warning("yt-dlp startup version check failed: %s", e)
        return None

    if not info.latest:
        logger.warning("yt-dlp startup check unavailable: %s", info.check_error)
        return {
            **_result(
                ok=False,
                old=info.installed,
                new=info.installed,
                output=info.check_error or "Version check unavailable",
            ),
            "skipped": True,
        }
    if not info.installed:
        logger.info("yt-dlp is not installed; installing latest build")
    elif not info.is_stale:
        logger.info(
            "yt-dlp startup auto-update skipped: installed=%s latest=%s",
            info.installed,
            info.latest or "unknown",
        )
        return {
            "ok": True,
            "old": info.installed,
            "new": info.installed,
            "output": "",
            "restart_recommended": False,
            "skipped": True,
        }

    logger.info(
        "yt-dlp startup auto-update started: installed=%s latest=%s",
        info.installed,
        info.latest or "unknown",
    )
    result = upgrade(target=info.latest)
    if result.get("ok"):
        logger.info(
            "yt-dlp startup auto-update complete: %s -> %s", result.get("old"), result.get("new")
        )
        if result.get("restart_recommended") and "yt_dlp" in sys.modules:
            logger.warning(
                "yt-dlp was already loaded; restart the backend to use the updated version"
            )
    else:
        logger.warning("yt-dlp startup auto-update failed: %s", result.get("output", ""))
    return result


def schedule_process_restart(delay_sec: float = 1.0) -> None:
    """Restart this Python daemon process after the current HTTP response is sent."""

    def _restart() -> None:
        time.sleep(max(delay_sec, 0.0))
        args = [sys.executable, *sys.argv]
        logger.warning("Restarting backend process: %s", " ".join(args))
        try:
            os.execv(sys.executable, args)
        except Exception as e:
            logger.error("Backend process restart failed: %s", e)
            os._exit(3)

    thread = threading.Thread(target=_restart, name="mpp-backend-restart", daemon=True)
    thread.start()
