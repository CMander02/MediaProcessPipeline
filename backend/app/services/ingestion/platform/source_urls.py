"""Source urls for media ingestion."""

import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.core.logging_setup import log_event
from app.core.network import runtime_proxy_url as shared_runtime_proxy_url
from app.core.network import urllib_urlopen

logger = logging.getLogger(__name__)

_HTTP_URL_RE = re.compile(r'https?://[^\s<>"\'，。！？；、]+', re.IGNORECASE)


_BILIBILI_BVID_RE = r"BV[0-9A-Za-z]{10}"


_DIRECT_MEDIA_EXTS = {
    ".mp4",
    ".mkv",
    ".avi",
    ".webm",
    ".mov",
    ".m4v",
    ".mp3",
    ".wav",
    ".flac",
    ".m4a",
    ".ogg",
    ".aac",
}


def _extract_http_urls(value: str) -> list[str]:
    return [match.group(0).strip() for match in _HTTP_URL_RE.finditer(value)]


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
    try:
        parsed = urlparse(_ensure_http_url(value))
    except ValueError:
        return False
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
        return bool(re.fullmatch(rf"(?:{_BILIBILI_BVID_RE}|av\d+)", url.strip(), re.IGNORECASE))

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
    return _candidate_matches(url, r"xiaoyuzhoufm\.com/episode/[0-9a-fA-F]+")


def _is_xiaohongshu_url(url: str) -> bool:
    return _host_matches(url, ("xiaohongshu.com", "xhslink.com"))


def _is_zhihu_url(url: str) -> bool:
    return _candidate_matches(url, r"zhihu\.com/(?:pin/\d+|question/\d+/answer/\d+)")


def _is_direct_media_url(url: str) -> bool:
    for candidate in _candidate_urls(url):
        if "://" not in candidate:
            continue
        parsed = urlparse(candidate)
        if Path(parsed.path).suffix.lower() in _DIRECT_MEDIA_EXTS:
            return True
    return False


def _is_apple_podcast_url(url: str) -> bool:
    return _candidate_matches(url, r"podcasts\.apple\.com/(?:[a-z]{2}/)?podcast/[^?#/]*/id\d+")


def _is_youtube_url(url: str) -> bool:
    return _host_matches(url, ("youtube.com", "youtu.be"))


def _is_twitter_url(url: str) -> bool:
    return _host_matches(url, ("x.com", "twitter.com"))


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
    if _host_matches(
        url,
        (
            "youtube.com",
            "youtu.be",
            "vimeo.com",
            "x.com",
            "twitter.com",
            "tiktok.com",
            "douyin.com",
            "kuaishou.com",
            "weibo.com",
            "bilibili.com",
            "b23.tv",
        ),
    ):
        return False
    return True


def _extract_bilibili_bvid(url: str) -> str | None:
    """Extract or resolve a Bilibili BV id from BV or av/aid URLs."""
    url = normalize_bilibili_source_url(url)
    value = url.strip()
    if not value:
        return None

    bare_bv = re.fullmatch(rf"({_BILIBILI_BVID_RE})", value)
    if bare_bv:
        return bare_bv.group(1)

    bare_av = re.fullmatch(r"av(\d+)", value, re.IGNORECASE)
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

            path_match = re.search(rf"/(?:video/)?({_BILIBILI_BVID_RE})(?:/|$)", parsed.path)
            if path_match:
                return path_match.group(1)

            av_match = re.search(r"/(?:video/)?av(\d+)(?:/|$)", parsed.path, re.IGNORECASE)
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
                log_event(
                    logger, logging.WARNING, "bilibili.short_url.resolve_failed", url=url, error=e
                )
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


def _bilibili_display_title(
    view_data: dict[str, Any], page: dict[str, Any], page_number: int
) -> str:
    title = str(view_data.get("title") or "").strip()
    part = str(page.get("part") or "").strip()
    pages = view_data.get("pages") or []
    if len(pages) > 1 and part and part != title:
        return f"{title} P{page_number} {part}".strip()
    return title or part
