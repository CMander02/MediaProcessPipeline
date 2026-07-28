"""Thumbnail helpers for archived media."""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
import warnings
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
MAX_ARCHIVE_IMAGE_SOURCE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_IMAGE_PIXELS = 64_000_000
MAX_ARCHIVE_IMAGE_OUTPUT_BYTES = 16 * 1024 * 1024
SUPPORTED_ARCHIVE_IMAGE_FORMATS = {
    "AVIF",
    "BMP",
    "GIF",
    "JPEG",
    "PNG",
    "WEBP",
}


class ArchiveImageResizeError(ValueError):
    """Base error raised when an archive image cannot be delivered safely."""


class ArchiveImageTooLargeError(ArchiveImageResizeError):
    """The source pixels/file or resulting JPEG exceeds the configured limit."""


class ArchiveImageFormatError(ArchiveImageResizeError):
    """The source is not a supported, decodable image."""


class PillowUnavailableError(ArchiveImageResizeError):
    """Pillow is required for the archive image delivery endpoint."""


def _remove_partial_thumbnail(thumb_path: Path) -> None:
    try:
        thumb_path.unlink(missing_ok=True)
    except OSError:
        pass


def _create_pillow_thumbnail_from_bytes(
    data: bytes,
    thumb_path: Path,
    *,
    size: tuple[int, int],
    quality: int,
) -> bool:
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return False

    try:
        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            image.thumbnail(size, Image.Resampling.LANCZOS)
            image.save(thumb_path, "JPEG", quality=quality, optimize=True)
        return thumb_path.is_file() and thumb_path.stat().st_size > 0
    except Exception as exc:
        _remove_partial_thumbnail(thumb_path)
        logger.debug("Pillow image thumbnail conversion failed: %s", exc)
        return False


def _create_ffmpeg_thumbnail_from_bytes(
    data: bytes,
    thumb_path: Path,
    *,
    size: tuple[int, int],
) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False

    width, height = size
    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-y",
                "-i",
                "pipe:0",
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=decrease",
                "-frames:v",
                "1",
                "-q:v",
                "5",
                str(thumb_path),
            ],
            input=data,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0 and thumb_path.is_file() and thumb_path.stat().st_size > 0:
            return True
        _remove_partial_thumbnail(thumb_path)
        logger.debug(
            "ffmpeg image thumbnail conversion failed: returncode=%s stderr=%s",
            result.returncode,
            result.stderr.decode("utf-8", errors="replace").strip(),
        )
    except Exception as exc:
        _remove_partial_thumbnail(thumb_path)
        logger.debug("ffmpeg image thumbnail conversion failed: %s", exc)
    return False


def image_media_type(path: Path) -> str:
    return IMAGE_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def resize_archive_image_to_jpeg(
    source: Path,
    *,
    max_edge: int,
    quality: int,
) -> bytes:
    """Decode one bounded archive image and return an oriented, resized JPEG."""
    if not 256 <= max_edge <= 4096:
        raise ArchiveImageResizeError("max_edge must be between 256 and 4096")
    if not 55 <= quality <= 90:
        raise ArchiveImageResizeError("quality must be between 55 and 90")
    try:
        source_size = source.stat().st_size
    except OSError as exc:
        raise ArchiveImageFormatError("Archive image cannot be read") from exc
    if source_size <= 0:
        raise ArchiveImageFormatError("Archive image is empty")
    if source_size > MAX_ARCHIVE_IMAGE_SOURCE_BYTES:
        raise ArchiveImageTooLargeError(
            f"Archive image exceeds {MAX_ARCHIVE_IMAGE_SOURCE_BYTES} bytes",
        )

    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise PillowUnavailableError(
            "Pillow is required to resize archive images",
        ) from exc

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as opened:
                image_format = (opened.format or "").upper()
                if image_format not in SUPPORTED_ARCHIVE_IMAGE_FORMATS:
                    raise ArchiveImageFormatError(
                        f"Unsupported archive image format: {image_format or 'unknown'}",
                    )
                width, height = opened.size
                if width <= 0 or height <= 0:
                    raise ArchiveImageFormatError("Archive image has invalid dimensions")
                if width * height > MAX_ARCHIVE_IMAGE_PIXELS:
                    raise ArchiveImageTooLargeError(
                        f"Archive image exceeds {MAX_ARCHIVE_IMAGE_PIXELS} pixels",
                    )

                opened.seek(0)
                image = ImageOps.exif_transpose(opened)
                image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
                if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                    rgba = image.convert("RGBA")
                    flattened = Image.new("RGB", rgba.size, "white")
                    flattened.paste(rgba, mask=rgba.getchannel("A"))
                    image = flattened
                elif image.mode != "RGB":
                    image = image.convert("RGB")

                output = io.BytesIO()
                image.save(
                    output,
                    "JPEG",
                    quality=quality,
                    optimize=True,
                    progressive=True,
                )
                payload = output.getvalue()
    except ArchiveImageResizeError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ArchiveImageTooLargeError("Archive image pixel count is unsafe") from exc
    except (OSError, SyntaxError, ValueError) as exc:
        raise ArchiveImageFormatError("Archive image cannot be decoded") from exc

    if not payload:
        raise ArchiveImageFormatError("Archive image produced an empty JPEG")
    if len(payload) > MAX_ARCHIVE_IMAGE_OUTPUT_BYTES:
        raise ArchiveImageTooLargeError(
            f"Resized JPEG exceeds {MAX_ARCHIVE_IMAGE_OUTPUT_BYTES} bytes",
        )
    return payload


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
    thumb_path = archive_dir / "thumbnail.jpg"
    if _create_pillow_thumbnail_from_bytes(
        data,
        thumb_path,
        size=size,
        quality=quality,
    ):
        return thumb_path
    if _create_ffmpeg_thumbnail_from_bytes(data, thumb_path, size=size):
        return thumb_path
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
