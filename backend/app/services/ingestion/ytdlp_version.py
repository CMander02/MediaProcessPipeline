"""yt-dlp version checking + self-upgrade.

YouTube actively breaks older yt-dlp versions (SABR streaming, n-sig changes).
We don't auto-upgrade on every startup (slow + risk of breaking changes), but:
  - check version age in the background at daemon startup
  - expose an upgrade endpoint / CLI command for one-shot bumps
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.network import urllib_urlopen

logger = logging.getLogger(__name__)

_PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"
_STALE_DAYS = 30


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
            f"`POST /api/system/upgrade-ytdlp` to update."
        )
    else:
        logger.info(f"yt-dlp version: {info.installed} (latest: {info.latest or 'unknown'})")
    return info


def upgrade() -> dict:
    """Run `pip install -U yt-dlp` against the current interpreter.

    Returns {ok, old, new, output}. Caller should reload yt_dlp module or restart
    the daemon to actually use the new version.
    """
    old = _installed_version()
    cmd = [sys.executable, "-m", "pip", "install", "-U", "--quiet", "yt-dlp"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "old": old, "new": None, "output": "pip install timed out after 180s"}

    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return {"ok": False, "old": old, "new": old, "output": output.strip()[-2000:]}

    # Read fresh dist metadata. The already-imported yt_dlp module in this
    # process won't be replaced; restart the daemon to actually use the new code.
    new = _installed_version() or old

    return {
        "ok": True,
        "old": old,
        "new": new,
        "output": output.strip()[-2000:],
        "restart_recommended": True,
    }
