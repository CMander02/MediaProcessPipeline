"""Media download service — yt-dlp for general sites, BBDown for Bilibili."""

import hashlib
import logging
import re
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging_setup import log_event
from app.core.network import urllib_urlopen
from app.core.paths import get_workspace_paths
from app.core.settings import get_runtime_settings
from app.models import ChapterInfo, MediaMetadata, MediaType
from app.services.ingestion.platform.bilibili.subtitle_download import (
    _download_bilibili_subtitle as _subtitle_download_download_bilibili_subtitle,
)
from app.services.ingestion.platform.bilibili.subtitle_download import (
    _download_bilibili_subtitle_legacy as _subtitle_download_download_bilibili_subtitle_legacy,
)
from app.services.ingestion.platform.source_urls import _BILIBILI_BVID_RE as _BILIBILI_BVID_RE
from app.services.ingestion.platform.source_urls import _DIRECT_MEDIA_EXTS as _DIRECT_MEDIA_EXTS
from app.services.ingestion.platform.source_urls import _HTTP_URL_RE as _HTTP_URL_RE
from app.services.ingestion.platform.source_urls import (
    _bilibili_canonical_video_url as _bilibili_canonical_video_url,
)
from app.services.ingestion.platform.source_urls import (
    _bilibili_display_title as _bilibili_display_title,
)
from app.services.ingestion.platform.source_urls import _candidate_matches as _candidate_matches
from app.services.ingestion.platform.source_urls import _candidate_urls as _candidate_urls
from app.services.ingestion.platform.source_urls import _ensure_http_url as _ensure_http_url
from app.services.ingestion.platform.source_urls import (
    _extract_bilibili_bvid as _extract_bilibili_bvid,
)
from app.services.ingestion.platform.source_urls import (
    _extract_bilibili_page_number as _extract_bilibili_page_number,
)
from app.services.ingestion.platform.source_urls import _extract_http_urls as _extract_http_urls
from app.services.ingestion.platform.source_urls import _host_matches as _host_matches
from app.services.ingestion.platform.source_urls import (
    _is_apple_podcast_url as _is_apple_podcast_url,
)
from app.services.ingestion.platform.source_urls import (
    _is_bilibili_article_url as _is_bilibili_article_url,
)
from app.services.ingestion.platform.source_urls import (
    _is_bilibili_image_note_url as _is_bilibili_image_note_url,
)
from app.services.ingestion.platform.source_urls import (
    _is_bilibili_short_url as _is_bilibili_short_url,
)
from app.services.ingestion.platform.source_urls import _is_bilibili_url as _is_bilibili_url
from app.services.ingestion.platform.source_urls import (
    _is_bilibili_video_url as _is_bilibili_video_url,
)
from app.services.ingestion.platform.source_urls import _is_direct_media_url as _is_direct_media_url
from app.services.ingestion.platform.source_urls import (
    _is_generic_webpage_url as _is_generic_webpage_url,
)
from app.services.ingestion.platform.source_urls import _is_twitter_url as _is_twitter_url
from app.services.ingestion.platform.source_urls import _is_xiaohongshu_url as _is_xiaohongshu_url
from app.services.ingestion.platform.source_urls import _is_xiaoyuzhou_url as _is_xiaoyuzhou_url
from app.services.ingestion.platform.source_urls import _is_youtube_url as _is_youtube_url
from app.services.ingestion.platform.source_urls import _is_zhihu_url as _is_zhihu_url
from app.services.ingestion.platform.source_urls import _NoRedirectHandler as _NoRedirectHandler
from app.services.ingestion.platform.source_urls import (
    _normalize_bilibili_video_url as _normalize_bilibili_video_url,
)
from app.services.ingestion.platform.source_urls import (
    _resolve_bilibili_short_url as _resolve_bilibili_short_url,
)
from app.services.ingestion.platform.source_urls import (
    _resolve_bilibili_short_url_once as _resolve_bilibili_short_url_once,
)
from app.services.ingestion.platform.source_urls import (
    _select_bilibili_page as _select_bilibili_page,
)
from app.services.ingestion.platform.source_urls import (
    _urllib_urlopen_no_redirect as _urllib_urlopen_no_redirect,
)
from app.services.ingestion.platform.source_urls import (
    normalize_bilibili_source_url as normalize_bilibili_source_url,
)
from app.services.ingestion.platform.twitter.payload import (
    _clean_twitter_text as _clean_twitter_text,
)
from app.services.ingestion.platform.twitter.payload import (
    _clean_twitter_title as _clean_twitter_title,
)
from app.services.ingestion.platform.twitter.payload import (
    _dedupe_twitter_image_urls as _dedupe_twitter_image_urls,
)
from app.services.ingestion.platform.twitter.payload import (
    _extract_twitter_article_title as _extract_twitter_article_title,
)
from app.services.ingestion.platform.twitter.payload import (
    _extract_twitter_external_article_url as _extract_twitter_external_article_url,
)
from app.services.ingestion.platform.twitter.payload import (
    _is_twitter_content_image as _is_twitter_content_image,
)
from app.services.ingestion.platform.twitter.payload import (
    _twitter_image_dedupe_key as _twitter_image_dedupe_key,
)
from app.services.ingestion.subtitles import _bili_json_to_srt as _bili_json_to_srt
from app.services.ingestion.subtitles import _empty_subtitle_result as _empty_subtitle_result
from app.services.ingestion.subtitles import (
    _filter_and_sort_subtitle_tracks as _filter_and_sort_subtitle_tracks,
)
from app.services.ingestion.subtitles import _lang_rank as _lang_rank
from app.services.ingestion.subtitles import _parse_lang_priority as _parse_lang_priority
from app.services.ingestion.subtitles import _subtitle_track_type as _subtitle_track_type
from app.services.ingestion.subtitles import download_subtitles as _subtitles_download_subtitles
from app.services.ingestion.ytdlp_options import _BBDOWN_DIR as _BBDOWN_DIR
from app.services.ingestion.ytdlp_options import _BBDOWN_EXE as _BBDOWN_EXE
from app.services.ingestion.ytdlp_options import (
    _YTDLP_NETWORK_ERROR_MARKERS as _YTDLP_NETWORK_ERROR_MARKERS,
)
from app.services.ingestion.ytdlp_options import YoutubeNetworkError as YoutubeNetworkError
from app.services.ingestion.ytdlp_options import _normalize_proxy_url as _normalize_proxy_url
from app.services.ingestion.ytdlp_options import _youtube_network_error as _youtube_network_error
from app.services.ingestion.ytdlp_options import _YtdlpLogger as _YtdlpLogger
from app.services.ingestion.ytdlp_options import (
    is_youtube_network_error as is_youtube_network_error,
)
from app.services.ingestion.ytdlp_options import youtube_proxy_url as youtube_proxy_url
from app.services.ingestion.ytdlp_options import ytdlp_auth_opts as ytdlp_auth_opts
from app.services.ingestion.ytdlp_options import ytdlp_base_opts as ytdlp_base_opts

