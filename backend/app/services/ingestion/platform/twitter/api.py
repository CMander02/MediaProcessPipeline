"""X/Twitter image-note media downloads."""

from __future__ import annotations

import logging
import mimetypes
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from app.core.network import urllib_urlopen

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
_MAX_IMAGE_BYTES = 16 * 1024 * 1024


def download_images(
    info: dict[str, Any],
    output_dir: Path,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> list[Path]:
    """Download X/Twitter post images and return local paths."""
    extra = info.get("extra")
    if not isinstance(extra, dict):
        extra = {}
        info["extra"] = extra

    image_url_candidates = extra.get("image_url_candidates") or []
    image_urls = extra.get("image_urls") or []
    if not image_url_candidates:
        image_url_candidates = [[url] for url in image_urls]
    if not image_url_candidates:
        raise RuntimeError("X/Twitter post has no image URLs")

    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    referer = str(info.get("webpage_url") or "https://x.com/")
    paths: list[Path] = []
    diagnostics: dict[str, Any] = {
        "platform": "twitter",
        "referer": referer,
        "image_count": len(image_url_candidates),
        "fail_on_missing_images": True,
        "success": 0,
        "failed": 0,
        "failed_indices": [],
        "images": [],
    }

    for idx, candidates in enumerate(image_url_candidates):
        _raise_if_image_download_cancelled(should_cancel)
        urls = [url for url in candidates if isinstance(url, str) and url.startswith("http")]
        if not urls:
            diagnostics["failed"] += 1
            diagnostics["failed_indices"].append(idx)
            diagnostics["images"].append({
                "index": idx,
                "status": "failed",
                "reason": "no_http_candidates",
                "attempts": [],
            })
            continue

        dest = images_dir / f"{idx:02d}{_guess_image_ext(urls)}"
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

        try:
            record = _download_image_candidates(urls, dest, referer=referer, should_cancel=should_cancel)
            paths.append(dest)
            diagnostics["success"] += 1
            diagnostics["images"].append({
                "index": idx,
                "status": "completed",
                "path": str(dest),
                "candidate_count": len(urls),
                **record,
            })
        except Exception as exc:
            if should_cancel and should_cancel():
                diagnostics["cancelled"] = True
                extra["image_download_diagnostics"] = diagnostics
                raise
            diagnostics["failed"] += 1
            diagnostics["failed_indices"].append(idx)
            diagnostics["images"].append({
                "index": idx,
                "status": "failed",
                "candidate_count": len(urls),
                "error": str(exc),
            })
            logger.warning("Failed to download X/Twitter image %s: %s", idx, exc)

    extra["image_download_diagnostics"] = diagnostics
    return paths


def _download_image_candidates(
    urls: list[str],
    dest: Path,
    *,
    referer: str,
    should_cancel: Callable[[], bool] | None,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    last_error: Exception | None = None
    for raw_url in urls:
        for url in _twitter_image_candidates(raw_url):
            _raise_if_image_download_cancelled(should_cancel)
            try:
                req = urllib.request.Request(url, headers=_headers(referer))
                with urllib_urlopen(req, timeout=30) as resp:
                    _raise_if_image_download_cancelled(should_cancel)
                    content_type = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                    data = resp.read(_MAX_IMAGE_BYTES + 1)
                if len(data) > _MAX_IMAGE_BYTES:
                    raise RuntimeError(f"image exceeds {_MAX_IMAGE_BYTES} bytes")
                if not data:
                    raise RuntimeError("empty image response")
                dest.write_bytes(data)
                attempts.append({"url": url, "status": "completed", "content_type": content_type, "bytes": len(data)})
                return {
                    "url": raw_url,
                    "fetched_url": url,
                    "content_type": content_type,
                    "bytes": len(data),
                    "attempts": attempts,
                }
            except Exception as exc:
                last_error = exc
                attempts.append({"url": url, "status": "failed", "error": str(exc)})
    raise RuntimeError(str(last_error) if last_error else "no image candidates worked")


def _twitter_image_candidates(url: str) -> list[str]:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if not ((host == "pbs.twimg.com" or host.endswith(".pbs.twimg.com")) and "/media/" in parsed.path):
        return [url]

    candidates: list[str] = []
    path = parsed.path
    filename = path.rsplit("/", 1)[-1]
    if ":" in filename:
        path = path.rsplit(":", 1)[0]
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    for name in ("4096x4096", "large", "medium", "small"):
        next_query = dict(query)
        next_query["name"] = [name]
        candidate = urllib.parse.urlunparse((
            parsed.scheme or "https",
            parsed.netloc,
            path,
            "",
            urllib.parse.urlencode(next_query, doseq=True),
            "",
        ))
        if candidate not in candidates:
            candidates.append(candidate)
    if url not in candidates:
        candidates.append(url)
    return candidates


def _guess_image_ext(urls: list[str]) -> str:
    for url in urls:
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        fmt = (query.get("format") or [""])[0].strip().lower()
        if fmt:
            return _normalize_ext(fmt)
        clean_path = parsed.path.rsplit(":", 1)[0]
        suffix = Path(clean_path).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
            return suffix
        mime_type, _ = mimetypes.guess_type(urllib.parse.unquote(clean_path))
        guessed = mimetypes.guess_extension(mime_type or "")
        if guessed:
            return _normalize_ext(guessed.lstrip("."))
    return ".jpg"


def _normalize_ext(value: str) -> str:
    value = value.strip().lower().lstrip(".")
    if value == "jpeg":
        return ".jpg"
    if value in {"jpg", "png", "webp", "gif", "avif"}:
        return f".{value}"
    return ".jpg"


def _headers(referer: str) -> dict[str, str]:
    return {
        "User-Agent": _UA,
        "Referer": referer,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }


def _raise_if_image_download_cancelled(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel and should_cancel():
        raise RuntimeError("X/Twitter image download cancelled")
