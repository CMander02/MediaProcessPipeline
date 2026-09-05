"""Subtitle download for media ingestion."""

import logging
from pathlib import Path
from typing import Any

from app.core.logging_setup import log_event
from app.core.network import urllib_urlopen
from app.core.settings import get_runtime_settings
from app.services.ingestion.platform.source_urls import (
    _extract_bilibili_bvid,
    _extract_bilibili_page_number,
    _normalize_bilibili_video_url,
    _select_bilibili_page,
)
from app.services.ingestion.subtitles import (
    _bili_json_to_srt,
    _empty_subtitle_result,
    _filter_and_sort_subtitle_tracks,
    _parse_lang_priority,
    _subtitle_track_type,
)
from app.services.ingestion.ytdlp_options import _BBDOWN_DIR

logger = logging.getLogger(__name__)


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

    url = _normalize_bilibili_video_url(url)

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
        )
        from app.services.ingestion.platform.bilibili.api import (
            subtitle_url_matches_video,
        )
        from app.services.ingestion.platform.bilibili.api import (
            view as bili_view,
        )
    except ImportError as e:
        log_event(logger, logging.WARNING, "bilibili.api.import_failed", error=e)
        diagnostics.append(
            {
                "stage": "import",
                "status": "failed",
                "reason": "native_api_import_failed",
                "detail": str(e),
            }
        )
        if not rt.bilibili_subtitle_allow_legacy_fallback:
            return empty
        log_event(logger, logging.WARNING, "bilibili.subtitle.legacy_fallback")
        return self._download_bilibili_subtitle_legacy(url, output_dir, bvid, preferred_langs)

    # --- Fetch video metadata (aid, cid, duration) ---
    try:
        view_data = bili_view(bvid)
    except Exception as e:
        log_event(logger, logging.WARNING, "bilibili.view.failed", bvid=bvid, error=e)
        diagnostics.append(
            {"stage": "view", "status": "failed", "reason": "api_error", "detail": str(e)}
        )
        return empty

    aid = int(view_data.get("aid") or 0)
    pages = view_data.get("pages") or []
    if not pages:
        log_event(logger, logging.WARNING, "bilibili.view.no_pages", bvid=bvid)
        diagnostics.append({"stage": "view", "status": "failed", "reason": "no_pages"})
        return empty
    page_number = _extract_bilibili_page_number(url)
    page = _select_bilibili_page(view_data, page_number)
    selected_page_number = int(page.get("page") or page_number)
    cid = int(page.get("cid") or 0)
    video_duration = float(page.get("duration") or view_data.get("duration") or 0)

    if not aid or not cid:
        log_event(logger, logging.WARNING, "bilibili.view.missing_ids", bvid=bvid, aid=aid, cid=cid)
        diagnostics.append(
            {
                "stage": "view",
                "status": "failed",
                "reason": "missing_aid_or_cid",
                "aid": aid,
                "cid": cid,
                "page": selected_page_number,
            }
        )
        return empty

    # --- Fetch subtitle track list via wbi-signed endpoint ---
    try:
        pv2_data = bili_player_v2(bvid, aid, cid)
    except Exception as e:
        log_event(logger, logging.WARNING, "bilibili.player_wbi.failed", bvid=bvid, error=e)
        diagnostics.append(
            {
                "stage": "player_wbi_v2",
                "status": "failed",
                "reason": "api_error",
                "detail": str(e),
            }
        )
        return empty

    tracks = ((pv2_data.get("subtitle") or {}).get("subtitles")) or []
    if not tracks:
        reason = "login_required" if pv2_data.get("need_login_subtitle") else "no_tracks"
        log_event(logger, logging.INFO, "bilibili.subtitle.empty", bvid=bvid, reason=reason)
        diagnostics.append({"stage": "track_list", "status": "empty", "reason": reason})
        return empty

    usable = [t for t in tracks if t.get("subtitle_url")]
    if not usable:
        log_event(
            logger,
            logging.INFO,
            "bilibili.subtitle.empty",
            bvid=bvid,
            reason="all_tracks_missing_url",
            tracks=len(tracks),
        )
        diagnostics.append(
            {
                "stage": "track_list",
                "status": "empty",
                "reason": "all_tracks_missing_url",
                "track_count": len(tracks),
            }
        )
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
            diagnostics.append(
                {
                    "stage": "validate_url",
                    "status": "skipped",
                    "reason": "aid_cid_mismatch",
                    "lang": lan,
                    "type": t_label.lower(),
                    "aid": aid,
                    "cid": cid,
                    "url_tail": sub_url.split("/")[-1].split("?")[0],
                }
            )
            continue

        try:
            req = urllib.request.Request(sub_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib_urlopen(req, timeout=15) as resp:
                sub_json = json.loads(resp.read())
        except Exception as e:
            log_event(
                logger,
                logging.WARNING,
                "bilibili.subtitle.download_failed",
                bvid=bvid,
                page=selected_page_number,
                lang=lan,
                type=t_label.lower(),
                error=e,
            )
            diagnostics.append(
                {
                    "stage": "download",
                    "status": "failed",
                    "reason": "download_error",
                    "lang": lan,
                    "type": t_label.lower(),
                    "detail": str(e),
                }
            )
            continue

        body = sub_json.get("body") or []
        if len(body) < 3:
            log_event(
                logger,
                logging.INFO,
                "bilibili.subtitle.validation_skipped",
                bvid=bvid,
                lang=lan,
                type=t_label.lower(),
                reason="too_few_cues",
                cues=len(body),
            )
            diagnostics.append(
                {
                    "stage": "validate_body",
                    "status": "skipped",
                    "reason": "too_few_cues",
                    "lang": lan,
                    "type": t_label.lower(),
                    "cue_count": len(body),
                }
            )
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
                diagnostics.append(
                    {
                        "stage": "validate_body",
                        "status": "skipped",
                        "reason": "low_coverage",
                        "lang": lan,
                        "type": t_label.lower(),
                        "coverage": round(coverage, 4),
                        "min_coverage": min_coverage,
                        "last_cue_seconds": last_t,
                        "video_duration_seconds": video_duration,
                    }
                )
                continue

        srt_path = output_dir / f"{bvid}.{lan}.srt"
        srt_path.write_text(_bili_json_to_srt(body), encoding="utf-8")
        log_event(
            logger,
            logging.INFO,
            "bilibili.subtitle.saved",
            bvid=bvid,
            page=selected_page_number,
            lang=lan,
            type=t_label.lower(),
            cues=len(body),
            path=srt_path,
        )
        saved_tracks.append(
            {
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
            }
        )
        seen_langs.add(lan)

    if not saved_tracks:
        log_event(
            logger,
            logging.INFO,
            "bilibili.subtitle.empty",
            bvid=bvid,
            reason="all_validation_failed",
            tracks=len(usable),
        )
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
            log_event(
                logger, logging.WARNING, "bbdown.cookie.read_failed", path=cookie_file, error=e
            )

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": f"https://www.bilibili.com/video/{bvid}",
    }
    if cookie:
        headers["Cookie"] = cookie

    def _get_json(api_url: str, timeout: int = 10) -> dict | None:
        try:
            req = urllib.request.Request(api_url, headers=headers)
            with urllib_urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as e:
            log_event(
                logger, logging.WARNING, "bilibili.legacy_api.failed", api=api_url[:60], error=e
            )
            return None

    view_resp = _get_json(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
    if not view_resp or view_resp.get("code") != 0:
        return empty
    pages = view_resp["data"].get("pages") or []
    if not pages:
        return empty
    page_number = _extract_bilibili_page_number(url)
    page = _select_bilibili_page(view_resp["data"], page_number)
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
            with urllib_urlopen(req, timeout=15) as resp:
                sub_json = json.loads(resp.read())
        except Exception as e:
            log_event(
                logger,
                logging.WARNING,
                "bilibili.subtitle.download_failed",
                bvid=bvid,
                lang=lan,
                type=t_label.lower(),
                engine=engine,
                error=e,
            )
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
        saved_tracks.append(
            {
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
            }
        )
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
