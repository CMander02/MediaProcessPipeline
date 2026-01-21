"""yt-dlp download service."""

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.api.routes.settings import get_runtime_settings
from app.models import MediaMetadata, MediaType, ChapterInfo

logger = logging.getLogger(__name__)


class YtdlpService:
    def __init__(self):
        self._settings = get_settings()

    def download(self, url: str, output_dir: Path | None = None) -> dict[str, Any]:
        import yt_dlp

        if output_dir is None:
            rt = get_runtime_settings()
            output_dir = Path(rt.data_root).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
            "writeinfojson": False,  # Don't write info.json
            "quiet": not self._settings.debug,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
                "preferredquality": "192",
            }],
        }

        logger.info(f"Downloading: {url}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        if info is None:
            raise RuntimeError(f"Failed to download: {url}")

        title = info.get("title", "unknown")
        output_file = output_dir / f"{title}.wav"

        if not output_file.exists():
            matching = list(output_dir.glob("*.wav"))
            if matching:
                output_file = max(matching, key=lambda p: p.stat().st_mtime)

        # Clean up any leftover temporary files (original video, info.json, etc.)
        self._cleanup_temp_files(output_dir, title, keep_file=output_file)

        return {
            "url": url,
            "title": title,
            "file_path": str(output_file) if output_file.exists() else None,
            "info": info,
        }

    def _cleanup_temp_files(self, output_dir: Path, title: str, keep_file: Path | None = None):
        """Clean up temporary files after download (original video, info.json, etc.)."""
        # Extensions to clean up
        temp_extensions = {'.webm', '.mp4', '.mkv', '.m4a', '.info.json', '.json', '.part', '.ytdl'}

        for file in output_dir.iterdir():
            if not file.is_file():
                continue
            # Skip the file we want to keep
            if keep_file and file == keep_file:
                continue
            # Skip files that don't match the title pattern
            if title not in file.stem and not file.name.endswith('.info.json'):
                continue
            # Delete temp files
            if file.suffix in temp_extensions or file.name.endswith('.info.json'):
                try:
                    file.unlink()
                    logger.info(f"Cleaned up temp file: {file}")
                except Exception as e:
                    logger.warning(f"Failed to delete temp file {file}: {e}")

    def extract_metadata(self, info: dict[str, Any], file_path: str | None = None) -> MediaMetadata:
        """
        Extract comprehensive metadata from yt-dlp info dict.

        Extracts:
        - Basic info: title, uploader, upload_date, duration
        - Extended info: description, tags, chapters
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

        # Extract tags (handle both 'tags' and 'categories')
        tags = []
        if info.get("tags"):
            tags.extend(info["tags"])
        if info.get("categories"):
            tags.extend(info["categories"])
        # Remove duplicates while preserving order
        seen = set()
        unique_tags = []
        for tag in tags:
            if tag and tag not in seen:
                seen.add(tag)
                unique_tags.append(tag)

        # Extract chapters
        chapters = []
        if info.get("chapters"):
            for ch in info["chapters"]:
                if ch.get("title") and ch.get("start_time") is not None:
                    chapters.append(ChapterInfo(
                        title=ch["title"],
                        start_time=float(ch["start_time"])
                    ))

        # Extract description (limit length)
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


async def download_media(url: str) -> dict[str, Any]:
    service = get_ytdlp_service()
    result = service.download(url)
    metadata = service.extract_metadata(result["info"], result.get("file_path"))
    return {"file_path": result.get("file_path"), "metadata": metadata.model_dump(mode="json")}
