"""Payload for media ingestion."""

import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from urllib.parse import urlparse

from app.services.ingestion.platform.source_urls import _extract_http_urls

logger = logging.getLogger(__name__)


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
        for candidate in lines[idx + 1 :]:
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
