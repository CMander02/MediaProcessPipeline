"""Generic web page scraper.

The preferred path is Defuddle CLI because it is local and returns clean
Markdown. Jina Reader is the configured fallback for pages Defuddle cannot
extract.
"""

from __future__ import annotations

import html
import ipaddress
import json
import logging
import mimetypes
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from app.core.network import httpx_client_kwargs, urllib_urlopen
from app.core.logging_setup import log_event
from app.core.settings import get_runtime_settings

logger = logging.getLogger(__name__)

_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_HTML_IMAGE_RE = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
_TITLE_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)
_JINA_TITLE_RE = re.compile(r"^\s*Title:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_MAX_IMAGE_COUNT = 40
_MAX_IMAGE_BYTES = 16 * 1024 * 1024
_SCRAPE_RETRIES = 3


def fetch_metadata(url: str) -> dict[str, Any]:
    """Fetch clean Markdown and metadata for a generic web page."""
    page_url = _normalize_url(url)
    try:
        markdown = _run_defuddle_markdown(page_url)
        title = _run_defuddle_prop(page_url, "title") or _extract_title(markdown, page_url)
        description = _run_defuddle_prop(page_url, "description")
        domain = _run_defuddle_prop(page_url, "domain") or urllib.parse.urlparse(page_url).hostname
        engine = "defuddle"
        fallback_error = ""
    except Exception as exc:
        log_event(logger, logging.WARNING, "webpage.defuddle.failed", url=page_url, error=exc)
        markdown, jina_meta = _fetch_jina_markdown(page_url)
        title = jina_meta.get("title") or _extract_title(markdown, page_url)
        description = jina_meta.get("description") or ""
        domain = urllib.parse.urlparse(page_url).hostname
        engine = "jina"
        fallback_error = str(exc)

    return {
        "id": page_url,
        "title": title,
        "description": markdown,
        "webpage_url": page_url,
        "original_url": page_url,
        "platform": "webpage",
        "content_subtype": "text_note",
        "media_type": "image",
        "uploader": domain,
        "thumbnail": None,
        "extra": {
            "platform": "webpage",
            "scrape_engine": engine,
            "jina_description": description,
            "defuddle_error": fallback_error,
        },
    }


def download_webpage(url: str, output_dir: Path) -> dict[str, Any]:
    """Fetch a web page, localize media assets, and write source.md."""
    output_dir.mkdir(parents=True, exist_ok=True)
    info = fetch_metadata(url)
    page_url = str(info.get("webpage_url") or url)
    markdown = str(info.get("description") or "")

    localized, images = localize_markdown_images(markdown, page_url, output_dir)
    info["description"] = localized
    extra = info.setdefault("extra", {})
    if isinstance(extra, dict):
        extra["images"] = images
        extra["source_markdown_path"] = str(output_dir / "source.md")
        extra["image_count"] = len(images)
    if images:
        info["thumbnail"] = images[0].get("path")

    (output_dir / "source.md").write_text(localized, encoding="utf-8")
    return info


def localize_markdown_images(markdown: str, page_url: str, output_dir: Path) -> tuple[str, list[dict[str, str]]]:
    """Download Markdown/HTML image references and rewrite them to local paths."""
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, str]] = []
    seen: dict[str, str] = {}

    def save_image(raw_url: str, alt: str = "") -> str:
        absolute_url = _resolve_url(raw_url, page_url)
        if not absolute_url or not absolute_url.startswith(("http://", "https://")):
            return raw_url
        if absolute_url in seen:
            return seen[absolute_url]
        if len(records) >= _MAX_IMAGE_COUNT:
            return raw_url
        body = b""
        content_type = ""
        fetched_url = absolute_url
        last_error: Exception | None = None
        for candidate_url in _image_download_candidates(absolute_url):
            try:
                body, content_type = _download_binary(candidate_url, referer=page_url)
                fetched_url = candidate_url
                break
            except Exception as exc:
                last_error = exc
        if not body:
            log_event(logger, logging.WARNING, "webpage.image.download_failed", url=absolute_url, error=last_error)
            return raw_url
        if len(body) > _MAX_IMAGE_BYTES:
            log_event(logger, logging.WARNING, "webpage.image.too_large", url=absolute_url, size=len(body))
            return raw_url
        ext = _extension_for_image(absolute_url, content_type)
        filename = f"{len(records):02d}{ext}"
        target = images_dir / filename
        target.write_bytes(body)
        relative = f"images/{filename}"
        seen[absolute_url] = relative
        records.append({
            "url": absolute_url,
            "fetched_url": fetched_url,
            "path": str(target),
            "relative_path": relative,
            "alt": alt,
            "content_type": content_type,
        })
        return relative

    def replace_markdown(match: re.Match[str]) -> str:
        alt = match.group(1)
        raw_url = match.group(2).strip("<>")
        local_url = save_image(raw_url, alt)
        return f"![{alt}]({local_url})"

    result = _MARKDOWN_IMAGE_RE.sub(replace_markdown, markdown)

    def replace_html(match: re.Match[str]) -> str:
        raw_url = html.unescape(match.group(1))
        local_url = save_image(raw_url)
        return f"![image]({local_url})"

    result = _HTML_IMAGE_RE.sub(replace_html, result)
    return result, records


