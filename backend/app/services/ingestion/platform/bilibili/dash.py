"""Bilibili DASH downloader — pure Python, stdlib only.

Downloads Bilibili videos directly via the /x/player/wbi/playurl DASH API,
then muxes video + audio with ffmpeg. No BBDown required.

Quality codes (qn):
  16  = 360P  (no login required)
  32  = 480P  (no login required)
  64  = 720P  (login required)
  80  = 1080P (login required)
  112 = 1080P+ (premium)
  116 = 1080P60 (premium)
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# HTTP headers required for Bilibili CDN
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Origin": "https://www.bilibili.com",
}

_CHUNK_SIZE = 1024 * 1024  # 1 MB


def _sanitize_filename(s: str) -> str:
    """Remove characters illegal in Windows filenames."""
    return re.sub(r'[\\/:*?"<>|]', '_', s).strip()


def _download_stream(url: str, dest: Path, title: str, referer: str, cookie: str = "") -> None:
    """Download a single DASH stream (video or audio) to dest.

    Tries the primary URL first, then falls back to backup URLs on failure.
    Downloads in 1 MB chunks and writes directly to dest.

    Args:
        url: Primary CDN URL.
        dest: Destination file path.
        title: Human-readable label used in log messages.
        referer: Referer header value (e.g. https://www.bilibili.com/video/BVxxx).
        cookie: Optional cookie header string for authenticated requests.
    """
    headers = dict(_HEADERS)
    headers["Referer"] = referer
    if cookie:
        headers["Cookie"] = cookie

    req = urllib.request.Request(url, headers=headers)
    try:
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
                raise RuntimeError(
                    f"Incomplete download: {downloaded}/{total} bytes for {title}"
                )
        logger.info(f"Downloaded {title}: {downloaded:,} bytes -> {dest.name}")
    except (urllib.error.URLError, RuntimeError) as e:
        logger.warning(f"Primary URL failed for {title}: {e} — dest will be incomplete/missing")
        if dest.exists():
            dest.unlink()
        raise


def _try_download_with_backup(
    primary_url: str,
    backup_urls: list[str],
    dest: Path,
    title: str,
    referer: str,
    cookie: str = "",
) -> None:
    """Try primary URL, then each backup URL in order until one succeeds."""
    urls = [primary_url] + (backup_urls or [])
    last_err: Exception | None = None
    for attempt, url in enumerate(urls):
        try:
            _download_stream(url, dest, title, referer, cookie)
            return
        except Exception as e:
            last_err = e
            logger.warning(f"Download attempt {attempt + 1}/{len(urls)} failed for {title}: {e}")
    raise RuntimeError(
        f"All {len(urls)} download URLs failed for {title}. Last error: {last_err}"
    )


def _select_video_track(tracks: list[dict[str, Any]], qn: int) -> dict[str, Any]:
    """Select best video track for the requested quality code.

    Selection priority:
    1. Exact match for requested qn.
    2. Highest id that is <= requested qn (best available within budget).
    3. Lowest available id (fallback to whatever is there).
    """
    if not tracks:
        raise RuntimeError("No video tracks in DASH response")

    # Exact match
    for t in tracks:
        if t.get("id") == qn:
            return t

    # Best available <= requested
    candidates = [t for t in tracks if t.get("id", 0) <= qn]
    if candidates:
        return max(candidates, key=lambda t: t.get("id", 0))

    # Fallback: lowest available
    return min(tracks, key=lambda t: t.get("id", 0))


def _select_audio_track(tracks: list[dict[str, Any]]) -> dict[str, Any]:
    """Select the highest quality audio track."""
    if not tracks:
        raise RuntimeError("No audio tracks in DASH response")
    return max(tracks, key=lambda t: t.get("id", 0))


def download_video(
    bvid: str,
    output_dir: Path,
    qn: int = 64,
) -> tuple[Path, dict[str, Any]]:
    """Download a Bilibili video and mux it into an mp4 file.

    Uses the DASH API (/x/player/wbi/playurl) to fetch video+audio streams,
    downloads each separately, then muxes with ffmpeg -c copy.

    Args:
        bvid: BV identifier string, e.g. "BV1xx411c7mD".
        output_dir: Directory to write files into (must exist or be creatable).
        qn: Preferred quality code (see module docstring). Defaults to 720P.

    Returns:
        (mp4_path, info_dict) where info_dict contains:
            title, aid, cid, duration, actual_qn

    Raises:
        RuntimeError: If the video has no DASH streams (FLV-only), or if any
            download/mux step fails.
    """
    # Lazy imports so module-level import doesn't crash if .api doesn't exist yet
    from .api import view as bili_view, playurl as bili_playurl  # noqa: PLC0415
    from .auth import is_logged_in, get_cookie  # noqa: PLC0415

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    referer = f"https://www.bilibili.com/video/{bvid}"
    cookie = get_cookie() if is_logged_in() else ""

    # If not logged in, cap quality at 360P
    if not is_logged_in() and qn > 16:
        logger.warning(
            f"Bilibili: not logged in, falling back from qn={qn} to qn=16 (360P)"
        )
        qn = 16

    # ----- 1. Fetch video metadata -----
    logger.info(f"Fetching Bilibili view metadata for {bvid}")
    view_data = bili_view(bvid)
    title_raw: str = view_data.get("title", bvid)
    aid: int = view_data.get("aid", 0)
    pages: list[dict] = view_data.get("pages", [])
    if not pages:
        raise RuntimeError(f"No pages found for {bvid}")
    first_page = pages[0]
    cid: int = first_page.get("cid", 0)
    duration: int = first_page.get("duration", view_data.get("duration", 0))

    title = _sanitize_filename(title_raw)
    logger.info(f"Video title: {title_raw!r}  aid={aid}  cid={cid}  duration={duration}s")

    # ----- 2. Fetch DASH playurl -----
    logger.info(f"Fetching DASH playurl for {bvid} qn={qn} fnval=16")
    play_data = bili_playurl(bvid, aid, cid, qn=qn, fnval=16)

    dash = play_data.get("dash")
    if not dash:
        raise RuntimeError(
            "Video requires FLV download — not supported. "
            "DASH playurl returned no 'dash' key."
        )

    video_tracks: list[dict] = dash.get("video", [])
    audio_tracks: list[dict] = dash.get("audio", [])

    # ----- 3. Select tracks -----
    video_track = _select_video_track(video_tracks, qn)
    audio_track = _select_audio_track(audio_tracks)
    actual_qn: int = video_track.get("id", qn)

    logger.info(
        f"Selected video track: qn={actual_qn} codecs={video_track.get('codecs', 'unknown')}"
    )
    logger.info(
        f"Selected audio track: id={audio_track.get('id')} codecs={audio_track.get('codecs', 'unknown')}"
    )

    # ----- 4. Download streams -----
    video_dest = output_dir / f"{title}_video.m4s"
    audio_dest = output_dir / f"{title}_audio.m4s"

    logger.info(f"Downloading video stream -> {video_dest.name}")
    _try_download_with_backup(
        primary_url=video_track["baseUrl"],
        backup_urls=video_track.get("backupUrl") or [],
        dest=video_dest,
        title=f"{title} [video]",
        referer=referer,
        cookie=cookie,
    )

    logger.info(f"Downloading audio stream -> {audio_dest.name}")
    _try_download_with_backup(
        primary_url=audio_track["baseUrl"],
        backup_urls=audio_track.get("backupUrl") or [],
        dest=audio_dest,
        title=f"{title} [audio]",
        referer=referer,
        cookie=cookie,
    )

    # ----- 5. Mux with ffmpeg -----
    mp4_path = output_dir / f"{title}.mp4"
    logger.info(f"Muxing -> {mp4_path.name}")
    result = subprocess.run(
        [
            "ffmpeg",
            "-i", str(video_dest),
            "-i", str(audio_dest),
            "-c", "copy",
            str(mp4_path),
            "-y",
        ],
        capture_output=True,
        timeout=600,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"ffmpeg mux failed (rc={result.returncode}): {stderr[-500:]}")

    # ----- 6. Clean up .m4s files -----
    for tmp in (video_dest, audio_dest):
        try:
            tmp.unlink()
            logger.debug(f"Cleaned up temp stream: {tmp.name}")
        except Exception as e:
            logger.warning(f"Failed to delete temp stream {tmp}: {e}")

    logger.info(f"Bilibili download complete: {mp4_path.name}")

    info_dict: dict[str, Any] = {
        "title": title_raw,
        "aid": aid,
        "cid": cid,
        "duration": duration,
        "actual_qn": actual_qn,
    }
    return mp4_path, info_dict


def extract_audio(video_path: Path, audio_path: Path) -> None:
    """Extract mono 16 kHz PCM WAV from a video file using ffmpeg.

    This is the standard audio format expected by the ASR pipeline.

    Args:
        video_path: Source video file.
        audio_path: Destination WAV file path.

    Raises:
        subprocess.CalledProcessError: If ffmpeg returns non-zero exit code.
    """
    subprocess.run(
        [
            "ffmpeg",
            "-i", str(video_path),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            str(audio_path),
            "-y",
        ],
        capture_output=True,
        check=True,
        timeout=300,
    )
    logger.info(f"Extracted audio: {video_path.name} -> {audio_path.name}")
