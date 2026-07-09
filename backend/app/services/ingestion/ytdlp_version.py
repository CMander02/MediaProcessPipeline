"""yt-dlp version checking + self-upgrade.

YouTube actively breaks older yt-dlp versions (SABR streaming, n-sig changes).
We don't auto-upgrade on every startup (slow + risk of breaking changes), but:
  - check version age in the background at daemon startup
  - expose an upgrade endpoint / CLI command for one-shot bumps
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.network import urllib_urlopen

logger = logging.getLogger(__name__)

_PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"
_STALE_DAYS = 30
_upgrade_lock = threading.Lock()


@dataclass
class YtdlpVersionInfo:
    installed: str | None
    latest: str | None
    age_days: int | None
    is_stale: bool


def _installed_version() -> str | None:
    # yt_dlp doesn't expose __version__ at module level; use dist metadata.
    try:
        from importlib.metadata import version as _ver, PackageNotFoundError
        try:
            return _ver("yt-dlp")
        except PackageNotFoundError:
            return None
    except Exception:
        return None


def _pypi_latest() -> tuple[str | None, datetime | None]:
    try:
        req = urllib.request.Request(_PYPI_URL, headers={"User-Agent": "mpp-version-check"})
        with urllib_urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        logger.debug(f"yt-dlp PyPI lookup failed: {e}")
        return None, None

    latest = data.get("info", {}).get("version")
    upload_time = None
    files = data.get("releases", {}).get(latest, []) if latest else []
    if files:
        ts = files[0].get("upload_time_iso_8601") or files[0].get("upload_time")
        if ts:
            try:
                upload_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                upload_time = None
    return latest, upload_time


def check_version(stale_days: int = _STALE_DAYS) -> YtdlpVersionInfo:
    """Return (installed, latest, age_days, is_stale). Network errors → unknown."""
    installed = _installed_version()
    latest, latest_uploaded = _pypi_latest()

    age_days: int | None = None
    if latest_uploaded:
        age_days = (datetime.now(timezone.utc) - latest_uploaded).days

    is_stale = bool(
        installed and latest
        and installed != latest
        and (age_days is None or age_days >= 0)
    )
    return YtdlpVersionInfo(installed=installed, latest=latest, age_days=age_days, is_stale=is_stale)


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


def _upgrade_commands() -> list[list[str]]:
    commands: list[list[str]] = []
    uv = shutil.which("uv")
    if uv:
        commands.append([uv, "pip", "install", "--python", sys.executable, "--upgrade", "yt-dlp"])
    commands.append([sys.executable, "-m", "pip", "install", "-U", "--quiet", "yt-dlp"])
    return commands


def _run_upgrade_command(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
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


def upgrade(timeout: float = 180) -> dict[str, Any]:
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
        for cmd in _upgrade_commands():
            try:
                proc = _run_upgrade_command(cmd, timeout)
            except subprocess.TimeoutExpired:
                failures.append(f"{' '.join(cmd)} timed out after {timeout:.0f}s")
                continue

            output = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode != 0:
                failures.append(output.strip() or f"{' '.join(cmd)} exited with {proc.returncode}")
                continue

            new = _installed_version() or old
            return _result(
                ok=True,
                old=old,
                new=new,
                output=output,
                command=cmd,
                restart_recommended=True,
            )

        return _result(
            ok=False,
            old=old,
            new=old,
            output="\n\n".join(failures),
            command=_upgrade_commands()[-1],
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

    if not info.installed:
        logger.info("yt-dlp is not installed; installing latest build")
    elif not info.is_stale:
        logger.info("yt-dlp startup auto-update skipped: installed=%s latest=%s", info.installed, info.latest or "unknown")
        return {
            "ok": True,
            "old": info.installed,
            "new": info.installed,
            "output": "",
            "restart_recommended": False,
            "skipped": True,
        }

    logger.info("yt-dlp startup auto-update started: installed=%s latest=%s", info.installed, info.latest or "unknown")
    result = upgrade()
    if result.get("ok"):
        logger.info("yt-dlp startup auto-update complete: %s -> %s", result.get("old"), result.get("new"))
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
