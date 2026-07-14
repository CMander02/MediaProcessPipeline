"""Xiaohongshu note extraction and video download.

The public web page embeds note data in ``window.__INITIAL_STATE__``. This
module follows that path instead of trying to use yt-dlp's generic extractor:
resolve share links, fetch the canonical note page, parse the initial state,
select a playable video stream, download it, then extract pipeline-ready WAV.
"""

from __future__ import annotations

import html
import json
import logging
import random
import re
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app.core.network import runtime_proxy_url, urllib_urlopen
from app.core.settings import get_runtime_settings

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 1024 * 1024
_IMAGE_CANDIDATE_TIMEOUT_SEC = 20
_IMAGE_CANDIDATE_ATTEMPTS = 1
_IMAGE_ORIGINAL_HOSTS = (
    "ci.xiaohongshu.com",
    "sns-img-hw.xhscdn.com",
    "sns-img-bd.xhscdn.com",
    "sns-img-qn.xhscdn.com",
    "sns-img-qc.xhscdn.com",
)
_XHS_IMAGE_STRATEGIES = (
    "raw_url",
    "cdn_fallback",
    "browser_request",
    "browser_interactive",
)
_XHS_DEFAULT_IMAGE_STRATEGY_ORDER = (
    "raw_url",
    "cdn_fallback",
    "browser_request",
    "browser_interactive",
)
_SHORT_LINK_RE = re.compile(r"https?://xhslink\.com/[A-Za-z0-9/]+")
_FULL_URL_RE = re.compile(r"https?://(?:www\.)?xiaohongshu\.com/[^\s]+")
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36 Edg/137.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
]
_UA = random.choice(_UA_POOL)


class ImageDownloadError(RuntimeError):
    """Image candidate download failed with structured diagnostics."""

    def __init__(self, message: str, diagnostics: dict[str, Any]):
        super().__init__(message)
        self.diagnostics = diagnostics


class ImageDownloadCancelled(RuntimeError):
    """Image download was interrupted by task pause/cancel/delete."""


@dataclass(frozen=True)
class PostInfo:
    post_id: str
    xsec_token: str | None
    url: str


def is_xiaohongshu_url(value: str) -> bool:
    """Return True when text contains a Xiaohongshu note or share URL."""
    return bool(_SHORT_LINK_RE.search(value) or _FULL_URL_RE.search(value))


def resolve_url(value: str) -> str:
    """Resolve xhslink.com share text/URL to a Xiaohongshu URL when needed."""
    short = _SHORT_LINK_RE.search(value)
    if short:
        req = urllib.request.Request(short.group(0), headers=_headers())
        with urllib_urlopen(req, timeout=20) as resp:
            return resp.geturl()

    full = _FULL_URL_RE.search(value)
    if full:
        return html.unescape(full.group(0).rstrip("。），,)]"))

    raise RuntimeError("Invalid Xiaohongshu URL: no note URL or xhslink.com URL found")


def extract_post_info(value: str) -> PostInfo:
    """Extract note id and optional xsec_token from share text or full URL."""
    url = resolve_url(value)
    parsed = urllib.parse.urlparse(url)
    path = parsed.path
    match = re.search(r"/(?:explore|discovery/item|item|user/profile)/([A-Za-z0-9]+)", path)
    if not match:
        raise RuntimeError(f"Invalid Xiaohongshu URL: cannot extract note id from {url}")
    query = urllib.parse.parse_qs(parsed.query)
    token_values = query.get("xsec_token")
    xsec_token = token_values[0] if token_values else None
    return PostInfo(post_id=match.group(1), xsec_token=xsec_token, url=url)


def fetch_metadata(value: str) -> dict[str, Any]:
    """Fetch and normalize metadata for a Xiaohongshu note."""
    info = extract_post_info(value)
    page_url = _canonical_note_url(info)
    page_html = _fetch_text(page_url, referer="https://www.xiaohongshu.com/")
    state = _extract_initial_state(page_html)
    note = _extract_note(state, info.post_id)
    video_url, video_stream = _extract_video_url(note)
    image_url_candidates = _extract_image_url_candidates(note)
    image_urls = [candidates[0] for candidates in image_url_candidates if candidates]
    note_type = str(note.get("type") or "").lower()
    is_video = bool(video_url or note_type == "video" or note.get("video"))

    title = _extract_note_title(note, info.post_id)
    description = note.get("desc") or note.get("title")
    user = note.get("user") or note.get("userInfo") or {}
    published = _parse_xhs_timestamp(note.get("time") or note.get("lastUpdateTime"))

    content_subtype = "video_note" if is_video else "image_note"
    uploader_id = user.get("userId") or user.get("id")

    return {
        "id": info.post_id,
        "title": str(title),
        "description": str(description) if description else None,
        "uploader": user.get("nickname") or user.get("name"),
        "uploader_id": str(uploader_id) if uploader_id else None,
        "platform": "xiaohongshu",
        "content_subtype": content_subtype,
        "duration": _extract_duration(note),
        "upload_date": published.strftime("%Y%m%d") if published else None,
        "timestamp": int(published.timestamp()) if published else None,
        "webpage_url": page_url,
        "original_url": info.url,
        "thumbnail": _extract_thumbnail(note, image_urls),
        "url": video_url,
        "ext": "mp4" if video_url else None,
        "media_type": "video" if is_video else "image",
        "tags": ["小红书"],
        "extra": {
            "platform": "xiaohongshu",
            "post_id": info.post_id,
            "xsec_token": info.xsec_token,
            "note_type": note_type,
            "is_video": is_video,
            "image_urls": image_urls,
            "image_url_candidates": image_url_candidates,
            "video_url": video_url,
            "video_stream": video_stream,
            "liked_count": _nested_get(note, ["interactInfo", "likedCount"]),
            "collected_count": _nested_get(note, ["interactInfo", "collectedCount"]),
            "comment_count": _nested_get(note, ["interactInfo", "commentCount"]),
        },
    }


