"""Xiaoyuzhou episode extraction and audio download.

The public episode page is server-rendered and includes both JSON-LD and
Next.js page data. Those two sources are enough for episode metadata and the
public audio URL, without relying on yt-dlp's generic extractor.
"""

from __future__ import annotations

import html
import json
import logging
import re
import shutil
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 1024 * 1024
_EPISODE_RE = re.compile(r"xiaoyuzhoufm\.com/episode/([0-9a-fA-F]+)")
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.xiaoyuzhoufm.com/",
}


def is_xiaoyuzhou_url(url: str) -> bool:
    """Return True for Xiaoyuzhou episode URLs."""
    return bool(_EPISODE_RE.search(url))


def extract_episode_id(url: str) -> str | None:
    """Extract the Xiaoyuzhou episode id from an episode URL."""
    match = _EPISODE_RE.search(url)
    return match.group(1) if match else None


def fetch_metadata(url: str) -> dict[str, Any]:
    """Fetch and normalize metadata from a public Xiaoyuzhou episode page."""
    page_html = _fetch_text(url)
    episode_id = extract_episode_id(url)
    json_ld = _extract_json_ld(page_html)
    next_episode = _extract_next_episode(page_html, episode_id)

    episode = next_episode or {}
    podcast = episode.get("podcast") or {}
    media = episode.get("media") or {}
    media_source = media.get("source") if isinstance(media.get("source"), dict) else {}

    audio_url = (
        _nested_get(json_ld, ["associatedMedia", "contentUrl"])
        or _nested_get(json_ld, ["enclosure", "url"])
        or episode.get("enclosure", {}).get("url")
        or media_source.get("url")
        or episode.get("mediaUrl")
    )
    if not audio_url:
        og_audio = re.search(
            r'<meta\s+property=["\']og:audio["\']\s+content=["\']([^"\']+)["\']',
            page_html,
        )
        if og_audio:
            audio_url = html.unescape(og_audio.group(1))

    title = json_ld.get("name") or episode.get("title")
    podcast_title = _nested_get(json_ld, ["partOfSeries", "name"]) or podcast.get("title")
    description = json_ld.get("description") or episode.get("description")
    description = _strip_html(description) if description else None
    published = json_ld.get("datePublished") or episode.get("pubDate")
    duration = _parse_duration(episode.get("duration")) or _parse_iso_duration(
        json_ld.get("timeRequired")
    )

    image = (
        _meta_content(page_html, "og:image")
        or _meta_content(page_html, "twitter:image")
        or _nested_get(podcast, ["image", "picUrl"])
    )

    canonical = _canonical_url(page_html) or url
    tags = ["小宇宙"]
    if podcast_title:
        tags.append(str(podcast_title))

    podcast_id = podcast.get("pid")
    info: dict[str, Any] = {
        "id": episode_id or episode.get("eid") or episode.get("id"),
        "title": title or episode_id or "xiaoyuzhou_episode",
        "description": description,
        "uploader": podcast_title or podcast.get("author"),
        "uploader_id": str(podcast_id) if podcast_id else None,
        "platform": "xiaoyuzhou",
        "content_subtype": "podcast_episode",
        "channel": podcast_title,
        "duration": duration,
        "upload_date": _format_upload_date(published),
        "timestamp": _parse_timestamp(published),
        "webpage_url": canonical,
        "original_url": url,
        "thumbnail": image,
        "url": audio_url,
        "ext": _guess_ext(audio_url),
        "media_type": "podcast",
        "tags": tags,
        "chapters": _extract_chapters(description or ""),
        "extra": {
            "platform": "xiaoyuzhou",
            "episode_id": episode_id or episode.get("eid") or episode.get("id"),
            "podcast_id": podcast_id,
            "podcast_title": podcast_title,
            "podcast_author": podcast.get("author"),
            "podcast_description": podcast.get("description") or podcast.get("brief"),
            "audio_url": audio_url,
            "media_key": episode.get("mediaKey"),
            "media_size": media.get("size"),
            "mime_type": media.get("mimeType"),
            "transcript_media_id": episode.get("transcriptMediaId"),
            "risk_warning": episode.get("riskWarning"),
            "play_count": episode.get("playCount"),
            "favorite_count": episode.get("favoriteCount"),
            "comment_count": episode.get("commentCount"),
        },
    }
    return info


