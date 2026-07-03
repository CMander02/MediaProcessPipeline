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
from typing import Any

from app.core.network import runtime_proxy_url, urllib_urlopen
from app.core.settings import get_runtime_settings

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 1024 * 1024
_SHORT_LINK_RE = re.compile(r"https?://xhslink\.com/[A-Za-z0-9/]+")
_FULL_URL_RE = re.compile(r"https?://(?:www\.)?xiaohongshu\.com/[^\s]+")
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)


class ImageDownloadError(RuntimeError):
    """Image candidate download failed with structured diagnostics."""

    def __init__(self, message: str, diagnostics: dict[str, Any]):
        super().__init__(message)
        self.diagnostics = diagnostics


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

    title = (
        note.get("title")
        or note.get("displayTitle")
        or note.get("desc")
        or info.post_id
    )
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


def download_images(info: dict[str, Any], output_dir: Path) -> list[Path]:
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
    paths: list[Path] = []
    diagnostics: dict[str, Any] = {
        "platform": "xiaohongshu",
        "referer": referer,
        "image_count": len(image_url_candidates),
        "success": 0,
        "failed": 0,
        "failed_indices": [],
        "images": [],
    }
    for idx, candidates in enumerate(image_url_candidates):
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
            time.sleep(1.2)
        try:
            record = _download_file_candidates(urls, dest, referer=referer)
            paths.append(dest)
            diagnostics["success"] += 1
            diagnostics["images"].append({
                "index": idx,
                "status": "completed",
                "path": str(dest),
                "candidate_count": len(urls),
                **record,
            })
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
    if diagnostics["failed_indices"]:
        diagnostics["url_refresh_probe"] = _probe_image_url_freshness(info, image_url_candidates)
    extra["image_download_diagnostics"] = diagnostics
    return paths


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
    return headers


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
            for candidate in (_transform_image_to_original(normalized), normalized):
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
    try:
        parsed = urllib.parse.urlparse(value)
    except Exception:
        return value
    if "xhscdn.com" in parsed.netloc:
        segments = [s for s in parsed.path.split("/") if s]
        subdirs_and_id = segments[2:] if len(segments) > 2 else segments
        if subdirs_and_id:
            image_id = subdirs_and_id[-1].split("!")[0]
            subdirs_and_id[-1] = image_id
            return f"https://ci.xiaohongshu.com/{'/'.join(subdirs_and_id)}"
    if parsed.netloc == "ci.xiaohongshu.com":
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return value


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


def _download_file_candidates(urls: list[str], dest: Path, referer: str) -> dict[str, Any]:
    last_error: Exception | None = None
    seen: set[str] = set()
    attempts: list[dict[str, Any]] = []
    for order, url in enumerate(urls):
        if url in seen:
            continue
        seen.add(url)
        attempt = _download_attempt_record(order, url, referer)
        try:
            _download_file(url, dest, referer=referer, attempts=1)
            attempt["status"] = "success"
            if dest.exists():
                attempt["bytes"] = dest.stat().st_size
            attempts.append(attempt)
            return {"attempts": attempts}
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


def _download_file(url: str, dest: Path, referer: str, attempts: int = 2, timeout_sec: int = 25) -> None:
    last_error: Exception | None = None
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, attempts + 1):
        downloaded = 0
        dest.unlink(missing_ok=True)
        part.unlink(missing_ok=True)
        req = urllib.request.Request(url, headers=_headers(referer))
        try:
            with urllib_urlopen(req, timeout=timeout_sec) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                with open(part, "wb") as f:
                    while True:
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
        except Exception as e:
            last_error = e
            dest.unlink(missing_ok=True)
            part.unlink(missing_ok=True)
            if attempt >= attempts:
                raise RuntimeError(f"Xiaohongshu file download failed after {attempts} attempts: {last_error}") from e
            logger.warning(f"Xiaohongshu file download retry {attempt}/{attempts}: {e}")
            time.sleep(min(attempt, 3))


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
