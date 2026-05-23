"""Apple Podcasts episode extraction and audio download.

Apple Podcasts pages don't expose a direct audio URL. The trick: Apple's
public iTunes Lookup API gives us the canonical RSS feed URL for any show id,
and the show's RSS exposes per-episode <enclosure url=...> tags pointing at
the actual MP3/M4A files hosted by the show's podcast host (Xiaoyuzhou,
Libsyn, anchor.fm, etc.).

URL shape we handle:
  https://podcasts.apple.com/<region>/podcast/<slug>/id<showId>?i=<episodeTrackId>

Resolution:
  1. Parse showId and episodeTrackId (i= param) from the URL.
  2. iTunes lookup(showId) → feedUrl.
  3. iTunes lookup(episodeTrackId, entity=podcastEpisode) → episode title +
     releaseDate (used to match against the RSS feed).
  4. Fetch RSS, find matching <item> by title (with pubDate as tie-break).
  5. Extract <enclosure url> → download the audio file directly.
"""

from __future__ import annotations

import html
import json
import logging
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 1024 * 1024
_APPLE_RE = re.compile(
    r"podcasts\.apple\.com/(?:[a-z]{2}/)?podcast/[^?#/]*/id(\d+)",
    re.IGNORECASE,
)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}
_ITUNES_NS = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


def is_apple_podcast_url(url: str) -> bool:
    """Return True for Apple Podcasts episode/show URLs."""
    return bool(_APPLE_RE.search(url))


def _extract_show_and_episode_ids(url: str) -> tuple[str | None, str | None]:
    """Parse Apple Podcasts show id (idXXXX) and episode track id (i= param)."""
    match = _APPLE_RE.search(url)
    show_id = match.group(1) if match else None
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    episode_id = qs.get("i", [None])[0]
    return show_id, episode_id


def fetch_metadata(url: str) -> dict[str, Any]:
    """Resolve an Apple Podcasts URL → canonical RSS enclosure metadata."""
    show_id, episode_track_id = _extract_show_and_episode_ids(url)
    if not show_id:
        raise RuntimeError(f"Not a recognizable Apple Podcasts URL: {url}")

    show_lookup = _itunes_lookup(show_id)
    feed_url = show_lookup.get("feedUrl")
    podcast_title = show_lookup.get("collectionName") or show_lookup.get("trackName")
    podcast_author = show_lookup.get("artistName")
    artwork = (
        show_lookup.get("artworkUrl600")
        or show_lookup.get("artworkUrl100")
        or show_lookup.get("artworkUrl60")
    )
    if not feed_url:
        raise RuntimeError(
            f"iTunes lookup for show id {show_id} did not return a feedUrl"
        )

    target_title = None
    target_release_ts = None
    if episode_track_id:
        try:
            ep_lookup = _itunes_lookup(episode_track_id, entity="podcastEpisode")
            target_title = ep_lookup.get("trackName")
            target_release_ts = _parse_timestamp(ep_lookup.get("releaseDate"))
        except RuntimeError as e:
            logger.info(
                f"iTunes episode lookup failed for i={episode_track_id} ({e}); "
                "falling back to URL slug matching"
            )

    # Fallback hint: the URL slug often encodes a normalized episode title
    # (e.g. ".../podcast/139-agent的综述-和苏煜.../id..."). Use it if direct lookup
    # didn't yield a title.
    slug_hint = None
    if not target_title:
        slug_match = re.search(
            r"podcasts\.apple\.com/(?:[a-z]{2}/)?podcast/([^/?#]+)/id\d+",
            url,
            re.IGNORECASE,
        )
        if slug_match:
            slug_hint = urllib.parse.unquote(slug_match.group(1))

    item = _select_rss_item(
        feed_url,
        target_title,
        target_release_ts,
        episode_track_id,
        slug_hint=slug_hint,
    )
    if not item:
        raise RuntimeError(
            f"Could not locate episode in RSS feed {feed_url} (i={episode_track_id})"
        )

    audio_url = item.get("enclosure_url")
    if not audio_url:
        raise RuntimeError(
            f"RSS item has no <enclosure url>; cannot download (feed={feed_url})"
        )

    canonical_url = url
    description = _strip_html(item.get("description") or "")
    duration = _parse_duration(item.get("duration"))
    published_ts = _parse_rfc2822(item.get("pub_date"))

    title = item.get("title") or target_title or f"apple_podcast_{episode_track_id or show_id}"
    tags = ["Apple Podcasts"]
    if podcast_title:
        tags.append(str(podcast_title))

    info: dict[str, Any] = {
        "id": episode_track_id or item.get("guid") or show_id,
        "title": title,
        "description": description or None,
        "uploader": podcast_title or podcast_author,
        "uploader_id": str(show_id),
        "platform": "apple_podcast",
        "content_subtype": "podcast_episode",
        "channel": podcast_title,
        "duration": duration,
        "upload_date": _format_upload_date(published_ts),
        "timestamp": published_ts,
        "webpage_url": canonical_url,
        "original_url": url,
        "thumbnail": item.get("image") or artwork,
        "url": audio_url,
        "ext": _guess_ext(audio_url),
        "media_type": "podcast",
        "tags": tags,
        "chapters": _extract_chapters(description or ""),
        "extra": {
            "platform": "apple_podcast",
            "apple_show_id": show_id,
            "apple_episode_id": episode_track_id,
            "rss_feed_url": feed_url,
            "rss_guid": item.get("guid"),
            "podcast_title": podcast_title,
            "podcast_author": podcast_author,
            "audio_url": audio_url,
        },
    }
    return info