def download_audio(info: dict[str, Any], output_dir: Path) -> tuple[Path, Path | None]:
    """Download the episode audio and convert it to the pipeline wav format."""
    audio_url = info.get("url") or (info.get("extra") or {}).get("audio_url")
    if not audio_url:
        raise RuntimeError("Xiaoyuzhou page did not expose a public audio URL")

    output_dir.mkdir(parents=True, exist_ok=True)
    title = _sanitize_filename(str(info.get("title") or info.get("id") or "xiaoyuzhou_episode"))
    ext = _guess_ext(str(audio_url)) or "m4a"
    source_path = _dedupe_path(output_dir / f"{title}.{ext}")
    wav_path = _dedupe_path(output_dir / f"{title}.wav")

    logger.info(f"Downloading Xiaoyuzhou audio: {audio_url}")
    _download_file(str(audio_url), source_path, referer=str(info.get("webpage_url") or ""))

    if shutil.which("ffmpeg"):
        result = subprocess.run(
            [
                "ffmpeg",
                "-i",
                str(source_path),
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
        if result.returncode == 0 and wav_path.exists():
            return wav_path, source_path
        stderr = result.stderr.decode("utf-8", errors="replace")
        logger.warning(f"Xiaoyuzhou ffmpeg conversion failed, using source audio: {stderr[-500:]}")
    else:
        logger.warning("ffmpeg not found; using original Xiaoyuzhou audio file")

    return source_path, None


def _fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
        encoding = resp.headers.get_content_charset() or "utf-8"
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(encoding, errors="replace")


def _extract_json_ld(page_html: str) -> dict[str, Any]:
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page_html,
        flags=re.DOTALL,
    ):
        try:
            data = json.loads(html.unescape(match.group(1)))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "PodcastEpisode":
            return data
    return {}


def _extract_next_episode(page_html: str, episode_id: str | None) -> dict[str, Any]:
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        page_html,
        flags=re.DOTALL,
    )
    if not match:
        return {}
    try:
        data = json.loads(html.unescape(match.group(1)))
    except json.JSONDecodeError:
        return {}

    page_props = _nested_get(data, ["props", "pageProps"])
    if isinstance(page_props, dict):
        for key in ("episode", "episodeDetail", "data"):
            value = page_props.get(key)
            if isinstance(value, dict) and _looks_like_episode(value, episode_id):
                return value
        found = _find_episode_dict(page_props, episode_id)
        if found:
            return found
    return {}


def _find_episode_dict(value: Any, episode_id: str | None) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if _looks_like_episode(value, episode_id):
            return value
        for child in value.values():
            found = _find_episode_dict(child, episode_id)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_episode_dict(child, episode_id)
            if found:
                return found
    return None


def _looks_like_episode(value: dict[str, Any], episode_id: str | None) -> bool:
    if episode_id and value.get("id") == episode_id and ("media" in value or "enclosure" in value):
        return True
    return bool(value.get("type") == "EPISODE" and ("media" in value or "enclosure" in value))


def _nested_get(data: Any, path: list[str]) -> Any:
    cur = data
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _meta_content(page_html: str, prop: str) -> str | None:
    match = re.search(
        rf'<meta\s+(?:property|name)=["\']{re.escape(prop)}["\']\s+content=["\']([^"\']+)["\']',
        page_html,
    )
    return html.unescape(match.group(1)) if match else None


def _canonical_url(page_html: str) -> str | None:
    match = re.search(r'<link\s+rel=["\']canonical["\']\s+href=["\']([^"\']+)["\']', page_html)
    return html.unescape(match.group(1)) if match else None


def _strip_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</p\s*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return html.unescape(value).strip()


def _parse_iso_duration(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(
        r"P(?:(?P<days>\d+)D)?T?(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?",
        value,
    )
    if not match:
        return None
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    return float(days * 86400 + hours * 3600 + minutes * 60 + seconds)


def _parse_duration(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.isdigit():
        return float(value)
    return None


def _parse_timestamp(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _format_upload_date(value: Any) -> str | None:
    ts = _parse_timestamp(value)
    if ts is None:
        return None
    return datetime.fromtimestamp(ts).strftime("%Y%m%d")


def _extract_chapters(description: str) -> list[dict[str, Any]]:
    chapters: list[dict[str, Any]] = []
    for line in description.splitlines():
        match = re.match(r"^\s*((?:(\d{1,2}):)?\d{1,2}:\d{2})\s+(.+?)\s*$", line)
        if not match:
            continue
        start = _timestamp_to_seconds(match.group(1))
        title = match.group(3).strip()
        if title:
            chapters.append({"title": title, "start_time": start})
    return chapters


def _timestamp_to_seconds(value: str) -> float:
    parts = [int(p) for p in value.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return float(minutes * 60 + seconds)
    hours, minutes, seconds = parts
    return float(hours * 3600 + minutes * 60 + seconds)


def _guess_ext(url: Any) -> str:
    if not isinstance(url, str):
        return "m4a"
    match = re.search(r"\.([a-zA-Z0-9]{2,5})(?:[?#]|$)", url)
    return match.group(1).lower() if match else "m4a"


def _sanitize_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return value[:100] or "xiaoyuzhou_episode"


def _dedupe_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 2
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _download_file(url: str, dest: Path, referer: str = "") -> None:
    headers = dict(_HEADERS)
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
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
    logger.info(f"Downloaded Xiaoyuzhou audio: {downloaded:,} bytes -> {dest.name}")