def download_video(info: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    """Download Xiaohongshu video and extract a 16k mono WAV for ASR."""
    video_url = info.get("url") or (info.get("extra") or {}).get("video_url")
    if not video_url:
        raise RuntimeError("Xiaohongshu note is not a video note or no public video URL was found")

    output_dir.mkdir(parents=True, exist_ok=True)
    title = _sanitize_filename(str(info.get("title") or info.get("id") or "xiaohongshu_video"))
    video_path = _dedupe_path(output_dir / f"{title}.mp4")
    wav_path = _dedupe_path(output_dir / f"{title}.wav")
    referer = str(info.get("webpage_url") or "https://www.xiaohongshu.com/")

    logger.info(f"Downloading Xiaohongshu video: {video_url}")
    video_urls = _video_download_urls(str(video_url), info)
    _download_file_with_fallback(video_urls, video_path, referer=referer)

    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found; cannot extract audio from Xiaohongshu video")

    result = subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(video_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(wav_path),
            "-y",
        ],
        capture_output=True,
        timeout=600,
    )
    if result.returncode != 0 or not wav_path.exists():
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"ffmpeg audio extraction failed: {stderr[-500:]}")
    return video_path, wav_path


def download_images(
    info: dict[str, Any],
    output_dir: Path,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> list[Path]:
    """Download all images from an image_note and return their local paths."""
    extra = info.get("extra") or {}
    info["extra"] = extra
    image_urls: list[str] = extra.get("image_urls") or []
    image_url_candidates = extra.get("image_url_candidates") or []
    if not image_url_candidates:
        image_url_candidates = [[url] for url in image_urls]
    if not image_url_candidates:
        raise RuntimeError("Xiaohongshu note has no image URLs — is it really an image_note?")

    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    referer = str(info.get("webpage_url") or "https://www.xiaohongshu.com/")
    strategy_order = _xhs_image_strategy_order()
    paths: list[Path] = []
    diagnostics: dict[str, Any] = {
        "platform": "xiaohongshu",
        "referer": referer,
        "image_count": len(image_url_candidates),
        "strategy_order": strategy_order,
        "fail_on_missing_images": _xhs_fail_on_missing_images(),
        "success": 0,
        "failed": 0,
        "failed_indices": [],
        "images": [],
    }
    try:
        for idx, candidates in enumerate(image_url_candidates):
            _raise_if_image_download_cancelled(should_cancel)
            urls = [url for url in candidates if isinstance(url, str) and url.startswith("http")]
            if not urls:
                diagnostics["images"].append({
                    "index": idx,
                    "status": "skipped",
                    "reason": "no_http_candidates",
                    "candidate_count": 0,
                    "attempts": [],
                })
                continue
            ext = _guess_image_ext_from_urls(urls)
            dest = images_dir / f"{idx:02d}.{ext}"
            if dest.exists():
                paths.append(dest)
                diagnostics["success"] += 1
                diagnostics["images"].append({
                    "index": idx,
                    "status": "completed",
                    "reason": "already_exists",
                    "path": str(dest),
                    "candidate_count": len(urls),
                    "attempts": [],
                })
                continue
            if idx:
                _sleep_with_cancel(1.2, should_cancel)
            try:
                record = _download_image_with_strategy_order(
                    info,
                    urls,
                    dest,
                    referer=referer,
                    strategy_order=strategy_order,
                    should_cancel=should_cancel,
                )
                paths.append(dest)
                diagnostics["success"] += 1
                diagnostics["images"].append({
                    "index": idx,
                    "status": "completed",
                    "path": str(dest),
                    "candidate_count": len(urls),
                    **record,
                })
            except ImageDownloadCancelled:
                raise
            except ImageDownloadError as e:
                diagnostics["failed"] += 1
                diagnostics["failed_indices"].append(idx)
                diagnostics["images"].append({
                    "index": idx,
                    "status": "failed",
                    "candidate_count": len(urls),
                    "error": str(e),
                    **e.diagnostics,
                })
                logger.warning(f"Failed to download XHS image {idx}: {e}")
            except Exception as e:
                diagnostics["failed"] += 1
                diagnostics["failed_indices"].append(idx)
                diagnostics["images"].append({
                    "index": idx,
                    "status": "failed",
                    "candidate_count": len(urls),
                    "error": str(e),
                    "attempts": [],
                })
                logger.warning(f"Failed to download XHS image {idx}: {e}")
    except ImageDownloadCancelled:
        diagnostics["cancelled"] = True
        extra["image_download_diagnostics"] = diagnostics
        raise
    if diagnostics["failed_indices"]:
        diagnostics["url_refresh_probe"] = _probe_image_url_freshness(info, image_url_candidates)
    extra["image_download_diagnostics"] = diagnostics
    return paths


def _xhs_platform_config() -> dict[str, Any]:
    rt = get_runtime_settings()
    try:
        stored = json.loads(rt.platform_configs or "{}")
    except Exception:
        return {}
    config = stored.get("xiaohongshu")
    return config if isinstance(config, dict) else {}


def _xhs_image_strategy_order() -> list[str]:
    config = _xhs_platform_config()
    raw = config.get("image_strategy_order")
    if isinstance(raw, str):
        raw_order: list[Any] = [item.strip() for item in raw.split(",")]
    elif isinstance(raw, list):
        raw_order = raw
    else:
        raw_order = []

    order: list[str] = []
    for item in raw_order:
        value = str(item or "").strip()
        if value in _XHS_IMAGE_STRATEGIES and value not in order:
            order.append(value)
    for value in _XHS_DEFAULT_IMAGE_STRATEGY_ORDER:
        if value not in order:
            order.append(value)
    return order


def _xhs_fail_on_missing_images() -> bool:
    value = _xhs_platform_config().get("fail_on_missing_images", True)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _download_image_with_strategy_order(
    info: dict[str, Any],
    urls: list[str],
    dest: Path,
    referer: str,
    strategy_order: list[str],
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    strategy_records: list[dict[str, Any]] = []
    flattened_attempts: list[dict[str, Any]] = []
    last_error: Exception | None = None

    for strategy in strategy_order:
        _raise_if_image_download_cancelled(should_cancel)
        if strategy in {"raw_url", "cdn_fallback"}:
            strategy_urls = _image_urls_for_strategy(urls, strategy)
            if not strategy_urls:
                strategy_records.append({
                    "strategy": strategy,
                    "status": "skipped",
                    "reason": "no_matching_candidates",
                    "attempts": [],
                })
                continue
            try:
                record = _download_file_candidates(
                    strategy_urls,
                    dest,
                    referer=referer,
                    strategy=strategy,
                    should_cancel=should_cancel,
                )
                attempts = record.get("attempts", [])
                flattened_attempts.extend(attempts)
                return {
                    "strategy": strategy,
                    "attempts": [
                        attempt for attempt in flattened_attempts
                        if attempt.get("status") != "error"
                    ],
                    "strategy_attempts": [
                        *strategy_records,
                        {"strategy": strategy, "status": "completed", "attempts": attempts},
                    ],
                }
            except ImageDownloadCancelled:
                raise
            except ImageDownloadError as e:
                last_error = e
                attempts = e.diagnostics.get("attempts", [])
                if isinstance(attempts, list):
                    flattened_attempts.extend(attempts)
                strategy_records.append({
                    "strategy": strategy,
                    "status": "failed",
                    "error": str(e),
                    **e.diagnostics,
                })
                continue

        if strategy == "browser_request":
            record = _download_image_with_browser_request(
                info,
                urls,
                dest,
                referer=referer,
                should_cancel=should_cancel,
            )
        elif strategy == "browser_interactive":
            record = _download_image_with_browser_interactive(
                info,
                urls,
                dest,
                referer=referer,
                should_cancel=should_cancel,
            )
        else:
            continue

        attempts = record.get("attempts", [])
        if isinstance(attempts, list):
            flattened_attempts.extend(attempts)
        strategy_records.append(record)
        if record.get("status") == "completed" and dest.exists():
            return {
                "strategy": strategy,
                "attempts": flattened_attempts,
                "strategy_attempts": strategy_records,
            }
        last_error = RuntimeError(str(record.get("error") or record.get("reason") or f"{strategy} failed"))

    raise ImageDownloadError(
        f"All Xiaohongshu image strategies failed: {last_error}",
        {"attempts": flattened_attempts, "strategy_attempts": strategy_records},
    )


def _image_urls_for_strategy(urls: list[str], strategy: str) -> list[str]:
    selected: list[str] = []
    for url in urls:
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        is_generated_cdn = host in _IMAGE_ORIGINAL_HOSTS and host != "ci.xiaohongshu.com"
        if strategy == "cdn_fallback" and not is_generated_cdn:
            continue
        if strategy == "raw_url" and is_generated_cdn:
            continue
        if url not in selected:
            selected.append(url)
    return selected


def _download_image_with_browser_request(
    info: dict[str, Any],
    urls: list[str],
    dest: Path,
    referer: str,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        return {
            "strategy": "browser_request",
            "status": "failed",
            "reason": "playwright_not_installed",
            "error": str(e),
            "attempts": [],
        }

    attempts: list[dict[str, Any]] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = _new_xhs_browser_context(browser)
            page = context.new_page()
            try:
                page.goto(referer, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1200)
            except Exception:
                pass
            ok = _download_image_via_browser_context(
                context,
                urls,
                dest,
                referer=referer,
                attempts=attempts,
                via="browser_request",
                should_cancel=should_cancel,
            )
            browser.close()
            if ok:
                return {
                    "strategy": "browser_request",
                    "status": "completed",
                    "reason": "browser_request",
                    "path": str(dest),
                    "candidate_count": len(urls),
                    "attempts": attempts,
                }
    except ImageDownloadCancelled:
        raise
    except Exception as e:
        attempts.append({
            "status": "error",
            "error": str(e),
            "via": "browser_request",
        })
    return {
        "strategy": "browser_request",
        "status": "failed",
        "reason": "browser_request_failed",
        "candidate_count": len(urls),
        "attempts": attempts,
    }


def _download_image_with_browser_interactive(
    info: dict[str, Any],
    urls: list[str],
    dest: Path,
    referer: str,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        return {
            "strategy": "browser_interactive",
            "status": "failed",
            "reason": "playwright_not_installed",
            "error": str(e),
            "attempts": [],
        }

    attempts: list[dict[str, Any]] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = _new_xhs_browser_context(browser)
            page = context.new_page()
            discovered = _collect_xhs_browser_image_urls(page, referer, should_cancel=should_cancel)
            target_urls = _match_browser_image_urls(discovered, urls)
            if not target_urls:
                target_urls = urls
            ok = _download_image_via_browser_context(
                context,
                target_urls,
                dest,
                referer=referer,
                attempts=attempts,
                via="browser_interactive",
                should_cancel=should_cancel,
            )
            browser.close()
            if ok:
                return {
                    "strategy": "browser_interactive",
                    "status": "completed",
                    "reason": "browser_interactive",
                    "path": str(dest),
                    "candidate_count": len(target_urls),
                    "discovered_count": len(discovered),
                    "attempts": attempts,
                }
            return {
                "strategy": "browser_interactive",
                "status": "failed",
                "reason": "browser_interactive_failed",
                "candidate_count": len(target_urls),
                "discovered_count": len(discovered),
                "attempts": attempts,
            }
    except ImageDownloadCancelled:
        raise
    except Exception as e:
        attempts.append({
            "status": "error",
            "error": str(e),
            "via": "browser_interactive",
        })
        return {
            "strategy": "browser_interactive",
            "status": "failed",
            "reason": "browser_interactive_failed",
            "candidate_count": len(urls),
            "attempts": attempts,
        }


def _new_xhs_browser_context(browser: Any) -> Any:
    context_kwargs: dict[str, Any] = {
        "locale": "zh-CN",
        "user_agent": _UA,
        "viewport": {"width": 1280, "height": 860},
    }
    storage_state = _storage_state_path()
    if storage_state.exists():
        context_kwargs["storage_state"] = str(storage_state)
    return browser.new_context(**context_kwargs)


def _collect_xhs_browser_image_urls(
    page: Any,
    referer: str,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> list[str]:
    seen: set[str] = set()

    def add_url(value: Any) -> None:
        if isinstance(value, str):
            for part in re.split(r"[\s,]+", value):
                url = part.strip()
                if url.startswith("//"):
                    url = f"https:{url}"
                if url.startswith("http") and _looks_like_image_url(url):
                    seen.add(_normalize_url(url))

    try:
        page.goto(referer, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1200)
        for _ in range(5):
            _raise_if_image_download_cancelled(should_cancel)
            page.mouse.wheel(0, 700)
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(500)
        raw_urls = page.evaluate(
            """() => {
              const urls = new Set();
              const add = (value) => {
                if (!value || typeof value !== "string") return;
                value.split(/[\\s,]+/).forEach((part) => {
                  const url = part.trim();
                  if (url.startsWith("http") || url.startsWith("//")) urls.add(url);
                });
              };
              document.querySelectorAll("img, source").forEach((node) => {
                add(node.currentSrc);
                add(node.src);
                add(node.srcset);
                add(node.getAttribute("data-src"));
                add(node.getAttribute("data-original"));
              });
              performance.getEntriesByType("resource").forEach((entry) => add(entry.name));
              return Array.from(urls);
            }"""
        )
        if isinstance(raw_urls, list):
            for url in raw_urls:
                add_url(url)
    except ImageDownloadCancelled:
        raise
    except Exception as e:
        logger.warning("XHS browser interactive collection failed: %s", e)
    return list(seen)


def _looks_like_image_url(url: str) -> bool:
    lower = url.lower()
    return (
        "xhscdn.com" in lower
        or "xiaohongshu.com" in lower and ("/spectrum/" in lower or "/ci/" in lower or "/notes/" in lower)
        or any(marker in lower for marker in (".jpg", ".jpeg", ".png", ".webp", "image"))
    )


def _match_browser_image_urls(discovered: list[str], target_urls: list[str]) -> list[str]:
    target_ids = {_candidate_image_id(url) for url in target_urls}
    target_ids.discard("")
    selected: list[str] = []
    for url in discovered:
        image_id = _candidate_image_id(url)
        if target_ids and image_id not in target_ids:
            continue
        if url not in selected:
            selected.append(url)
            for variant in _image_original_url_variants(url):
                if variant not in selected:
                    selected.append(variant)
    return selected


def _candidate_image_id(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return ""
    return parts[-1].split("!")[0]


def _download_image_via_browser_context(
    context: Any,
    urls: list[str],
    dest: Path,
    referer: str,
    attempts: list[dict[str, Any]],
    via: str,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> bool:
    seen: set[str] = set()
    for url in urls:
        _raise_if_image_download_cancelled(should_cancel)
        if not isinstance(url, str) or not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        try:
            response = context.request.get(
                url,
                headers=_headers(referer),
                timeout=20000,
            )
            attempts.append({
                "url_redacted": _redact_url_for_log(url),
                "host": urllib.parse.urlparse(url).hostname,
                "status": response.status,
                "ok": response.ok,
                "via": via,
            })
            if response.ok:
                body = response.body()
                if body:
                    dest.write_bytes(body)
                    return True
        except Exception as e:
            attempts.append({
                "url_redacted": _redact_url_for_log(url),
                "status": "error",
                "error": str(e),
                "via": via,
            })
            dest.unlink(missing_ok=True)
    return False


def _download_failed_images_with_browser(
    info: dict[str, Any],
    output_dir: Path,
    image_url_candidates: list[list[str]],
    failed_indices: list[int],
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    if not failed_indices:
        return []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    referer = str(info.get("webpage_url") or "https://www.xiaohongshu.com/")
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    storage_state = _storage_state_path()
    records: list[dict[str, Any]] = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context_kwargs: dict[str, Any] = {
                "locale": "zh-CN",
                "user_agent": _UA,
                "viewport": {"width": 1280, "height": 860},
            }
            if storage_state.exists():
                context_kwargs["storage_state"] = str(storage_state)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            try:
                page.goto(referer, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
            except Exception:
                pass

            for idx in failed_indices:
                _raise_if_image_download_cancelled(should_cancel)
                if idx < 0 or idx >= len(image_url_candidates):
                    continue
                urls = [url for url in image_url_candidates[idx] if isinstance(url, str) and url.startswith("http")]
                if not urls:
                    continue
                dest = images_dir / f"{idx:02d}.{_guess_image_ext_from_urls(urls)}"
                if dest.exists():
                    records.append({
                        "index": idx,
                        "status": "completed",
                        "reason": "already_exists_browser_fallback",
                        "path": str(dest),
                        "candidate_count": len(urls),
                        "attempts": [],
                    })
                    continue
                attempts: list[dict[str, Any]] = []
                for url in urls:
                    _raise_if_image_download_cancelled(should_cancel)
                    try:
                        response = context.request.get(
                            url,
                            headers=_headers(referer),
                            timeout=15000,
                        )
                        attempts.append({
                            "url": url,
                            "status": response.status,
                            "ok": response.ok,
                            "via": "playwright_request",
                        })
                        if response.ok:
                            body = response.body()
                            if body:
                                dest.write_bytes(body)
                                records.append({
                                    "index": idx,
                                    "status": "completed",
                                    "reason": "browser_fallback",
                                    "path": str(dest),
                                    "candidate_count": len(urls),
                                    "attempts": attempts,
                                })
                                break
                    except Exception as e:
                        attempts.append({
                            "url": url,
                            "status": "error",
                            "error": str(e),
                            "via": "playwright_request",
                        })
                if not dest.exists():
                    records.append({
                        "index": idx,
                        "status": "failed",
                        "reason": "browser_fallback_failed",
                        "candidate_count": len(urls),
                        "attempts": attempts,
                    })
            browser.close()
    except ImageDownloadCancelled:
        raise
    except Exception as e:
        logger.warning("XHS browser image fallback failed: %s", e)
    return [record for record in records if record.get("status") == "completed"]


def _guess_image_ext_from_urls(urls: list[str]) -> str:
    for url in urls:
        ext = _guess_image_ext(url)
        if ext:
            return ext
    return "jpg"


def _guess_image_ext(url: str) -> str:
    if "webp" in url.lower():
        return "webp"
    match = re.search(r"\.([a-zA-Z0-9]{2,5})(?:[?#!]|$)", url.split("?")[0])
    if match:
        ext = match.group(1).lower()
        if ext in ("jpg", "jpeg", "png", "webp", "gif"):
            return ext
    return "jpg"


def _canonical_note_url(info: PostInfo) -> str:
    query = {}
    if info.xsec_token:
        query["xsec_token"] = info.xsec_token
    qs = urllib.parse.urlencode(query)
    suffix = f"?{qs}" if qs else ""
    return f"https://www.xiaohongshu.com/explore/{info.post_id}{suffix}"


def _headers(referer: str = "https://www.xiaohongshu.com/") -> dict[str, str]:
    headers = {
        "User-Agent": _UA,
        "Referer": referer,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
            "image/webp,image/apng,image/*,*/*;q=0.8"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cookie": "webId=anonymous",
    }
    cookie = (get_runtime_settings().xiaohongshu_cookie or "").strip()
    if cookie:
        headers["Cookie"] = cookie
    else:
        storage_cookie = _storage_state_cookie_header()
        if storage_cookie:
            headers["Cookie"] = storage_cookie
    return headers


def _storage_state_path() -> Path:
    rt = get_runtime_settings()
    configured = str(getattr(rt, "xiaohongshu_storage_state_path", "") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(rt.data_root).resolve() / "auth" / "xiaohongshu_storage_state.json"


def _storage_state_cookie_header() -> str:
    path = _storage_state_path()
    if not path.exists():
        return ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    cookies = raw.get("cookies") if isinstance(raw, dict) else None
    if not isinstance(cookies, list):
        return ""
    parts: list[str] = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        domain = str(cookie.get("domain") or "").lstrip(".").lower()
        if not (domain == "xiaohongshu.com" or domain.endswith(".xiaohongshu.com")):
            continue
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "").strip()
        if name and value:
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def auth_state_status() -> dict[str, Any]:
    path = _storage_state_path()
    status: dict[str, Any] = {
        "configured_cookie": bool((get_runtime_settings().xiaohongshu_cookie or "").strip()),
        "storage_state_path": str(path),
        "storage_state_exists": path.exists(),
        "cookie_count": 0,
        "login_cookie": False,
    }
    if not path.exists():
        return status
    try:
        status["updated_at"] = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
        raw = json.loads(path.read_text(encoding="utf-8"))
        cookies = raw.get("cookies") if isinstance(raw, dict) else []
        names = []
        if not isinstance(cookies, list):
            cookies = []
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            domain = str(cookie.get("domain") or "").lstrip(".").lower()
            if domain == "xiaohongshu.com" or domain.endswith(".xiaohongshu.com"):
                name = str(cookie.get("name") or "")
                names.append(name)
        status["cookie_count"] = len(names)
        status["login_cookie"] = "web_session" in names
    except Exception as e:
        status["error"] = str(e)
    return status


def interactive_login(timeout_sec: int = 180) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError("Playwright is not installed. Run `uv run playwright install chromium`.") from e

    timeout_sec = max(30, min(int(timeout_sec), 600))
    path = _storage_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context_kwargs: dict[str, Any] = {
            "locale": "zh-CN",
            "viewport": {"width": 1280, "height": 860},
            "user_agent": _UA,
        }
        if path.exists():
            context_kwargs["storage_state"] = str(path)
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        page.goto("https://www.xiaohongshu.com/explore", wait_until="domcontentloaded", timeout=30000)
        start = time.time()
        while time.time() - start < timeout_sec:
            cookies = context.cookies("https://www.xiaohongshu.com")
            if any(cookie.get("name") == "web_session" for cookie in cookies):
                break
            if page.is_closed():
                break
            page.wait_for_timeout(1000)
        context.storage_state(path=str(path))
        browser.close()
    return auth_state_status()


def _fetch_text(url: str, referer: str) -> str:
    req = urllib.request.Request(url, headers=_headers(referer))
    with urllib_urlopen(req, timeout=25) as resp:
        raw = resp.read()
        encoding = resp.headers.get_content_charset() or "utf-8"
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(encoding, errors="replace")


def _extract_initial_state(page_html: str) -> dict[str, Any]:
    match = re.search(
        r"window\.__INITIAL_STATE__\s*=\s*({[\s\S]*?})(?:</script>|;|$)",
        page_html,
    )
    if not match:
        raise RuntimeError("Xiaohongshu initial state not found")
    raw = html.unescape(match.group(1))
    raw = raw.replace(":undefined", ":null")
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse Xiaohongshu initial state: {e}") from e
    if not isinstance(state, dict):
        raise RuntimeError("Xiaohongshu initial state is not an object")
    return state


def _extract_note(state: dict[str, Any], post_id: str) -> dict[str, Any]:
    note_detail_map = _nested_get(state, ["note", "noteDetailMap"])
    if isinstance(note_detail_map, dict):
        wrapper = note_detail_map.get(post_id)
        if isinstance(wrapper, dict):
            note = wrapper.get("note") or wrapper.get("noteInfo") or wrapper
            if isinstance(note, dict):
                return note

    found = _find_note_dict(state, post_id)
    if found:
        return found
    raise RuntimeError(f"Xiaohongshu note data not found for {post_id}")


def _find_note_dict(value: Any, post_id: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if (
            value.get("noteId") == post_id
            or value.get("id") == post_id
            or value.get("note_id") == post_id
        ) and ("type" in value or "video" in value or "imageList" in value):
            return value
        for child in value.values():
            found = _find_note_dict(child, post_id)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_note_dict(child, post_id)
            if found:
                return found
    return None


def _extract_video_url(note: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    stream = _nested_get(note, ["video", "media", "stream"])
    if not isinstance(stream, dict):
        stream = _nested_get(note, ["video", "stream"])
    candidates: list[dict[str, Any]] = []
    if isinstance(stream, dict):
        for codec in ("h264", "h265", "av1"):
            tracks = stream.get(codec)
            if isinstance(tracks, list):
                candidates.extend(t for t in tracks if isinstance(t, dict))

    for item in candidates:
        url = _pick_stream_url(item)
        if url:
            return url, item

    video_list = note.get("videoList")
    if isinstance(video_list, list):
        for item in video_list:
            if isinstance(item, dict):
                url = _pick_stream_url(item)
                if url:
                    return url, item

    for key in ("videoUrl", "url", "masterUrl", "master_url"):
        value = note.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return _normalize_url(value), {"source_key": key}

    return None, None


def _pick_stream_url(item: dict[str, Any]) -> str | None:
    for key in ("masterUrl", "master_url", "url", "backupUrl", "backup_url"):
        value = item.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return _normalize_url(value)
        if isinstance(value, list):
            for candidate in value:
                if isinstance(candidate, str) and candidate.startswith("http"):
                    return _normalize_url(candidate)
    backup_urls = item.get("backupUrls") or item.get("backup_urls")
    if isinstance(backup_urls, list):
        for candidate in backup_urls:
            if isinstance(candidate, str) and candidate.startswith("http"):
                return _normalize_url(candidate)
    return None


def _extract_image_urls(note: dict[str, Any]) -> list[str]:
    return [candidates[0] for candidates in _extract_image_url_candidates(note) if candidates]


def _extract_image_url_candidates(note: dict[str, Any]) -> list[list[str]]:
    images = note.get("imageList")
    if not isinstance(images, list):
        return []
    all_candidates: list[list[str]] = []
    for image in images:
        if not isinstance(image, dict):
            continue
        candidates: list[str] = []

        def add_url(value: Any) -> None:
            if not isinstance(value, str) or not value.startswith("http"):
                return
            normalized = _normalize_url(value)
            for candidate in [*_image_original_url_variants(normalized), normalized]:
                if candidate and candidate not in candidates:
                    candidates.append(candidate)

        add_url(image.get("urlDefault"))
        add_url(image.get("url"))
        add_url(image.get("urlPre"))
        info_list = image.get("infoList")
        if isinstance(info_list, list):
            for item in info_list:
                if isinstance(item, dict):
                    add_url(item.get("url"))
        if candidates:
            all_candidates.append(candidates)
    return all_candidates


def _transform_image_to_original(value: str) -> str:
    variants = _image_original_url_variants(value)
    return variants[0] if variants else value


def _image_original_url_variants(value: str) -> list[str]:
    try:
        parsed = urllib.parse.urlparse(value)
    except Exception:
        return [value]
    path_parts = [s for s in parsed.path.split("/") if s]
    if "xhscdn.com" in parsed.netloc:
        subdirs_and_id = path_parts[2:] if len(path_parts) > 2 else path_parts
    elif parsed.netloc == "ci.xiaohongshu.com":
        subdirs_and_id = path_parts
    else:
        return [value]
    if not subdirs_and_id:
        return [value]
    image_id = subdirs_and_id[-1].split("!")[0]
    subdirs_and_id[-1] = image_id
    path = "/".join(subdirs_and_id)
    variants = [f"https://{host}/{path}" for host in _IMAGE_ORIGINAL_HOSTS]
    if parsed.netloc == "ci.xiaohongshu.com":
        canonical = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if canonical not in variants:
            variants.insert(0, canonical)
    return variants


def _extract_thumbnail(note: dict[str, Any], image_urls: list[str]) -> str | None:
    cover = _nested_get(note, ["video", "image", "url"]) or _nested_get(
        note, ["video", "image", "urlDefault"]
    )
    if isinstance(cover, str):
        return cover
    return image_urls[0] if image_urls else None


def _extract_duration(note: dict[str, Any]) -> float | None:
    candidates = [
        _nested_get(note, ["video", "media", "duration"]),
        _nested_get(note, ["video", "duration"]),
        note.get("duration"),
    ]
    for value in candidates:
        if isinstance(value, (int, float)):
            duration = float(value)
            return duration / 1000 if duration > 10000 else duration
        if isinstance(value, str) and value.isdigit():
            duration = float(value)
            return duration / 1000 if duration > 10000 else duration
    return None


def _parse_xhs_timestamp(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000 if value > 10_000_000_000 else float(value)
        try:
            return datetime.fromtimestamp(seconds)
        except (OSError, ValueError):
            return None
    return None


def _clean_note_title(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"\s+", " ", text).strip()


def _extract_note_title(note: dict[str, Any], fallback: str) -> str:
    for key in ("title", "displayTitle"):
        title = _clean_note_title(note.get(key))
        if title:
            return title

    description = str(note.get("desc") or "")
    for line in description.splitlines():
        title = _clean_note_title(line)
        if title:
            return title

    return fallback


def _nested_get(data: Any, path: list[str]) -> Any:
    cur = data
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _normalize_url(value: str) -> str:
    value = html.unescape(value)
    if value.startswith("http://"):
        value = "https://" + value[len("http://") :]
    return value


def _sanitize_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return value[:100] or "xiaohongshu_video"


def _dedupe_path(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _download_file_candidates(
    urls: list[str],
    dest: Path,
    referer: str,
    *,
    strategy: str = "direct",
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    last_error: Exception | None = None
    seen: set[str] = set()
    attempts: list[dict[str, Any]] = []
    for order, url in enumerate(urls):
        _raise_if_image_download_cancelled(should_cancel)
        if url in seen:
            continue
        seen.add(url)
        attempt = _download_attempt_record(order, url, referer)
        attempt["strategy"] = strategy
        try:
            _download_file(
                url,
                dest,
                referer=referer,
                attempts=_IMAGE_CANDIDATE_ATTEMPTS,
                timeout_sec=_IMAGE_CANDIDATE_TIMEOUT_SEC,
                should_cancel=should_cancel,
            )
            attempt["status"] = "success"
            if dest.exists():
                attempt["bytes"] = dest.stat().st_size
            attempts.append(attempt)
            return {"attempts": attempts}
        except ImageDownloadCancelled:
            dest.unlink(missing_ok=True)
            dest.with_name(dest.name + ".part").unlink(missing_ok=True)
            raise
        except Exception as e:
            last_error = e
            attempt.update(_download_error_details(e))
            attempts.append(attempt)
            logger.warning(f"Xiaohongshu image download URL failed: {e}")
            dest.unlink(missing_ok=True)
            dest.with_name(dest.name + ".part").unlink(missing_ok=True)
    detail = " | ".join(
        f"{item.get('url_redacted')} -> {item.get('error_type')}: {item.get('error')}"
        for item in attempts[-3:]
    )
    raise ImageDownloadError(
        f"All Xiaohongshu image URLs failed: {last_error}; candidates: {detail}",
        {"attempts": attempts},
    )


def _download_attempt_record(order: int, url: str, referer: str) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    headers = _headers(referer)
    cookie = (get_runtime_settings().xiaohongshu_cookie or "").strip()
    return {
        "order": order,
        "status": "pending",
        "url_redacted": _redact_url_for_log(url),
        "host": parsed.hostname,
        "path_tail": Path(parsed.path).name[:48],
        "query_keys": sorted(urllib.parse.parse_qs(parsed.query).keys()),
        "url_kind": _classify_image_candidate_url(url),
        "request": {
            "referer": referer,
            "headers": sorted(k for k in headers.keys() if k.lower() != "cookie"),
            "cookie_state": "runtime_cookie" if cookie else "anonymous_webId",
            "proxy": _proxy_diagnostics(url),
        },
    }


def _classify_image_candidate_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if host == "ci.xiaohongshu.com":
        return "original"
    if "xhscdn.com" in host:
        return "preview_or_cdn"
    return "candidate"


def _proxy_diagnostics(url: str) -> dict[str, Any]:
    proxy = runtime_proxy_url()
    if proxy == "":
        return {"mode": "direct"}
    if proxy is None:
        return {"mode": "client_default"}
    return {"mode": "configured", "url": _redact_proxy_for_log(proxy)}


def _redact_proxy_for_log(proxy: str) -> str:
    try:
        parsed = urllib.parse.urlparse(proxy)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        return urllib.parse.urlunparse((parsed.scheme, f"{host}{port}", "", "", "", ""))
    except Exception:
        return proxy.split("@")[-1]


def _download_error_details(error: Exception) -> dict[str, Any]:
    root = error.__cause__ or error
    detail: dict[str, Any] = {
        "status": "failed",
        "error_type": type(root).__name__,
        "error": str(root)[:500],
        "classification": _classify_download_error(root),
    }
    if isinstance(root, urllib.error.HTTPError):
        detail["http_status"] = root.code
        detail["http_reason"] = root.reason
    elif isinstance(root, urllib.error.URLError):
        detail["url_error_reason"] = str(root.reason)
    return detail


def _classify_download_error(error: Exception) -> str:
    text = str(error).lower()
    if isinstance(error, urllib.error.HTTPError):
        if error.code in {401, 403}:
            return "headers_cookie_referer_or_auth"
        if error.code in {404, 410}:
            return "url_expired_or_invalid"
        if error.code == 429:
            return "access_frequency_limited"
        return "http_error"
    if isinstance(error, ssl.SSLError) or "ssl" in text or "eof" in text or "handshake" in text:
        return "tls_or_cdn_rejected"
    if "timed out" in text or "timeout" in text:
        return "network_timeout_or_rate_limit"
    if "connection reset" in text or "connection aborted" in text:
        return "cdn_or_proxy_rejected"
    return "network_or_cdn_error"


def _probe_image_url_freshness(info: dict[str, Any], old_candidates: list[list[str]]) -> dict[str, Any]:
    source = str(info.get("original_url") or info.get("webpage_url") or "").strip()
    if not source:
        return {"status": "skipped", "reason": "no_source_url"}
    try:
        refreshed = fetch_metadata(source)
        new_candidates = (refreshed.get("extra") or {}).get("image_url_candidates") or []
        old_fingerprints = [_candidate_fingerprint(group[0]) for group in old_candidates if group]
        new_fingerprints = [_candidate_fingerprint(group[0]) for group in new_candidates if group]
        return {
            "status": "completed",
            "old_groups": len(old_candidates),
            "new_groups": len(new_candidates),
            "changed": old_fingerprints != new_fingerprints,
            "old_first": old_fingerprints[:5],
            "new_first": new_fingerprints[:5],
        }
    except Exception as exc:
        return {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        }


def _candidate_fingerprint(url: str) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    return {
        "host": parsed.hostname,
        "path_tail": Path(parsed.path).name[:48],
        "query_keys": sorted(urllib.parse.parse_qs(parsed.query).keys()),
        "kind": _classify_image_candidate_url(url),
    }


def _redact_url_for_log(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
        tail = Path(parsed.path).name
        return f"{parsed.netloc}/.../{tail[:32]}"
    except Exception:
        return url[:80]


def _raise_if_image_download_cancelled(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel and should_cancel():
        raise ImageDownloadCancelled("Xiaohongshu image download cancelled")


def _sleep_with_cancel(seconds: float, should_cancel: Callable[[], bool] | None) -> None:
    deadline = time.monotonic() + seconds
    while True:
        _raise_if_image_download_cancelled(should_cancel)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.2))


def _download_file(
    url: str,
    dest: Path,
    referer: str,
    attempts: int = 2,
    timeout_sec: int = 25,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    last_error: Exception | None = None
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, attempts + 1):
        _raise_if_image_download_cancelled(should_cancel)
        downloaded = 0
        dest.unlink(missing_ok=True)
        part.unlink(missing_ok=True)
        req = urllib.request.Request(url, headers=_headers(referer))
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
            logger.info(f"Downloaded Xiaohongshu file: {downloaded:,} bytes -> {dest.name}")
            return
        except ImageDownloadCancelled:
            dest.unlink(missing_ok=True)
            part.unlink(missing_ok=True)
            raise
        except Exception as e:
            last_error = e
            dest.unlink(missing_ok=True)
            part.unlink(missing_ok=True)
            if attempt >= attempts:
                raise RuntimeError(f"Xiaohongshu file download failed after {attempts} attempts: {last_error}") from e
            logger.warning(f"Xiaohongshu file download retry {attempt}/{attempts}: {e}")
            _sleep_with_cancel(min(attempt, 3), should_cancel)


def _video_download_urls(primary_url: str, info: dict[str, Any]) -> list[str]:
    urls = [primary_url]
    stream = (info.get("extra") or {}).get("video_stream")
    if isinstance(stream, dict):
        for key in ("backupUrls", "backup_urls", "backupUrl", "backup_url"):
            value = stream.get(key)
            if isinstance(value, str):
                urls.append(_normalize_url(value))
            elif isinstance(value, list):
                urls.extend(_normalize_url(v) for v in value if isinstance(v, str))
    out: list[str] = []
    for url in urls:
        if url and url not in out:
            out.append(url)
    return out


def _download_file_with_fallback(urls: list[str], dest: Path, referer: str) -> None:
    last_error: Exception | None = None
    for url in urls:
        try:
            _download_file_resumable(url, dest, referer=referer)
            return
        except Exception as e:
            last_error = e
            logger.warning(f"Xiaohongshu video download URL failed: {e}")
            dest.unlink(missing_ok=True)
    raise RuntimeError(f"All Xiaohongshu video URLs failed: {last_error}")


def _download_file_resumable(url: str, dest: Path, referer: str, attempts: int = 4) -> None:
    expected_total: int | None = None
    for attempt in range(1, attempts + 1):
        downloaded = dest.stat().st_size if dest.exists() else 0
        headers = _headers(referer)
        if downloaded:
            headers["Range"] = f"bytes={downloaded}-"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib_urlopen(req, timeout=120) as resp:
                status = getattr(resp, "status", 200)
                content_length = int(resp.headers.get("Content-Length", 0))
                content_range = resp.headers.get("Content-Range", "")
                if downloaded and status != 206:
                    downloaded = 0
                    dest.unlink(missing_ok=True)
                if content_range:
                    total_match = re.search(r"/(\d+)$", content_range)
                    if total_match:
                        expected_total = int(total_match.group(1))
                elif content_length:
                    expected_total = downloaded + content_length

                mode = "ab" if downloaded else "wb"
                with open(dest, mode) as f:
                    while True:
                        chunk = resp.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        f.write(chunk)

            size = dest.stat().st_size if dest.exists() else 0
            if expected_total is None or size >= expected_total:
                logger.info(f"Downloaded Xiaohongshu video: {size:,} bytes -> {dest.name}")
                return
            raise RuntimeError(f"Incomplete download: {size}/{expected_total} bytes")
        except (TimeoutError, urllib.error.URLError, RuntimeError) as e:
            if attempt >= attempts:
                raise
            logger.warning(f"Xiaohongshu download retry {attempt}/{attempts}: {e}")
            time.sleep(min(2 * attempt, 8))
