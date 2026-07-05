"""Bilibili opus/dynamic image-note ingestion."""

from __future__ import annotations

import html
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

from app.core.network import urllib_urlopen

from .auth import get_cookie

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_CHUNK_SIZE = 1024 * 1024
_CDN_HOST_RE = re.compile(r"(^|\.)hdslb\.com$", re.IGNORECASE)


def fetch_metadata(value: str) -> dict[str, Any]:
    """Fetch metadata for a Bilibili opus/dynamic image-text post."""
    opus_id = extract_opus_id(value)
    if not opus_id:
        raise RuntimeError(f"Invalid Bilibili opus/dynamic URL: {value}")

    item = _fetch_dynamic_detail(opus_id)
    modules = item.get("modules") if isinstance(item, dict) else {}
    if not isinstance(modules, dict):
        modules = {}
    dynamic = modules.get("module_dynamic") if isinstance(modules.get("module_dynamic"), dict) else {}
    major = dynamic.get("major") if isinstance(dynamic.get("major"), dict) else {}
    author = modules.get("module_author") if isinstance(modules.get("module_author"), dict) else {}
    stat = modules.get("module_stat") if isinstance(modules.get("module_stat"), dict) else {}
    article = major.get("article") if isinstance(major.get("article"), dict) else {}

    title = _extract_title(dynamic, major, opus_id)
    body = _extract_body_text(dynamic, major, title)
    article_url = _article_url(article)
    article_id = _article_id(article, article_url)
    article_data: dict[str, Any] | None = None
    if article_id:
        try:
            article_data = _fetch_article_api_data(article_id, article_url)
            article_body = _article_markdown_from_api_data(article_data, title=title)
            if len(article_body) > len(body or ""):
                body = article_body
        except Exception as exc:
            logger.warning("Bilibili article API fetch failed: %s", exc)
    if article_url and not article_data:
        body = _fetch_article_webpage_markdown(article_url, body)
    image_url_candidates = _extract_image_url_candidates(dynamic, major)
    if article_data:
        image_url_candidates = _merge_image_candidate_groups(
            image_url_candidates,
            _extract_article_api_image_candidates(article_data),
        )
    image_urls = [candidates[0] for candidates in image_url_candidates if candidates]
    pub_ts = _parse_pub_ts(author.get("pub_ts") or author.get("pub_time"))
    content_subtype = "image_note" if image_urls else "text_note"

    return {
        "id": opus_id,
        "title": title,
        "description": body,
        "uploader": author.get("name") or author.get("uname"),
        "uploader_id": str(author.get("mid")) if author.get("mid") else None,
        "platform": "bilibili_opus",
        "content_subtype": content_subtype,
        "duration": None,
        "upload_date": datetime.fromtimestamp(pub_ts).strftime("%Y%m%d") if pub_ts else None,
        "timestamp": pub_ts,
        "webpage_url": f"https://www.bilibili.com/opus/{opus_id}",
        "original_url": value,
        "thumbnail": image_urls[0] if image_urls else None,
        "url": None,
        "ext": None,
        "media_type": "image" if image_urls else "other",
        "tags": ["Bilibili"],
        "extra": {
            "platform": "bilibili_opus",
            "bilibili_type": "article" if article_url else "opus",
            "opus_id": opus_id,
            "article_url": article_url,
            "article_id": article_id,
            "image_urls": image_urls,
            "image_url_candidates": image_url_candidates,
            "dynamic_type": item.get("type"),
            "bilibili_metadata": _compact_bilibili_metadata(author, stat, item, article_data),
        },
    }


