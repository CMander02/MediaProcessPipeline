"""Media download service — yt-dlp for general sites, BBDown for Bilibili."""

import hashlib
import logging
import re
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.core.config import get_settings
from app.core.logging_setup import log_event
from app.core.network import runtime_proxy_url as shared_runtime_proxy_url
from app.core.network import urllib_urlopen
from app.core.settings import get_runtime_settings
from app.models import ChapterInfo, MediaMetadata, MediaType

logger = logging.getLogger(__name__)

# BBDown executable — shipped with the project
_BBDOWN_DIR = Path(__file__).resolve().parent.parent.parent.parent / "tools" / "bbdown"
_BBDOWN_EXE = _BBDOWN_DIR / "BBDown.exe"
_HTTP_URL_RE = re.compile(r'https?://[^\s<>"\'，。！？；、]+', re.IGNORECASE)
_BILIBILI_BVID_RE = r'BV[0-9A-Za-z]{10}'


def _extract_http_urls(value: str) -> list[str]:
    return [match.group(0).strip() for match in _HTTP_URL_RE.finditer(value)]


def _extract_twitter_external_article_url(value: str) -> str:
    """Return the first article URL that leaves X/Twitter infrastructure."""
    for candidate in _extract_http_urls(value):
        parsed = urlparse(candidate.rstrip(".,;:!?)]}"))
        host = (parsed.hostname or "").lower()
        if host and not any(
            host == suffix or host.endswith(f".{suffix}")
            for suffix in ("x.com", "twitter.com", "t.co", "twimg.com")
        ):
            return parsed.geturl()
    return ""


def _candidate_urls(value: str) -> list[str]:
    urls = _extract_http_urls(value)
    return urls or [value.strip()]


def _candidate_matches(value: str, pattern: str) -> bool:
    return any(re.search(pattern, candidate, re.IGNORECASE) for candidate in _candidate_urls(value))


def _host_matches(value: str, suffixes: tuple[str, ...]) -> bool:
    for candidate in _candidate_urls(value):
        if "://" not in candidate:
            candidate = f"https://{candidate}"
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").lower()
        if any(host == suffix or host.endswith(f".{suffix}") for suffix in suffixes):
            return True
    return False


def _ensure_http_url(value: str) -> str:
    return value if "://" in value else f"https://{value}"


def _is_bilibili_short_url(value: str) -> bool:
    parsed = urlparse(_ensure_http_url(value))
    host = (parsed.hostname or "").lower()
    return host == "b23.tv" or host.endswith(".b23.tv")


def normalize_bilibili_source_url(url: str) -> str:
    """Resolve b23.tv short links before selecting a Bilibili ingestor."""
    for candidate in _candidate_urls(url):
        if not _is_bilibili_short_url(candidate):
            continue
        ensured = _ensure_http_url(candidate)
        resolved = _resolve_bilibili_short_url(ensured)
        if resolved and resolved != ensured:
            return resolved
    return url


def _is_bilibili_article_url(url: str) -> bool:
    url = normalize_bilibili_source_url(url)
    for candidate in _candidate_urls(url):
        if "://" not in candidate:
            candidate = f"https://{candidate}"
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").lower()
        if not (host == "bilibili.com" or host.endswith(".bilibili.com")):
            continue
        path = parsed.path.rstrip("/")
        if re.match(r"^/read/(?:cv\d+|mobile|readlist)", path, re.IGNORECASE):
            return True
        if re.match(r"^/h5/note-app/view", path, re.IGNORECASE):
            return True
    return False


def _is_bilibili_image_note_url(url: str) -> bool:
    url = normalize_bilibili_source_url(url)
    for candidate in _candidate_urls(url):
        if "://" not in candidate:
            candidate = f"https://{candidate}"
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").lower()
        path = parsed.path.rstrip("/")
        if host == "t.bilibili.com" or host.endswith(".t.bilibili.com"):
            return bool(re.match(r"^/(?:dynamic/)?\d+$", path, re.IGNORECASE))
        if not (host == "bilibili.com" or host.endswith(".bilibili.com")):
            continue
        if re.match(r"^/(?:opus|dynamic)/\d+$", path, re.IGNORECASE):
            return True
        if re.match(r"^/h5/dynamic/detail/\d+$", path, re.IGNORECASE):
            return True
    return False


def _is_bilibili_video_url(url: str) -> bool:
    url = normalize_bilibili_source_url(url)
    if not _extract_http_urls(url):
        return bool(re.fullmatch(rf'(?:{_BILIBILI_BVID_RE}|av\d+)', url.strip(), re.IGNORECASE))

    for candidate in _candidate_urls(url):
        if "://" not in candidate:
            candidate = f"https://{candidate}"
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").lower()
        host_token = parsed.netloc.rsplit("@", 1)[-1].split(":", 1)[0]
        path = parsed.path
        query = parse_qs(parsed.query)
        if re.fullmatch(_BILIBILI_BVID_RE, host_token):
            return True
        if not (host == "bilibili.com" or host.endswith(".bilibili.com")):
            continue
        if re.search(rf"/(?:video/)?(?:{_BILIBILI_BVID_RE}|av\d+)(?:/|$)", path, re.IGNORECASE):
            return True
        if query.get("bvid") or query.get("aid"):
            return True
        if path.startswith("/x/web-interface/view"):
            return True
    return False


def _is_bilibili_url(url: str) -> bool:
    return _is_bilibili_video_url(url)


def _is_xiaoyuzhou_url(url: str) -> bool:
    return _candidate_matches(url, r'xiaoyuzhoufm\.com/episode/[0-9a-fA-F]+')


def _is_xiaohongshu_url(url: str) -> bool:
    return _host_matches(url, ("xiaohongshu.com", "xhslink.com"))


def _is_zhihu_url(url: str) -> bool:
    return _candidate_matches(url, r'zhihu\.com/(?:pin/\d+|question/\d+/answer/\d+)')


_DIRECT_MEDIA_EXTS = {
    ".mp4", ".mkv", ".avi", ".webm", ".mov", ".m4v",
    ".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac",
}


def _is_direct_media_url(url: str) -> bool:
    for candidate in _candidate_urls(url):
        if "://" not in candidate:
            continue
        parsed = urlparse(candidate)
        if Path(parsed.path).suffix.lower() in _DIRECT_MEDIA_EXTS:
            return True
    return False


def _is_apple_podcast_url(url: str) -> bool:
    return _candidate_matches(url, r'podcasts\.apple\.com/(?:[a-z]{2}/)?podcast/[^?#/]*/id\d+')


def _is_youtube_url(url: str) -> bool:
    return _host_matches(url, ("youtube.com", "youtu.be"))


def _is_twitter_url(url: str) -> bool:
    return _host_matches(url, ("x.com", "twitter.com"))


def _clean_twitter_title(title: str) -> str:
    title = re.sub(r"\s*/\s*X\s*$", "", title or "").strip()
    title = re.sub(r"\s+on\s+X:\s+.*$", " on X", title).strip()
    return title


def _clean_twitter_text(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines()]
    stop_markers = {
        "New to X?",
        "Relevant people",
        "Terms",
        "Don't miss what's happening",
        "People on X are the first to know.",
    }
    drop_exact = {
        "",
        "Post",
        "Log in",
        "Sign up",
        "Sign up with Google",
        "Sign up with Apple",
        "Create account",
    }
    cleaned: list[str] = []
    for line in lines:
        if line in stop_markers:
            break
        if line in drop_exact:
            continue
        if line.startswith("By signing up,"):
            break
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _extract_twitter_article_title(value: Any) -> str:
    lines = [line.strip() for line in str(value or "").splitlines()]
    for idx, line in enumerate(lines):
        if line != "Article":
            continue
        for candidate in lines[idx + 1:]:
            if candidate and not candidate.startswith(("http://", "https://")):
                return candidate
    return ""


def _is_twitter_content_image(value: Any) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    return (host == "pbs.twimg.com" or host.endswith(".pbs.twimg.com")) and "/media/" in parsed.path


def _dedupe_twitter_image_urls(values: Any) -> list[str]:
    if not isinstance(values, list):
        values = [values] if values else []
    image_urls: list[str] = []
    seen: set[str] = set()
    for value in values:
        raw = str(value or "").strip()
        if not _is_twitter_content_image(raw):
            continue
        dedupe_key = _twitter_image_dedupe_key(raw)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        image_urls.append(raw)
    return image_urls


def _twitter_image_dedupe_key(value: str) -> str:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    path = urllib.parse.unquote(parsed.path)
    filename = path.rsplit("/", 1)[-1].split(":", 1)[0]
    media_id = filename.rsplit(".", 1)[0] if "." in filename else filename
    return f"{host}/media/{media_id}" if media_id else value


def _is_generic_webpage_url(url: str) -> bool:
    url = normalize_bilibili_source_url(url)
    if not _extract_http_urls(url) and not url.strip().startswith(("http://", "https://")):
        return False
    if _is_direct_media_url(url):
        return False
    if _is_bilibili_article_url(url) or _is_bilibili_image_note_url(url):
        return False
    if _is_bilibili_video_url(url):
        return False
    if _host_matches(url, (
        "youtube.com", "youtu.be", "vimeo.com",
        "x.com", "twitter.com", "tiktok.com", "douyin.com",
        "kuaishou.com", "weibo.com",
        "bilibili.com", "b23.tv",
    )):
        return False
    return True


