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
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.settings import get_runtime_settings

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 1024 * 1024
_SHORT_LINK_RE = re.compile(r"https?://xhslink\.com/[A-Za-z0-9/]+")
_FULL_URL_RE = re.compile(r"https?://(?:www\.)?xiaohongshu\.com/[^\s]+")
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)


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
        with urllib.request.urlopen(req, timeout=20) as resp:
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
    image_urls = _extract_image_urls(note)
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
    image_urls: list[str] = (info.get("extra") or {}).get("image_urls") or []
    if not image_urls:
        raise RuntimeError("Xiaohongshu note has no image URLs — is it really an image_note?")

    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    referer = str(info.get("webpage_url") or "https://www.xiaohongshu.com/")
    paths: list[Path] = []
    for idx, url in enumerate(image_urls):
        ext = _guess_image_ext(url)
        dest = images_dir / f"{idx:02d}.{ext}"
        if dest.exists():
            paths.append(dest)
            continue
        try:
            _download_file(url, dest, referer=referer)
            paths.append(dest)
        except Exception as e:
            logger.warning(f"Failed to download XHS image {idx}: {e}")
    return paths


def _guess_image_ext(url: str) -> str:
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
        "Cookie": "webId=anonymous",
    }
    cookie = (get_runtime_settings().xiaohongshu_cookie or "").strip()
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _fetch_text(url: str, referer: str) -> str:
    req = urllib.request.Request(url, headers=_headers(referer))
    with urllib.request.urlopen(req, timeout=25) as resp:
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
    images = note.get("imageList")
    if not isinstance(images, list):
        return []
    urls: list[str] = []
    for image in images:
        if not isinstance(image, dict):
            continue
        url = image.get("urlDefault") or image.get("url") or image.get("urlPre")
        if isinstance(url, str) and url.startswith("http"):
            urls.append(_transform_image_to_original(url))
    return urls


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


def _download_file(url: str, dest: Path, referer: str) -> None:
    req = urllib.request.Request(url, headers=_headers(referer))
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(_CHUNK_SIZE)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
    if total and downloaded < total:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"Incomplete download: {downloaded}/{total} bytes")
    logger.info(f"Downloaded Xiaohongshu video: {downloaded:,} bytes -> {dest.name}")


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
            with urllib.request.urlopen(req, timeout=120) as resp:
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