def download_images(
    info: dict[str, Any],
    output_dir: Path,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> list[Path]:
    """Download Bilibili opus images and return local paths."""
    extra = info.get("extra") or {}
    image_urls: list[str] = extra.get("image_urls") or []
    image_url_candidates = extra.get("image_url_candidates") or []
    if not image_url_candidates:
        image_url_candidates = [[url] for url in image_urls]
    if not image_url_candidates:
        raise RuntimeError("Bilibili note has no image URLs.")

    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    referer = str(info.get("webpage_url") or "https://www.bilibili.com/")
    paths: list[Path] = []
    for idx, candidates in enumerate(image_url_candidates):
        _raise_if_image_download_cancelled(should_cancel)
        urls = [url for url in candidates if isinstance(url, str) and url.startswith("http")]
        if not urls:
            continue
        ext = _guess_image_ext(urls)
        dest = images_dir / f"{idx:02d}.{ext}"
        if dest.exists():
            paths.append(dest)
            continue
        try:
            _download_file_candidates(urls, dest, referer=referer, should_cancel=should_cancel)
            paths.append(dest)
        except Exception as exc:
            if should_cancel and should_cancel():
                raise
            logger.warning("Failed to download Bilibili image %s: %s", idx, exc)
    return paths


def extract_opus_id(value: str) -> str | None:
    for candidate in _candidate_urls(value):
        parsed = urllib.parse.urlparse(_ensure_url(candidate))
        host = (parsed.hostname or "").lower()
        path = parsed.path.strip("/")
        query = urllib.parse.parse_qs(parsed.query)
        if host == "t.bilibili.com" or host.endswith(".t.bilibili.com"):
            match = re.match(r"(?:dynamic/)?(\d+)$", path, re.IGNORECASE)
            if match:
                return match.group(1)
        if host == "bilibili.com" or host.endswith(".bilibili.com"):
            match = re.match(r"(?:opus|dynamic)/(\d+)$", path, re.IGNORECASE)
            if match:
                return match.group(1)
            match = re.match(r"h5/dynamic/detail/(\d+)$", path, re.IGNORECASE)
            if match:
                return match.group(1)
            for key in ("id", "dynamic_id", "opus_id"):
                values = query.get(key) or []
                if values and values[0].isdigit():
                    return values[0]
    return None


def bilibili_image_candidates(value: str) -> list[str]:
    """Return original-first candidates for a Bilibili CDN image URL."""
    url = _normalize_image_url(value)
    if not url:
        return []
    candidates: list[str] = []
    original = _strip_bilibili_image_suffix(url)
    for candidate in (original, url):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _fetch_dynamic_detail(opus_id: str) -> dict[str, Any]:
    item = _fetch_dynamic_detail_request(opus_id, features="itemOpusStyle")
    if item.get("type") == "DYNAMIC_TYPE_ARTICLE":
        try:
            raw_item = _fetch_dynamic_detail_request(opus_id)
            item = _merge_dynamic_detail_items(item, raw_item)
        except Exception as exc:
            logger.warning("Bilibili article fallback detail fetch failed: %s", exc)
    return item


def _fetch_dynamic_detail_request(opus_id: str, features: str | None = None) -> dict[str, Any]:
    params_map: dict[str, Any] = {"id": opus_id, "timezone_offset": -480}
    if features:
        params_map["features"] = features
    params = urllib.parse.urlencode(params_map)
    url = f"https://api.bilibili.com/x/polymer/web-dynamic/v1/detail?{params}"
    headers = {
        "User-Agent": _UA,
        "Referer": f"https://www.bilibili.com/opus/{opus_id}",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": "https://www.bilibili.com",
    }
    cookie = get_cookie()
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    with urllib_urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read())
    if body.get("code") != 0:
        raise RuntimeError(f"Bilibili dynamic API code={body.get('code')} msg={body.get('message')!r}")
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    item = data.get("item") if isinstance(data.get("item"), dict) else {}
    if not item:
        raise RuntimeError("Bilibili dynamic API returned no item.")
    return item