def _extract_bilibili_bvid(url: str) -> str | None:
    """Extract or resolve a Bilibili BV id from BV or av/aid URLs."""
    url = normalize_bilibili_source_url(url)
    value = url.strip()
    if not value:
        return None

    bare_bv = re.fullmatch(rf'({_BILIBILI_BVID_RE})', value)
    if bare_bv:
        return bare_bv.group(1)

    bare_av = re.fullmatch(r'av(\d+)', value, re.IGNORECASE)
    if bare_av:
        aid = bare_av.group(1)
    else:
        aid = None
        for candidate in _candidate_urls(value):
            if "://" not in candidate:
                candidate = f"https://{candidate}"
            parsed = urlparse(candidate)
            host = (parsed.hostname or "").lower()
            host_token = parsed.netloc.rsplit("@", 1)[-1].split(":", 1)[0]
            if re.fullmatch(_BILIBILI_BVID_RE, host_token):
                return host_token
            if host == "b23.tv" or host.endswith(".b23.tv"):
                resolved = _resolve_bilibili_short_url(candidate)
                if resolved and resolved != candidate:
                    bvid = _extract_bilibili_bvid(resolved)
                    if bvid:
                        return bvid
                continue
            if not (host == "bilibili.com" or host.endswith(".bilibili.com")):
                continue

            bvid_values = parse_qs(parsed.query).get("bvid") or []
            for bvid in bvid_values:
                if re.fullmatch(_BILIBILI_BVID_RE, bvid):
                    return bvid

            path_match = re.search(rf'/(?:video/)?({_BILIBILI_BVID_RE})(?:/|$)', parsed.path)
            if path_match:
                return path_match.group(1)

            av_match = re.search(r'/(?:video/)?av(\d+)(?:/|$)', parsed.path, re.IGNORECASE)
            if av_match:
                aid = av_match.group(1)
                break

    if not aid:
        return None

    try:
        import json

        req = urllib.request.Request(
            f"https://api.bilibili.com/x/web-interface/view?aid={aid}",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": f"https://www.bilibili.com/video/av{aid}/",
            },
        )
        with urllib_urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read()).get("data", {})
        bvid = data.get("bvid")
        return str(bvid) if bvid else None
    except Exception as e:
        log_event(logger, logging.WARNING, "bilibili.bvid.resolve_failed", aid=aid, error=e)
        return None


def _resolve_bilibili_short_url(url: str) -> str | None:
    """Resolve b23.tv short links to their final Bilibili URL."""
    for method in ("HEAD", "GET"):
        try:
            return _resolve_bilibili_short_url_once(url, method=method)
        except Exception as e:
            if method == "GET":
                log_event(logger, logging.WARNING, "bilibili.short_url.resolve_failed", url=url, error=e)
    return None


def _resolve_bilibili_short_url_once(url: str, *, method: str) -> str | None:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.bilibili.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
        method=method,
    )
    try:
        with _urllib_urlopen_no_redirect(req, timeout=10) as resp:
            location = resp.headers.get("Location")
            return urllib.parse.urljoin(url, location) if location else resp.geturl()
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            location = e.headers.get("Location")
            if location:
                return urllib.parse.urljoin(url, location)
        raise


def _urllib_urlopen_no_redirect(req: urllib.request.Request, *, timeout: float):
    handlers: list[urllib.request.BaseHandler] = [_NoRedirectHandler()]
    proxy = shared_runtime_proxy_url()
    if proxy == "":
        handlers.append(urllib.request.ProxyHandler({}))
    elif proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    return opener.open(req, timeout=timeout)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _extract_bilibili_page_number(url: str) -> int:
    """Return the selected Bilibili page number from ?p=, defaulting to 1."""
    url = normalize_bilibili_source_url(url)
    for candidate in _candidate_urls(url):
        if "://" not in candidate:
            candidate = f"https://{candidate}"
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").lower()
        if host == "b23.tv" or host.endswith(".b23.tv"):
            resolved = _resolve_bilibili_short_url(candidate)
            if resolved and resolved != candidate:
                return _extract_bilibili_page_number(resolved)
        query = parse_qs(parsed.query)
        for key in ("p", "page"):
            values = query.get(key) or []
            if not values:
                continue
            try:
                page_number = int(values[0])
            except (TypeError, ValueError):
                continue
            return max(page_number, 1)
    return 1


def _select_bilibili_page(view_data: dict[str, Any], page_number: int) -> dict[str, Any]:
    pages = view_data.get("pages") or []
    if not pages:
        return {}
    page_number = max(int(page_number or 1), 1)
    for page in pages:
        if int(page.get("page") or 0) == page_number:
            return page
    index = min(page_number - 1, len(pages) - 1)
    return pages[index]


def _bilibili_canonical_video_url(bvid: str, page_number: int = 1) -> str:
    suffix = f"?p={page_number}" if page_number > 1 else ""
    return f"https://www.bilibili.com/video/{bvid}{suffix}"


def _normalize_bilibili_video_url(url: str) -> str:
    url = normalize_bilibili_source_url(url)
    bvid = _extract_bilibili_bvid(url)
    if bvid:
        return _bilibili_canonical_video_url(bvid, _extract_bilibili_page_number(url))
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def _bilibili_display_title(view_data: dict[str, Any], page: dict[str, Any], page_number: int) -> str:
    title = str(view_data.get("title") or "").strip()
    part = str(page.get("part") or "").strip()
    pages = view_data.get("pages") or []
    if len(pages) > 1 and part and part != title:
        return f"{title} P{page_number} {part}".strip()
    return title or part


class YoutubeNetworkError(RuntimeError):
    """Raised when YouTube is unreachable after yt-dlp's bounded retries."""


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
        "retries": 3,                 # video-data retries
        "fragment_retries": 3,        # DASH fragment retries
        "extractor_retries": 3,       # extractor-level retries
        "socket_timeout": 15,         # cap each TCP attempt
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


def _bili_json_to_srt(body: list[dict]) -> str:
    """Convert Bilibili player/v2 subtitle JSON body to SRT text."""
    def _fmt(t: float) -> str:
        if t < 0:
            t = 0
        h = int(t // 3600); m = int((t % 3600) // 60)
        s = t - h * 3600 - m * 60
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")
    out: list[str] = []
    for i, cue in enumerate(body, 1):
        out.append(str(i))
        out.append(f"{_fmt(float(cue.get('from') or 0))} --> {_fmt(float(cue.get('to') or 0))}")
        out.append(str(cue.get("content") or ""))
        out.append("")
    return "\n".join(out)


def _parse_lang_priority(langs: list[str] | str | None = None) -> list[str]:
    """Normalize subtitle language priority strings like 'zh,en'."""
    if langs is None:
        raw = get_runtime_settings().subtitle_languages
        parts = raw.split(",") if raw else []
    elif isinstance(langs, str):
        parts = langs.split(",")
    else:
        parts = langs
    return [p.strip().lower() for p in parts if p and p.strip()]


def _lang_rank(lang: str, preferred: list[str]) -> int:
    """Return the priority rank for a language code, or a large value."""
    if not preferred:
        return 0
    normalized = (lang or "").lower()
    for idx, want in enumerate(preferred):
        if (
            normalized == want
            or normalized.startswith(want)
            or want.startswith(normalized)
            or want in normalized
        ):
            return idx
    return 999


def _subtitle_track_type(track: dict[str, Any]) -> int:
    """Return Bilibili subtitle type, preserving 0 as manual CC."""
    raw = track.get("type")
    return int(raw) if raw is not None else 1


def _filter_and_sort_subtitle_tracks(
    tracks: list[dict[str, Any]],
    preferred_langs: list[str],
) -> list[dict[str, Any]]:
    """Prefer configured languages first, then manual CC before AI."""
    indexed = list(enumerate(tracks))

    if preferred_langs:
        matched = [
            (i, t) for i, t in indexed
            if _lang_rank(str(t.get("lan") or ""), preferred_langs) < 999
        ]
        if matched:
            indexed = matched

    indexed.sort(key=lambda item: (
        _lang_rank(str(item[1].get("lan") or ""), preferred_langs),
        _subtitle_track_type(item[1]),  # 0=CC, 1=AI
        item[0],
    ))
    return [t for _, t in indexed]


def _empty_subtitle_result(
    *,
    engine: str | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "tracks": [],
        "subtitle_path": None,
        "subtitle_lang": None,
        "subtitle_format": None,
        "subtitle_engine": engine,
        "diagnostics": diagnostics if diagnostics is not None else [],
    }


def _run_subprocess_streamed(
    cmd: list[str],
    cwd: str | None,
    timeout: int,
    log_prefix: str,
    tail: int = 20,
) -> tuple[int, list[str]]:
    """Run a subprocess and relay stdout line-by-line to logger.

    Each non-empty line becomes its own INFO log record, so it gets a real
    timestamp and can be correlated with main-pipeline events. Keeps the last
    `tail` lines around for error reporting.

    Returns (returncode, tail_lines).
    """
    # stdout=PIPE, stderr=STDOUT so we get a single ordered stream
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,  # line-buffered
    )
    buf: deque[str] = deque(maxlen=tail)

    def _reader():
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            # BBDown outputs GBK on Windows; decode with fallback
            try:
                line = raw.decode("utf-8").rstrip()
            except UnicodeDecodeError:
                line = raw.decode("gbk", errors="replace").rstrip()
            if not line:
                continue
            buf.append(line)
            log_event(logger, logging.INFO, "subprocess.output", prefix=log_prefix, line=line)
        proc.stdout.close()

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        t.join(timeout=2)
        raise RuntimeError(f"{log_prefix} timed out after {timeout}s")
    t.join(timeout=2)
    return rc, list(buf)


