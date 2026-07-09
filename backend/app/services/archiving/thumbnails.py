"""Thumbnail helpers for archived media."""

from __future__ import annotations

import io
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
}

DEFAULT_THUMBNAIL_SIZE = (480, 480)
DEFAULT_THUMBNAIL_QUALITY = 82


def image_media_type(path: Path) -> str:
    return IMAGE_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def first_image_note_image(archive_dir: Path) -> Path | None:
    image_dir = archive_dir / "images"
    if not image_dir.is_dir():
        return None
    for image in sorted(image_dir.iterdir(), key=lambda item: item.name.lower()):
        if image.is_file() and image.suffix.lower() in IMAGE_EXTS:
            return image
    return None


def create_image_thumbnail_from_bytes(
    data: bytes,
    archive_dir: Path,
    *,
    size: tuple[int, int] = DEFAULT_THUMBNAIL_SIZE,
    quality: int = DEFAULT_THUMBNAIL_QUALITY,
) -> Path | None:
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None

    thumb_path = archive_dir / "thumbnail.jpg"
    try:
        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            image.thumbnail(size, Image.Resampling.LANCZOS)
            image.save(thumb_path, "JPEG", quality=quality, optimize=True)
        return thumb_path if thumb_path.exists() else None
    except Exception as exc:
        logger.debug("image thumbnail conversion failed: %s", exc)
        return None


def create_image_thumbnail(
    source: Path,
    archive_dir: Path,
    *,
    size: tuple[int, int] = DEFAULT_THUMBNAIL_SIZE,
    quality: int = DEFAULT_THUMBNAIL_QUALITY,
) -> Path | None:
    try:
        with source.open("rb") as file:
            return create_image_thumbnail_from_bytes(
                file.read(),
                archive_dir,
                size=size,
                quality=quality,
            )
    except Exception as exc:
        logger.debug("image thumbnail read failed: %s", exc)
        return None
