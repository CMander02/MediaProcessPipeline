"""yt-dlp download service."""

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.models import MediaMetadata, MediaType

logger = logging.getLogger(__name__)


class YtdlpService:
    def __init__(self):
        self._settings = get_settings()

    def download(self, url: str, output_dir: Path | None = None) -> dict[str, Any]:
        import yt_dlp

        output_dir = output_dir or self._settings.data_processing.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
            "writeinfojson": True,
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

        return {
            "url": url,
            "title": title,
            "file_path": str(output_file) if output_file.exists() else None,
            "info": info,
        }

    def extract_metadata(self, info: dict[str, Any], file_path: str | None = None) -> MediaMetadata:
        upload_date = None
        if info.get("upload_date"):
            try:
                upload_date = datetime.strptime(info["upload_date"], "%Y%m%d")
            except ValueError:
                pass

        file_hash = None
        if file_path and Path(file_path).exists():
            file_hash = self._compute_hash(file_path)

        return MediaMetadata(
            title=info.get("title", "Unknown"),
            source_url=info.get("webpage_url") or info.get("original_url"),
            uploader=info.get("uploader") or info.get("channel"),
            upload_date=upload_date,
            duration_seconds=info.get("duration"),
            media_type=MediaType.VIDEO,
            file_path=file_path,
            file_hash=file_hash,
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