def _merge_dynamic_detail_items(primary: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(primary)
    primary_modules = merged.setdefault("modules", {})
    fallback_modules = fallback.get("modules") if isinstance(fallback.get("modules"), dict) else {}

    for key in ("module_author", "module_stat", "module_more"):
        if key not in primary_modules and key in fallback_modules:
            primary_modules[key] = deepcopy(fallback_modules[key])

    primary_dynamic = primary_modules.setdefault("module_dynamic", {})
    fallback_dynamic = fallback_modules.get("module_dynamic") if isinstance(fallback_modules.get("module_dynamic"), dict) else {}
    if not primary_dynamic.get("desc") and fallback_dynamic.get("desc"):
        primary_dynamic["desc"] = deepcopy(fallback_dynamic["desc"])
    if not primary_dynamic.get("topic") and fallback_dynamic.get("topic"):
        primary_dynamic["topic"] = deepcopy(fallback_dynamic["topic"])
    if not primary_dynamic.get("additional") and fallback_dynamic.get("additional"):
        primary_dynamic["additional"] = deepcopy(fallback_dynamic["additional"])

    primary_major = primary_dynamic.setdefault("major", {})
    fallback_major = fallback_dynamic.get("major") if isinstance(fallback_dynamic.get("major"), dict) else {}
    for key in ("article", "opus", "draw"):
        if key not in primary_major and key in fallback_major:
            primary_major[key] = deepcopy(fallback_major[key])
    return merged


def _compact_bilibili_metadata(
    author: dict[str, Any],
    stat: dict[str, Any],
    item: dict[str, Any],
    article_data: dict[str, Any] | None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "pub_time": author.get("pub_time"),
        "pub_ts": _parse_pub_ts(author.get("pub_ts")),
        "author_face": author.get("face"),
        "author_space_url": _normalize_bilibili_url(author.get("jump_url")),
        "comment_id": ((item.get("basic") or {}).get("comment_id_str") if isinstance(item.get("basic"), dict) else None),
        "stats": _compact_dynamic_stats(stat),
    }
    if article_data:
        metadata["article"] = {
            "publish_time": _parse_pub_ts(article_data.get("publish_time")),
            "ctime": _parse_pub_ts(article_data.get("ctime")),
            "mtime": _parse_pub_ts(article_data.get("mtime")),
            "words": article_data.get("words"),
            "stats": article_data.get("stats") if isinstance(article_data.get("stats"), dict) else None,
        }
    return {key: value for key, value in metadata.items() if value not in (None, "", {}, [])}


def _compact_dynamic_stats(stat: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for key in ("like", "comment", "forward"):
        value = stat.get(key)
        if isinstance(value, dict) and isinstance(value.get("count"), int):
            out[key] = value["count"]
    return out


def _normalize_bilibili_url(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("//"):
        return f"https:{raw}"
    if raw.startswith("/"):
        return f"https://www.bilibili.com{raw}"
    if raw.startswith(("http://", "https://")):
        return raw
    return None


def _extract_title(dynamic: dict[str, Any], major: dict[str, Any], opus_id: str) -> str:
    opus = major.get("opus") if isinstance(major.get("opus"), dict) else {}
    article = major.get("article") if isinstance(major.get("article"), dict) else {}
    title = _clean_text(article.get("title") or opus.get("title") or dynamic.get("title"))
    if title:
        return title
    desc = dynamic.get("desc") if isinstance(dynamic.get("desc"), dict) else {}
    text = _clean_text(desc.get("text"))
    first_line = text.splitlines()[0].strip() if text else ""
    return first_line[:80] or f"bilibili_opus_{opus_id}"


def _extract_body_text(dynamic: dict[str, Any], major: dict[str, Any], title: str) -> str:
    parts: list[str] = []
    if title:
        parts.append(f"# {title}")

    desc = dynamic.get("desc") if isinstance(dynamic.get("desc"), dict) else {}
    desc_text = _text_from_value(desc)
    if desc_text:
        parts.append(desc_text)

    opus = major.get("opus") if isinstance(major.get("opus"), dict) else {}
    article = major.get("article") if isinstance(major.get("article"), dict) else {}
    opus_text = _text_from_value(opus)
    if opus_text and opus_text not in parts:
        parts.append(opus_text)
    article_desc = _clean_text(article.get("desc")) if article else ""
    if article_desc and article_desc not in parts:
        parts.append(article_desc)

    body = "\n\n".join(_dedupe_preserve_order(parts)).strip()
    return body or (f"# {title}" if title else "")


def _text_from_value(value: Any) -> str:
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, list):
        parts = [_text_from_value(item) for item in value]
        return "\n".join(part for part in parts if part)
    if not isinstance(value, dict):
        return ""

    parts: list[str] = []
    rich_nodes = value.get("rich_text_nodes")
    if isinstance(rich_nodes, list):
        for node in rich_nodes:
            if isinstance(node, dict):
                text = _clean_text(node.get("orig_text") or node.get("text"))
                if text:
                    parts.append(text)

    for key in ("text", "raw_text", "content", "summary", "desc"):
        item = value.get(key)
        if isinstance(item, str):
            text = _clean_text(item)
            if text:
                parts.append(text)
        elif isinstance(item, (dict, list)):
            text = _text_from_value(item)
            if text:
                parts.append(text)

    return "\n".join(_dedupe_preserve_order(parts))


def _extract_image_url_candidates(dynamic: dict[str, Any], major: dict[str, Any]) -> list[list[str]]:
    grouped: list[list[str]] = []
    for container in _target_image_containers(dynamic, major):
        _collect_image_groups(container, grouped)
    if not grouped:
        urls: list[str] = []
        _collect_image_urls(major or dynamic, urls)
        grouped = [bilibili_image_candidates(url) for url in urls]
    return [candidates for candidates in grouped if candidates]


def _target_image_containers(dynamic: dict[str, Any], major: dict[str, Any]) -> list[Any]:
    containers: list[Any] = []
    opus = major.get("opus") if isinstance(major.get("opus"), dict) else {}
    draw = major.get("draw") if isinstance(major.get("draw"), dict) else {}
    article = major.get("article") if isinstance(major.get("article"), dict) else {}
    for source in (
        opus.get("pics"),
        opus.get("pictures"),
        opus.get("items"),
        draw.get("items"),
        draw.get("pics"),
        article.get("covers"),
        dynamic.get("pics"),
    ):
        if source:
            containers.append(source)
    return containers


def _collect_image_groups(value: Any, grouped: list[list[str]]) -> None:
    if isinstance(value, list):
        for item in value:
            urls: list[str] = []
            _collect_image_urls(item, urls)
            candidates: list[str] = []
            for url in urls:
                for candidate in bilibili_image_candidates(url):
                    if candidate not in candidates:
                        candidates.append(candidate)
            if candidates:
                grouped.append(candidates)
        return

    urls: list[str] = []
    _collect_image_urls(value, urls)
    for url in urls:
        candidates = bilibili_image_candidates(url)
        if candidates:
            grouped.append(candidates)


def _collect_image_urls(value: Any, urls: list[str]) -> None:
    if isinstance(value, str):
        normalized = _normalize_image_url(value)
        if normalized and _looks_like_bilibili_image(normalized) and normalized not in urls:
            urls.append(normalized)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in {"avatar", "face", "icon", "pendant", "decorate_card"}:
                continue
            if isinstance(item, str):
                normalized = _normalize_image_url(item)
                if normalized and _looks_like_bilibili_image(normalized) and normalized not in urls:
                    urls.append(normalized)
            else:
                _collect_image_urls(item, urls)
    elif isinstance(value, list):
        for item in value:
            _collect_image_urls(item, urls)


def _looks_like_bilibili_image(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    host = parsed.hostname or ""
    path = parsed.path.lower()
    if not _CDN_HOST_RE.search(host):
        return False
    if "/face/" in path or "/emote/" in path or "/garb/" in path:
        return False
    return "/bfs/" in path and bool(re.search(r"\.(?:jpg|jpeg|png|webp|gif)(?:$|@)", path))


def _normalize_image_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = f"https:{raw}"
    if raw.startswith("http://"):
        raw = "https://" + raw[len("http://") :]
    if not raw.startswith("https://"):
        return ""
    return urllib.parse.unquote(raw)


def _strip_bilibili_image_suffix(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if not parsed.hostname or not _CDN_HOST_RE.search(parsed.hostname):
        return value
    path = parsed.path
    if "@" in path:
        path = path.split("@", 1)[0]
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _article_url(article: dict[str, Any]) -> str:
    raw = str(article.get("jump_url") or article.get("url") or "").strip()
    if not raw:
        article_id = article.get("id")
        return f"https://www.bilibili.com/read/cv{article_id}/" if article_id else ""
    if raw.startswith("//"):
        return f"https:{raw}"
    if raw.startswith("/"):
        return f"https://www.bilibili.com{raw}"
    return raw if raw.startswith(("http://", "https://")) else ""


def _article_id(article: dict[str, Any], article_url: str = "") -> str | None:
    raw_id = article.get("id") if isinstance(article, dict) else None
    if isinstance(raw_id, int):
        return str(raw_id)
    if isinstance(raw_id, str) and raw_id.isdigit():
        return raw_id
    match = re.search(r"/read/cv(\d+)", article_url or "", re.IGNORECASE)
    return match.group(1) if match else None


def _fetch_article_api_data(article_id: str, article_url: str = "") -> dict[str, Any]:
    params = urllib.parse.urlencode({"id": article_id})
    url = f"https://api.bilibili.com/x/article/view?{params}"
    referer = article_url or f"https://www.bilibili.com/read/cv{article_id}/"
    headers = {
        "User-Agent": _UA,
        "Referer": referer,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": "https://www.bilibili.com",
    }
    cookie = get_cookie()
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    with urllib_urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read())
    if body.get("code") != 0:
        raise RuntimeError(f"Bilibili article API code={body.get('code')} msg={body.get('message')!r}")
    data = body.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Bilibili article API returned no data.")
    return data


def _article_markdown_from_api_data(data: dict[str, Any], title: str = "") -> str:
    opus = data.get("opus") if isinstance(data.get("opus"), dict) else {}
    api_title = _clean_text(data.get("title") or opus.get("title") or title)
    parts: list[str] = [f"# {api_title}"] if api_title else []

    opus_content = opus.get("content") if isinstance(opus.get("content"), dict) else {}
    body = _markdown_from_article_opus_content(opus_content)
    if not body:
        body = _markdown_from_article_content_field(data.get("content"))
    if not body:
        body = _clean_text(data.get("summary"))
    if body:
        parts.append(body)

    return _clean_article_markdown("\n\n".join(parts))


def _markdown_from_article_opus_content(content: dict[str, Any]) -> str:
    paragraphs = content.get("paragraphs") if isinstance(content, dict) else None
    if not isinstance(paragraphs, list):
        return ""

    parts: list[str] = []
    for para in paragraphs:
        if not isinstance(para, dict):
            continue
        image_markdown = _article_paragraph_image_markdown(para)
        if image_markdown:
            parts.append(image_markdown)
            continue
        text = _article_paragraph_text(para)
        if not text:
            continue
        heading_type = _article_heading_type(para)
        if heading_type:
            level = min(max(heading_type + 1, 2), 6)
            parts.append(f"{'#' * level} {text}")
        else:
            parts.append(text)
    return _clean_article_markdown("\n\n".join(parts))


def _article_paragraph_image_markdown(para: dict[str, Any]) -> str:
    pic = para.get("pic") if isinstance(para.get("pic"), dict) else {}
    pics = pic.get("pics") if isinstance(pic.get("pics"), list) else []
    parts: list[str] = []
    for item in pics:
        if not isinstance(item, dict):
            continue
        url = _normalize_image_url(str(item.get("url") or ""))
        if not url:
            continue
        caption = _clean_text(item.get("comment") or item.get("alt") or "")
        alt = _markdown_image_alt(caption or "图片")
        parts.append(f"![{alt}]({url})")
        if caption:
            parts.append(caption)
    return "\n\n".join(parts)


def _markdown_image_alt(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _article_paragraph_text(para: dict[str, Any]) -> str:
    text = para.get("text") if isinstance(para.get("text"), dict) else {}
    nodes = text.get("nodes") if isinstance(text.get("nodes"), list) else []
    parts: list[str] = []
    for node in nodes:
        piece = _article_node_text(node)
        if piece:
            parts.append(piece)
    if parts:
        return _clean_text("".join(parts))
    return _clean_text(para.get("text") if isinstance(para.get("text"), str) else "")


def _article_node_text(node: Any) -> str:
    if isinstance(node, str):
        return html.unescape(node)
    if not isinstance(node, dict):
        return ""

    for key in ("word", "link", "text", "emoji"):
        value = node.get(key)
        if isinstance(value, str):
            return html.unescape(value)
        if isinstance(value, dict):
            for text_key in ("words", "text", "title", "content", "orig_text"):
                text = value.get(text_key)
                if isinstance(text, str):
                    return html.unescape(text)
    for text_key in ("words", "text", "title", "content", "orig_text"):
        text = node.get(text_key)
        if isinstance(text, str):
            return html.unescape(text)
    return ""


def _article_heading_type(para: dict[str, Any]) -> int | None:
    fmt = para.get("format") if isinstance(para.get("format"), dict) else {}
    raw = fmt.get("heading_type")
    if isinstance(raw, int) and raw > 0:
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return 1 if para.get("para_type") == 9 else None


def _markdown_from_article_content_field(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    text = html.unescape(value)
    if "<" in text and ">" in text:
        text = _ArticleHTMLToMarkdown().convert(text)
    return _clean_article_markdown(text)


class _ArticleHTMLToMarkdown(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._heading_level: int | None = None
        self._list_item = False

    def convert(self, value: str) -> str:
        self.feed(value)
        self.close()
        return _clean_article_markdown("".join(self.parts))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"p", "div", "section", "article", "blockquote", "ul", "ol"}:
            self._newline()
        elif tag == "br":
            self.parts.append("\n")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._newline()
            self._heading_level = int(tag[1])
            self.parts.append("#" * self._heading_level + " ")
        elif tag == "li":
            self._newline()
            self._list_item = True
            self.parts.append("- ")
        elif tag == "img":
            attrs_dict = {key.lower(): value for key, value in attrs}
            src = _normalize_image_url(attrs_dict.get("src") or "")
            if src:
                alt = _clean_text(attrs_dict.get("alt"))
                self._newline()
                self.parts.append(f"![{alt}]({src})")
                self._newline()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"p", "div", "section", "article", "blockquote", "ul", "ol", "li"}:
            self._newline()
            if tag == "li":
                self._list_item = False
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_level = None
            self._newline()

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def _newline(self) -> None:
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")


def _extract_article_api_image_candidates(data: dict[str, Any]) -> list[list[str]]:
    grouped: list[list[str]] = []
    for key in ("origin_image_urls", "image_urls"):
        values = data.get(key)
        if isinstance(values, list):
            for value in values:
                candidates = bilibili_image_candidates(str(value))
                if candidates:
                    grouped.append(candidates)

    content_pic_list = data.get("content_pic_list")
    if content_pic_list:
        _collect_image_groups(content_pic_list, grouped)

    opus = data.get("opus") if isinstance(data.get("opus"), dict) else {}
    opus_content = opus.get("content") if isinstance(opus.get("content"), dict) else {}
    if opus_content:
        _collect_image_groups(opus_content, grouped)

    return _merge_image_candidate_groups([], grouped)


def _merge_image_candidate_groups(*groups_list: list[list[str]]) -> list[list[str]]:
    merged: list[list[str]] = []
    seen_keys: set[str] = set()
    for groups in groups_list:
        for candidates in groups:
            normalized_candidates: list[str] = []
            for candidate in candidates:
                for expanded in bilibili_image_candidates(candidate):
                    if expanded not in normalized_candidates:
                        normalized_candidates.append(expanded)
            if not normalized_candidates:
                continue
            key = normalized_candidates[0]
            if key in seen_keys:
                continue
            seen_keys.add(key)
            merged.append(normalized_candidates)
    return merged


def _clean_article_markdown(value: str) -> str:
    text = html.unescape(str(value or "")).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _fetch_article_webpage_markdown(article_url: str, fallback: str) -> str:
    try:
        from app.services.ingestion.platform.webpage.api import fetch_metadata as fetch_webpage_metadata

        info = fetch_webpage_metadata(article_url)
        markdown = _clean_text(info.get("description"))
        if len(markdown) > len(fallback or ""):
            return markdown
    except Exception as exc:
        logger.warning("Bilibili article markdown fetch failed: %s", exc)
    return fallback


def _raise_if_image_download_cancelled(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel and should_cancel():
        raise RuntimeError("Bilibili image download cancelled")


def _sleep_with_cancel(seconds: float, should_cancel: Callable[[], bool] | None) -> None:
    deadline = time.monotonic() + seconds
    while True:
        _raise_if_image_download_cancelled(should_cancel)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.2))


def _download_file_candidates(
    urls: list[str],
    dest: Path,
    referer: str,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    last_error: Exception | None = None
    seen: set[str] = set()
    for url in urls:
        _raise_if_image_download_cancelled(should_cancel)
        if url in seen:
            continue
        seen.add(url)
        try:
            _download_file(url, dest, referer=referer, should_cancel=should_cancel)
            return
        except Exception as exc:
            if should_cancel and should_cancel():
                dest.unlink(missing_ok=True)
                dest.with_name(dest.name + ".part").unlink(missing_ok=True)
                raise
            last_error = exc
            logger.warning("Bilibili image download URL failed: %s", exc)
            dest.unlink(missing_ok=True)
            dest.with_name(dest.name + ".part").unlink(missing_ok=True)
    raise RuntimeError(f"All Bilibili image URLs failed: {last_error}")


def _download_file(
    url: str,
    dest: Path,
    referer: str,
    attempts: int = 2,
    timeout_sec: int = 30,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    last_error: Exception | None = None
    part = dest.with_name(dest.name + ".part")
    headers = {
        "User-Agent": _UA,
        "Referer": referer,
        "Origin": "https://www.bilibili.com",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    cookie = get_cookie()
    if cookie:
        headers["Cookie"] = cookie
    for attempt in range(1, attempts + 1):
        _raise_if_image_download_cancelled(should_cancel)
        downloaded = 0
        dest.unlink(missing_ok=True)
        part.unlink(missing_ok=True)
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib_urlopen(req, timeout=timeout_sec) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                _raise_if_image_download_cancelled(should_cancel)
                with open(part, "wb") as f:
                    while True:
                        _raise_if_image_download_cancelled(should_cancel)
                        chunk = resp.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
            if total and downloaded < total:
                raise RuntimeError(f"Incomplete download: {downloaded}/{total} bytes")
            part.replace(dest)
            logger.info("Downloaded Bilibili image: %s bytes -> %s", f"{downloaded:,}", dest.name)
            return
        except Exception as exc:
            if should_cancel and should_cancel():
                dest.unlink(missing_ok=True)
                part.unlink(missing_ok=True)
                raise
            last_error = exc
            dest.unlink(missing_ok=True)
            part.unlink(missing_ok=True)
            if attempt >= attempts:
                raise RuntimeError(f"Bilibili image download failed after {attempts} attempts: {last_error}") from exc
            _sleep_with_cancel(min(attempt, 3), should_cancel)


def _guess_image_ext(urls: list[str]) -> str:
    for url in urls:
        path = urllib.parse.urlparse(url).path.lower()
        match = re.search(r"\.([a-z0-9]{2,5})(?:@|$)", path)
        if match:
            ext = match.group(1)
            if ext == "jpeg":
                return "jpg"
            if ext in {"jpg", "png", "webp", "gif"}:
                return ext
    return "jpg"


def _candidate_urls(value: str) -> list[str]:
    urls = re.findall(r'https?://[^\s<>"\'，。！？；、]+', str(value or ""), flags=re.IGNORECASE)
    return urls or [str(value or "").strip()]


def _ensure_url(value: str) -> str:
    raw = str(value or "").strip()
    return raw if raw.startswith(("http://", "https://")) else f"https://{raw}"


def _parse_pub_ts(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(value / 1000) if value > 10_000_000_000 else int(value)
    if isinstance(value, str) and value.isdigit():
        return _parse_pub_ts(int(value))
    return None


def _clean_text(value: Any) -> str:
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", str(value or ""))).strip()


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out
