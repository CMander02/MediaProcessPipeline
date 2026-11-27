"""Thumbnail helpers for archived media."""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
import sys
import tempfile
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
    ".heic": "image/heic",
}
FORMAT_MIME_TYPES = {
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "avif": "image/avif",
    "heic": "image/heic",
}

DEFAULT_THUMBNAIL_SIZE = (480, 480)
DEFAULT_THUMBNAIL_QUALITY = 82


def detect_image_format(data: bytes) -> str | None:
    """Detect real image container format from file header magic bytes."""
    if not data:
        return None
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data.startswith(b"BM"):
        return "bmp"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12].lower()
        if brand in {b"avif", b"avis"}:
            return "avif"
        if brand in {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}:
            return "heic"
    return None


def image_mime_for_format(fmt: str | None) -> str | None:
    if not fmt:
        return None
    return FORMAT_MIME_TYPES.get(fmt.lower())


def is_probably_error_payload(data: bytes) -> bool:
    """Return True when downloaded payload looks empty or an HTML/JSON/XML error body."""
    if not data:
        return True
    if detect_image_format(data[:64]) is not None:
        return False
    head = data[:512].lstrip().lower()
    return head.startswith((
        b"<!doctype",
        b"<html",
        b"<head",
        b"<body",
        b"<?xml",
        b"<error",
        b'{"code":',
        b'{"success":false',
        b'{"error"',
        b'{"msg":',
        b'{"message":',
    ))


def transcode_heic_to_jpeg_bytes(data: bytes, *, source_path: Path | None = None) -> bytes:
    """Decode HEIC/HEIF image bytes into standard JPEG bytes."""
    try:
        import pillow_heif  # type: ignore[import-not-found]
        from PIL import Image, ImageOps

        heif = pillow_heif.read_heif(io.BytesIO(data))
        image = Image.frombytes(heif.mode, heif.size, heif.data, "raw")
        image = ImageOps.exif_transpose(image)
        if image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=92, optimize=True)
        out = buf.getvalue()
        if out.startswith(b"\xff\xd8\xff"):
            return out
    except Exception:
        pass

    with tempfile.TemporaryDirectory(prefix="mpp-heic-") as tmpdir:
        tmp_dir = Path(tmpdir)
        in_path = source_path if source_path and source_path.is_file() else (tmp_dir / "input.heic")
        if in_path != source_path:
            in_path.write_bytes(data)
        out_path = tmp_dir / "output.jpg"
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            proc = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(in_path), "-frames:v", "1", "-q:v", "2", str(out_path)],
                capture_output=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if proc.returncode == 0 and out_path.is_file():
                out = out_path.read_bytes()
                if out.startswith(b"\xff\xd8\xff"):
                    return out
        if sys.platform == "darwin" and shutil.which("sips"):
            proc = subprocess.run(
                ["sips", "-s", "format", "jpeg", str(in_path), "--out", str(out_path)],
                capture_output=True,
                timeout=30,
            )
            if proc.returncode == 0 and out_path.is_file():
                out = out_path.read_bytes()
                if out.startswith(b"\xff\xd8\xff"):
                    return out
    raise RuntimeError("HEIC/HEIF image could not be decoded to JPEG")


def image_media_type(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            fmt = detect_image_format(handle.read(64))
        if fmt and (mime := image_mime_for_format(fmt)):
            return mime
    except OSError:
        pass
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
        if detect_image_format(data[:64]) == "heic":
            data = transcode_heic_to_jpeg_bytes(data)
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
