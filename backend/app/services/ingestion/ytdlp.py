"""Media download service — yt-dlp for general sites, BBDown for Bilibili."""

import hashlib
import logging
import os
import re
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging_setup import log_event
from app.core.settings import get_runtime_settings
from app.models import ChapterInfo, MediaMetadata, MediaType

logger = logging.getLogger(__name__)

# BBDown executable — shipped with the project
_BBDOWN_DIR = Path(__file__).resolve().parent.parent.parent.parent / "tools" / "bbdown"
_BBDOWN_EXE = _BBDOWN_DIR / "BBDown.exe"


def _is_bilibili_url(url: str) -> bool:
    return bool(re.search(r'bilibili\.com|b23\.tv|BV[0-9A-Za-z]+|av\d+', url))


def _is_xiaoyuzhou_url(url: str) -> bool:
    return bool(re.search(r'xiaoyuzhoufm\.com/episode/[0-9a-fA-F]+', url))


def _is_xiaohongshu_url(url: str) -> bool:
    return bool(re.search(r'xiaohongshu\.com|xhslink\.com', url))


def _is_zhihu_url(url: str) -> bool:
    return bool(re.search(r'zhihu\.com/(?:pin/\d+|question/\d+/answer/\d+)', url, re.IGNORECASE))


def _is_apple_podcast_url(url: str) -> bool:
    return bool(re.search(r'podcasts\.apple\.com/(?:[a-z]{2}/)?podcast/[^?#/]*/id\d+', url, re.IGNORECASE))


def _is_youtube_url(url: str) -> bool:
    return bool(re.search(r'(?:youtube\.com|youtu\.be)', url, re.IGNORECASE))