logger = logging.getLogger(__name__)

# BBDown executable — shipped with the project


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
        url = normalize_bilibili_source_url(url)
        if output_dir is None:
            rt = get_runtime_settings()
            output_dir = get_workspace_paths(rt.data_root).temporary("download")
        output_dir.mkdir(parents=True, exist_ok=True)

        if _is_bilibili_article_url(url):
            return self._download_bilibili_article(url, output_dir)
        if _is_bilibili_image_note_url(url):
            return self._download_bilibili_note(url, output_dir)
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
        if _is_generic_webpage_url(url):
            return self._download_webpage(url, output_dir)

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
            log_event(
                logger,
                logging.WARNING,
                "download.video.failed",
                url=url,
                fallback="audio_only",
                error=e,
            )

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
                if _is_twitter_url(url):
                    return self._download_twitter_webpage_note(url, output_dir, e)
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
            log_event(
                logger,
                logging.INFO,
                "audio.extract.started",
                input=video_file.name,
                output=audio_file.name,
            )
            try:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-i",
                        str(video_file),
                        "-vn",
                        "-acodec",
                        "pcm_s16le",
                        "-ar",
                        "16000",
                        "-ac",
                        "1",
                        str(audio_file),
                        "-y",
                    ],
                    capture_output=True,
                    check=True,
                )
            except subprocess.CalledProcessError as e:
                stderr = e.stderr.decode(errors="replace")[:500] if e.stderr else ""
                log_event(logger, logging.ERROR, "audio.extract.failed", stderr=stderr)
                audio_file = self._download_audio_only(url, output_dir, title)
        else:
            log_event(logger, logging.WARNING, "download.video.missing", fallback="audio_only")
            if _is_twitter_url(url):
                return self._download_twitter_webpage_note(
                    url,
                    output_dir,
                    RuntimeError("yt-dlp did not download a video file for this X/Twitter status"),
                )
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
            with urllib_urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read()).get("data", {})

            page_number = _extract_bilibili_page_number(url)
            selected_page = _select_bilibili_page(data, page_number)
            selected_page_number = int(selected_page.get("page") or page_number)
            owner = data.get("owner", {})
            title = _bilibili_display_title(data, selected_page, selected_page_number)
            info.update(
                {
                    "title": title or data.get("title"),
                    "description": data.get("desc"),
                    "uploader": owner.get("name"),
                    "uploader_id": str(owner.get("mid", "")) if owner.get("mid") else None,
                    "platform": "bilibili_video",
                    "content_subtype": "video",
                    "duration": selected_page.get("duration") or data.get("duration"),
                    "upload_date": datetime.fromtimestamp(data["pubdate"]).strftime("%Y%m%d")
                    if data.get("pubdate")
                    else None,
                    "webpage_url": _bilibili_canonical_video_url(bvid, selected_page_number),
                    "thumbnail": data.get("pic"),
                    "extra": {
                        "platform": "bilibili_video",
                        "bilibili_type": "video",
                        "bvid": bvid,
                        "aid": data.get("aid"),
                        "cid": selected_page.get("cid"),
                        "page_number": selected_page_number,
                        "part": selected_page.get("part"),
                        "pages_count": len(data.get("pages") or []),
                    },
                }
            )
        except Exception as e:
            log_event(logger, logging.WARNING, "bilibili.view.failed", bvid=bvid, error=e)

        # Fetch tags
        try:
            req = urllib.request.Request(
                f"https://api.bilibili.com/x/tag/archive/tags?bvid={bvid}",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib_urlopen(req, timeout=10) as resp:
                tag_data = json.loads(resp.read()).get("data", [])
            info["tags"] = [t["tag_name"] for t in tag_data if t.get("tag_name")]
        except Exception as e:
            log_event(logger, logging.WARNING, "bilibili.tags.failed", bvid=bvid, error=e)

        return info

    def _download_bilibili_article(self, url: str, output_dir: Path) -> dict[str, Any]:
        """Process a Bilibili article through the generic webpage path."""
        from app.services.ingestion.platform.webpage.api import download_webpage

        info = download_webpage(url, output_dir)
        info["platform"] = "bilibili_opus"
        info["content_subtype"] = "text_note"
        extra = info.setdefault("extra", {})
        if isinstance(extra, dict):
            extra["platform"] = "bilibili_opus"
            extra["bilibili_type"] = "article"
            extra.setdefault("source_platform", "webpage")
        return {
            "url": url,
            "title": info.get("title", "bilibili_article"),
            "file_path": None,
            "video_path": None,
            "info": info,
        }

    def _download_bilibili_note(self, url: str, output_dir: Path) -> dict[str, Any]:
        """Fetch Bilibili opus/dynamic metadata; images are downloaded by the note branch."""
        from app.services.ingestion.platform.bilibili.note import (
            fetch_metadata as fetch_bilibili_note,
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        info = fetch_bilibili_note(url)
        return {
            "url": url,
            "title": info.get("title", "bilibili_opus"),
            "file_path": None,
            "video_path": None,
            "info": info,
        }

    def _download_bilibili(self, url: str, output_dir: Path) -> dict[str, Any]:
        """Download Bilibili video using native DASH API (replaces BBDown)."""
        from app.services.ingestion.platform.bilibili.auth import is_logged_in
        from app.services.ingestion.platform.bilibili.dash import download_video, extract_audio

        url = _normalize_bilibili_video_url(url)

        rt = get_runtime_settings()
        qn = rt.bilibili_preferred_quality if is_logged_in() else 16
        if not is_logged_in() and qn > 16:
            log_event(
                logger, logging.WARNING, "bilibili.quality.forced", reason="not_logged_in", qn=16
            )
            qn = 16

        bvid = _extract_bilibili_bvid(url)
        if not bvid:
            raise RuntimeError(f"Cannot extract Bilibili video id from URL: {url}")

        page_number = _extract_bilibili_page_number(url)
        log_event(
            logger, logging.INFO, "bilibili.download.started", bvid=bvid, qn=qn, page=page_number
        )
        video_file, info = download_video(bvid, output_dir, qn=qn, page_number=page_number)

        title = video_file.stem
        audio_file = output_dir / f"{title}.wav"
        extract_audio(video_file, audio_file)

        meta = self._fetch_bilibili_metadata(url)
        meta["title"] = info.get("display_title") or info.get("title") or title

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
        )
        from app.services.ingestion.platform.xiaoyuzhou.api import (
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
        )
        from app.services.ingestion.platform.apple_podcast.api import (
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
        )
        from app.services.ingestion.platform.xiaohongshu.api import (
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

    def _download_webpage(self, url: str, output_dir: Path) -> dict[str, Any]:
        """Fetch a generic web page as a text note with localized media."""
        from app.services.ingestion.platform.webpage.api import download_webpage

        log_event(logger, logging.INFO, "webpage.metadata.fetch_started", url=url)
        info = download_webpage(url, output_dir)
        return {
            "url": url,
            "title": info.get("title", "webpage"),
            "file_path": None,
            "video_path": None,
            "info": info,
        }

    def _download_twitter_webpage_note(
        self,
        url: str,
        output_dir: Path,
        fallback_error: Exception | None = None,
    ) -> dict[str, Any]:
        """Fallback for X/Twitter status/article links unsupported by yt-dlp."""
        output_dir.mkdir(parents=True, exist_ok=True)
        info = self._fetch_twitter_webpage_note(url, fallback_error=fallback_error)
        extra = info.setdefault("extra", {})
        external_article_url = (
            extra.get("external_article_url") if isinstance(extra, dict) else None
        )
        if external_article_url and extra.get("content_kind") == "long_article":
            try:
                from app.services.ingestion.platform.webpage.api import download_webpage

                article_info = download_webpage(str(external_article_url), output_dir)
                article_extra = article_info.setdefault("extra", {})
                if not isinstance(article_extra, dict):
                    article_extra = {}
                    article_info["extra"] = article_extra
                external_scrape_engine = article_extra.get("scrape_engine")
                article_extra.update(extra)
                article_extra.update(
                    {
                        "platform": "twitter",
                        "external_article_url": str(external_article_url),
                        "external_scrape_engine": external_scrape_engine,
                        "article_body_status": "complete",
                        "article_body_engine": external_scrape_engine or "webpage",
                        "source_markdown_path": str(output_dir / "source.md"),
                    }
                )
                article_info.update(
                    {
                        "title": extra.get("article_title")
                        or article_info.get("title")
                        or info.get("title"),
                        "original_url": url,
                        "platform": "twitter",
                        "content_subtype": "text_note",
                        "uploader": info.get("uploader") or article_info.get("uploader"),
                    }
                )
                info = article_info
                extra = article_extra
                log_event(
                    logger,
                    logging.INFO,
                    "twitter.article.external_fetched",
                    status_url=url,
                    article_url=external_article_url,
                )
            except Exception as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "twitter.article.external_fetch_failed",
                    status_url=url,
                    article_url=external_article_url,
                    error=exc,
                )
        if (
            isinstance(extra, dict)
            and extra.get("content_kind") == "long_article"
            and extra.get("article_body_status") != "complete"
        ):
            try:
                from app.services.ingestion.platform.webpage.api import download_webpage

                article_info = download_webpage(url, output_dir)
                article_markdown = str(article_info.get("description") or "").strip()
                preview_markdown = str(info.get("description") or "").strip()
                if len(article_markdown) <= max(800, len(preview_markdown) * 2):
                    raise RuntimeError("Defuddle returned only the X article preview")
                article_extra = article_info.setdefault("extra", {})
                if not isinstance(article_extra, dict):
                    article_extra = {}
                    article_info["extra"] = article_extra
                article_scrape_engine = article_extra.get("scrape_engine")
                article_extra.update(extra)
                article_extra.update(
                    {
                        "platform": "twitter",
                        "status_url": url,
                        "article_body_status": "complete",
                        "article_body_engine": article_scrape_engine or "defuddle",
                        "source_markdown_path": str(output_dir / "source.md"),
                    }
                )
                article_info.update(
                    {
                        "title": extra.get("article_title")
                        or article_info.get("title")
                        or info.get("title"),
                        "original_url": url,
                        "webpage_url": url,
                        "platform": "twitter",
                        "content_subtype": "text_note",
                        "uploader": info.get("uploader") or article_info.get("uploader"),
                    }
                )
                info = article_info
                extra = article_extra
                log_event(
                    logger,
                    logging.INFO,
                    "twitter.article.status_fetched",
                    status_url=url,
                    engine=article_scrape_engine,
                    markdown_chars=len(article_markdown),
                )
            except Exception as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "twitter.article.status_fetch_failed",
                    status_url=url,
                    error=exc,
                )
        if isinstance(extra, dict):
            extra["source_markdown_path"] = str(output_dir / "source.md")
            extra.setdefault("image_count", 0)
        source_path = output_dir / "source.md"
        if not source_path.exists():
            source_path.write_text(str(info.get("description") or ""), encoding="utf-8")
        return {
            "url": url,
            "title": info.get("title", "x_status"),
            "file_path": None,
            "video_path": None,
            "info": info,
        }

    def _fetch_twitter_webpage_note(
        self,
        url: str,
        fallback_error: Exception | None = None,
    ) -> dict[str, Any]:
        page = self._scrape_twitter_page(url)
        resolved_url = page.get("url") or url
        body_text = _clean_twitter_text(str(page.get("text") or ""))
        article_body = _clean_twitter_text(str(page.get("article_body") or ""))
        article_title = _extract_twitter_article_title(page.get("article_text") or body_text)
        external_article_url = _extract_twitter_external_article_url(body_text)
        article_url = str(page.get("article_url") or "")
        is_x_article = bool(
            re.search(r"(?:x|twitter)\.com/i/article/\d+", article_url, re.IGNORECASE)
        )
        title = article_title or _clean_twitter_title(str(page.get("title") or "")) or "X post"
        image_urls = _dedupe_twitter_image_urls(page.get("image_urls"))
        markdown_parts = [f"# {title}", f"Source: {resolved_url}"]
        if article_body:
            markdown_parts.append(article_body)
        elif body_text:
            markdown_parts.append(body_text)
        for idx, image_url in enumerate(image_urls, start=1):
            markdown_parts.append(f"![X image {idx}]({image_url})")
        markdown = "\n\n".join(markdown_parts).strip() + "\n"
        uploader = page.get("uploader")
        thumbnail = image_urls[0] if image_urls else page.get("thumbnail")
        extra = {
            "platform": "twitter",
            "scrape_engine": "playwright",
            "twitter_type": "article" if is_x_article else (page.get("type") or "status"),
            "content_kind": "long_article" if is_x_article else "status",
            "article_url": article_url,
            "article_title": article_title,
            "article_body_status": "complete"
            if article_body
            else ("auth_required" if is_x_article else "not_applicable"),
            "external_article_url": external_article_url,
            "status_url": resolved_url,
            "image_urls": image_urls,
            "image_url_candidates": [[url] for url in image_urls],
            "image_count": len(image_urls),
        }
        if fallback_error:
            extra["ytdlp_error"] = str(fallback_error)
        return {
            "id": resolved_url,
            "title": title,
            "description": markdown,
            "webpage_url": resolved_url,
            "original_url": url,
            "platform": "twitter",
            "content_subtype": "text_note"
            if is_x_article
            else ("image_note" if image_urls else "text_note"),
            "media_type": "image",
            "uploader": uploader,
            "thumbnail": thumbnail,
            "extra": extra,
        }

    def _scrape_twitter_page(self, url: str) -> dict[str, Any]:
        if not bool(getattr(get_runtime_settings(), "playwright_enabled", True)):
            raise RuntimeError("Playwright browser extraction is disabled")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError("Playwright is required for X article/status fallback.") from e

        from app.services.ingestion.platform.twitter.api import storage_state_path

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context_kwargs = {
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
                ),
                "locale": "en-US",
            }
            auth_path = storage_state_path()
            if auth_path.exists():
                context_kwargs["storage_state"] = str(auth_path)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
            data = page.evaluate(
                """() => {
                    const meta = (name, property = name) => {
                        const byName = document.querySelector(`meta[name="${name}"]`);
                        const byProp = document.querySelector(`meta[property="${property}"]`);
                        return (byName?.getAttribute("content") || byProp?.getAttribute("content") || "").trim();
                    };
                    const links = Array.from(document.querySelectorAll("a")).map((a) => ({
                        href: a.href,
                        text: (a.innerText || "").trim(),
                    }));
                    const imageUrls = [];
                    const addImage = (value) => {
                        const url = (value || "").trim();
                        if (!/pbs\\.twimg\\.com\\/media\\//i.test(url)) return;
                        if (!imageUrls.includes(url)) imageUrls.push(url);
                    };
                    const addSrcset = (value) => {
                        (value || "").split(",").forEach((entry) => {
                            addImage(entry.trim().split(/\\s+/)[0]);
                        });
                    };
                    addImage(meta("twitter:image"));
                    addImage(meta("og:image"));
                    addImage(meta("image", "og:image"));
                    Array.from(document.querySelectorAll("article img, img")).forEach((img) => {
                        addImage(img.currentSrc || img.src);
                        addSrcset(img.getAttribute("srcset") || "");
                    });
                    const article = links.find((item) => /\\/i\\/article\\/\\d+/.test(item.href));
                    const articleText = article?.text || "";
                    const author = Array.from(document.querySelectorAll('a[href^="/"], a[href^="https://x.com/"]'))
                        .map((a) => (a.innerText || "").trim())
                        .find((text) => text && !text.includes("\\n") && !text.startsWith("@"));
                    return {
                        url: location.href,
                        title: document.title || meta("title", "og:title"),
                        text: document.body.innerText || "",
                        uploader: author || "",
                        thumbnail: meta("twitter:image", "og:image"),
                        image_urls: imageUrls,
                        article_url: article?.href || "",
                        article_text: articleText,
                        type: article ? "article" : (imageUrls.length ? "image_status" : "status"),
                    };
                }"""
            )
            article_url = str(data.get("article_url") or "") if isinstance(data, dict) else ""
            if article_url and auth_path.exists():
                article_page = context.new_page()
                article_page.goto(article_url, wait_until="domcontentloaded", timeout=45000)
                article_page.wait_for_timeout(4000)
                if "/i/flow/login" not in article_page.url:
                    article_data = article_page.evaluate(
                        """() => {
                            const clean = (value) => (value || "").replace(/\\n{3,}/g, "\\n\\n").trim();
                            const candidates = Array.from(document.querySelectorAll(
                                'main article, main [data-testid="article"], main [role="article"], main'
                            )).map((node) => clean(node.innerText));
                            const body = candidates.sort((a, b) => b.length - a.length)[0] || "";
                            const imageUrls = Array.from(document.querySelectorAll('main img'))
                                .map((img) => img.currentSrc || img.src || "")
                                .filter((src) => /pbs\\.twimg\\.com\\/media\\//i.test(src));
                            return { body, image_urls: [...new Set(imageUrls)] };
                        }"""
                    )
                    if isinstance(article_data, dict):
                        article_body = str(article_data.get("body") or "").strip()
                        preview = str(data.get("article_text") or "")
                        if len(article_body) > max(600, len(preview) * 2):
                            data["article_body"] = article_body
                            data["image_urls"] = _dedupe_twitter_image_urls(
                                [
                                    *(data.get("image_urls") or []),
                                    *(article_data.get("image_urls") or []),
                                ]
                            )
            browser.close()
        return data if isinstance(data, dict) else {}

    def _download_bilibili_subtitle(
        self,
        url: str,
        output_dir: Path,
        langs: list[str] | None = None,
    ) -> dict[str, Any]:
        return _subtitle_download_download_bilibili_subtitle(self, url, output_dir, langs)

    def _download_bilibili_subtitle_legacy(
        self,
        url: str,
        output_dir: Path,
        bvid: str,
        preferred_langs: list[str] | None = None,
    ) -> dict[str, Any]:
        return _subtitle_download_download_bilibili_subtitle_legacy(
            self, url, output_dir, bvid, preferred_langs
        )

    def fetch_metadata(self, url: str) -> dict[str, Any]:
        """Fetch video metadata without downloading the video.

        Returns the same info dict format as download() so extract_metadata() works.
        Bilibili: uses public API. YouTube/other: uses yt-dlp --skip-download.
        """
        url = normalize_bilibili_source_url(url)
        if _is_bilibili_article_url(url):
            from app.services.ingestion.platform.webpage.api import (
                fetch_metadata as fetch_webpage_metadata,
            )

            info = fetch_webpage_metadata(url)
            info["platform"] = "bilibili_opus"
            info["content_subtype"] = "text_note"
            extra = info.setdefault("extra", {})
            if isinstance(extra, dict):
                extra["platform"] = "bilibili_opus"
                extra["bilibili_type"] = "article"
            return info
        if _is_bilibili_image_note_url(url):
            from app.services.ingestion.platform.bilibili.note import (
                fetch_metadata as fetch_bilibili_note_metadata,
            )

            return fetch_bilibili_note_metadata(url)
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
        if _is_generic_webpage_url(url):
            from app.services.ingestion.platform.webpage.api import (
                fetch_metadata as fetch_webpage_metadata,
            )

            return fetch_webpage_metadata(url)

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
            if _is_twitter_url(url):
                return self._fetch_twitter_webpage_note(url, fallback_error=e)
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
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "wav",
                    "preferredquality": "192",
                }
            ],
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
                if re.search(r"\.f\d+\.[^.]+$", f.name):
                    continue
                candidates.append(f)
        if candidates:
            return max(candidates, key=lambda p: p.stat().st_mtime)
        return None

    def _cleanup_temp_files(
        self, output_dir: Path, title: str, keep_files: set[Path | None] | None = None
    ):
        """Clean up temporary files after download."""
        import re

        keep = {f for f in (keep_files or set()) if f is not None}
        temp_extensions = {".m4a", ".webm", ".part", ".ytdl", ".info.json", ".json"}

        for file in output_dir.iterdir():
            if not file.is_file():
                continue
            if file in keep:
                continue
            if title not in file.stem and not file.name.endswith(".info.json"):
                continue

            is_temp = (
                file.suffix in temp_extensions
                or file.name.endswith(".info.json")
                or file.name.endswith(".part")
                # yt-dlp intermediate format files: 'title.f399.mp4', 'title.f140.m4a'
                or re.search(r"\.f\d+\.[^.]+$", file.name)
            )
            if is_temp:
                try:
                    file.unlink()
                    log_event(logger, logging.INFO, "cleanup.temp_file.deleted", path=file)
                except Exception as e:
                    log_event(
                        logger,
                        logging.WARNING,
                        "cleanup.temp_file.delete_failed",
                        path=file,
                        error=e,
                    )

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
                    chapters.append(
                        ChapterInfo(title=ch["title"], start_time=float(ch["start_time"]))
                    )

        description = info.get("description")
        content_subtype = str(info.get("content_subtype") or "").strip().lower()
        if (
            description
            and len(description) > 5000
            and content_subtype not in {"image_note", "text_note"}
        ):
            description = description[:5000] + "..."

        media_type = MediaType.VIDEO
        raw_media_type = str(info.get("media_type") or "").lower()
        if raw_media_type == "podcast":
            media_type = MediaType.PODCAST
        elif raw_media_type == "audio" or str(info.get("ext") or "").lower() in {
            "mp3",
            "m4a",
            "wav",
            "flac",
            "ogg",
        }:
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
        if info.get("thumbnail"):
            metadata.extra.setdefault("thumbnail", info["thumbnail"])
        return metadata

    def download_subtitles(
        self,
        url: str,
        output_dir: Path,
        langs: list[str] | None = None,
    ) -> dict[str, Any]:
        return _subtitles_download_subtitles(self, url, output_dir, langs)

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
    from app.core.media_retention import record_media

    playback = (
        result.get("video_path") or result.get("source_audio_path") or result.get("file_path")
    )
    if playback:
        directory = Path(playback).parent
        record_media(directory, playback, "source", playback=True, regenerate_from=url)
        audio = result.get("file_path")
        if audio and audio != playback:
            record_media(directory, audio, "working", regenerate_from=playback)
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
