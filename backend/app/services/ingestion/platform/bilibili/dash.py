"""Bilibili DASH downloader with anonymous progressive 360P fallback.

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

import http.client
import logging
import re
import ssl
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import httpx

from app.core.network import httpx_client_kwargs, urllib_urlopen

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
        with urllib_urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
            if not downloaded or (total and downloaded < total):
                raise RuntimeError(
                    f"Incomplete download: {downloaded}/{total} bytes for {title}"
                )
        logger.info(f"Downloaded {title}: {downloaded:,} bytes -> {dest.name}")
    except Exception as e:
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
    urls = list(dict.fromkeys(url for url in [primary_url, *(backup_urls or [])] if url))
    last_err: Exception | None = None
    for url in urls:
        try:
            _download_stream(url, dest, title, referer, cookie)
            return
        except (urllib.error.URLError, ssl.SSLError, http.client.HTTPException,
                ConnectionError, TimeoutError, RuntimeError) as exc:
            last_err = exc
        # A separate HTTP transport can recover urllib TLS/proxy EOF failures.
        # Keep the configured proxy and certificate verification in both clients.
        try:
            _download_stream_httpx(url, dest, referer, cookie)
            return
        except (httpx.HTTPError, RuntimeError) as exc:
            last_err = exc
            logger.warning("CDN transfer failed for %s (%s)", title, type(exc).__name__)
    raise DownloadError(
        f"All {len(urls)} download URLs failed for {title}. Last error: {last_err}"
    ) from last_err


class DownloadError(RuntimeError):
    """All media transfer attempts failed."""


def _download_stream_httpx(url: str, dest: Path, referer: str, cookie: str) -> None:
    headers = {**_HEADERS, "Referer": referer, "Accept-Encoding": "identity"}
    if cookie:
        headers["Cookie"] = cookie
    try:
        with httpx.Client(**httpx_client_kwargs(url), timeout=60, follow_redirects=True) as client:
            with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                total = int(response.headers.get("Content-Length", 0))
                downloaded = 0
                with dest.open("wb") as output:
                    for chunk in response.iter_bytes(_CHUNK_SIZE):
                        output.write(chunk)
                        downloaded += len(chunk)
                if not downloaded or (total and downloaded < total):
                    raise RuntimeError(f"Incomplete download: {downloaded}/{total} bytes")
    except Exception:
        dest.unlink(missing_ok=True)
        raise


def _download_media(play_data: dict, qn: int, output_dir: Path, title: str,
                    referer: str, cookie: str, temporary: list[Path]) -> tuple[list[Path], int]:
    dash = play_data.get("dash")
    if dash:
        video = _select_video_track(dash.get("video") or [], qn)
        audio = _select_audio_track(dash.get("audio") or [])
        tracks = [(video, "video"), (audio, "audio")]
        actual_qn = int(video.get("id", qn))
    else:
        segments = play_data.get("durl") or []
        if len(segments) != 1:
            raise RuntimeError("Bilibili did not return a single progressive video stream")
        tracks = [(segments[0], "progressive")]
        actual_qn = int(play_data.get("quality", qn))
    inputs = []
    for track, label in tracks:
        dest = output_dir / f"{title}_{label}.m4s"
        temporary.append(dest)
        _try_download_with_backup(
            track.get("baseUrl") or track.get("base_url") or track.get("url") or "",
            track.get("backupUrl") or track.get("backup_url") or [],
            dest, f"{title} [{label}]", referer, cookie,
        )
        inputs.append(dest)
    return inputs, actual_qn


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
    page_number: int = 1,
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
        RuntimeError: If both media download paths fail, or muxing fails.
    """
    # Lazy imports so module-level import doesn't crash if .api doesn't exist yet
    from .api import view as bili_view, playurl as bili_playurl  # noqa: PLC0415
    from .auth import is_logged_in, get_cookie  # noqa: PLC0415

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    page_number = max(int(page_number or 1), 1)
    referer = f"https://www.bilibili.com/video/{bvid}" + (f"?p={page_number}" if page_number > 1 else "")
    logged_in = is_logged_in()
    cookie = get_cookie() if logged_in else ""

    # If not logged in, cap quality at 360P
    if not logged_in and qn > 16:
        logger.warning(
            f"Bilibili: not logged in, falling back from qn={qn} to qn=16 (360P)"
        )
        qn = 16

    # ----- 1. Fetch video metadata -----
    logger.info(f"Fetching Bilibili view metadata for {bvid}")
    view_data = bili_view(bvid, cookie=cookie)
    title_raw: str = view_data.get("title", bvid)
    aid: int = view_data.get("aid", 0)
    pages: list[dict] = view_data.get("pages", [])
    if not pages:
        raise RuntimeError(f"No pages found for {bvid}")
    selected_page = _select_page(pages, page_number)
    selected_page_number = int(selected_page.get("page") or page_number)
    cid: int = selected_page.get("cid", 0)
    duration: int = selected_page.get("duration", view_data.get("duration", 0))
    part_title = str(selected_page.get("part") or "").strip()

    display_title = title_raw
    if len(pages) > 1 and part_title and part_title != title_raw:
        display_title = f"{title_raw} P{selected_page_number} {part_title}"

    title = _sanitize_filename(display_title)
    logger.info(
        f"Video title: {display_title!r} aid={aid} cid={cid} page={selected_page_number} duration={duration}s"
    )

    temporary: list[Path] = []
    mp4_path = output_dir / f"{title}.mp4"
    try:
        try:
            play_data = bili_playurl(bvid, aid, cid, qn=qn,
                                     fnval=16 if logged_in else 1, cookie=cookie)
            inputs, actual_qn = _download_media(
                play_data, qn, output_dir, title, referer, cookie, temporary)
        except (RuntimeError, urllib.error.URLError, ssl.SSLError,
                http.client.HTTPException, ConnectionError, TimeoutError) as exc:
            logger.warning("Bilibili transfer failed (%s); refreshing anonymous 360P URL",
                           type(exc).__name__)
            play_data = bili_playurl(bvid, aid, cid, qn=16, fnval=1, cookie="")
            inputs, actual_qn = _download_media(
                play_data, 16, output_dir, title, referer, "", temporary)
        command = ["ffmpeg", "-nostdin", "-y"]
        for source in inputs:
            command.extend(["-i", str(source)])
        command.extend(["-c", "copy", str(mp4_path)])
        result = subprocess.run(command, capture_output=True, timeout=600)
        if result.returncode != 0:
            mp4_path.unlink(missing_ok=True)
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"ffmpeg mux failed (rc={result.returncode}): {stderr[-500:]}")
    finally:
        for tmp in temporary:
            tmp.unlink(missing_ok=True)

    logger.info(f"Bilibili download complete: {mp4_path.name}")

    info_dict: dict[str, Any] = {
        "title": title_raw,
        "display_title": display_title,
        "aid": aid,
        "cid": cid,
        "duration": duration,
        "actual_qn": actual_qn,
        "page_number": selected_page_number,
        "part": part_title,
        "pages_count": len(pages),
    }
    return mp4_path, info_dict


def _select_page(pages: list[dict], page_number: int) -> dict:
    page_number = max(int(page_number or 1), 1)
    for page in pages:
        if int(page.get("page") or 0) == page_number:
            return page
    index = min(page_number - 1, len(pages) - 1)
    return pages[index]


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
