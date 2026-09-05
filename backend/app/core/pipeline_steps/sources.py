"""Sources responsibilities for the media pipeline."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

from app.core.settings import get_runtime_settings
from app.core.source_normalization import normalize_source_input
from app.models import MediaMetadata

logger = logging.getLogger(__name__)

_DOWNLOAD_RESOLVES_TITLE_ROUTES = {
    "xiaohongshu",
    "zhihu",
    "bilibili_opus",
    "xiaoyuzhou",
    "apple_podcast",
    "webpage",
    "twitter",
}


def _canonical_image_url(value: Any) -> str:
    raw = str(value or "").strip()
    if raw.startswith("//"):
        raw = f"https:{raw}"
    if raw.startswith("http://"):
        raw = "https://" + raw[len("http://") :]
    if not raw.startswith("https://"):
        return ""
    parsed = urllib.parse.urlparse(raw)
    path = urllib.parse.unquote(parsed.path)
    if "@" in path:
        path = path.split("@", 1)[0]
    host = parsed.netloc.lower()
    if (host == "pbs.twimg.com" or host.endswith(".pbs.twimg.com")) and "/media/" in path:
        prefix, filename = path.rsplit("/", 1)
        filename = filename.split(":", 1)[0]
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        path = f"{prefix}/{stem}"
    return urllib.parse.urlunparse(("https", host, path, "", "", ""))


def _localize_note_markdown_image_refs(
    text: str, metadata: MediaMetadata, image_paths: list[Path]
) -> str:
    extra = metadata.extra if isinstance(metadata.extra, dict) else {}
    image_urls = extra.get("image_urls")
    image_candidates = extra.get("image_url_candidates")
    if not isinstance(image_urls, list) and not isinstance(image_candidates, list):
        return text
    if not image_paths:
        return text

    mapping: dict[str, str] = {}
    for fallback_idx, path in enumerate(image_paths):
        idx = int(path.stem) if path.stem.isdigit() else fallback_idx
        local_path = f"images/{path.name}"
        urls: list[Any] = []
        if isinstance(image_urls, list) and 0 <= idx < len(image_urls):
            urls.append(image_urls[idx])
        if isinstance(image_candidates, list) and 0 <= idx < len(image_candidates):
            group = image_candidates[idx]
            if isinstance(group, list):
                urls.extend(group)
        for url in urls:
            key = _canonical_image_url(url)
            if key:
                mapping[key] = local_path
    if not mapping:
        return text

    def replace(match: re.Match[str]) -> str:
        key = _canonical_image_url(match.group(2))
        local_path = mapping.get(key)
        if not local_path:
            return match.group(0)
        return f"{match.group(1)}{local_path}{match.group(3)}"

    return re.sub(r"(!\[[^\]]*]\()([^)]+)(\))", replace, text)


def _detect_source_type(source: str) -> str:
    """Detect the type of media source."""
    source = _clean_source_path(source)
    source_lower = source.lower()
    if source_lower.startswith(("http://", "https://")):
        return "url"
    if any(source_lower.endswith(ext) for ext in [".mp4", ".mkv", ".avi", ".webm", ".mov"]):
        return "local_video"
    if any(source_lower.endswith(ext) for ext in [".mp3", ".wav", ".flac", ".m4a", ".ogg"]):
        return "local_audio"
    return "unknown"


def _platform_prefer_subtitles(source_type: str) -> bool:
    """Resolve subtitle preference with per-platform config fallback."""
    rt = get_runtime_settings()
    try:
        configs = json.loads(rt.platform_configs or "{}")
    except Exception:
        configs = {}

    if source_type == "webpage":
        return False

    platform_config_key = (
        "bilibili"
        if source_type in {"bilibili", "bilibili_video", "bilibili_opus"}
        else source_type
    )
    supported_platforms = {
        "bilibili",
        "youtube",
        "xiaoyuzhou",
        "xiaohongshu",
        "zhihu",
        "apple_podcast",
    }
    platform_cfg = (
        configs.get(platform_config_key) if platform_config_key in supported_platforms else None
    )
    if isinstance(platform_cfg, dict) and "prefer_subtitle" in platform_cfg:
        return bool(platform_cfg["prefer_subtitle"])
    return bool(rt.prefer_platform_subtitles)


def _subtitle_unavailable_message(metadata: MediaMetadata | None) -> str:
    diagnostics = []
    if metadata is not None and isinstance(metadata.extra, dict):
        value = metadata.extra.get("subtitle_diagnostics")
        if isinstance(value, list):
            diagnostics = value
    if any(
        isinstance(item, dict) and item.get("reason") == "rate_limited_or_unreachable"
        for item in diagnostics
    ):
        return "平台字幕请求受限，转入媒体下载与 ASR"
    return "未发现可用平台字幕"


def _download_resolves_url_title(route_type: str) -> bool:
    return route_type in _DOWNLOAD_RESOLVES_TITLE_ROUTES


def _clean_source_path(source: str) -> str:
    """Clean up source path by removing quotes and whitespace.

    Also extracts the first URL from share-text blobs like the ones copied from
    the Xiaohongshu mobile/web app:
      '77 【标题 | 小红书】 😆 n7715oGO82X4J5v 😆 https://www.xiaohongshu.com/...'
    """
    return normalize_source_input(source)


def _looks_like_local_path(source: str) -> bool:
    """Check if source looks like a local file path (not a URL)."""
    source = _clean_source_path(source)
    if source.startswith(("http://", "https://", "ftp://", "rtmp://")):
        return False
    if len(source) >= 2 and source[1] == ":":
        return True
    if source.startswith("/"):
        return True
    if "." in source and "://" not in source:
        ext = source.rsplit(".", 1)[-1].lower()
        media_exts = {"mp4", "mkv", "avi", "webm", "mov", "mp3", "wav", "flac", "m4a", "ogg"}
        if ext in media_exts:
            return True
    return False


def _extract_audio_from_video(video_path: Path, output_path: Path) -> Path:
    """Extract audio from video file using ffmpeg."""
    # Resolve to absolute paths so filenames starting with '-' can't be
    # misinterpreted as ffmpeg options.
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path.resolve()),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(output_path.resolve()),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    return output_path
