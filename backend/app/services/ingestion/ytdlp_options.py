"""Ytdlp options for media ingestion."""

import logging
from pathlib import Path
from typing import Any

from app.core.logging_setup import log_event
from app.core.network import runtime_proxy_url as shared_runtime_proxy_url
from app.core.settings import get_runtime_settings
from app.services.ingestion.platform.source_urls import _is_youtube_url

logger = logging.getLogger(__name__)

_BBDOWN_DIR = Path(__file__).resolve().parent.parent.parent.parent / "tools" / "bbdown"


_BBDOWN_EXE = _BBDOWN_DIR / "BBDown.exe"


_YTDLP_NETWORK_ERROR_MARKERS = (
    "http error 429",
    "too many requests",
    "failed to establish a new connection",
    "connection refused",
    "actively refused",
    "winerror 10061",
    "nameresolutionerror",
    "getaddrinfo failed",
    "temporary failure in name resolution",
    "no route to host",
    "network is unreachable",
    "connection reset by peer",
    "connect timeout",
    "connecttimeout",
    "read timed out",
    "timed out",
    "proxyerror",
    "unable to connect to proxy",
    "unable to download api page",
)


class YoutubeNetworkError(RuntimeError):
    """Raised when YouTube is unreachable after yt-dlp's bounded retries."""


class _YtdlpLogger:
    """Route yt-dlp output through app logging instead of raw stderr."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, msg: str) -> None:
        if msg.startswith("[debug] "):
            self.messages.append(msg)
            log_event(logger, logging.DEBUG, "ytdlp.debug", message=msg)

    def info(self, msg: str) -> None:
        self.messages.append(msg)
        log_event(logger, logging.INFO, "ytdlp.info", message=msg)

    def warning(self, msg: str) -> None:
        self.messages.append(msg)
        log_event(logger, logging.WARNING, "ytdlp.warning", message=msg)

    def error(self, msg: str) -> None:
        self.messages.append(msg)
        log_event(logger, logging.ERROR, "ytdlp.error", message=msg)

    def has_youtube_network_error(self, url: str | None = None) -> bool:
        return any(is_youtube_network_error(msg, url) for msg in self.messages)

    def network_error_summary(self) -> str:
        for msg in reversed(self.messages):
            if is_youtube_network_error(msg):
                return msg
        return self.messages[-1] if self.messages else "unknown yt-dlp error"


def _normalize_proxy_url(raw: str) -> str:
    proxy = raw.strip()
    if not proxy:
        return ""
    if "://" not in proxy:
        proxy = f"http://{proxy}"
    return proxy


def youtube_proxy_url() -> str | None:
    """Resolve proxy for YouTube yt-dlp calls.

    Returns:
        str: explicit or auto-detected proxy URL.
        None: no proxy option should be set.
        "": explicitly disable proxy use in yt-dlp.
    """
    rt = get_runtime_settings()
    configured = (rt.youtube_proxy or "").strip()
    if configured:
        if configured.lower() in {"direct", "none", "off", "false", "0"}:
            return ""
        return _normalize_proxy_url(configured)

    return shared_runtime_proxy_url()


def ytdlp_base_opts(ydl_logger: _YtdlpLogger | None = None) -> dict[str, Any]:
    """Shared yt-dlp options: fail fast on network errors instead of retrying
    forever. Without this, a dead proxy or DNS issue produces ~9 retries
    × multiple clients (tv/android/web) × ~3 socket retries each = looks like
    an infinite loop in the log.

    Proxy handling: YouTube requests may run inside the cmd-launched daemon,
    which often does not inherit PowerShell-scoped proxy variables. Resolve the
    dedicated runtime setting first, then the shared app proxy resolution, and
    pass it explicitly to yt-dlp.

    EJS solver: YouTube's n-parameter signature challenge now requires a JS
    runtime via yt-dlp's EJS subsystem. Without it, extraction succeeds for
    metadata but all video/audio formats are filtered out (only images remain).
    `ejs:github` fetches the solver from the official yt-dlp release on demand
    and caches it; first call may take a few extra seconds.
    """
    opts: dict[str, Any] = {
        "retries": 3,  # video-data retries
        "fragment_retries": 3,  # DASH fragment retries
        "extractor_retries": 3,  # extractor-level retries
        "socket_timeout": 15,  # cap each TCP attempt
        "remote_components": ["ejs:github"],
        "logger": ydl_logger or _YtdlpLogger(),
        "noprogress": True,
        "no_color": True,
    }
    proxy = youtube_proxy_url()
    if proxy is not None:
        opts["proxy"] = proxy
    return opts


def is_youtube_network_error(error: BaseException | str, url: str | None = None) -> bool:
    if url and not _is_youtube_url(url):
        return False
    text = str(error).lower()
    if not text:
        return False
    return any(marker in text for marker in _YTDLP_NETWORK_ERROR_MARKERS)


def _youtube_network_error(url: str, error: BaseException) -> YoutubeNetworkError:
    return YoutubeNetworkError(
        "YouTube is unreachable or rate-limited after limited yt-dlp retries. "
        "Check Settings > YouTube > Proxy and cookies/browser auth, or configure youtube_proxy "
        "for the server network environment. "
        f"Last error: {error}"
    )


def ytdlp_auth_opts() -> dict[str, Any]:
    """yt-dlp options for YouTube (and other sites) auth cookies.

    YouTube increasingly blocks unauthenticated requests ("Sign in to confirm
    you're not a bot"). Users can either point to an exported cookies.txt or
    name a browser for yt-dlp to read cookies from directly.
    """
    rt = get_runtime_settings()
    opts: dict[str, Any] = {}
    cookie_file = (rt.youtube_cookies_file or "").strip()
    cookie_browser = (rt.youtube_cookies_browser or "").strip().lower()
    if cookie_file:
        p = Path(cookie_file)
        if p.exists():
            opts["cookiefile"] = str(p)
        else:
            log_event(logger, logging.WARNING, "youtube.cookies.missing", path=cookie_file)
    elif cookie_browser:
        # yt-dlp expects a tuple (browser, profile, keyring, container)
        opts["cookiesfrombrowser"] = (cookie_browser,)
    return opts