def _normalize_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        raise ValueError("URL is required")
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    return value


def _run_defuddle_markdown(url: str) -> str:
    rt = get_runtime_settings()
    if not bool(getattr(rt, "defuddle_enabled", True)):
        raise RuntimeError("Defuddle extraction is disabled")
    command = _defuddle_command()
    if not command:
        raise RuntimeError("Defuddle CLI is not installed")
    timeout = float(getattr(rt, "web_scrape_timeout_sec", 30) or 30)
    last_error = ""
    for attempt in range(_SCRAPE_RETRIES):
        try:
            completed = subprocess.run(
                [*command, "parse", url, "--md"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            markdown = (completed.stdout or "").strip()
            if completed.returncode != 0:
                stderr = (completed.stderr or "").strip()
                raise RuntimeError(stderr or f"Defuddle exited with {completed.returncode}")
            if len(markdown) < 80:
                raise RuntimeError("Defuddle returned too little content")
            return markdown
        except Exception as exc:
            last_error = str(exc)
            if attempt < _SCRAPE_RETRIES - 1:
                time.sleep(1.0 + attempt)
    raise RuntimeError(last_error or "Defuddle failed")


def _run_defuddle_prop(url: str, prop: str) -> str:
    rt = get_runtime_settings()
    if not bool(getattr(rt, "defuddle_enabled", True)):
        return ""
    command = _defuddle_command()
    if not command:
        return ""
    timeout = float(getattr(rt, "web_scrape_timeout_sec", 30) or 30)
    completed = subprocess.run(
        [*command, "parse", url, "-p", prop],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        return ""
    return (completed.stdout or "").strip()


def _fetch_jina_markdown(url: str) -> tuple[str, dict[str, Any]]:
    rt = get_runtime_settings()
    if not bool(getattr(rt, "jina_reader_enabled", True)):
        raise RuntimeError("Jina Reader fallback is disabled")
    base = str(getattr(rt, "jina_reader_api_base", "https://r.jina.ai") or "https://r.jina.ai").strip().rstrip("/")
    if not base:
        base = "https://r.jina.ai"
    timeout = float(getattr(rt, "web_scrape_timeout_sec", 30) or 30)
    request_url = f"{base}/{url}"
    headers = {
        "Accept": "application/json",
        "User-Agent": "MediaProcessPipeline/1.0",
    }
    api_key = str(getattr(rt, "jina_reader_api_key", "") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-With-Generated-Alt"] = "true"
    if bool(getattr(rt, "jina_reader_bypass_cache", False)):
        headers["X-No-Cache"] = "true"

    body, content_type = _get_text_with_retries(request_url, headers=headers, timeout=timeout, label="Jina Reader")

    if "application/json" in content_type:
        payload = json.loads(body)
        if isinstance(payload, dict):
            data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            markdown = str(data.get("content") or data.get("markdown") or "")
            if markdown:
                return markdown, data
    if body.strip().startswith("{"):
        try:
            payload = json.loads(body)
            data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
            if isinstance(data, dict):
                markdown = str(data.get("content") or data.get("markdown") or "")
                if markdown:
                    return markdown, data
        except json.JSONDecodeError:
            pass
    markdown = body.strip()
    if len(markdown) < 80:
        raise RuntimeError("Jina Reader returned too little content")
    return markdown, {"title": _extract_title(markdown, url)}


def _download_binary(url: str, *, referer: str) -> tuple[bytes, str]:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": referer,
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    rt = get_runtime_settings()
    timeout = float(getattr(rt, "web_scrape_timeout_sec", 30) or 30)
    try:
        import httpx

        with httpx.Client(
            headers=headers,
            follow_redirects=True,
            timeout=timeout,
            **httpx_client_kwargs(url),
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            content_type = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            return resp.content, content_type
    except Exception as exc:
        log_event(logger, logging.DEBUG, "webpage.image.httpx_failed", url=url, error=exc)

    req = urllib.request.Request(url, headers=headers)
    with urllib_urlopen(req, timeout=timeout) as resp:
        content_type = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        return resp.read(_MAX_IMAGE_BYTES + 1), content_type


def _image_download_candidates(url: str) -> list[str]:
    candidates = []
    for candidate in _bilibili_image_candidates(url):
        if candidate not in candidates:
            candidates.append(candidate)
    if url not in candidates:
        candidates.append(url)
    proxy_url = _image_proxy_url(url)
    if proxy_url:
        candidates.append(proxy_url)
    return candidates


def _bilibili_image_candidates(url: str) -> list[str]:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if not (host == "hdslb.com" or host.endswith(".hdslb.com")):
        return []
    path = parsed.path
    if "@" not in path:
        return [url]
    original_path = path.split("@", 1)[0]
    original = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, original_path, "", "", ""))
    return [original, url]


def _image_proxy_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    if _is_private_host(parsed.hostname):
        return ""
    stripped = urllib.parse.urlunparse(("", "", parsed.netloc + parsed.path, "", parsed.query, ""))
    return "https://images.weserv.nl/?url=" + urllib.parse.quote(stripped, safe="/:?=&%")


def _is_private_host(hostname: str) -> bool:
    host = hostname.strip().lower()
    if host in {"localhost"} or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)
    except ValueError:
        return False


def _get_text(url: str, *, headers: dict[str, str], timeout: float, label: str) -> tuple[str, str]:
    try:
        import httpx

        with httpx.Client(
            headers=headers,
            follow_redirects=True,
            timeout=timeout,
            **httpx_client_kwargs(url),
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text, (resp.headers.get("Content-Type") or "").lower()
    except Exception as httpx_exc:
        log_event(logger, logging.DEBUG, "webpage.text.httpx_failed", url=url, label=label, error=httpx_exc)

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib_urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace"), (resp.headers.get("Content-Type") or "").lower()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"{label} failed ({exc.code}): {detail}") from exc


def _get_text_with_retries(url: str, *, headers: dict[str, str], timeout: float, label: str) -> tuple[str, str]:
    last_error: Exception | None = None
    for attempt in range(_SCRAPE_RETRIES):
        try:
            return _get_text(url, headers=headers, timeout=timeout, label=label)
        except Exception as exc:
            last_error = exc
            if attempt < _SCRAPE_RETRIES - 1:
                time.sleep(1.0 + attempt)
    raise RuntimeError(str(last_error) if last_error else f"{label} failed")


def _defuddle_command() -> list[str]:
    bundled = _bundled_defuddle_cli()
    node = shutil.which("node")
    if bundled and node:
        return [node, str(bundled)]

    executable = shutil.which("defuddle")
    if not executable:
        return []
    if executable.lower().endswith(".ps1"):
        cmd_shim = Path(executable).with_suffix(".cmd")
        if cmd_shim.exists():
            return [str(cmd_shim)]
        return [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            executable,
        ]
    return [executable]


def _bundled_defuddle_cli() -> Path | None:
    repo_root = Path(__file__).resolve().parents[6]
    candidate = repo_root / "web" / "node_modules" / "defuddle" / "dist" / "cli.js"
    return candidate if candidate.exists() else None


def _extension_for_image(url: str, content_type: str) -> str:
    parsed_ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if parsed_ext in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".svg"}:
        return ".jpg" if parsed_ext == ".jpeg" else parsed_ext
    guessed = mimetypes.guess_extension(content_type or "")
    if guessed:
        return ".jpg" if guessed == ".jpe" else guessed
    return ".jpg"


def _resolve_url(raw_url: str, page_url: str) -> str:
    value = html.unescape(str(raw_url or "").strip().strip("'\""))
    if not value or value.startswith(("data:", "blob:", "mailto:")):
        return ""
    return urllib.parse.urljoin(page_url, value)


def _extract_title(markdown: str, url: str) -> str:
    jina_title = _JINA_TITLE_RE.search(markdown)
    if jina_title:
        return _clean_title(jina_title.group(1))
    title = _TITLE_RE.search(markdown)
    if title:
        return _clean_title(title.group(1))
    parsed = urllib.parse.urlparse(url)
    stem = Path(parsed.path.rstrip("/") or parsed.hostname or "webpage").name
    return _clean_title(urllib.parse.unquote(stem)) or "webpage"


def _clean_title(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().strip("#")).strip()
