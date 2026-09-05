"""Subtitles for media ingestion."""

import logging
from pathlib import Path
from typing import Any

from app.core.logging_setup import log_event
from app.core.settings import get_runtime_settings
from app.services.ingestion.platform.source_urls import (
    _is_apple_podcast_url,
    _is_bilibili_url,
    _is_generic_webpage_url,
    _is_xiaohongshu_url,
    _is_xiaoyuzhou_url,
    _is_zhihu_url,
    normalize_bilibili_source_url,
)
from app.services.ingestion.ytdlp_options import (
    YoutubeNetworkError,
    _youtube_network_error,
    _YtdlpLogger,
    is_youtube_network_error,
    ytdlp_auth_opts,
    ytdlp_base_opts,
)

logger = logging.getLogger(__name__)


def _bili_json_to_srt(body: list[dict]) -> str:
    """Convert Bilibili player/v2 subtitle JSON body to SRT text."""

    def _fmt(t: float) -> str:
        if t < 0:
            t = 0
        h = int(t // 3600)
        m = int((t % 3600) // 60)
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
            (i, t) for i, t in indexed if _lang_rank(str(t.get("lan") or ""), preferred_langs) < 999
        ]
        if matched:
            indexed = matched

    indexed.sort(
        key=lambda item: (
            _lang_rank(str(item[1].get("lan") or ""), preferred_langs),
            _subtitle_track_type(item[1]),  # 0=CC, 1=AI
            item[0],
        )
    )
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
        "diagnostics": diagnostics if diagnostics is not None else [],
    }


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
    url = normalize_bilibili_source_url(url)
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
            diagnostics=[
                {
                    "stage": "transcript",
                    "status": "skipped",
                    "reason": "no_public_transcript_endpoint",
                    "transcript_media_id": (info.get("extra") or {}).get("transcript_media_id"),
                }
            ],
        )
    if _is_apple_podcast_url(url):
        return _empty_subtitle_result(
            engine="apple-podcast-rss",
            diagnostics=[
                {
                    "stage": "subtitle",
                    "status": "skipped",
                    "reason": "no_public_transcript_in_rss",
                }
            ],
        )
    if _is_xiaohongshu_url(url):
        return _empty_subtitle_result(
            engine="xiaohongshu-page",
            diagnostics=[
                {
                    "stage": "subtitle",
                    "status": "skipped",
                    "reason": "no_public_subtitle_endpoint",
                }
            ],
        )
    if _is_zhihu_url(url):
        return _empty_subtitle_result(
            engine="zhihu-page",
            diagnostics=[
                {
                    "stage": "subtitle",
                    "status": "skipped",
                    "reason": "text_note_no_subtitle_endpoint",
                }
            ],
        )
    if _is_generic_webpage_url(url):
        return _empty_subtitle_result(
            engine="webpage-scrape",
            diagnostics=[
                {
                    "stage": "subtitle",
                    "status": "skipped",
                    "reason": "text_note_no_subtitle_endpoint",
                }
            ],
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    # Probe all available subtitle languages via yt-dlp metadata
    metadata_logger = _YtdlpLogger()
    subtitle_network_error: YoutubeNetworkError | None = None
    metadata_opts = {
        "quiet": True,
        "skip_download": True,
        **ytdlp_base_opts(metadata_logger),
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
    if not manual_subs and not auto_subs and metadata_logger.has_youtube_network_error(url):
        raise _youtube_network_error(url, RuntimeError(metadata_logger.network_error_summary()))

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
        nonlocal subtitle_network_error
        if not target_langs or subtitle_network_error is not None:
            return
        subtitle_logger = _YtdlpLogger()
        ydl_opts = {
            "skip_download": True,
            "writesubtitles": not use_auto,
            "writeautomaticsub": use_auto,
            "subtitleslangs": target_langs,
            "subtitlesformat": "json3/srt/best",
            "outtmpl": str(output_dir / "%(id)s"),
            "quiet": True,
            **ytdlp_base_opts(subtitle_logger),
            **ytdlp_auth_opts(),
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception as e:
            if is_youtube_network_error(e, url):
                subtitle_network_error = _youtube_network_error(url, e)
                log_event(
                    logger,
                    logging.WARNING,
                    "ytdlp.subtitle.network_limited",
                    auto=use_auto,
                    langs=",".join(target_langs),
                    error=e,
                    fallback="media_download_asr",
                )
                return
            log_event(
                logger,
                logging.WARNING,
                "ytdlp.subtitle.download_failed",
                auto=use_auto,
                langs=",".join(target_langs),
                error=e,
            )
            return
        if subtitle_logger.has_youtube_network_error(url):
            subtitle_network_error = _youtube_network_error(
                url,
                RuntimeError(subtitle_logger.network_error_summary()),
            )
        for lang in target_langs:
            # Find the file yt-dlp wrote for this lang
            for ext in ["json3", "srt", "vtt"]:
                for f in output_dir.glob(f"*.{lang}.{ext}"):
                    if any(t["path"] == str(f) for t in tracks):
                        continue
                    tracks.append(
                        {
                            "path": str(f),
                            "lang": lang,
                            "format": ext,
                            "type": type_label,
                        }
                    )
                    break
                else:
                    continue
                break

    _try_download(False, manual_langs, "cc")
    _try_download(True, auto_langs, "ai")

    if not tracks:
        if subtitle_network_error:
            return _empty_subtitle_result(
                engine="yt-dlp",
                diagnostics=[
                    {
                        "stage": "subtitle",
                        "status": "failed",
                        "reason": "rate_limited_or_unreachable",
                        "detail": str(subtitle_network_error),
                        "fallback": "media_download_asr",
                    }
                ],
            )
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
