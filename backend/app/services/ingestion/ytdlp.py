"""yt-dlp download service."""

import hashlib
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.settings import get_runtime_settings
from app.models import MediaMetadata, MediaType, ChapterInfo

logger = logging.getLogger(__name__)


class YtdlpService:
    def __init__(self):
        self._settings = get_settings()

    def download(self, url: str, output_dir: Path | None = None) -> dict[str, Any]:
        """Download video (1080p preferred) + audio separately.

        Returns video file for playback and audio file for pipeline processing.
        """
        import yt_dlp

        if output_dir is None:
            rt = get_runtime_settings()
            output_dir = Path(rt.data_root).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

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
        }

        logger.info(f"Downloading video: {url}")
        with yt_dlp.YoutubeDL(video_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        if info is None:
            raise RuntimeError(f"Failed to download: {url}")

        title = info.get("title", "unknown")

        # Find the downloaded video file
        video_file = self._find_file(output_dir, title, {".mp4", ".mkv", ".webm"})

        # Step 2: Extract audio from video using ffmpeg
        audio_file = output_dir / f"{title}.wav"
        if video_file and video_file.exists():
            logger.info(f"Extracting audio: {video_file.name} -> {audio_file.name}")
            try:
                subprocess.run(
                    ["ffmpeg", "-i", str(video_file), "-vn",
                     "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                     str(audio_file), "-y"],
                    capture_output=True, check=True,
                )
            except subprocess.CalledProcessError as e:
                logger.error(f"ffmpeg audio extraction failed: {e.stderr.decode()[:500]}")
                # Fallback: download audio-only
                audio_file = self._download_audio_only(url, output_dir, title)
        else:
            # No video downloaded — fallback to audio-only
            logger.warning("Video file not found, falling back to audio-only download")
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
        }

        logger.info(f"Downloading audio-only: {url}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)

        audio_file = output_dir / f"{title}.wav"
        if not audio_file.exists():
            matching = list(output_dir.glob("*.wav"))
            if matching:
                audio_file = max(matching, key=lambda p: p.stat().st_mtime)
        return audio_file

    def _find_file(self, directory: Path, title: str, extensions: set[str]) -> Path | None:
        """Find a file matching title with given extensions."""
        for ext in extensions:
            candidate = directory / f"{title}{ext}"
            if candidate.exists():
                return candidate
        # Fallback: most recent file with matching extension
        candidates = []
        for ext in extensions:
            candidates.extend(directory.glob(f"*{ext}"))
        if candidates:
            return max(candidates, key=lambda p: p.stat().st_mtime)
        return None

    def _cleanup_temp_files(self, output_dir: Path, title: str, keep_files: set[Path | None] | None = None):
        """Clean up temporary files after download."""
        keep = {f for f in (keep_files or set()) if f is not None}
        temp_extensions = {'.m4a', '.webm', '.part', '.ytdl', '.info.json', '.json'}

        for file in output_dir.iterdir():
            if not file.is_file():
                continue
            if file in keep:
                continue
            if title not in file.stem and not file.name.endswith('.info.json'):
                continue
            if file.suffix in temp_extensions or file.name.endswith('.info.json'):
                try:
                    file.unlink()
                    logger.info(f"Cleaned up temp file: {file}")
                except Exception as e:
                    logger.warning(f"Failed to delete temp file {file}: {e}")

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

        return MediaMetadata(
            title=info.get("title", "Unknown"),
            source_url=info.get("webpage_url") or info.get("original_url"),
            uploader=info.get("uploader") or info.get("channel") or info.get("uploader_id"),
            upload_date=upload_date,
            duration_seconds=info.get("duration"),
            media_type=MediaType.VIDEO,
            file_path=file_path,
            file_hash=file_hash,
            description=description,
            tags=unique_tags,
            chapters=chapters,
        )

    def download_subtitles(
        self,
        url: str,
        output_dir: Path,
        langs: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Download platform subtitles without downloading the video.

        Args:
            url: Video URL
            output_dir: Directory to save subtitle files
            langs: Language priority list, e.g. ["zh", "en"]

        Returns:
            {"subtitle_path": str|None, "subtitle_lang": str|None,
             "subtitle_format": "json3"|"srt"|None}
        """
        import yt_dlp

        if langs is None:
            from app.core.settings import get_runtime_settings
            rt = get_runtime_settings()
            langs = [l.strip() for l in rt.subtitle_languages.split(",") if l.strip()]

        output_dir.mkdir(parents=True, exist_ok=True)

        # Try manual subtitles first, then auto-generated
        for write_auto in [False, True]:
            ydl_opts = {
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": write_auto,
                "subtitleslangs": langs,
                "subtitlesformat": "json3/srt/best",
                "outtmpl": str(output_dir / "%(id)s"),
                "quiet": True,
            }

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
            except Exception as e:
                logger.warning(f"Subtitle download failed (auto={write_auto}): {e}")
                continue

            result = self._find_best_subtitle(output_dir, langs)
            if result:
                kind = "auto" if write_auto else "manual"
                logger.info(f"Downloaded {kind} subtitle: {result['subtitle_path']}")
                return result

        logger.info(f"No subtitles found for {url}")
        return {"subtitle_path": None, "subtitle_lang": None, "subtitle_format": None}

    def _find_best_subtitle(self, output_dir: Path, langs: list[str]) -> dict | None:
        """Find the best subtitle file in output_dir by language and format priority."""
        for lang in langs:
            for ext in ["json3", "srt", "vtt"]:
                for f in output_dir.glob(f"*.{lang}.{ext}"):
                    return {
                        "subtitle_path": str(f),
                        "subtitle_lang": lang,
                        "subtitle_format": ext,
                    }
            for variant in [f"{lang}-Hans", f"{lang}-CN", f"{lang}-Hant"]:
                for ext in ["json3", "srt", "vtt"]:
                    for f in output_dir.glob(f"*.{variant}.{ext}"):
                        return {
                            "subtitle_path": str(f),
                            "subtitle_lang": lang,
                            "subtitle_format": ext,
                        }
        for ext in ["json3", "srt", "vtt"]:
            files = list(output_dir.glob(f"*.{ext}"))
            if files:
                return {
                    "subtitle_path": str(files[0]),
                    "subtitle_lang": "unknown",
                    "subtitle_format": ext,
                }
        return None

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
    }


async def download_subtitles(
    url: str, output_dir: Path, langs: list[str] | None = None
) -> dict[str, Any]:
    import asyncio
    service = get_ytdlp_service()
    return await asyncio.to_thread(service.download_subtitles, url, output_dir, langs)
