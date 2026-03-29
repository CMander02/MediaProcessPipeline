"""Ingestion service - yt-dlp download + local file scanning.

yt-dlp (~120ms) is loaded lazily on first use, not at import time.
"""


def download_media(*args, **kwargs):
    from app.services.ingestion.ytdlp import download_media as _fn
    return _fn(*args, **kwargs)


def scan_inbox(*args, **kwargs):
    from app.services.ingestion.scanner import scan_inbox as _fn
    return _fn(*args, **kwargs)


__all__ = [
    "download_media",
    "scan_inbox",
]
