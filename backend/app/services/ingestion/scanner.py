"""Local file scanner service."""

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.api.routes.settings import get_runtime_settings

logger = logging.getLogger(__name__)


class LocalScanner:
    def __init__(self):
        self._settings = get_settings()
        self._index: dict[str, str] = {}

    def scan(self) -> list[dict[str, Any]]:
        rt = get_runtime_settings()
        inbox = Path(rt.data_root).resolve()
        inbox.mkdir(parents=True, exist_ok=True)

        audio_ext = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus"}
        new_files = []

        for f in inbox.iterdir():
            if f.suffix.lower() not in audio_ext:
                continue

            file_hash = self._compute_hash(str(f))
            if file_hash not in self._index:
                self._index[file_hash] = str(f)
                new_files.append({
                    "file_path": str(f),
                    "file_hash": file_hash,
                    "size": f.stat().st_size,
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                })
                logger.info(f"New file: {f.name}")

        return new_files

    def _compute_hash(self, file_path: str) -> str:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()


_scanner: LocalScanner | None = None


def get_scanner() -> LocalScanner:
    global _scanner
    if _scanner is None:
        _scanner = LocalScanner()
    return _scanner


async def scan_inbox() -> list[dict[str, Any]]:
    return get_scanner().scan()