class YtdlpService:
    def __init__(self):
        self._settings = get_settings()

    def download(self, url: str, output_dir: Path | None = None) -> dict[str, Any]:
        """Download video (1080p preferred) + audio separately.

        Uses BBDown for Bilibili URLs (requires login via BBDown.exe login),
        yt-dlp for everything else.
        """
        url = normalize_bilibili_source_url(url)
        if output_dir is None:
            rt = get_runtime_settings()
            output_dir = Path(rt.data_root).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        if _is_bilibili_article_url(url):
            return self._download_bilibili_article(url, output_dir)
        if _is_bilibili_image_note_url(url):
            return self._download_bilibili_note(url, output_dir)
        if _is_bilibili_url(url):
            return self._download_bilibili(url, output_dir)
        if _is_xiaoyuzhou_url(url):
            return self._download_xiaoyuzhou(url, output_dir)
        if _is_apple_podcast_url(url):
            return self._download_apple_podcast(url, output_dir)
        if _is_xiaohongshu_url(url):
            return self._download_xiaohongshu(url, output_dir)
        if _is_zhihu_url(url):
            return self._download_zhihu(url, output_dir)
        if _is_generic_webpage_url(url):
            return self._download_webpage(url, output_dir)

        import yt_dlp
        outtmpl = str(output_dir / "%(title)s.%(ext)s")

        # Step 1: Download video (1080p preferred, degrade gracefully)
        video_opts = {
            "format": (
                "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
                "bestvideo[height<=1080]+bestaudio/"
                "best[height<=1080]/"
                "bestvideo+bestaudio/"
                "best"
            ),
            "merge_output_format": "mp4",
            "outtmpl": outtmpl,
            "writeinfojson": False,
            "quiet": not self._settings.debug,
            **ytdlp_base_opts(),
            **ytdlp_auth_opts(),
        }

        # Try video+audio download; on failure fall back to audio-only
        video_file = None
        info = None

        log_event(logger, logging.INFO, "download.video.started", url=url)
        try:
            with yt_dlp.YoutubeDL(video_opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as e:
            if is_youtube_network_error(e, url):
                raise _youtube_network_error(url, e) from e
            log_event(logger, logging.WARNING, "download.video.failed", url=url, fallback="audio_only", error=e)

        if info is None:
            # Video download failed entirely — get metadata + audio only
            meta_opts = {
                "outtmpl": outtmpl,
                "skip_download": True,
                "quiet": True,
                **ytdlp_base_opts(),
                **ytdlp_auth_opts(),
            }
            try:
                with yt_dlp.YoutubeDL(meta_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
            except Exception as e:
                if _is_twitter_url(url):
                    return self._download_twitter_webpage_note(url, output_dir, e)
                if is_youtube_network_error(e, url):
                    raise _youtube_network_error(url, e) from e
                raise
            if info is None:
                raise RuntimeError(f"Failed to extract info: {url}")

        title = info.get("title", "unknown")

        # Find the downloaded video file
        if video_file is None:
            video_file = self._find_file(output_dir, title, {".mp4", ".mkv", ".webm"})

        # Step 2: Extract audio from video using ffmpeg
        audio_file = output_dir / f"{title}.wav"
        if video_file and video_file.exists():
            log_event(logger, logging.INFO, "audio.extract.started", input=video_file.name, output=audio_file.name)
            try:
                subprocess.run(
                    ["ffmpeg", "-i", str(video_file), "-vn",
                     "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                     str(audio_file), "-y"],
                    capture_output=True, check=True,
                )
            except subprocess.CalledProcessError as e:
                stderr = e.stderr.decode(errors="replace")[:500] if e.stderr else ""
                log_event(logger, logging.ERROR, "audio.extract.failed", stderr=stderr)
                audio_file = self._download_audio_only(url, output_dir, title)
        else:
            log_event(logger, logging.WARNING, "download.video.missing", fallback="audio_only")
            if _is_twitter_url(url):
                return self._download_twitter_webpage_note(
                    url,
                    output_dir,
                    RuntimeError("yt-dlp did not download a video file for this X/Twitter status"),
                )
            audio_file = self._download_audio_only(url, output_dir, title)
            video_file = None

        # Clean up intermediate files (m4a, webm parts, etc.) but keep video + audio
        keep = {audio_file, video_file} if video_file else {audio_file}
        self._cleanup_temp_files(output_dir, title, keep_files=keep)

        return {
            "url": url,
            "title": title,
            "file_path": str(audio_file) if audio_file and audio_file.exists() else None,
            "video_path": str(video_file) if video_file and video_file.exists() else None,
            "info": info,
        }

    @staticmethod
    def _fetch_bilibili_metadata(url: str) -> dict[str, Any]:
        """Fetch video metadata from Bilibili public API (no auth needed)."""
        import json

        bvid = _extract_bilibili_bvid(url)
        if not bvid:
            log_event(logger, logging.WARNING, "bilibili.bvid.missing", url=url)
            return {"webpage_url": url}

        info: dict[str, Any] = {"webpage_url": url}

        # Fetch video info
        try:
            req = urllib.request.Request(
                f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib_urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read()).get("data", {})

            page_number = _extract_bilibili_page_number(url)
            selected_page = _select_bilibili_page(data, page_number)
            selected_page_number = int(selected_page.get("page") or page_number)
            owner = data.get("owner", {})
            title = _bilibili_display_title(data, selected_page, selected_page_number)
            info.update({
                "title": title or data.get("title"),
                "description": data.get("desc"),
                "uploader": owner.get("name"),
                "uploader_id": str(owner.get("mid", "")) if owner.get("mid") else None,
                "platform": "bilibili_video",
                "content_subtype": "video",
                "duration": selected_page.get("duration") or data.get("duration"),
                "upload_date": datetime.fromtimestamp(data["pubdate"]).strftime("%Y%m%d") if data.get("pubdate") else None,
                "webpage_url": _bilibili_canonical_video_url(bvid, selected_page_number),
                "thumbnail": data.get("pic"),
                "extra": {
                    "platform": "bilibili_video",
                    "bilibili_type": "video",
                    "bvid": bvid,
                    "aid": data.get("aid"),
                    "cid": selected_page.get("cid"),
                    "page_number": selected_page_number,
                    "part": selected_page.get("part"),
                    "pages_count": len(data.get("pages") or []),
                },
            })
        except Exception as e:
            log_event(logger, logging.WARNING, "bilibili.view.failed", bvid=bvid, error=e)

        # Fetch tags
        try:
            req = urllib.request.Request(
                f"https://api.bilibili.com/x/tag/archive/tags?bvid={bvid}",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib_urlopen(req, timeout=10) as resp:
                tag_data = json.loads(resp.read()).get("data", [])
            info["tags"] = [t["tag_name"] for t in tag_data if t.get("tag_name")]
        except Exception as e:
            log_event(logger, logging.WARNING, "bilibili.tags.failed", bvid=bvid, error=e)

        return info

    def _download_bilibili_article(self, url: str, output_dir: Path) -> dict[str, Any]:
        """Process a Bilibili article through the generic webpage path."""
        from app.services.ingestion.platform.webpage.api import download_webpage

        info = download_webpage(url, output_dir)
        info["platform"] = "bilibili_opus"
        info["content_subtype"] = "text_note"
        extra = info.setdefault("extra", {})
        if isinstance(extra, dict):
            extra["platform"] = "bilibili_opus"
            extra["bilibili_type"] = "article"
            extra.setdefault("source_platform", "webpage")
        return {
            "url": url,
            "title": info.get("title", "bilibili_article"),
            "file_path": None,
            "video_path": None,
            "info": info,
        }

    def _download_bilibili_note(self, url: str, output_dir: Path) -> dict[str, Any]:
        """Fetch Bilibili opus/dynamic metadata; images are downloaded by the note branch."""
        from app.services.ingestion.platform.bilibili.note import fetch_metadata as fetch_bilibili_note

        output_dir.mkdir(parents=True, exist_ok=True)
        info = fetch_bilibili_note(url)
        return {
            "url": url,
            "title": info.get("title", "bilibili_opus"),
            "file_path": None,
            "video_path": None,
            "info": info,
        }

    def _download_bilibili(self, url: str, output_dir: Path) -> dict[str, Any]:
        """Download Bilibili video using native DASH API (replaces BBDown)."""
        from app.services.ingestion.platform.bilibili.dash import download_video, extract_audio
        from app.services.ingestion.platform.bilibili.auth import is_logged_in

        url = _normalize_bilibili_video_url(url)

        rt = get_runtime_settings()
        qn = rt.bilibili_preferred_quality if is_logged_in() else 16
        if not is_logged_in() and qn > 16:
            log_event(logger, logging.WARNING, "bilibili.quality.forced", reason="not_logged_in", qn=16)
            qn = 16

        bvid = _extract_bilibili_bvid(url)
        if not bvid:
            raise RuntimeError(f"Cannot extract Bilibili video id from URL: {url}")

        page_number = _extract_bilibili_page_number(url)
        log_event(logger, logging.INFO, "bilibili.download.started", bvid=bvid, qn=qn, page=page_number)
        video_file, info = download_video(bvid, output_dir, qn=qn, page_number=page_number)

        title = video_file.stem
        audio_file = output_dir / f"{title}.wav"
        extract_audio(video_file, audio_file)

        meta = self._fetch_bilibili_metadata(url)
        meta["title"] = info.get("display_title") or info.get("title") or title

        return {
            "url": url,
            "title": meta["title"],
            "file_path": str(audio_file),
            "video_path": str(video_file),
            "info": meta,
        }

    def _download_xiaoyuzhou(self, url: str, output_dir: Path) -> dict[str, Any]:
        """Download a Xiaoyuzhou podcast episode via page metadata + audio URL."""
        from app.services.ingestion.platform.xiaoyuzhou.api import (
            download_audio,
            fetch_metadata as fetch_xiaoyuzhou_metadata,
        )

        log_event(logger, logging.INFO, "xiaoyuzhou.metadata.fetch_started", url=url)
        info = fetch_xiaoyuzhou_metadata(url)
        audio_file, source_audio = download_audio(info, output_dir)
        return {
            "url": url,
            "title": info.get("title", "xiaoyuzhou_episode"),
            "file_path": str(audio_file),
            "video_path": None,
            "source_audio_path": str(source_audio) if source_audio else None,
            "info": info,
        }

    def _download_apple_podcast(self, url: str, output_dir: Path) -> dict[str, Any]:
        """Download an Apple Podcasts episode by resolving RSS enclosure URL."""
        from app.services.ingestion.platform.apple_podcast.api import (
            download_audio,
            fetch_metadata as fetch_apple_metadata,
        )

        log_event(logger, logging.INFO, "apple_podcast.metadata.fetch_started", url=url)
        info = fetch_apple_metadata(url)
        audio_file, source_audio = download_audio(info, output_dir)
        return {
            "url": url,
            "title": info.get("title", "apple_podcast_episode"),
            "file_path": str(audio_file),
            "video_path": None,
            "source_audio_path": str(source_audio) if source_audio else None,
            "info": info,
        }

    def _download_xiaohongshu(self, url: str, output_dir: Path) -> dict[str, Any]:
        """Download a Xiaohongshu note. Video notes are downloaded + WAV extracted;
        image notes return metadata only (images are fetched later by the pipeline)."""
        from app.services.ingestion.platform.xiaohongshu.api import (
            download_video,
            fetch_metadata as fetch_xiaohongshu_metadata,
        )

        log_event(logger, logging.INFO, "xiaohongshu.metadata.fetch_started", url=url)
        info = fetch_xiaohongshu_metadata(url)
        is_video = (info.get("extra") or {}).get("is_video", False)

        if not is_video:
            # Image note: return metadata only; pipeline will download images + run VLM
            return {
                "url": url,
                "title": info.get("title", "xiaohongshu_image"),
                "file_path": None,
                "video_path": None,
                "info": info,
            }

        video_file, audio_file = download_video(info, output_dir)
        return {
            "url": url,
            "title": info.get("title", "xiaohongshu_video"),
            "file_path": str(audio_file),
            "video_path": str(video_file),
            "info": info,
        }

    def _download_zhihu(self, url: str, output_dir: Path) -> dict[str, Any]:
        """Fetch a Zhihu pin/answer as a text note. No media file is downloaded."""
        from app.services.ingestion.platform.zhihu.api import fetch_metadata as fetch_zhihu_metadata

        log_event(logger, logging.INFO, "zhihu.metadata.fetch_started", url=url)
        info = fetch_zhihu_metadata(url)
        return {
            "url": url,
            "title": info.get("title", "zhihu_note"),
            "file_path": None,
            "video_path": None,
            "info": info,
        }

    def _download_webpage(self, url: str, output_dir: Path) -> dict[str, Any]:
        """Fetch a generic web page as a text note with localized media."""
        from app.services.ingestion.platform.webpage.api import download_webpage

        log_event(logger, logging.INFO, "webpage.metadata.fetch_started", url=url)
        info = download_webpage(url, output_dir)
        return {
            "url": url,
            "title": info.get("title", "webpage"),
            "file_path": None,
            "video_path": None,
            "info": info,
        }

    def _download_twitter_webpage_note(
        self,
        url: str,
        output_dir: Path,
        fallback_error: Exception | None = None,
    ) -> dict[str, Any]:
        """Fallback for X/Twitter status/article links unsupported by yt-dlp."""
        output_dir.mkdir(parents=True, exist_ok=True)
        info = self._fetch_twitter_webpage_note(url, fallback_error=fallback_error)
        extra = info.setdefault("extra", {})
        external_article_url = extra.get("external_article_url") if isinstance(extra, dict) else None
        if external_article_url and extra.get("content_kind") == "long_article":
            try:
                from app.services.ingestion.platform.webpage.api import download_webpage

                article_info = download_webpage(str(external_article_url), output_dir)
                article_extra = article_info.setdefault("extra", {})
                if not isinstance(article_extra, dict):
                    article_extra = {}
                    article_info["extra"] = article_extra
                external_scrape_engine = article_extra.get("scrape_engine")
                article_extra.update(extra)
                article_extra.update({
                    "platform": "twitter",
                    "external_article_url": str(external_article_url),
                    "external_scrape_engine": external_scrape_engine,
                    "article_body_status": "complete",
                    "article_body_engine": external_scrape_engine or "webpage",
                    "source_markdown_path": str(output_dir / "source.md"),
                })
                article_info.update({
                    "title": extra.get("article_title") or article_info.get("title") or info.get("title"),
                    "original_url": url,
                    "platform": "twitter",
                    "content_subtype": "text_note",
                    "uploader": info.get("uploader") or article_info.get("uploader"),
                })
                info = article_info
                extra = article_extra
                log_event(
                    logger,
                    logging.INFO,
                    "twitter.article.external_fetched",
                    status_url=url,
                    article_url=external_article_url,
                )
            except Exception as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "twitter.article.external_fetch_failed",
                    status_url=url,
                    article_url=external_article_url,
                    error=exc,
                )
        if (
            isinstance(extra, dict)
            and extra.get("content_kind") == "long_article"
            and extra.get("article_body_status") != "complete"
        ):
            try:
                from app.services.ingestion.platform.webpage.api import download_webpage

                article_info = download_webpage(url, output_dir)
                article_markdown = str(article_info.get("description") or "").strip()
                preview_markdown = str(info.get("description") or "").strip()
                if len(article_markdown) <= max(800, len(preview_markdown) * 2):
                    raise RuntimeError("Defuddle returned only the X article preview")
                article_extra = article_info.setdefault("extra", {})
                if not isinstance(article_extra, dict):
                    article_extra = {}
                    article_info["extra"] = article_extra
                article_scrape_engine = article_extra.get("scrape_engine")
                article_extra.update(extra)
                article_extra.update({
                    "platform": "twitter",
                    "status_url": url,
                    "article_body_status": "complete",
                    "article_body_engine": article_scrape_engine or "defuddle",
                    "source_markdown_path": str(output_dir / "source.md"),
                })
                article_info.update({
                    "title": extra.get("article_title") or article_info.get("title") or info.get("title"),
                    "original_url": url,
                    "webpage_url": url,
                    "platform": "twitter",
                    "content_subtype": "text_note",
                    "uploader": info.get("uploader") or article_info.get("uploader"),
                })
                info = article_info
                extra = article_extra
                log_event(
                    logger,
                    logging.INFO,
                    "twitter.article.status_fetched",
                    status_url=url,
                    engine=article_scrape_engine,
                    markdown_chars=len(article_markdown),
                )
            except Exception as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "twitter.article.status_fetch_failed",
                    status_url=url,
                    error=exc,
                )
        if isinstance(extra, dict):
            extra["source_markdown_path"] = str(output_dir / "source.md")
            extra.setdefault("image_count", 0)
        source_path = output_dir / "source.md"
        if not source_path.exists():
            source_path.write_text(str(info.get("description") or ""), encoding="utf-8")
        return {
            "url": url,
            "title": info.get("title", "x_status"),
            "file_path": None,
            "video_path": None,
            "info": info,
        }

    def _fetch_twitter_webpage_note(
        self,
        url: str,
        fallback_error: Exception | None = None,
    ) -> dict[str, Any]:
        page = self._scrape_twitter_page(url)
        resolved_url = page.get("url") or url
        body_text = _clean_twitter_text(str(page.get("text") or ""))
        article_body = _clean_twitter_text(str(page.get("article_body") or ""))
        article_title = _extract_twitter_article_title(page.get("article_text") or body_text)
        external_article_url = _extract_twitter_external_article_url(body_text)
        article_url = str(page.get("article_url") or "")
        is_x_article = bool(re.search(r"(?:x|twitter)\.com/i/article/\d+", article_url, re.IGNORECASE))
        title = article_title or _clean_twitter_title(str(page.get("title") or "")) or "X post"
        image_urls = _dedupe_twitter_image_urls(page.get("image_urls"))
        markdown_parts = [f"# {title}", f"Source: {resolved_url}"]
        if article_body:
            markdown_parts.append(article_body)
        elif body_text:
            markdown_parts.append(body_text)
        for idx, image_url in enumerate(image_urls, start=1):
            markdown_parts.append(f"![X image {idx}]({image_url})")
        markdown = "\n\n".join(markdown_parts).strip() + "\n"
        uploader = page.get("uploader")
        thumbnail = image_urls[0] if image_urls else page.get("thumbnail")
        extra = {
            "platform": "twitter",
            "scrape_engine": "playwright",
            "twitter_type": "article" if is_x_article else (page.get("type") or "status"),
            "content_kind": "long_article" if is_x_article else "status",
            "article_url": article_url,
            "article_title": article_title,
            "article_body_status": "complete" if article_body else ("auth_required" if is_x_article else "not_applicable"),
            "external_article_url": external_article_url,
            "status_url": resolved_url,
            "image_urls": image_urls,
            "image_url_candidates": [[url] for url in image_urls],
            "image_count": len(image_urls),
        }
        if fallback_error:
            extra["ytdlp_error"] = str(fallback_error)
        return {
            "id": resolved_url,
            "title": title,
            "description": markdown,
            "webpage_url": resolved_url,
            "original_url": url,
            "platform": "twitter",
            "content_subtype": "text_note" if is_x_article else ("image_note" if image_urls else "text_note"),
            "media_type": "image",
            "uploader": uploader,
            "thumbnail": thumbnail,
            "extra": extra,
        }

    def _scrape_twitter_page(self, url: str) -> dict[str, Any]:
        if not bool(getattr(get_runtime_settings(), "playwright_enabled", True)):
            raise RuntimeError("Playwright browser extraction is disabled")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError("Playwright is required for X article/status fallback.") from e

        from app.services.ingestion.platform.twitter.api import storage_state_path

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context_kwargs = {
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
                ),
                "locale": "en-US",
            }
            auth_path = storage_state_path()
            if auth_path.exists():
                context_kwargs["storage_state"] = str(auth_path)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
            data = page.evaluate(
                """() => {
                    const meta = (name, property = name) => {
                        const byName = document.querySelector(`meta[name="${name}"]`);
                        const byProp = document.querySelector(`meta[property="${property}"]`);
                        return (byName?.getAttribute("content") || byProp?.getAttribute("content") || "").trim();
                    };
                    const links = Array.from(document.querySelectorAll("a")).map((a) => ({
                        href: a.href,
                        text: (a.innerText || "").trim(),
                    }));
                    const imageUrls = [];
                    const addImage = (value) => {
                        const url = (value || "").trim();
                        if (!/pbs\\.twimg\\.com\\/media\\//i.test(url)) return;
                        if (!imageUrls.includes(url)) imageUrls.push(url);
                    };
                    const addSrcset = (value) => {
                        (value || "").split(",").forEach((entry) => {
                            addImage(entry.trim().split(/\\s+/)[0]);
                        });
                    };
                    addImage(meta("twitter:image"));
                    addImage(meta("og:image"));
                    addImage(meta("image", "og:image"));
                    Array.from(document.querySelectorAll("article img, img")).forEach((img) => {
                        addImage(img.currentSrc || img.src);
                        addSrcset(img.getAttribute("srcset") || "");
                    });
                    const article = links.find((item) => /\\/i\\/article\\/\\d+/.test(item.href));
                    const articleText = article?.text || "";
                    const author = Array.from(document.querySelectorAll('a[href^="/"], a[href^="https://x.com/"]'))
                        .map((a) => (a.innerText || "").trim())
                        .find((text) => text && !text.includes("\\n") && !text.startsWith("@"));
                    return {
                        url: location.href,
                        title: document.title || meta("title", "og:title"),
                        text: document.body.innerText || "",
                        uploader: author || "",
                        thumbnail: meta("twitter:image", "og:image"),
                        image_urls: imageUrls,
                        article_url: article?.href || "",
                        article_text: articleText,
                        type: article ? "article" : (imageUrls.length ? "image_status" : "status"),
                    };
                }"""
            )
            article_url = str(data.get("article_url") or "") if isinstance(data, dict) else ""
            if article_url and auth_path.exists():
                article_page = context.new_page()
                article_page.goto(article_url, wait_until="domcontentloaded", timeout=45000)
                article_page.wait_for_timeout(4000)
                if "/i/flow/login" not in article_page.url:
                    article_data = article_page.evaluate(
                        """() => {
                            const clean = (value) => (value || "").replace(/\\n{3,}/g, "\\n\\n").trim();
                            const candidates = Array.from(document.querySelectorAll(
                                'main article, main [data-testid="article"], main [role="article"], main'
                            )).map((node) => clean(node.innerText));
                            const body = candidates.sort((a, b) => b.length - a.length)[0] || "";
                            const imageUrls = Array.from(document.querySelectorAll('main img'))
                                .map((img) => img.currentSrc || img.src || "")
                                .filter((src) => /pbs\\.twimg\\.com\\/media\\//i.test(src));
                            return { body, image_urls: [...new Set(imageUrls)] };
                        }"""
                    )
                    if isinstance(article_data, dict):
                        article_body = str(article_data.get("body") or "").strip()
                        preview = str(data.get("article_text") or "")
                        if len(article_body) > max(600, len(preview) * 2):
                            data["article_body"] = article_body
                            data["image_urls"] = _dedupe_twitter_image_urls(
                                [*(data.get("image_urls") or []), *(article_data.get("image_urls") or [])]
                            )
            browser.close()
        return data if isinstance(data, dict) else {}

    def _download_bilibili_subtitle(
        self,
        url: str,
        output_dir: Path,
        langs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Download ALL usable Bilibili subtitle tracks via the wbi-signed player/v2 API.

        Uses wbi-signed /x/player/wbi/v2 (authenticated via SESSDATA from settings or
        BBDown.data fallback) to avoid the stale-URL bug in the unsigned endpoint.

        Returns:
            {
                "tracks": [{"path": str, "lang": str, "format": "srt", "type": "cc"|"ai"}, ...],
                # Back-compat single-track fields (first good track):
                "subtitle_path": str|None,
                "subtitle_lang": str|None,
                "subtitle_format": "srt"|None,
            }
        """
        import json
        import urllib.request

        rt = get_runtime_settings()
        engine = rt.bilibili_subtitle_engine or "native_wbi"
        strict_validation = bool(rt.bilibili_subtitle_strict_validation)
        min_coverage = float(rt.bilibili_subtitle_min_coverage)
        preferred_langs = _parse_lang_priority(langs)
        diagnostics: list[dict[str, Any]] = []

        empty = _empty_subtitle_result(engine=engine, diagnostics=diagnostics)

        url = _normalize_bilibili_video_url(url)

        bvid = _extract_bilibili_bvid(url)
        if not bvid:
            log_event(logger, logging.WARNING, "bilibili.bvid.missing", url=url)
            diagnostics.append({"stage": "resolve", "status": "failed", "reason": "missing_bvid"})
            return empty

        output_dir.mkdir(parents=True, exist_ok=True)

        # Try new wbi-signed path first; fall back to old unsigned path on import error
        try:
            from app.services.ingestion.platform.bilibili.api import (
                player_v2 as bili_player_v2,
                view as bili_view,
                subtitle_url_matches_video,
            )
        except ImportError as e:
            log_event(logger, logging.WARNING, "bilibili.api.import_failed", error=e)
            diagnostics.append({
                "stage": "import",
                "status": "failed",
                "reason": "native_api_import_failed",
                "detail": str(e),
            })
            if not rt.bilibili_subtitle_allow_legacy_fallback:
                return empty
            log_event(logger, logging.WARNING, "bilibili.subtitle.legacy_fallback")
            return self._download_bilibili_subtitle_legacy(url, output_dir, bvid, preferred_langs)

        # --- Fetch video metadata (aid, cid, duration) ---
        try:
            view_data = bili_view(bvid)
        except Exception as e:
            log_event(logger, logging.WARNING, "bilibili.view.failed", bvid=bvid, error=e)
            diagnostics.append({"stage": "view", "status": "failed", "reason": "api_error", "detail": str(e)})
            return empty

        aid = int(view_data.get("aid") or 0)
        pages = view_data.get("pages") or []
        if not pages:
            log_event(logger, logging.WARNING, "bilibili.view.no_pages", bvid=bvid)
            diagnostics.append({"stage": "view", "status": "failed", "reason": "no_pages"})
            return empty
        page_number = _extract_bilibili_page_number(url)
        page = _select_bilibili_page(view_data, page_number)
        selected_page_number = int(page.get("page") or page_number)
        cid = int(page.get("cid") or 0)
        video_duration = float(page.get("duration") or view_data.get("duration") or 0)

        if not aid or not cid:
            log_event(logger, logging.WARNING, "bilibili.view.missing_ids", bvid=bvid, aid=aid, cid=cid)
            diagnostics.append({
                "stage": "view",
                "status": "failed",
                "reason": "missing_aid_or_cid",
                "aid": aid,
                "cid": cid,
                "page": selected_page_number,
            })
            return empty

        # --- Fetch subtitle track list via wbi-signed endpoint ---
        try:
            pv2_data = bili_player_v2(bvid, aid, cid)
        except Exception as e:
            log_event(logger, logging.WARNING, "bilibili.player_wbi.failed", bvid=bvid, error=e)
            diagnostics.append({
                "stage": "player_wbi_v2",
                "status": "failed",
                "reason": "api_error",
                "detail": str(e),
            })
            return empty

        tracks = ((pv2_data.get("subtitle") or {}).get("subtitles")) or []
        if not tracks:
            reason = "login_required" if pv2_data.get("need_login_subtitle") else "no_tracks"
            log_event(logger, logging.INFO, "bilibili.subtitle.empty", bvid=bvid, reason=reason)
            diagnostics.append({"stage": "track_list", "status": "empty", "reason": reason})
            return empty

        usable = [t for t in tracks if t.get("subtitle_url")]
        if not usable:
            log_event(logger, logging.INFO, "bilibili.subtitle.empty", bvid=bvid, reason="all_tracks_missing_url", tracks=len(tracks))
            diagnostics.append({
                "stage": "track_list",
                "status": "empty",
                "reason": "all_tracks_missing_url",
                "track_count": len(tracks),
            })
            return empty
        usable = _filter_and_sort_subtitle_tracks(usable, preferred_langs)

        saved_tracks: list[dict[str, Any]] = []
        seen_langs: set[str] = set()
        for track in usable:
            sub_url = track["subtitle_url"]
            if sub_url.startswith("//"):
                sub_url = "https:" + sub_url
            lan = track.get("lan", "unknown")
            t_type = _subtitle_track_type(track)
            t_label = "CC" if t_type == 0 else "AI"

            # Prefer CC over AI when same language has both
            if lan in seen_langs:
                continue

            # Validate that the blob URL encodes this video's aid+cid
            matches_video = subtitle_url_matches_video(sub_url, aid, cid)
            if strict_validation and not matches_video:
                log_event(
                    logger,
                    logging.WARNING,
                    "bilibili.subtitle.validation_failed",
                    bvid=bvid,
                    lang=lan,
                    type=t_label.lower(),
                    reason="aid_cid_mismatch",
                    aid=aid,
                    cid=cid,
                )
                diagnostics.append({
                    "stage": "validate_url",
                    "status": "skipped",
                    "reason": "aid_cid_mismatch",
                    "lang": lan,
                    "type": t_label.lower(),
                    "aid": aid,
                    "cid": cid,
                    "url_tail": sub_url.split("/")[-1].split("?")[0],
                })
                continue

            try:
                req = urllib.request.Request(sub_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib_urlopen(req, timeout=15) as resp:
                    sub_json = json.loads(resp.read())
            except Exception as e:
                log_event(logger, logging.WARNING, "bilibili.subtitle.download_failed", bvid=bvid, page=selected_page_number, lang=lan, type=t_label.lower(), error=e)
                diagnostics.append({
                    "stage": "download",
                    "status": "failed",
                    "reason": "download_error",
                    "lang": lan,
                    "type": t_label.lower(),
                    "detail": str(e),
                })
                continue

            body = sub_json.get("body") or []
            if len(body) < 3:
                log_event(logger, logging.INFO, "bilibili.subtitle.validation_skipped", bvid=bvid, lang=lan, type=t_label.lower(), reason="too_few_cues", cues=len(body))
                diagnostics.append({
                    "stage": "validate_body",
                    "status": "skipped",
                    "reason": "too_few_cues",
                    "lang": lan,
                    "type": t_label.lower(),
                    "cue_count": len(body),
                })
                continue

            coverage = None
            if video_duration > 0:
                last_t = float(body[-1].get("from") or 0)
                coverage = last_t / video_duration
                if coverage < min_coverage:
                    log_event(
                        logger,
                        logging.WARNING,
                        "bilibili.subtitle.validation_failed",
                        bvid=bvid,
                        lang=lan,
                        type=t_label.lower(),
                        reason="low_coverage",
                        coverage=round(coverage, 4),
                        min_coverage=min_coverage,
                        last_cue_seconds=round(last_t),
                        video_duration_seconds=round(video_duration),
                    )
                    diagnostics.append({
                        "stage": "validate_body",
                        "status": "skipped",
                        "reason": "low_coverage",
                        "lang": lan,
                        "type": t_label.lower(),
                        "coverage": round(coverage, 4),
                        "min_coverage": min_coverage,
                        "last_cue_seconds": last_t,
                        "video_duration_seconds": video_duration,
                    })
                    continue

            srt_path = output_dir / f"{bvid}.{lan}.srt"
            srt_path.write_text(_bili_json_to_srt(body), encoding="utf-8")
            log_event(logger, logging.INFO, "bilibili.subtitle.saved", bvid=bvid, page=selected_page_number, lang=lan, type=t_label.lower(), cues=len(body), path=srt_path)
            saved_tracks.append({
                "path": str(srt_path),
                "lang": lan,
                "format": "srt",
                "type": "cc" if t_type == 0 else "ai",
                "source_engine": engine,
                "validation": {
                    "strict_url_match": strict_validation,
                    "url_matches_video": matches_video,
                    "coverage": round(coverage, 4) if coverage is not None else None,
                    "min_coverage": min_coverage,
                    "aid": aid,
                    "cid": cid,
                },
            })
            seen_langs.add(lan)

        if not saved_tracks:
            log_event(logger, logging.INFO, "bilibili.subtitle.empty", bvid=bvid, reason="all_validation_failed", tracks=len(usable))
            return empty

        first = saved_tracks[0]
        return {
            "tracks": saved_tracks,
            "subtitle_path": first["path"],
            "subtitle_lang": first["lang"],
            "subtitle_format": first["format"],
            "subtitle_engine": engine,
            "diagnostics": diagnostics,
        }

    def _download_bilibili_subtitle_legacy(
        self,
        url: str,
        output_dir: Path,
        bvid: str,
        preferred_langs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Legacy fallback: unsigned /x/player/v2 (used only if new api.py fails to import)."""
        import json
        import urllib.request

        engine = "legacy_unsigned"
        empty = _empty_subtitle_result(engine=engine)

        cookie_file = _BBDOWN_DIR / "BBDown.data"
        cookie = ""
        if cookie_file.exists():
            try:
                cookie = cookie_file.read_text(encoding="utf-8", errors="ignore").strip()
            except Exception as e:
                log_event(logger, logging.WARNING, "bbdown.cookie.read_failed", path=cookie_file, error=e)

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": f"https://www.bilibili.com/video/{bvid}",
        }
        if cookie:
            headers["Cookie"] = cookie

        def _get_json(api_url: str, timeout: int = 10) -> dict | None:
            try:
                req = urllib.request.Request(api_url, headers=headers)
                with urllib_urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read())
            except Exception as e:
                log_event(logger, logging.WARNING, "bilibili.legacy_api.failed", api=api_url[:60], error=e)
                return None

        view_resp = _get_json(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
        if not view_resp or view_resp.get("code") != 0:
            return empty
        pages = view_resp["data"].get("pages") or []
        if not pages:
            return empty
        page_number = _extract_bilibili_page_number(url)
        page = _select_bilibili_page(view_resp["data"], page_number)
        cid = page["cid"]
        video_duration = float(page.get("duration") or view_resp["data"].get("duration") or 0)

        pv2 = _get_json(f"https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}")
        if not pv2 or pv2.get("code") != 0:
            return empty
        tracks = ((pv2.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
        if not tracks:
            return empty

        usable = [t for t in tracks if t.get("subtitle_url")]
        if not usable:
            return empty
        usable = _filter_and_sort_subtitle_tracks(usable, preferred_langs or [])

        saved_tracks: list[dict[str, Any]] = []
        seen_langs: set[str] = set()
        for track in usable:
            sub_url = track["subtitle_url"]
            if sub_url.startswith("//"):
                sub_url = "https:" + sub_url
            lan = track.get("lan", "unknown")
            t_type = _subtitle_track_type(track)
            t_label = "CC" if t_type == 0 else "AI"
            if lan in seen_langs:
                continue
            try:
                req = urllib.request.Request(sub_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib_urlopen(req, timeout=15) as resp:
                    sub_json = json.loads(resp.read())
            except Exception as e:
                log_event(logger, logging.WARNING, "bilibili.subtitle.download_failed", bvid=bvid, lang=lan, type=t_label.lower(), engine=engine, error=e)
                continue
            body = sub_json.get("body") or []
            if len(body) < 3:
                continue
            if video_duration > 0:
                last_t = float(body[-1].get("from") or 0)
                if (last_t / video_duration) < 0.6:
                    continue
            srt_path = output_dir / f"{bvid}.{lan}.srt"
            srt_path.write_text(_bili_json_to_srt(body), encoding="utf-8")
            saved_tracks.append({
                "path": str(srt_path),
                "lang": lan,
                "format": "srt",
                "type": "cc" if t_type == 0 else "ai",
                "source_engine": engine,
                "validation": {
                    "strict_url_match": False,
                    "url_matches_video": None,
                    "coverage": round(last_t / video_duration, 4) if video_duration > 0 else None,
                    "min_coverage": 0.6,
                },
            })
            seen_langs.add(lan)

        if not saved_tracks:
            return empty
        first = saved_tracks[0]
        return {
            "tracks": saved_tracks,
            "subtitle_path": first["path"],
            "subtitle_lang": first["lang"],
            "subtitle_format": first["format"],
            "subtitle_engine": engine,
            "diagnostics": [],
        }

    def fetch_metadata(self, url: str) -> dict[str, Any]:
        """Fetch video metadata without downloading the video.

        Returns the same info dict format as download() so extract_metadata() works.
        Bilibili: uses public API. YouTube/other: uses yt-dlp --skip-download.
        """
        url = normalize_bilibili_source_url(url)
        if _is_bilibili_article_url(url):
            from app.services.ingestion.platform.webpage.api import (
                fetch_metadata as fetch_webpage_metadata,
            )

            info = fetch_webpage_metadata(url)
            info["platform"] = "bilibili_opus"
            info["content_subtype"] = "text_note"
            extra = info.setdefault("extra", {})
            if isinstance(extra, dict):
                extra["platform"] = "bilibili_opus"
                extra["bilibili_type"] = "article"
            return info
        if _is_bilibili_image_note_url(url):
            from app.services.ingestion.platform.bilibili.note import (
                fetch_metadata as fetch_bilibili_note_metadata,
            )

            return fetch_bilibili_note_metadata(url)
        if _is_bilibili_url(url):
            info = self._fetch_bilibili_metadata(url)
            return info
        if _is_xiaoyuzhou_url(url):
            from app.services.ingestion.platform.xiaoyuzhou.api import (
                fetch_metadata as fetch_xiaoyuzhou_metadata,
            )
            return fetch_xiaoyuzhou_metadata(url)
        if _is_apple_podcast_url(url):
            from app.services.ingestion.platform.apple_podcast.api import (
                fetch_metadata as fetch_apple_metadata,
            )
            return fetch_apple_metadata(url)
        if _is_xiaohongshu_url(url):
            from app.services.ingestion.platform.xiaohongshu.api import (
                fetch_metadata as fetch_xiaohongshu_metadata,
            )
            return fetch_xiaohongshu_metadata(url)
        if _is_zhihu_url(url):
            from app.services.ingestion.platform.zhihu.api import (
                fetch_metadata as fetch_zhihu_metadata,
            )
            return fetch_zhihu_metadata(url)
        if _is_generic_webpage_url(url):
            from app.services.ingestion.platform.webpage.api import (
                fetch_metadata as fetch_webpage_metadata,
            )
            return fetch_webpage_metadata(url)

        import yt_dlp

        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            **ytdlp_base_opts(),
            **ytdlp_auth_opts(),
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            if _is_twitter_url(url):
                return self._fetch_twitter_webpage_note(url, fallback_error=e)
            if is_youtube_network_error(e, url):
                raise _youtube_network_error(url, e) from e
            raise
        if info is None:
            raise RuntimeError(f"Failed to extract metadata: {url}")
        return info

    def _download_audio_only(self, url: str, output_dir: Path, title: str) -> Path:
        """Fallback: download audio only using yt-dlp."""
        import yt_dlp

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
            "writeinfojson": False,
            "quiet": not self._settings.debug,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
                "preferredquality": "192",
            }],
            **ytdlp_base_opts(),
            **ytdlp_auth_opts(),
        }

        log_event(logger, logging.INFO, "download.audio_only.started", url=url)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(url, download=True)
        except Exception as e:
            if is_youtube_network_error(e, url):
                raise _youtube_network_error(url, e) from e
            raise

        audio_file = output_dir / f"{title}.wav"
        if not audio_file.exists():
            matching = list(output_dir.glob("*.wav"))
            if matching:
                audio_file = max(matching, key=lambda p: p.stat().st_mtime)
        return audio_file

    def _find_file(self, directory: Path, title: str, extensions: set[str]) -> Path | None:
        """Find a file matching title with given extensions.

        Prefers the merged output (exact title match) over intermediate
        format-specific files like '.f399.mp4' or '.f140.m4a' that yt-dlp
        creates before merging.
        """
        import re

        # 1. Exact title match — this is the merged output
        for ext in extensions:
            candidate = directory / f"{title}{ext}"
            if candidate.exists():
                return candidate

        # 2. Fallback: most recent file with matching extension,
        #    but skip intermediate format files (.fNNN.ext) and .part files
        candidates = []
        for ext in extensions:
            for f in directory.glob(f"*{ext}"):
                if f.name.endswith(".part"):
                    continue
                # Skip yt-dlp intermediate files like 'title.f399.mp4'
                if re.search(r'\.f\d+\.[^.]+$', f.name):
                    continue
                candidates.append(f)
        if candidates:
            return max(candidates, key=lambda p: p.stat().st_mtime)
        return None

    def _cleanup_temp_files(self, output_dir: Path, title: str, keep_files: set[Path | None] | None = None):
        """Clean up temporary files after download."""
        import re
        keep = {f for f in (keep_files or set()) if f is not None}
        temp_extensions = {'.m4a', '.webm', '.part', '.ytdl', '.info.json', '.json'}

        for file in output_dir.iterdir():
            if not file.is_file():
                continue
            if file in keep:
                continue
            if title not in file.stem and not file.name.endswith('.info.json'):
                continue

            is_temp = (
                file.suffix in temp_extensions
                or file.name.endswith('.info.json')
                or file.name.endswith('.part')
                # yt-dlp intermediate format files: 'title.f399.mp4', 'title.f140.m4a'
                or re.search(r'\.f\d+\.[^.]+$', file.name)
            )
            if is_temp:
                try:
                    file.unlink()
                    log_event(logger, logging.INFO, "cleanup.temp_file.deleted", path=file)
                except Exception as e:
                    log_event(logger, logging.WARNING, "cleanup.temp_file.delete_failed", path=file, error=e)

    def extract_metadata(self, info: dict[str, Any], file_path: str | None = None) -> MediaMetadata:
        """
        Extract comprehensive metadata from yt-dlp info dict.
        """
        upload_date = None
        if info.get("upload_date"):
            try:
                upload_date = datetime.strptime(info["upload_date"], "%Y%m%d")
            except ValueError:
                pass
        elif info.get("timestamp"):
            try:
                upload_date = datetime.fromtimestamp(int(info["timestamp"]))
            except (TypeError, ValueError, OSError):
                pass

        file_hash = None
        if file_path and Path(file_path).exists():
            file_hash = self._compute_hash(file_path)

        tags = []
        if info.get("tags"):
            tags.extend(info["tags"])
        if info.get("categories"):
            tags.extend(info["categories"])
        seen = set()
        unique_tags = []
        for tag in tags:
            if tag and tag not in seen:
                seen.add(tag)
                unique_tags.append(tag)

        chapters = []
        if info.get("chapters"):
            for ch in info["chapters"]:
                if ch.get("title") and ch.get("start_time") is not None:
                    chapters.append(ChapterInfo(
                        title=ch["title"],
                        start_time=float(ch["start_time"])
                    ))

        description = info.get("description")
        content_subtype = str(info.get("content_subtype") or "").strip().lower()
        if (
            description
            and len(description) > 5000
            and content_subtype not in {"image_note", "text_note"}
        ):
            description = description[:5000] + "..."

        media_type = MediaType.VIDEO
        raw_media_type = str(info.get("media_type") or "").lower()
        if raw_media_type == "podcast":
            media_type = MediaType.PODCAST
        elif (
            raw_media_type == "audio"
            or str(info.get("ext") or "").lower() in {"mp3", "m4a", "wav", "flac", "ogg"}
        ):
            media_type = MediaType.AUDIO
        elif raw_media_type == "image":
            media_type = MediaType.OTHER
        elif raw_media_type == "video":
            media_type = MediaType.VIDEO

        # Derive platform slug from extractor key
        extractor_key = str(info.get("extractor_key") or info.get("extractor") or "").lower()
        platform_map = {
            "bilibili": "bilibili",
            "youtube": "youtube",
            "youtubeTab": "youtube",
            "twitter": "twitter",
            "douyin": "douyin",
            "tiktok": "douyin",
            "weibo": "weibo",
            "zhihu": "zhihu",
        }
        platform = next(
            (v for k, v in platform_map.items() if k.lower() in extractor_key),
            "generic" if extractor_key else None,
        )
        # Prefer explicit top-level platform field (set by custom ingestors like xhs/xiaoyuzhou/bilibili)
        if info.get("platform"):
            platform = info["platform"]
        elif isinstance(info.get("extra"), dict) and info["extra"].get("platform"):
            platform = info["extra"]["platform"]

        uploader_id = (
            info.get("uploader_id")
            or info.get("channel_id")
            or info.get("uploader_url")  # last resort
        )
        # Infer content_subtype from media_type
        subtype_map = {
            MediaType.PODCAST: "podcast_episode",
            MediaType.AUDIO: "audio",
            MediaType.VIDEO: "video",
            MediaType.MEETING: "meeting",
        }
        content_subtype = subtype_map.get(media_type, "video")
        if info.get("content_subtype"):
            content_subtype = info["content_subtype"]

        metadata = MediaMetadata(
            title=info.get("title", "Unknown"),
            source_url=info.get("webpage_url") or info.get("original_url"),
            uploader=info.get("uploader") or info.get("channel") or info.get("uploader_id"),
            uploader_id=str(uploader_id) if uploader_id else None,
            platform=platform,
            upload_date=upload_date,
            duration_seconds=info.get("duration"),
            media_type=media_type,
            content_subtype=content_subtype,
            file_path=file_path,
            file_hash=file_hash,
            description=description,
            tags=unique_tags,
            chapters=chapters,
        )
        if isinstance(info.get("extra"), dict):
            metadata.extra.update(info["extra"])
        if info.get("thumbnail"):
            metadata.extra.setdefault("thumbnail", info["thumbnail"])
        return metadata

    def download_subtitles(
        self,
        url: str,
        output_dir: Path,
        langs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Download ALL available platform subtitle tracks without downloading video.

        Returns:
            {
                "tracks": [{"path": str, "lang": str, "format": str, "type": "cc"|"ai"}],
                # Back-compat: first track as single fields
                "subtitle_path": str|None,
                "subtitle_lang": str|None,
                "subtitle_format": "json3"|"srt"|None,
            }
        """
        url = normalize_bilibili_source_url(url)
        import yt_dlp

        preferred_langs = _parse_lang_priority(langs)
        empty = _empty_subtitle_result()

        if _is_bilibili_url(url):
            return self._download_bilibili_subtitle(url, output_dir, preferred_langs)
        if _is_xiaoyuzhou_url(url):
            try:
                info = self.fetch_metadata(url)
            except Exception as e:
                log_event(logger, logging.WARNING, "xiaoyuzhou.subtitle.probe_failed", error=e)
                return _empty_subtitle_result(
                    engine="xiaoyuzhou-page",
                    diagnostics=[{"stage": "metadata", "status": "failed", "detail": str(e)}],
                )
            return _empty_subtitle_result(
                engine="xiaoyuzhou-page",
                diagnostics=[{
                    "stage": "transcript",
                    "status": "skipped",
                    "reason": "no_public_transcript_endpoint",
                    "transcript_media_id": (info.get("extra") or {}).get("transcript_media_id"),
                }],
            )
        if _is_apple_podcast_url(url):
            return _empty_subtitle_result(
                engine="apple-podcast-rss",
                diagnostics=[{
                    "stage": "subtitle",
                    "status": "skipped",
                    "reason": "no_public_transcript_in_rss",
                }],
            )
        if _is_xiaohongshu_url(url):
            return _empty_subtitle_result(
                engine="xiaohongshu-page",
                diagnostics=[{
                    "stage": "subtitle",
                    "status": "skipped",
                    "reason": "no_public_subtitle_endpoint",
                }],
            )
        if _is_zhihu_url(url):
            return _empty_subtitle_result(
                engine="zhihu-page",
                diagnostics=[{
                    "stage": "subtitle",
                    "status": "skipped",
                    "reason": "text_note_no_subtitle_endpoint",
                }],
            )
        if _is_generic_webpage_url(url):
            return _empty_subtitle_result(
                engine="webpage-scrape",
                diagnostics=[{
                    "stage": "subtitle",
                    "status": "skipped",
                    "reason": "text_note_no_subtitle_endpoint",
                }],
            )

        output_dir.mkdir(parents=True, exist_ok=True)

        # Probe all available subtitle languages via yt-dlp metadata
        metadata_logger = _YtdlpLogger()
        subtitle_network_error: YoutubeNetworkError | None = None
        metadata_opts = {
            "quiet": True,
            "skip_download": True,
            **ytdlp_base_opts(metadata_logger),
            **ytdlp_auth_opts(),
        }
        try:
            with yt_dlp.YoutubeDL(metadata_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            if is_youtube_network_error(e, url):
                raise _youtube_network_error(url, e) from e
            log_event(logger, logging.WARNING, "ytdlp.subtitle.probe_failed", url=url, error=e)
            return empty
        if not info:
            return empty

        manual_subs = info.get("subtitles") or {}
        auto_subs = info.get("automatic_captions") or {}
        if not manual_subs and not auto_subs and metadata_logger.has_youtube_network_error(url):
            raise _youtube_network_error(url, RuntimeError(metadata_logger.network_error_summary()))

        # If user provided langs, filter; otherwise take ALL available
        def _filter(avail: dict, want: list[str] | None) -> list[str]:
            if not want:
                return list(avail.keys())
            out = []
            for w in want:
                w_l = w.lower()
                for k in avail.keys():
                    if k.lower() == w_l or k.lower().startswith(w_l) or w_l in k.lower():
                        if k not in out:
                            out.append(k)
            return out

        manual_langs = _filter(manual_subs, preferred_langs)
        auto_langs = _filter(auto_subs, preferred_langs)
        # Skip auto-captions for languages where a manual track exists
        auto_langs = [l for l in auto_langs if l not in manual_langs]

        tracks: list[dict[str, Any]] = []

        def _try_download(use_auto: bool, target_langs: list[str], type_label: str) -> None:
            nonlocal subtitle_network_error
            if not target_langs or subtitle_network_error is not None:
                return
            subtitle_logger = _YtdlpLogger()
            ydl_opts = {
                "skip_download": True,
                "writesubtitles": not use_auto,
                "writeautomaticsub": use_auto,
                "subtitleslangs": target_langs,
                "subtitlesformat": "json3/srt/best",
                "outtmpl": str(output_dir / "%(id)s"),
                "quiet": True,
                **ytdlp_base_opts(subtitle_logger),
                **ytdlp_auth_opts(),
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
            except Exception as e:
                if is_youtube_network_error(e, url):
                    subtitle_network_error = _youtube_network_error(url, e)
                    log_event(
                        logger,
                        logging.WARNING,
                        "ytdlp.subtitle.network_limited",
                        auto=use_auto,
                        langs=",".join(target_langs),
                        error=e,
                        fallback="media_download_asr",
                    )
                    return
                log_event(
                    logger,
                    logging.WARNING,
                    "ytdlp.subtitle.download_failed",
                    auto=use_auto,
                    langs=",".join(target_langs),
                    error=e,
                )
                return
            if subtitle_logger.has_youtube_network_error(url):
                subtitle_network_error = _youtube_network_error(
                    url,
                    RuntimeError(subtitle_logger.network_error_summary()),
                )
            for lang in target_langs:
                # Find the file yt-dlp wrote for this lang
                for ext in ["json3", "srt", "vtt"]:
                    for f in output_dir.glob(f"*.{lang}.{ext}"):
                        if any(t["path"] == str(f) for t in tracks):
                            continue
                        tracks.append({
                            "path": str(f),
                            "lang": lang,
                            "format": ext,
                            "type": type_label,
                        })
                        break
                    else:
                        continue
                    break

        _try_download(False, manual_langs, "cc")
        _try_download(True, auto_langs, "ai")

        if not tracks:
            if subtitle_network_error:
                return _empty_subtitle_result(
                    engine="yt-dlp",
                    diagnostics=[{
                        "stage": "subtitle",
                        "status": "failed",
                        "reason": "rate_limited_or_unreachable",
                        "detail": str(subtitle_network_error),
                        "fallback": "media_download_asr",
                    }],
                )
            log_event(logger, logging.INFO, "subtitle.empty", url=url, engine="yt-dlp")
            return empty

        log_event(
            logger,
            logging.INFO,
            "subtitle.downloaded",
            engine="yt-dlp",
            tracks=len(tracks),
            langs=",".join(t["lang"] for t in tracks),
        )
        first = tracks[0]
        return {
            "tracks": tracks,
            "subtitle_path": first["path"],
            "subtitle_lang": first["lang"],
            "subtitle_format": first["format"],
            "subtitle_engine": "yt-dlp",
            "diagnostics": [],
        }

    def _compute_hash(self, file_path: str) -> str:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()


_service: YtdlpService | None = None


def get_ytdlp_service() -> YtdlpService:
    global _service
    if _service is None:
        _service = YtdlpService()
    return _service


async def download_media(url: str, output_dir: Path | None = None) -> dict[str, Any]:
    import asyncio
    service = get_ytdlp_service()
    result = await asyncio.to_thread(service.download, url, output_dir=output_dir)
    metadata = service.extract_metadata(result["info"], result.get("file_path"))
    return {
        "file_path": result.get("file_path"),
        "video_path": result.get("video_path"),
        "metadata": metadata.model_dump(mode="json"),
        "info": result.get("info"),  # raw ingest info (needed for image-note pipeline)
    }


async def download_subtitles(
    url: str, output_dir: Path, langs: list[str] | None = None
) -> dict[str, Any]:
    import asyncio
    service = get_ytdlp_service()
    return await asyncio.to_thread(service.download_subtitles, url, output_dir, langs)


async def fetch_metadata(url: str) -> "MediaMetadata":
    """Fetch metadata without downloading — for subtitle fast path."""
    import asyncio
    service = get_ytdlp_service()
    info = await asyncio.to_thread(service.fetch_metadata, url)
    return service.extract_metadata(info)