def download_audio(info: dict[str, Any], output_dir: Path) -> tuple[Path, Path | None]:
    """Download the resolved enclosure and convert to the pipeline wav format."""
    audio_url = info.get("url") or (info.get("extra") or {}).get("audio_url")
    if not audio_url:
        raise RuntimeError("Apple Podcasts metadata did not expose an audio URL")

    output_dir.mkdir(parents=True, exist_ok=True)
    title = _sanitize_filename(str(info.get("title") or info.get("id") or "apple_podcast_episode"))
    ext = _guess_ext(str(audio_url)) or "m4a"
    source_path = _dedupe_path(output_dir / f"{title}.{ext}")
    wav_path = _dedupe_path(output_dir / f"{title}.wav")

    logger.info(f"Downloading Apple Podcasts audio (via RSS enclosure): {audio_url}")
    _download_file(str(audio_url), source_path)

    # Save the cover art (per-episode if RSS has one, else show artwork).
    # Frontend archive endpoint looks for thumbnail.jpg/cover.jpg/cover.png;
    # write under the extension matching the source bytes so the served
    # Content-Type matches.
    thumb_url = info.get("thumbnail") or (info.get("extra") or {}).get("thumbnail")
    if thumb_url:
        url_lower = str(thumb_url).lower()
        if ".png" in url_lower:
            thumb_name = "cover.png"
        else:
            thumb_name = "thumbnail.jpg"
        thumb_path = output_dir / thumb_name
        if not thumb_path.exists():
            try:
                _download_file(str(thumb_url), thumb_path)
            except Exception as e:
                logger.warning(f"Failed to download podcast cover from {thumb_url}: {e}")

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
        logger.warning(f"Apple Podcasts ffmpeg conversion failed, using source audio: {stderr[-500:]}")
    else:
        logger.warning("ffmpeg not found; using original Apple Podcasts audio file")

    return source_path, None


# --- iTunes lookup -----------------------------------------------------------


def _itunes_lookup(entity_id: str, entity: str | None = None) -> dict[str, Any]:
    params = {"id": entity_id}
    if entity:
        params["entity"] = entity
    url = "https://itunes.apple.com/lookup?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise RuntimeError(f"iTunes lookup returned non-JSON: {e}")
    results = data.get("results") or []
    if not results:
        raise RuntimeError(f"iTunes lookup returned empty results for id={entity_id}")
    return results[0]


