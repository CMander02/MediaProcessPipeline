"""Bilibili authentication — SESSDATA management and wbi key caching.

Reads credentials from RuntimeSettings.bilibili_sessdata first;
falls back to parsing BBDown.data if the settings field is empty.
"""

import json
import logging
import re
import time
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# BBDown data file path relative to this module:
# platform/bilibili/auth.py -> platform/bilibili/ -> platform/ -> ingestion/ -> services/ -> app/ -> backend/
# backend/ -> tools/bbdown/BBDown.data
_BBDOWN_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent.parent / "tools" / "bbdown"
_BBDOWN_DATA = _BBDOWN_DIR / "BBDown.data"

# WBI key cache: {img_key, sub_key, fetched_at}
_wbi_cache: dict = {}
_WBI_TTL = 12 * 3600  # 12 hours


def _read_bbdown_cookie() -> str:
    """Parse cookie string from BBDown.data file."""
    if not _BBDOWN_DATA.exists():
        return ""
    try:
        return _BBDOWN_DATA.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception as e:
        logger.warning(f"Failed to read BBDown.data: {e}")
        return ""


def get_cookie() -> str:
    """Return Cookie header value string.

    Priority:
      1. RuntimeSettings bilibili_sessdata / bili_jct / dede_user_id fields
      2. BBDown.data raw cookie string

    Returns e.g. "SESSDATA=xxx; bili_jct=yyy; DedeUserID=zzz"
    """
    try:
        from app.core.settings import get_runtime_settings
        rt = get_runtime_settings()
        sessdata = (rt.bilibili_sessdata or "").strip()
        bili_jct = (rt.bilibili_bili_jct or "").strip()
        dede_uid = (rt.bilibili_dede_user_id or "").strip()
        if sessdata:
            parts = [f"SESSDATA={sessdata}"]
            if bili_jct:
                parts.append(f"bili_jct={bili_jct}")
            if dede_uid:
                parts.append(f"DedeUserID={dede_uid}")
            return "; ".join(parts)
    except Exception as e:
        logger.warning(f"Failed to read bilibili settings: {e}")

    # Fallback: BBDown.data
    return _read_bbdown_cookie()


def get_sessdata() -> str:
    """Return just the SESSDATA value."""
    cookie = get_cookie()
    if not cookie:
        return ""
    m = re.search(r'SESSDATA=([^;]+)', cookie)
    return m.group(1).strip() if m else ""


def _nav_request() -> Optional[dict]:
    """Fetch /x/web-interface/nav with current cookie. Returns parsed JSON data or None."""
    cookie = get_cookie()
    headers: dict[str, str] = {"User-Agent": _UA}
    if cookie:
        headers["Cookie"] = cookie
    try:
        req = urllib.request.Request(
            "https://api.bilibili.com/x/web-interface/nav",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
        if body.get("code") == 0:
            return body.get("data") or {}
    except Exception as e:
        logger.warning(f"Bilibili nav API failed: {e}")
    return None


def is_logged_in() -> bool:
    """Return True if the current cookie is valid and logged in."""
    data = _nav_request()
    if data is None:
        return False
    return bool(data.get("isLogin"))


def get_wbi_keys() -> tuple[str, str]:
    """Return (img_key, sub_key) for wbi signing, cached for 12 hours.

    Fetches from /x/web-interface/nav wbi_img field if cache is stale.
    Returns ("", "") if fetch fails.
    """
    now = time.time()
    if _wbi_cache.get("fetched_at", 0) + _WBI_TTL > now:
        return _wbi_cache.get("img_key", ""), _wbi_cache.get("sub_key", "")

    data = _nav_request()
    if not data:
        return "", ""

    wbi_img = data.get("wbi_img") or {}
    img_url: str = wbi_img.get("img_url") or ""
    sub_url: str = wbi_img.get("sub_url") or ""

    # Extract key from URL: last path segment without extension
    def _extract_key(url: str) -> str:
        if not url:
            return ""
        stem = url.rstrip("/").split("/")[-1]
        if "." in stem:
            stem = stem.rsplit(".", 1)[0]
        return stem

    img_key = _extract_key(img_url)
    sub_key = _extract_key(sub_url)

    _wbi_cache["img_key"] = img_key
    _wbi_cache["sub_key"] = sub_key
    _wbi_cache["fetched_at"] = now

    return img_key, sub_key