def _extract_bilibili_bvid(url: str) -> str | None:
    """Extract or resolve a Bilibili BV id from BV or av/aid URLs."""
    bv_match = re.search(r'(BV[0-9A-Za-z]+)', url)
    if bv_match:
        return bv_match.group(1)

    av_match = re.search(r'(?:/av|av)(\d+)', url, re.IGNORECASE)
    if not av_match:
        return None

    aid = av_match.group(1)
    try:
        import json
        import urllib.request

        req = urllib.request.Request(
            f"https://api.bilibili.com/x/web-interface/view?aid={aid}",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": f"https://www.bilibili.com/video/av{aid}/",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read()).get("data", {})
        bvid = data.get("bvid")
        return str(bvid) if bvid else None
    except Exception as e:
        log_event(logger, logging.WARNING, "bilibili.bvid.resolve_failed", aid=aid, error=e)
        return None


class YoutubeNetworkError(RuntimeError):
    """Raised when YouTube is unreachable after yt-dlp's bounded retries."""


_YTDLP_NETWORK_ERROR_MARKERS = (
    "failed to establish a new connection",
    "connection refused",
    "actively refused",
    "winerror 10061",
    "nameresolutionerror",
    "getaddrinfo failed",
    "temporary failure in name resolution",
    "no route to host",
    "network is unreachable",
    "connection reset by peer",
    "connect timeout",
    "connecttimeout",
    "read timed out",
    "timed out",
    "proxyerror",
    "unable to connect to proxy",
    "unable to download api page",
)


def _normalize_proxy_url(raw: str) -> str:
    proxy = raw.strip()
    if not proxy:
        return ""
    if "://" not in proxy:
        proxy = f"http://{proxy}"
    return proxy


def _proxy_from_windows_user_settings() -> str:
    """Return the current Windows user proxy as a yt-dlp-compatible URL."""
    if sys.platform != "win32":
        return ""
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return ""
            proxy_server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return ""

    raw = str(proxy_server or "").strip()
    if not raw:
        return ""
    if ";" not in raw and "=" not in raw:
        return _normalize_proxy_url(raw)

    entries: dict[str, str] = {}
    for part in raw.split(";"):
        if "=" not in part:
            continue
        scheme, value = part.split("=", 1)
        entries[scheme.strip().lower()] = value.strip()

    for scheme in ("https", "http"):
        if entries.get(scheme):
            return _normalize_proxy_url(entries[scheme])
    if entries.get("socks"):
        socks_proxy = entries["socks"]
        if "://" not in socks_proxy:
            socks_proxy = f"socks5://{socks_proxy}"
        return socks_proxy
    return ""


def youtube_proxy_url() -> str | None:
    """Resolve proxy for YouTube yt-dlp calls.

    Returns:
        str: explicit or auto-detected proxy URL.
        None: no proxy option should be set.
        "": explicitly disable proxy use in yt-dlp.
    """
    rt = get_runtime_settings()
    configured = (rt.youtube_proxy or "").strip()
    if configured:
        if configured.lower() in {"direct", "none", "off", "false", "0"}:
            return ""
        return _normalize_proxy_url(configured)

    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.environ.get(key, "").strip()
        if value:
            return _normalize_proxy_url(value)

    detected = _proxy_from_windows_user_settings()
    return detected or None


def ytdlp_base_opts() -> dict[str, Any]:
    """Shared yt-dlp options: fail fast on network errors instead of retrying
    forever. Without this, a dead proxy or DNS issue produces ~9 retries
    × multiple clients (tv/android/web) × ~3 socket retries each = looks like
    an infinite loop in the log.

    Proxy handling: YouTube requests may run inside the cmd-launched daemon,
    which often does not inherit PowerShell-scoped proxy variables. Resolve a
    dedicated runtime setting first, then process/env proxy, then the Windows
    user proxy, and pass it explicitly to yt-dlp.

    EJS solver: YouTube's n-parameter signature challenge now requires a JS
    runtime via yt-dlp's EJS subsystem. Without it, extraction succeeds for
    metadata but all video/audio formats are filtered out (only images remain).
    `ejs:github` fetches the solver from the official yt-dlp release on demand
    and caches it; first call may take a few extra seconds.
    """
    opts: dict[str, Any] = {
        "retries": 3,                 # video-data retries
        "fragment_retries": 3,        # DASH fragment retries
        "extractor_retries": 3,       # extractor-level retries
        "socket_timeout": 15,         # cap each TCP attempt
        "remote_components": ["ejs:github"],
    }
    proxy = youtube_proxy_url()
    if proxy is not None:
        opts["proxy"] = proxy
    return opts


def is_youtube_network_error(error: BaseException | str, url: str | None = None) -> bool:
    if url and not _is_youtube_url(url):
        return False
    text = str(error).lower()
    if not text:
        return False
    return any(marker in text for marker in _YTDLP_NETWORK_ERROR_MARKERS)


def _youtube_network_error(url: str, error: BaseException) -> YoutubeNetworkError:
    return YoutubeNetworkError(
        "YouTube is unreachable after limited yt-dlp retries. "
        "Check Settings > YouTube > Proxy, or set youtube_proxy to your local proxy "
        "(for example http://127.0.0.1:7897). "
        f"Last error: {error}"
    )


def ytdlp_auth_opts() -> dict[str, Any]:
    """yt-dlp options for YouTube (and other sites) auth cookies.

    YouTube increasingly blocks unauthenticated requests ("Sign in to confirm
    you're not a bot"). Users can either point to an exported cookies.txt or
    name a browser for yt-dlp to read cookies from directly.
    """
    rt = get_runtime_settings()
    opts: dict[str, Any] = {}
    cookie_file = (rt.youtube_cookies_file or "").strip()
    cookie_browser = (rt.youtube_cookies_browser or "").strip().lower()
    if cookie_file:
        p = Path(cookie_file)
        if p.exists():
            opts["cookiefile"] = str(p)
        else:
            log_event(logger, logging.WARNING, "youtube.cookies.missing", path=cookie_file)
    elif cookie_browser:
        # yt-dlp expects a tuple (browser, profile, keyring, container)
        opts["cookiesfrombrowser"] = (cookie_browser,)
    return opts


def _bili_json_to_srt(body: list[dict]) -> str:
    """Convert Bilibili player/v2 subtitle JSON body to SRT text."""
    def _fmt(t: float) -> str:
        if t < 0:
            t = 0
        h = int(t // 3600); m = int((t % 3600) // 60)
        s = t - h * 3600 - m * 60
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")
    out: list[str] = []
    for i, cue in enumerate(body, 1):
        out.append(str(i))
        out.append(f"{_fmt(float(cue.get('from') or 0))} --> {_fmt(float(cue.get('to') or 0))}")
        out.append(str(cue.get("content") or ""))
        out.append("")
    return "\n".join(out)


def _parse_lang_priority(langs: list[str] | str | None = None) -> list[str]:
    """Normalize subtitle language priority strings like 'zh,en'."""
    if langs is None:
        raw = get_runtime_settings().subtitle_languages
        parts = raw.split(",") if raw else []
    elif isinstance(langs, str):
        parts = langs.split(",")
    else:
        parts = langs
    return [p.strip().lower() for p in parts if p and p.strip()]


def _lang_rank(lang: str, preferred: list[str]) -> int:
    """Return the priority rank for a language code, or a large value."""
    if not preferred:
        return 0
    normalized = (lang or "").lower()
    for idx, want in enumerate(preferred):
        if (
            normalized == want
            or normalized.startswith(want)
            or want.startswith(normalized)
            or want in normalized
        ):
            return idx
    return 999


def _subtitle_track_type(track: dict[str, Any]) -> int:
    """Return Bilibili subtitle type, preserving 0 as manual CC."""
    raw = track.get("type")
    return int(raw) if raw is not None else 1


def _filter_and_sort_subtitle_tracks(
    tracks: list[dict[str, Any]],
    preferred_langs: list[str],
) -> list[dict[str, Any]]:
    """Prefer configured languages first, then manual CC before AI."""
    indexed = list(enumerate(tracks))

    if preferred_langs:
        matched = [
            (i, t) for i, t in indexed
            if _lang_rank(str(t.get("lan") or ""), preferred_langs) < 999
        ]
        if matched:
            indexed = matched

    indexed.sort(key=lambda item: (
        _lang_rank(str(item[1].get("lan") or ""), preferred_langs),
        _subtitle_track_type(item[1]),  # 0=CC, 1=AI
        item[0],
    ))
    return [t for _, t in indexed]


def _empty_subtitle_result(
    *,
    engine: str | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "tracks": [],
        "subtitle_path": None,
        "subtitle_lang": None,
        "subtitle_format": None,
        "subtitle_engine": engine,
        "diagnostics": diagnostics or [],
    }


def _run_subprocess_streamed(
    cmd: list[str],
    cwd: str | None,
    timeout: int,
    log_prefix: str,
    tail: int = 20,
) -> tuple[int, list[str]]:
    """Run a subprocess and relay stdout line-by-line to logger.

    Each non-empty line becomes its own INFO log record, so it gets a real
    timestamp and can be correlated with main-pipeline events. Keeps the last
    `tail` lines around for error reporting.

    Returns (returncode, tail_lines).
    """
    # stdout=PIPE, stderr=STDOUT so we get a single ordered stream
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,  # line-buffered
    )
    buf: deque[str] = deque(maxlen=tail)

    def _reader():
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            # BBDown outputs GBK on Windows; decode with fallback
            try:
                line = raw.decode("utf-8").rstrip()
            except UnicodeDecodeError:
                line = raw.decode("gbk", errors="replace").rstrip()
            if not line:
                continue
            buf.append(line)
            log_event(logger, logging.INFO, "subprocess.output", prefix=log_prefix, line=line)
        proc.stdout.close()

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        t.join(timeout=2)
        raise RuntimeError(f"{log_prefix} timed out after {timeout}s")
    t.join(timeout=2)
    return rc, list(buf)


class YtdlpService:
    def __init__(self):
        self._settings = get_settings()

    def download(self, url: str, output_dir: Path | None = None) -> dict[str, Any]:
        """Download video (1080p preferred) + audio separately.

        Uses BBDown for Bilibili URLs (requires login via BBDown.exe login),
        yt-dlp for everything else.
        """
        if output_dir is None:
            rt = get_runtime_settings()
            output_dir = Path(rt.data_root).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        if _is_bilibili_url(url):
            return self._download_bilibili(url, output_dir)
        if _is_xiaoyuzhou_url(url):
            return self._download_xiaoyuzhou(url, output_dir)
        if _is_apple_podcast_url(url):
            return self._download_apple_podcast(url, output_dir)
        if _is_xiaohongshu_url(url):
            return self._download_xiaohongshu(url, output_dir)
        if _is_zhihu_url(url):
            return self._download_zhihu(url, output_dir)

        import yt_dlp
        outtmpl = str(output_dir / "%(title)s.%(ext)s")

        # Step 1: Download video (1080p preferred, degrade gracefully)
        video_opts = {
            "format": (
                "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
                "bestvideo[height<=1080]+bestaudio/"
                "best[height<=1080]/"
                "bestvideo+bestaudio/"
                "best"
            ),
            "merge_output_format": "mp4",
            "outtmpl": outtmpl,
            "writeinfojson": False,
            "quiet": not self._settings.debug,
            **ytdlp_base_opts(),
            **ytdlp_auth_opts(),
        }

        # Try video+audio download; on failure fall back to audio-only
        video_file = None
        info = None

        log_event(logger, logging.INFO, "download.video.started", url=url)
        try:
            with yt_dlp.YoutubeDL(video_opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as e:
            if is_youtube_network_error(e, url):
                raise _youtube_network_error(url, e) from e
            log_event(logger, logging.WARNING, "download.video.failed", url=url, fallback="audio_only", error=e)

        if info is None:
            # Video download failed entirely — get metadata + audio only
            meta_opts = {
                "outtmpl": outtmpl,
                "skip_download": True,
                "quiet": True,
                **ytdlp_base_opts(),
                **ytdlp_auth_opts(),
            }
            try:
                with yt_dlp.YoutubeDL(meta_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
            except Exception as e:
                if is_youtube_network_error(e, url):
                    raise _youtube_network_error(url, e) from e
                raise
            if info is None:
                raise RuntimeError(f"Failed to extract info: {url}")

        title = info.get("title", "unknown")

        # Find the downloaded video file
        if video_file is None:
            video_file = self._find_file(output_dir, title, {".mp4", ".mkv", ".webm"})

        # Step 2: Extract audio from video using ffmpeg
        audio_file = output_dir / f"{title}.wav"
        if video_file and video_file.exists():
            log_event(logger, logging.INFO, "audio.extract.started", input=video_file.name, output=audio_file.name)
            try:
                subprocess.run(
                    ["ffmpeg", "-i", str(video_file), "-vn",
                     "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                     str(audio_file), "-y"],
                    capture_output=True, check=True,
                )
            except subprocess.CalledProcessError as e:
                stderr = e.stderr.decode(errors="replace")[:500] if e.stderr else ""
                log_event(logger, logging.ERROR, "audio.extract.failed", stderr=stderr)
                audio_file = self._download_audio_only(url, output_dir, title)
        else:
            log_event(logger, logging.WARNING, "download.video.missing", fallback="audio_only")
            audio_file = self._download_audio_only(url, output_dir, title)
            video_file = None

        # Clean up intermediate files (m4a, webm parts, etc.) but keep video + audio
        keep = {audio_file, video_file} if video_file else {audio_file}
        self._cleanup_temp_files(output_dir, title, keep_files=keep)

        return {
            "url": url,
            "title": title,
            "file_path": str(audio_file) if audio_file and audio_file.exists() else None,
            "video_path": str(video_file) if video_file and video_file.exists() else None,
            "info": info,
        }

    @staticmethod
    def _fetch_bilibili_metadata(url: str) -> dict[str, Any]:
        """Fetch video metadata from Bilibili public API (no auth needed)."""
        import json
        import urllib.request

        bvid = _extract_bilibili_bvid(url)
        if not bvid:
            log_event(logger, logging.WARNING, "bilibili.bvid.missing", url=url)
            return {"webpage_url": url}

        info: dict[str, Any] = {"webpage_url": url}

        # Fetch video info
        try:
            req = urllib.request.Request(
                f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read()).get("data", {})

            owner = data.get("owner", {})
            info.update({
                "title": data.get("title"),
                "description": data.get("desc"),
                "uploader": owner.get("name"),
                "uploader_id": str(owner.get("mid", "")) if owner.get("mid") else None,
                "platform": "bilibili",
                "content_subtype": "video",
                "duration": data.get("duration"),
                "upload_date": datetime.fromtimestamp(data["pubdate"]).strftime("%Y%m%d") if data.get("pubdate") else None,
                "webpage_url": f"https://www.bilibili.com/video/{bvid}",
                "thumbnail": data.get("pic"),
                "extra": {"platform": "bilibili"},
            })
        except Exception as e:
            log_event(logger, logging.WARNING, "bilibili.view.failed", bvid=bvid, error=e)

        # Fetch tags
        try:
            req = urllib.request.Request(
                f"https://api.bilibili.com/x/tag/archive/tags?bvid={bvid}",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                tag_data = json.loads(resp.read()).get("data", [])
            info["tags"] = [t["tag_name"] for t in tag_data if t.get("tag_name")]
        except Exception as e:
            log_event(logger, logging.WARNING, "bilibili.tags.failed", bvid=bvid, error=e)

        return info

    def _download_bilibili(self, url: str, output_dir: Path) -> dict[str, Any]:
        """Download Bilibili video using native DASH API (replaces BBDown)."""
        from app.services.ingestion.platform.bilibili.dash import download_video, extract_audio
        from app.services.ingestion.platform.bilibili.auth import is_logged_in

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        rt = get_runtime_settings()
        qn = rt.bilibili_preferred_quality if is_logged_in() else 16
        if not is_logged_in() and qn > 16:
            log_event(logger, logging.WARNING, "bilibili.quality.forced", reason="not_logged_in", qn=16)
            qn = 16

        bvid = _extract_bilibili_bvid(url)
        if not bvid:
            raise RuntimeError(f"Cannot extract Bilibili video id from URL: {url}")

        log_event(logger, logging.INFO, "bilibili.download.started", bvid=bvid, qn=qn)
        video_file, info = download_video(bvid, output_dir, qn=qn)

        title = video_file.stem
        audio_file = output_dir / f"{title}.wav"
        extract_audio(video_file, audio_file)

        meta = self._fetch_bilibili_metadata(url)
        meta["title"] = info.get("title", title)

        return {
            "url": url,
            "title": meta["title"],
            "file_path": str(audio_file),
            "video_path": str(video_file),
            "info": meta,
        }

    def _download_xiaoyuzhou(self, url: str, output_dir: Path) -> dict[str, Any]:
        """Download a Xiaoyuzhou podcast episode via page metadata + audio URL."""
        from app.services.ingestion.platform.xiaoyuzhou.api import (
            download_audio,
            fetch_metadata as fetch_xiaoyuzhou_metadata,
        )

        log_event(logger, logging.INFO, "xiaoyuzhou.metadata.fetch_started", url=url)
        info = fetch_xiaoyuzhou_metadata(url)
        audio_file, source_audio = download_audio(info, output_dir)
        return {
            "url": url,
            "title": info.get("title", "xiaoyuzhou_episode"),
            "file_path": str(audio_file),
            "video_path": None,
            "source_audio_path": str(source_audio) if source_audio else None,
            "info": info,
        }

    def _download_apple_podcast(self, url: str, output_dir: Path) -> dict[str, Any]:
        """Download an Apple Podcasts episode by resolving RSS enclosure URL."""
        from app.services.ingestion.platform.apple_podcast.api import (
            download_audio,
            fetch_metadata as fetch_apple_metadata,
        )

        log_event(logger, logging.INFO, "apple_podcast.metadata.fetch_started", url=url)
        info = fetch_apple_metadata(url)
        audio_file, source_audio = download_audio(info, output_dir)
        return {
            "url": url,
            "title": info.get("title", "apple_podcast_episode"),
            "file_path": str(audio_file),
            "video_path": None,
            "source_audio_path": str(source_audio) if source_audio else None,
            "info": info,
        }

    def _download_xiaohongshu(self, url: str, output_dir: Path) -> dict[str, Any]:
        """Download a Xiaohongshu note. Video notes are downloaded + WAV extracted;
        image notes return metadata only (images are fetched later by the pipeline)."""
        from app.services.ingestion.platform.xiaohongshu.api import (
            download_video,
            fetch_metadata as fetch_xiaohongshu_metadata,
        )

        log_event(logger, logging.INFO, "xiaohongshu.metadata.fetch_started", url=url)
        info = fetch_xiaohongshu_metadata(url)
        is_video = (info.get("extra") or {}).get("is_video", False)

        if not is_video:
            # Image note: return metadata only; pipeline will download images + run VLM
            return {
                "url": url,
                "title": info.get("title", "xiaohongshu_image"),
                "file_path": None,
                "video_path": None,
                "info": info,
            }

        video_file, audio_file = download_video(info, output_dir)
        return {
            "url": url,
            "title": info.get("title", "xiaohongshu_video"),
            "file_path": str(audio_file),
            "video_path": str(video_file),
            "info": info,
        }

    def _download_zhihu(self, url: str, output_dir: Path) -> dict[str, Any]:
        """Fetch a Zhihu pin/answer as a text note. No media file is downloaded."""
        from app.services.ingestion.platform.zhihu.api import fetch_metadata as fetch_zhihu_metadata

        log_event(logger, logging.INFO, "zhihu.metadata.fetch_started", url=url)
        info = fetch_zhihu_metadata(url)
        return {
            "url": url,
            "title": info.get("title", "zhihu_note"),
            "file_path": None,
            "video_path": None,
            "info": info,
        }

    def _download_bilibili_subtitle(
        self,
        url: str,
        output_dir: Path,
        langs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Download ALL usable Bilibili subtitle tracks via the wbi-signed player/v2 API.

        Uses wbi-signed /x/player/wbi/v2 (authenticated via SESSDATA from settings or
        BBDown.data fallback) to avoid the stale-URL bug in the unsigned endpoint.

        Returns:
            {
                "tracks": [{"path": str, "lang": str, "format": "srt", "type": "cc"|"ai"}, ...],
                # Back-compat single-track fields (first good track):
                "subtitle_path": str|None,
                "subtitle_lang": str|None,
                "subtitle_format": "srt"|None,
            }
        """
        import json
        import urllib.request

        rt = get_runtime_settings()
        engine = rt.bilibili_subtitle_engine or "native_wbi"
        strict_validation = bool(rt.bilibili_subtitle_strict_validation)
        min_coverage = float(rt.bilibili_subtitle_min_coverage)
        preferred_langs = _parse_lang_priority(langs)
        diagnostics: list[dict[str, Any]] = []

        empty = _empty_subtitle_result(engine=engine, diagnostics=diagnostics)

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        bvid = _extract_bilibili_bvid(url)
        if not bvid:
            log_event(logger, logging.WARNING, "bilibili.bvid.missing", url=url)
            diagnostics.append({"stage": "resolve", "status": "failed", "reason": "missing_bvid"})
            return empty

        output_dir.mkdir(parents=True, exist_ok=True)

        # Try new wbi-signed path first; fall back to old unsigned path on import error
        try:
            from app.services.ingestion.platform.bilibili.api import (
                player_v2 as bili_player_v2,
                view as bili_view,
                subtitle_url_matches_video,
            )
        except ImportError as e:
            log_event(logger, logging.WARNING, "bilibili.api.import_failed", error=e)
            diagnostics.append({
                "stage": "import",
                "status": "failed",
                "reason": "native_api_import_failed",
                "detail": str(e),
            })
            if not rt.bilibili_subtitle_allow_legacy_fallback:
                return empty
            log_event(logger, logging.WARNING, "bilibili.subtitle.legacy_fallback")
            return self._download_bilibili_subtitle_legacy(url, output_dir, bvid, preferred_langs)

        # --- Fetch video metadata (aid, cid, duration) ---
        try:
            view_data = bili_view(bvid)
        except Exception as e:
            log_event(logger, logging.WARNING, "bilibili.view.failed", bvid=bvid, error=e)
            diagnostics.append({"stage": "view", "status": "failed", "reason": "api_error", "detail": str(e)})
            return empty

        aid = int(view_data.get("aid") or 0)
        pages = view_data.get("pages") or []
        if not pages:
            log_event(logger, logging.WARNING, "bilibili.view.no_pages", bvid=bvid)
            diagnostics.append({"stage": "view", "status": "failed", "reason": "no_pages"})
            return empty
        page = pages[0]
        cid = int(page.get("cid") or 0)
        video_duration = float(page.get("duration") or view_data.get("duration") or 0)

        if not aid or not cid:
            log_event(logger, logging.WARNING, "bilibili.view.missing_ids", bvid=bvid, aid=aid, cid=cid)
            diagnostics.append({
                "stage": "view",
                "status": "failed",
                "reason": "missing_aid_or_cid",
                "aid": aid,
                "cid": cid,
            })
            return empty

        # --- Fetch subtitle track list via wbi-signed endpoint ---
        try:
            pv2_data = bili_player_v2(bvid, aid, cid)
        except Exception as e:
            log_event(logger, logging.WARNING, "bilibili.player_wbi.failed", bvid=bvid, error=e)
            diagnostics.append({
                "stage": "player_wbi_v2",
                "status": "failed",
                "reason": "api_error",
                "detail": str(e),
            })
            return empty

        tracks = ((pv2_data.get("subtitle") or {}).get("subtitles")) or []
        if not tracks:
            log_event(logger, logging.INFO, "bilibili.subtitle.empty", bvid=bvid, reason="no_tracks")
            diagnostics.append({"stage": "track_list", "status": "empty", "reason": "no_tracks"})
            return empty

        usable = [t for t in tracks if t.get("subtitle_url")]
        if not usable:
            log_event(logger, logging.INFO, "bilibili.subtitle.empty", bvid=bvid, reason="all_tracks_missing_url", tracks=len(tracks))
            diagnostics.append({
                "stage": "track_list",
                "status": "empty",
                "reason": "all_tracks_missing_url",
                "track_count": len(tracks),
            })
            return empty
        usable = _filter_and_sort_subtitle_tracks(usable, preferred_langs)

        saved_tracks: list[dict[str, Any]] = []
        seen_langs: set[str] = set()
        for track in usable:
            sub_url = track["subtitle_url"]
            if sub_url.startswith("//"):
                sub_url = "https:" + sub_url
            lan = track.get("lan", "unknown")
            t_type = _subtitle_track_type(track)
            t_label = "CC" if t_type == 0 else "AI"

            # Prefer CC over AI when same language has both
            if lan in seen_langs:
                continue

            # Validate that the blob URL encodes this video's aid+cid
            matches_video = subtitle_url_matches_video(sub_url, aid, cid)
            if strict_validation and not matches_video:
                log_event(
                    logger,
                    logging.WARNING,
                    "bilibili.subtitle.validation_failed",
                    bvid=bvid,
                    lang=lan,
                    type=t_label.lower(),
                    reason="aid_cid_mismatch",
                    aid=aid,
                    cid=cid,
                )
                diagnostics.append({
                    "stage": "validate_url",
                    "status": "skipped",
                    "reason": "aid_cid_mismatch",
                    "lang": lan,
                    "type": t_label.lower(),
                    "aid": aid,
                    "cid": cid,
                    "url_tail": sub_url.split("/")[-1].split("?")[0],
                })
                continue

            try:
                req = urllib.request.Request(sub_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    sub_json = json.loads(resp.read())
            except Exception as e:
                log_event(logger, logging.WARNING, "bilibili.subtitle.download_failed", bvid=bvid, lang=lan, type=t_label.lower(), error=e)
                diagnostics.append({
                    "stage": "download",
                    "status": "failed",
                    "reason": "download_error",
                    "lang": lan,
                    "type": t_label.lower(),
                    "detail": str(e),
                })
                continue

            body = sub_json.get("body") or []
            if len(body) < 3:
                log_event(logger, logging.INFO, "bilibili.subtitle.validation_skipped", bvid=bvid, lang=lan, type=t_label.lower(), reason="too_few_cues", cues=len(body))
                diagnostics.append({
                    "stage": "validate_body",
                    "status": "skipped",
                    "reason": "too_few_cues",
                    "lang": lan,
                    "type": t_label.lower(),
                    "cue_count": len(body),
                })
                continue

            coverage = None
            if video_duration > 0:
                last_t = float(body[-1].get("from") or 0)
                coverage = last_t / video_duration
                if coverage < min_coverage:
                    log_event(
                        logger,
                        logging.WARNING,
                        "bilibili.subtitle.validation_failed",
                        bvid=bvid,
                        lang=lan,
                        type=t_label.lower(),
                        reason="low_coverage",
                        coverage=round(coverage, 4),
                        min_coverage=min_coverage,
                        last_cue_seconds=round(last_t),
                        video_duration_seconds=round(video_duration),
                    )
                    diagnostics.append({
                        "stage": "validate_body",
                        "status": "skipped",
                        "reason": "low_coverage",
                        "lang": lan,
                        "type": t_label.lower(),
                        "coverage": round(coverage, 4),
                        "min_coverage": min_coverage,
                        "last_cue_seconds": last_t,
                        "video_duration_seconds": video_duration,
                    })
                    continue

            srt_path = output_dir / f"{bvid}.{lan}.srt"
            srt_path.write_text(_bili_json_to_srt(body), encoding="utf-8")
            log_event(logger, logging.INFO, "bilibili.subtitle.saved", bvid=bvid, lang=lan, type=t_label.lower(), cues=len(body), path=srt_path)
            saved_tracks.append({
                "path": str(srt_path),
                "lang": lan,
                "format": "srt",
                "type": "cc" if t_type == 0 else "ai",
                "source_engine": engine,
                "validation": {
                    "strict_url_match": strict_validation,
                    "url_matches_video": matches_video,
                    "coverage": round(coverage, 4) if coverage is not None else None,
                    "min_coverage": min_coverage,
                    "aid": aid,
                    "cid": cid,
                },
            })
            seen_langs.add(lan)

        if not saved_tracks:
            log_event(logger, logging.INFO, "bilibili.subtitle.empty", bvid=bvid, reason="all_validation_failed", tracks=len(usable))
            return empty

        first = saved_tracks[0]
        return {
            "tracks": saved_tracks,
            "subtitle_path": first["path"],
            "subtitle_lang": first["lang"],
            "subtitle_format": first["format"],
            "subtitle_engine": engine,
            "diagnostics": diagnostics,
        }

    def _download_bilibili_subtitle_legacy(
        self,
        url: str,
        output_dir: Path,
        bvid: str,
        preferred_langs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Legacy fallback: unsigned /x/player/v2 (used only if new api.py fails to import)."""
        import json
        import urllib.request

        engine = "legacy_unsigned"
        empty = _empty_subtitle_result(engine=engine)

        cookie_file = _BBDOWN_DIR / "BBDown.data"
        cookie = ""
        if cookie_file.exists():
            try:
                cookie = cookie_file.read_text(encoding="utf-8", errors="ignore").strip()
            except Exception as e:
                log_event(logger, logging.WARNING, "bbdown.cookie.read_failed", path=cookie_file, error=e)

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": f"https://www.bilibili.com/video/{bvid}",
        }
        if cookie:
            headers["Cookie"] = cookie

        def _get_json(api_url: str, timeout: int = 10) -> dict | None:
            try:
                req = urllib.request.Request(api_url, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read())
            except Exception as e:
                log_event(logger, logging.WARNING, "bilibili.legacy_api.failed", api=api_url[:60], error=e)
                return None

        view_resp = _get_json(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
        if not view_resp or view_resp.get("code") != 0:
            return empty
        pages = view_resp["data"].get("pages") or []
        if not pages:
            return empty
        page = pages[0]
        cid = page["cid"]
        video_duration = float(page.get("duration") or view_resp["data"].get("duration") or 0)

        pv2 = _get_json(f"https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}")
        if not pv2 or pv2.get("code") != 0:
            return empty
        tracks = ((pv2.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
        if not tracks:
            return empty

        usable = [t for t in tracks if t.get("subtitle_url")]
        if not usable:
            return empty
        usable = _filter_and_sort_subtitle_tracks(usable, preferred_langs or [])

        saved_tracks: list[dict[str, Any]] = []
        seen_langs: set[str] = set()
        for track in usable:
            sub_url = track["subtitle_url"]
            if sub_url.startswith("//"):
                sub_url = "https:" + sub_url
            lan = track.get("lan", "unknown")
            t_type = _subtitle_track_type(track)
            t_label = "CC" if t_type == 0 else "AI"
            if lan in seen_langs:
                continue
            try:
                req = urllib.request.Request(sub_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    sub_json = json.loads(resp.read())
            except Exception as e:
                log_event(logger, logging.WARNING, "bilibili.subtitle.download_failed", bvid=bvid, lang=lan, type=t_label.lower(), engine=engine, error=e)
                continue
            body = sub_json.get("body") or []
            if len(body) < 3:
                continue
            if video_duration > 0:
                last_t = float(body[-1].get("from") or 0)
                if (last_t / video_duration) < 0.6:
                    continue
            srt_path = output_dir / f"{bvid}.{lan}.srt"
            srt_path.write_text(_bili_json_to_srt(body), encoding="utf-8")
            saved_tracks.append({
                "path": str(srt_path),
                "lang": lan,
                "format": "srt",
                "type": "cc" if t_type == 0 else "ai",
                "source_engine": engine,
                "validation": {
                    "strict_url_match": False,
                    "url_matches_video": None,
                    "coverage": round(last_t / video_duration, 4) if video_duration > 0 else None,
                    "min_coverage": 0.6,
                },
            })
            seen_langs.add(lan)

        if not saved_tracks:
            return empty
        first = saved_tracks[0]
        return {
            "tracks": saved_tracks,
            "subtitle_path": first["path"],
            "subtitle_lang": first["lang"],
            "subtitle_format": first["format"],
            "subtitle_engine": engine,
            "diagnostics": [],
        }

    def fetch_metadata(self, url: str) -> dict[str, Any]:
        """Fetch video metadata without downloading the video.

        Returns the same info dict format as download() so extract_metadata() works.
        Bilibili: uses public API. YouTube/other: uses yt-dlp --skip-download.
        """
        if _is_bilibili_url(url):
            info = self._fetch_bilibili_metadata(url)
            return info
        if _is_xiaoyuzhou_url(url):
            from app.services.ingestion.platform.xiaoyuzhou.api import (
                fetch_metadata as fetch_xiaoyuzhou_metadata,
            )
            return fetch_xiaoyuzhou_metadata(url)
        if _is_apple_podcast_url(url):
            from app.services.ingestion.platform.apple_podcast.api import (
                fetch_metadata as fetch_apple_metadata,
            )
            return fetch_apple_metadata(url)
        if _is_xiaohongshu_url(url):
            from app.services.ingestion.platform.xiaohongshu.api import (
                fetch_metadata as fetch_xiaohongshu_metadata,
            )
            return fetch_xiaohongshu_metadata(url)
        if _is_zhihu_url(url):
            from app.services.ingestion.platform.zhihu.api import (
                fetch_metadata as fetch_zhihu_metadata,
            )
            return fetch_zhihu_metadata(url)

        import yt_dlp

        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            **ytdlp_base_opts(),
            **ytdlp_auth_opts(),
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            if is_youtube_network_error(e, url):
                raise _youtube_network_error(url, e) from e
            raise
        if info is None:
            raise RuntimeError(f"Failed to extract metadata: {url}")
        return info

    def _download_audio_only(self, url: str, output_dir: Path, title: str) -> Path:
        """Fallback: download audio only using yt-dlp."""
        import yt_dlp

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
            "writeinfojson": False,
            "quiet": not self._settings.debug,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
                "preferredquality": "192",
            }],
            **ytdlp_base_opts(),
            **ytdlp_auth_opts(),
        }

        log_event(logger, logging.INFO, "download.audio_only.started", url=url)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(url, download=True)
        except Exception as e:
            if is_youtube_network_error(e, url):
                raise _youtube_network_error(url, e) from e
            raise

        audio_file = output_dir / f"{title}.wav"
        if not audio_file.exists():
            matching = list(output_dir.glob("*.wav"))
            if matching:
                audio_file = max(matching, key=lambda p: p.stat().st_mtime)
        return audio_file

    def _find_file(self, directory: Path, title: str, extensions: set[str]) -> Path | None:
        """Find a file matching title with given extensions.

        Prefers the merged output (exact title match) over intermediate
        format-specific files like '.f399.mp4' or '.f140.m4a' that yt-dlp
        creates before merging.
        """
        import re

        # 1. Exact title match — this is the merged output
        for ext in extensions:
            candidate = directory / f"{title}{ext}"
            if candidate.exists():
                return candidate

        # 2. Fallback: most recent file with matching extension,
        #    but skip intermediate format files (.fNNN.ext) and .part files
        candidates = []
        for ext in extensions:
            for f in directory.glob(f"*{ext}"):
                if f.name.endswith(".part"):
                    continue
                # Skip yt-dlp intermediate files like 'title.f399.mp4'
                if re.search(r'\.f\d+\.[^.]+$', f.name):
                    continue
                candidates.append(f)
        if candidates:
            return max(candidates, key=lambda p: p.stat().st_mtime)
        return None

    def _cleanup_temp_files(self, output_dir: Path, title: str, keep_files: set[Path | None] | None = None):
        """Clean up temporary files after download."""
        import re
        keep = {f for f in (keep_files or set()) if f is not None}
        temp_extensions = {'.m4a', '.webm', '.part', '.ytdl', '.info.json', '.json'}

        for file in output_dir.iterdir():
            if not file.is_file():
                continue
            if file in keep:
                continue
            if title not in file.stem and not file.name.endswith('.info.json'):
                continue

            is_temp = (
                file.suffix in temp_extensions
                or file.name.endswith('.info.json')
                or file.name.endswith('.part')
                # yt-dlp intermediate format files: 'title.f399.mp4', 'title.f140.m4a'
                or re.search(r'\.f\d+\.[^.]+$', file.name)
            )
            if is_temp:
                try:
                    file.unlink()
                    log_event(logger, logging.INFO, "cleanup.temp_file.deleted", path=file)
                except Exception as e:
                    log_event(logger, logging.WARNING, "cleanup.temp_file.delete_failed", path=file, error=e)

    def extract_metadata(self, info: dict[str, Any], file_path: str | None = None) -> MediaMetadata:
        """
        Extract comprehensive metadata from yt-dlp info dict.
        """
        upload_date = None
        if info.get("upload_date"):
            try:
                upload_date = datetime.strptime(info["upload_date"], "%Y%m%d")
            except ValueError:
                pass
        elif info.get("timestamp"):
            try:
                upload_date = datetime.fromtimestamp(int(info["timestamp"]))
            except (TypeError, ValueError, OSError):
                pass

        file_hash = None
        if file_path and Path(file_path).exists():
            file_hash = self._compute_hash(file_path)

        tags = []
        if info.get("tags"):
            tags.extend(info["tags"])
        if info.get("categories"):
            tags.extend(info["categories"])
        seen = set()
        unique_tags = []
        for tag in tags:
            if tag and tag not in seen:
                seen.add(tag)
                unique_tags.append(tag)

        chapters = []
        if info.get("chapters"):
            for ch in info["chapters"]:
                if ch.get("title") and ch.get("start_time") is not None:
                    chapters.append(ChapterInfo(
                        title=ch["title"],
                        start_time=float(ch["start_time"])
                    ))

        description = info.get("description")
        if description and len(description) > 5000:
            description = description[:5000] + "..."

        media_type = MediaType.VIDEO
        raw_media_type = str(info.get("media_type") or "").lower()
        if raw_media_type == "podcast":
            media_type = MediaType.PODCAST
        elif (
            raw_media_type == "audio"
            or str(info.get("ext") or "").lower() in {"mp3", "m4a", "wav", "flac", "ogg"}
        ):
            media_type = MediaType.AUDIO
        elif raw_media_type == "image":
            media_type = MediaType.OTHER
        elif raw_media_type == "video":
            media_type = MediaType.VIDEO

        # Derive platform slug from extractor key
        extractor_key = str(info.get("extractor_key") or info.get("extractor") or "").lower()
        platform_map = {
            "bilibili": "bilibili",
            "youtube": "youtube",
            "youtubeTab": "youtube",
            "twitter": "twitter",
            "douyin": "douyin",
            "tiktok": "douyin",
            "weibo": "weibo",
            "zhihu": "zhihu",
        }
        platform = next(
            (v for k, v in platform_map.items() if k.lower() in extractor_key),
            "generic" if extractor_key else None,
        )
        # Prefer explicit top-level platform field (set by custom ingestors like xhs/xiaoyuzhou/bilibili)
        if info.get("platform"):
            platform = info["platform"]
        elif isinstance(info.get("extra"), dict) and info["extra"].get("platform"):
            platform = info["extra"]["platform"]

        uploader_id = (
            info.get("uploader_id")
            or info.get("channel_id")
            or info.get("uploader_url")  # last resort
        )
        # Infer content_subtype from media_type
        subtype_map = {
            MediaType.PODCAST: "podcast_episode",
            MediaType.AUDIO: "audio",
            MediaType.VIDEO: "video",
            MediaType.MEETING: "meeting",
        }
        content_subtype = subtype_map.get(media_type, "video")
        if info.get("content_subtype"):
            content_subtype = info["content_subtype"]

        metadata = MediaMetadata(
            title=info.get("title", "Unknown"),
            source_url=info.get("webpage_url") or info.get("original_url"),
            uploader=info.get("uploader") or info.get("channel") or info.get("uploader_id"),
            uploader_id=str(uploader_id) if uploader_id else None,
            platform=platform,
            upload_date=upload_date,
            duration_seconds=info.get("duration"),
            media_type=media_type,
            content_subtype=content_subtype,
            file_path=file_path,
            file_hash=file_hash,
            description=description,
            tags=unique_tags,
            chapters=chapters,
        )
        if isinstance(info.get("extra"), dict):
            metadata.extra.update(info["extra"])
        return metadata

    def download_subtitles(
        self,
        url: str,
        output_dir: Path,
        langs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Download ALL available platform subtitle tracks without downloading video.

        Returns:
            {
                "tracks": [{"path": str, "lang": str, "format": str, "type": "cc"|"ai"}],
                # Back-compat: first track as single fields
                "subtitle_path": str|None,
                "subtitle_lang": str|None,
                "subtitle_format": "json3"|"srt"|None,
            }
        """
        import yt_dlp

        preferred_langs = _parse_lang_priority(langs)
        empty = _empty_subtitle_result()

        if _is_bilibili_url(url):
            return self._download_bilibili_subtitle(url, output_dir, preferred_langs)
        if _is_xiaoyuzhou_url(url):
            try:
                info = self.fetch_metadata(url)
            except Exception as e:
                log_event(logger, logging.WARNING, "xiaoyuzhou.subtitle.probe_failed", error=e)
                return _empty_subtitle_result(
                    engine="xiaoyuzhou-page",
                    diagnostics=[{"stage": "metadata", "status": "failed", "detail": str(e)}],
                )
            return _empty_subtitle_result(
                engine="xiaoyuzhou-page",
                diagnostics=[{
                    "stage": "transcript",
                    "status": "skipped",
                    "reason": "no_public_transcript_endpoint",
                    "transcript_media_id": (info.get("extra") or {}).get("transcript_media_id"),
                }],
            )
        if _is_apple_podcast_url(url):
            return _empty_subtitle_result(
                engine="apple-podcast-rss",
                diagnostics=[{
                    "stage": "subtitle",
                    "status": "skipped",
                    "reason": "no_public_transcript_in_rss",
                }],
            )
        if _is_xiaohongshu_url(url):
            return _empty_subtitle_result(
                engine="xiaohongshu-page",
                diagnostics=[{
                    "stage": "subtitle",
                    "status": "skipped",
                    "reason": "no_public_subtitle_endpoint",
                }],
            )
        if _is_zhihu_url(url):
            return _empty_subtitle_result(
                engine="zhihu-page",
                diagnostics=[{
                    "stage": "subtitle",
                    "status": "skipped",
                    "reason": "text_note_no_subtitle_endpoint",
                }],
            )

        output_dir.mkdir(parents=True, exist_ok=True)

        # Probe all available subtitle languages via yt-dlp metadata
        metadata_opts = {
            "quiet": True,
            "skip_download": True,
            **ytdlp_base_opts(),
            **ytdlp_auth_opts(),
        }
        try:
            with yt_dlp.YoutubeDL(metadata_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            if is_youtube_network_error(e, url):
                raise _youtube_network_error(url, e) from e
            log_event(logger, logging.WARNING, "ytdlp.subtitle.probe_failed", url=url, error=e)
            return empty
        if not info:
            return empty

        manual_subs = info.get("subtitles") or {}
        auto_subs = info.get("automatic_captions") or {}

        # If user provided langs, filter; otherwise take ALL available
        def _filter(avail: dict, want: list[str] | None) -> list[str]:
            if not want:
                return list(avail.keys())
            out = []
            for w in want:
                w_l = w.lower()
                for k in avail.keys():
                    if k.lower() == w_l or k.lower().startswith(w_l) or w_l in k.lower():
                        if k not in out:
                            out.append(k)
            return out

        manual_langs = _filter(manual_subs, preferred_langs)
        auto_langs = _filter(auto_subs, preferred_langs)
        # Skip auto-captions for languages where a manual track exists
        auto_langs = [l for l in auto_langs if l not in manual_langs]

        tracks: list[dict[str, Any]] = []

        def _try_download(use_auto: bool, target_langs: list[str], type_label: str) -> None:
            if not target_langs:
                return
            ydl_opts = {
                "skip_download": True,
                "writesubtitles": not use_auto,
                "writeautomaticsub": use_auto,
                "subtitleslangs": target_langs,
                "subtitlesformat": "json3/srt/best",
                "outtmpl": str(output_dir / "%(id)s"),
                "quiet": True,
                **ytdlp_base_opts(),
                **ytdlp_auth_opts(),
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
            except Exception as e:
                log_event(
                    logger,
                    logging.WARNING,
                    "ytdlp.subtitle.download_failed",
                    auto=use_auto,
                    langs=",".join(target_langs),
                    error=e,
                )
                return
            for lang in target_langs:
                # Find the file yt-dlp wrote for this lang
                for ext in ["json3", "srt", "vtt"]:
                    for f in output_dir.glob(f"*.{lang}.{ext}"):
                        if any(t["path"] == str(f) for t in tracks):
                            continue
                        tracks.append({
                            "path": str(f),
                            "lang": lang,
                            "format": ext,
                            "type": type_label,
                        })
                        break
                    else:
                        continue
                    break

        _try_download(False, manual_langs, "cc")
        _try_download(True, auto_langs, "ai")

        if not tracks:
            log_event(logger, logging.INFO, "subtitle.empty", url=url, engine="yt-dlp")
            return empty

        log_event(
            logger,
            logging.INFO,
            "subtitle.downloaded",
            engine="yt-dlp",
            tracks=len(tracks),
            langs=",".join(t["lang"] for t in tracks),
        )
        first = tracks[0]
        return {
            "tracks": tracks,
            "subtitle_path": first["path"],
            "subtitle_lang": first["lang"],
            "subtitle_format": first["format"],
            "subtitle_engine": "yt-dlp",
            "diagnostics": [],
        }

    def _compute_hash(self, file_path: str) -> str:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()


_service: YtdlpService | None = None


def get_ytdlp_service() -> YtdlpService:
    global _service
    if _service is None:
        _service = YtdlpService()
    return _service


async def download_media(url: str, output_dir: Path | None = None) -> dict[str, Any]:
    import asyncio
    service = get_ytdlp_service()
    result = await asyncio.to_thread(service.download, url, output_dir=output_dir)
    metadata = service.extract_metadata(result["info"], result.get("file_path"))
    return {
        "file_path": result.get("file_path"),
        "video_path": result.get("video_path"),
        "metadata": metadata.model_dump(mode="json"),
        "info": result.get("info"),  # raw ingest info (needed for image-note pipeline)
    }


async def download_subtitles(
    url: str, output_dir: Path, langs: list[str] | None = None
) -> dict[str, Any]:
    import asyncio
    service = get_ytdlp_service()
    return await asyncio.to_thread(service.download_subtitles, url, output_dir, langs)


async def fetch_metadata(url: str) -> "MediaMetadata":
    """Fetch metadata without downloading — for subtitle fast path."""
    import asyncio
    service = get_ytdlp_service()
    info = await asyncio.to_thread(service.fetch_metadata, url)
    return service.extract_metadata(info)