# --- RSS parsing -------------------------------------------------------------


def _select_rss_item(
    feed_url: str,
    target_title: str | None,
    target_release_ts: int | None,
    episode_track_id: str | None,
    slug_hint: str | None = None,
) -> dict[str, Any] | None:
    """Fetch RSS and pick the item that best matches the requested episode."""
    req = urllib.request.Request(feed_url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise RuntimeError(f"Failed to parse RSS feed {feed_url}: {e}")

    channel = root.find("channel")
    if channel is None:
        return None

    items: list[dict[str, Any]] = []
    for item_el in channel.findall("item"):
        items.append(_normalize_rss_item(item_el))

    if not items:
        return None

    # If we have no hints at all, return newest (first) item.
    if not target_title and not target_release_ts and not episode_track_id and not slug_hint:
        return items[0]

    # Score each item.
    norm_target = _normalize_title(target_title) if target_title else None
    slug_tokens = _slug_tokens(slug_hint) if slug_hint else []
    best: tuple[int, dict[str, Any]] | None = None
    for item in items:
        score = 0
        item_title = item.get("title") or ""
        if target_title and item_title == target_title:
            score += 5
        elif norm_target and _normalize_title(item_title) == norm_target:
            score += 4
        if slug_tokens:
            score += _slug_token_overlap(item_title, slug_tokens)
        if target_release_ts:
            ts = _parse_rfc2822(item.get("pub_date"))
            if ts and abs(ts - target_release_ts) < 86400:
                score += 2
        if episode_track_id and episode_track_id in (item.get("guid") or ""):
            score += 1
        if score > 0 and (best is None or score > best[0]):
            best = (score, item)

    return best[1] if best else items[0]


def _slug_tokens(slug: str) -> list[str]:
    # Split slug on hyphens; keep tokens with length >= 2; lowercase.
    raw = re.split(r"[-_]+", slug)
    return [t.lower() for t in raw if len(t) >= 2]


def _slug_token_overlap(title: str, tokens: list[str]) -> int:
    if not title or not tokens:
        return 0
    norm = title.lower()
    return sum(1 for t in tokens if t in norm)


def _normalize_rss_item(item_el: ET.Element) -> dict[str, Any]:
    title = (item_el.findtext("title") or "").strip()
    guid = (item_el.findtext("guid") or "").strip()
    description = (item_el.findtext("description") or "").strip()
    pub_date = (item_el.findtext("pubDate") or "").strip()

    enclosure_url = None
    enc_el = item_el.find("enclosure")
    if enc_el is not None:
        enclosure_url = enc_el.attrib.get("url")

    duration = (item_el.findtext(f"{_ITUNES_NS}duration") or "").strip() or None
    image_el = item_el.find(f"{_ITUNES_NS}image")
    image = image_el.attrib.get("href") if image_el is not None else None

    return {
        "title": title,
        "guid": guid,
        "description": description,
        "pub_date": pub_date,
        "enclosure_url": enclosure_url,
        "duration": duration,
        "image": image,
    }


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", "", title).lower()


# --- shared helpers (mirrored from xiaoyuzhou.api) ---------------------------


def _strip_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</p\s*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return html.unescape(value).strip()


def _parse_duration(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    if value.isdigit():
        return float(value)
    # HH:MM:SS or MM:SS
    parts = value.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        return float(nums[0] * 3600 + nums[1] * 60 + nums[2])
    if len(nums) == 2:
        return float(nums[0] * 60 + nums[1])
    return None


def _parse_timestamp(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _parse_rfc2822(value: str | None) -> int | None:
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(value)
        if dt is None:
            return None
        return int(dt.timestamp())
    except (TypeError, ValueError):
        return None


def _format_upload_date(ts: int | None) -> str | None:
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
    return value[:100] or "apple_podcast_episode"


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


def _download_file(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers=_HEADERS)
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
    logger.info(f"Downloaded Apple Podcasts audio: {downloaded:,} bytes -> {dest.name}")
