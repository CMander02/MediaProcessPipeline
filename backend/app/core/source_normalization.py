"""Source input normalization shared by API, CLI, resolver, and pipeline."""

from __future__ import annotations

import re
from urllib.parse import urlparse

_HTTP_URL_RE = re.compile(r'https?://[^\s<>"\'，。！？；、]+', re.IGNORECASE)
_SCHEMELESS_URL_RE = re.compile(
    r'(?<![\w@:/\\])((?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?(?:/[^\s<>"\'，。！？；、]*)?(?:\?[^\s<>"\'，。！？；、]*)?)',
    re.IGNORECASE,
)

_KNOWN_SCHEMELESS_HOST_SUFFIXES = (
    "bilibili.com",
    "b23.tv",
    "xiaohongshu.com",
    "xhslink.com",
    "zhihu.com",
    "xiaoyuzhoufm.com",
    "podcasts.apple.com",
    "youtube.com",
    "youtu.be",
)


def normalize_source_input(source: str) -> str:
    """Clean quotes/share text and add https:// for safe schemeless web URLs."""
    value = str(source or "").strip()
    if (
        (value.startswith('"') and value.endswith('"'))
        or (value.startswith("'") and value.endswith("'"))
    ):
        value = value[1:-1].strip()

    http_match = _HTTP_URL_RE.search(value)
    if http_match and _can_extract_embedded_url(value):
        return _strip_url_tail(http_match.group(0))

    schemeless = _first_schemeless_url(value)
    if schemeless and _can_extract_embedded_url(value):
        return f"https://{schemeless}"

    return value


def _can_extract_embedded_url(value: str) -> bool:
    return (
        value.startswith(("http://", "https://"))
        or (
            not value.startswith(("ftp://", "rtmp://"))
            and not (len(value) >= 2 and value[1] == ":")
            and not value.startswith(("/", "\\"))
        )
    )


def _first_schemeless_url(value: str) -> str | None:
    for match in _SCHEMELESS_URL_RE.finditer(value):
        candidate = _strip_url_tail(match.group(1))
        parsed = urlparse(f"https://{candidate}")
        host = (parsed.hostname or "").lower()
        if _accept_schemeless_host(host):
            return candidate
    return None


def _accept_schemeless_host(host: str) -> bool:
    if not host:
        return False
    if host.startswith("www."):
        return True
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _KNOWN_SCHEMELESS_HOST_SUFFIXES)


def _strip_url_tail(value: str) -> str:
    return value.strip().rstrip(".,;!?)）]】")
