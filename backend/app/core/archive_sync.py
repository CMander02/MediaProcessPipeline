"""Safe archive transfer helpers for coordinator/EXE synchronization."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import stat
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, UploadFile

MAX_UPLOAD_BYTES = 10 * 1024 * 1024 * 1024
MAX_MEMBER_BYTES = 10 * 1024 * 1024 * 1024
MAX_EXTRACTED_BYTES = 20 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000
MAX_COMPRESSION_RATIO = 200
COMPRESSION_RATIO_ALLOWANCE = 10 * 1024 * 1024
MAX_PORTABLE_RESULT_JSON_BYTES = 4 * 1024 * 1024
MAX_STRUCTURED_JSON_BYTES = 16 * 1024 * 1024
_BOUNDED_JSON_FILENAMES = {"metadata.json", "analysis.json"}
PORTABLE_RESULT_FIELDS = frozenset(
    {
        "metadata",
        "image_descriptions",
        "image_download_diagnostics",
        "analysis",
        "warnings",
        "warning",
        "content_subtype",
        "subtitle_source",
        "transcript_segments",
    }
)

MEDIA_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".avi",
    ".webm",
    ".mov",
    ".flv",
    ".wmv",
    ".mp3",
    ".wav",
    ".aac",
    ".flac",
    ".ogg",
    ".m4a",
    ".wma",
    ".opus",
}
_SKIP_SUFFIXES = {".part", ".tmp", ".zip"}
_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?$", re.I)
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
_SYNC_STAGING_ROOTS = (
    "_remote_sync",
    "_sync_downloads",
    "_remote_sync_client",
)
_SYNC_BACKUP_PATTERN = re.compile(
    r"^\.(?P<target>[^/\\]+)\.sync-backup-[0-9a-f]{32}$",
    re.IGNORECASE,
)


def sweep_stale_sync_storage(
    data_root: Path,
    *,
    max_age_seconds: float = 24 * 60 * 60,
) -> dict[str, int]:
    """Recover interrupted archive swaps and remove bounded stale sync staging."""
    root = data_root.resolve()
    now = time.time()
    removed = 0
    restored = 0

    for root_name in _SYNC_STAGING_ROOTS:
        staging_root = root / root_name
        if not staging_root.is_dir() or staging_root.is_symlink():
            continue
        for child in list(staging_root.iterdir()):
            try:
                resolved = child.resolve()
                resolved.relative_to(staging_root.resolve())
                if child.is_symlink():
                    continue
                age = max(0.0, now - child.stat().st_mtime)
                if age < max_age_seconds:
                    continue
                if child.is_dir():
                    shutil.rmtree(resolved)
                else:
                    resolved.unlink()
                removed += 1
            except (OSError, ValueError):
                continue

    if root.is_dir():
        for backup in list(root.iterdir()):
            match = _SYNC_BACKUP_PATTERN.fullmatch(backup.name)
            if not match or not backup.is_dir() or backup.is_symlink():
                continue
            target = root / match.group("target")
            try:
                backup.resolve().relative_to(root)
                target.resolve().relative_to(root)
                if not target.exists():
                    os.replace(backup, target)
                    restored += 1
                    continue
                age = max(0.0, now - backup.stat().st_mtime)
                if age >= max_age_seconds:
                    shutil.rmtree(backup)
                    removed += 1
            except (OSError, ValueError):
                continue

    return {"removed": removed, "restored": restored}


def _archive_image_index(
    archive_dir: Path,
) -> tuple[dict[str, Path], dict[str, Path | None]]:
    images_dir = archive_dir / "images"
    by_relative: dict[str, Path] = {}
    by_basename: dict[str, Path | None] = {}
    if not images_dir.is_dir():
        return by_relative, by_basename

    for image in sorted(images_dir.rglob("*")):
        if not image.is_file() or image.suffix.lower() not in _IMAGE_EXTENSIONS:
            continue
        relative = image.relative_to(images_dir).as_posix().casefold()
        by_relative[relative] = image
        basename = image.name.casefold()
        if basename in by_basename:
            by_basename[basename] = None
        else:
            by_basename[basename] = image
    return by_relative, by_basename


def _match_archive_image(
    raw_path: Any,
    by_relative: dict[str, Path],
    by_basename: dict[str, Path | None],
) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    normalized = raw_path.strip().replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    image_positions = [
        index
        for index, part in enumerate(parts)
        if part.casefold() == "images"
    ]
    if image_positions:
        relative_parts = parts[image_positions[-1] + 1 :]
        if (
            relative_parts
            and not any(part in {"", ".", ".."} for part in relative_parts)
        ):
            match = by_relative.get(
                PurePosixPath(*relative_parts).as_posix().casefold()
            )
            if match is not None:
                return match
    basename = PurePosixPath(normalized).name.casefold()
    return by_basename.get(basename)


def _render_archive_image_path(
    image: Path,
    archive_dir: Path,
    *,
    relative: bool,
) -> str:
    if relative:
        return image.relative_to(archive_dir).as_posix()
    return str(image.resolve())


def _rewrite_metadata_image_paths(
    payload: dict[str, Any],
    archive_dir: Path,
    by_relative: dict[str, Path],
    by_basename: dict[str, Path | None],
    *,
    relative: bool,
) -> None:
    extra = payload.get("extra")
    if not isinstance(extra, dict):
        return

    downloaded = extra.get("downloaded_image_paths")
    if isinstance(downloaded, list):
        rewritten: list[str] = []
        for raw_path in downloaded:
            image = _match_archive_image(raw_path, by_relative, by_basename)
            if image is not None:
                rewritten.append(
                    _render_archive_image_path(
                        image,
                        archive_dir,
                        relative=relative,
                    )
                )
        extra["downloaded_image_paths"] = rewritten

    thumbnail_source = extra.get("thumbnail_source")
    image = _match_archive_image(thumbnail_source, by_relative, by_basename)
    if image is not None:
        extra["thumbnail_source"] = _render_archive_image_path(
            image,
            archive_dir,
            relative=relative,
        )


def _rewrite_nested_image_descriptions(
    value: Any,
    archive_dir: Path,
    by_relative: dict[str, Path],
    by_basename: dict[str, Path | None],
    *,
    relative: bool,
) -> None:
    if isinstance(value, list):
        for item in value:
            _rewrite_nested_image_descriptions(
                item,
                archive_dir,
                by_relative,
                by_basename,
                relative=relative,
            )
        return
    if not isinstance(value, dict):
        return

    descriptions = value.get("image_descriptions")
    if isinstance(descriptions, list):
        for description in descriptions:
            if not isinstance(description, dict):
                continue
            image = _match_archive_image(
                description.get("image_path"),
                by_relative,
                by_basename,
            )
            if image is not None:
                description["image_path"] = _render_archive_image_path(
                    image,
                    archive_dir,
                    relative=relative,
                )

    for child in value.values():
        _rewrite_nested_image_descriptions(
            child,
            archive_dir,
            by_relative,
            by_basename,
            relative=relative,
        )


def rewrite_result_image_paths(
    result: dict[str, Any],
    archive_dir: Path,
    *,
    relative: bool = False,
) -> dict[str, Any]:
    """Rewrite portable image references against one published archive."""
    rewritten = copy.deepcopy(result)
    by_relative, by_basename = _archive_image_index(archive_dir)
    if not by_relative:
        return rewritten

    metadata = rewritten.get("metadata")
    if isinstance(metadata, dict):
        _rewrite_metadata_image_paths(
            metadata,
            archive_dir,
            by_relative,
            by_basename,
            relative=relative,
        )
    _rewrite_nested_image_descriptions(
        rewritten,
        archive_dir,
        by_relative,
        by_basename,
        relative=relative,
    )
    return rewritten


def sanitize_portable_result(result: Any) -> dict[str, Any]:
    """Keep result metadata needed by readers while dropping local filesystem state."""
    if not isinstance(result, dict):
        return {}
    return {
        key: copy.deepcopy(result[key])
        for key in PORTABLE_RESULT_FIELDS
        if key in result
    }


def rewrite_archive_image_paths(archive_dir: Path) -> None:
    """Localize image references embedded in portable archive JSON files."""
    by_relative, by_basename = _archive_image_index(archive_dir)
    if not by_relative:
        return

    for filename in ("metadata.json", "analysis.json"):
        path = archive_dir / filename
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > MAX_STRUCTURED_JSON_BYTES:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        if filename == "metadata.json":
            _rewrite_metadata_image_paths(
                payload,
                archive_dir,
                by_relative,
                by_basename,
                relative=False,
            )
        _rewrite_nested_image_descriptions(
            payload,
            archive_dir,
            by_relative,
            by_basename,
            relative=False,
        )
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


async def stream_upload_to_path(
    upload: UploadFile,
    destination: Path,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> tuple[int, str]:
    """Stream an upload to disk while enforcing size and calculating SHA-256."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    digest = hashlib.sha256()
    try:
        with destination.open("wb") as output:
            while True:
                chunk = await upload.read(8 * 1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail="Archive upload exceeds the 10 GB limit",
                    )
                digest.update(chunk)
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return written, digest.hexdigest()


def _safe_member_path(raw_name: str) -> PurePosixPath:
    normalized = raw_name.replace("\\", "/")
    if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError(f"unsafe archive member path: {raw_name!r}")
    if "\x00" in normalized or any(ord(char) < 32 for char in normalized):
        raise ValueError(f"unsafe archive member path: {raw_name!r}")

    path = PurePosixPath(normalized)
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe archive member path: {raw_name!r}")
    for part in path.parts:
        if ":" in part or part.endswith((" ", ".")) or _WINDOWS_RESERVED.match(part):
            raise ValueError(f"unsafe archive member path: {raw_name!r}")
    return path


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_IFMT(unix_mode) == stat.S_IFLNK


def safe_extract_zip(zip_path: Path, extraction_dir: Path) -> Path:
    """Validate and extract an EXE-produced archive.

    The archive may contain files at its root or one top-level directory.
    ``metadata.json`` is required so the server can expose the result through
    the existing archive APIs.
    """
    extraction_dir.mkdir(parents=True, exist_ok=False)
    extracted_total = 0
    seen: set[str] = set()

    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_ARCHIVE_MEMBERS:
                raise ValueError("archive has an invalid number of members")

            validated: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
            for info in infos:
                member_path = _safe_member_path(info.filename)
                collision_key = member_path.as_posix().casefold()
                if collision_key in seen:
                    raise ValueError(f"duplicate archive member: {info.filename!r}")
                seen.add(collision_key)

                if info.flag_bits & 0x1:
                    raise ValueError("encrypted ZIP members are unsupported")
                if _is_symlink(info):
                    raise ValueError(f"symbolic links are unsupported: {info.filename!r}")
                if info.file_size < 0 or info.file_size > MAX_MEMBER_BYTES:
                    raise ValueError(f"archive member is too large: {info.filename!r}")
                if (
                    member_path.name.casefold() in _BOUNDED_JSON_FILENAMES
                    and info.file_size > MAX_STRUCTURED_JSON_BYTES
                ):
                    raise ValueError(
                        f"structured JSON member is too large: {info.filename!r}"
                    )
                extracted_total += info.file_size
                if extracted_total > MAX_EXTRACTED_BYTES:
                    raise ValueError("archive expands beyond the 20 GB limit")
                if (
                    not info.is_dir()
                    and info.file_size > COMPRESSION_RATIO_ALLOWANCE
                    and info.file_size
                    > info.compress_size * MAX_COMPRESSION_RATIO + COMPRESSION_RATIO_ALLOWANCE
                ):
                    raise ValueError(f"suspicious ZIP compression ratio: {info.filename!r}")
                validated.append((info, member_path))

            root = extraction_dir.resolve()
            actual_total = 0
            for info, member_path in validated:
                target = (extraction_dir / Path(*member_path.parts)).resolve()
                target.relative_to(root)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                member_written = 0
                with archive.open(info, "r") as source, target.open("xb") as output:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        member_written += len(chunk)
                        actual_total += len(chunk)
                        if member_written > info.file_size or actual_total > MAX_EXTRACTED_BYTES:
                            raise ValueError("archive expanded beyond its declared size")
                        output.write(chunk)
                if member_written != info.file_size:
                    raise ValueError(f"archive member size mismatch: {info.filename!r}")

        if (extraction_dir / "metadata.json").is_file():
            return extraction_dir
        children = [child for child in extraction_dir.iterdir()]
        if (
            len(children) == 1
            and children[0].is_dir()
            and (children[0] / "metadata.json").is_file()
        ):
            return children[0]
        raise ValueError("archive must contain metadata.json at its root")
    except Exception:
        shutil.rmtree(extraction_dir, ignore_errors=True)
        raise


def publish_archive(extracted_root: Path, destination: Path, data_root: Path) -> None:
    """Atomically replace a task's server-side archive directory."""
    root = data_root.resolve()
    target = destination.resolve()
    target.relative_to(root)
    if target == root:
        raise ValueError("archive destination cannot be data_root")

    backup = target.parent / f".{target.name}.sync-backup-{uuid4().hex}"
    moved_existing = False
    try:
        if target.exists():
            os.replace(target, backup)
            moved_existing = True
        os.replace(extracted_root, target)
    except Exception:
        if target.exists() and not moved_existing:
            shutil.rmtree(target, ignore_errors=True)
        if moved_existing and backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    else:
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)


def archive_file_manifest(archive_dir: Path) -> dict[str, str]:
    """Return the existing archive-result ``files`` mapping with safe relative keys."""
    files: dict[str, str] = {}
    for path in sorted(archive_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(archive_dir).as_posix()
        files[relative] = str(path)
    return files


def build_archive_zip(
    archive_dir: Path,
    destination: Path,
    *,
    include_media: bool = False,
) -> dict[str, Any]:
    """Create a portable result ZIP and embed a checksum manifest."""
    archive_root = archive_dir.resolve()
    candidates: list[tuple[Path, str]] = []
    for path in sorted(archive_root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"archive contains a symbolic link: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(archive_root).as_posix()
        safe_relative = _safe_member_path(relative).as_posix()
        if safe_relative == "_sync_manifest.json":
            continue
        if path.suffix.lower() in _SKIP_SUFFIXES:
            continue
        if not include_media and path.suffix.lower() in MEDIA_EXTENSIONS:
            continue
        candidates.append((path, safe_relative))
    if len(candidates) + 1 > MAX_ARCHIVE_MEMBERS:
        raise ValueError("archive has too many files")

    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_files: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(
            destination,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            allowZip64=True,
        ) as archive:
            for source_path, relative in candidates:
                digest = hashlib.sha256()
                size = 0
                with (
                    source_path.open("rb") as source,
                    archive.open(relative, "w", force_zip64=True) as output,
                ):
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_MEMBER_BYTES:
                            raise ValueError(f"archive member is too large: {relative}")
                        digest.update(chunk)
                        output.write(chunk)
                manifest_files.append(
                    {
                        "path": relative,
                        "size": size,
                        "sha256": digest.hexdigest(),
                    }
                )

            manifest = {
                "version": 1,
                "include_media": include_media,
                "files": manifest_files,
            }
            archive.writestr(
                "_sync_manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    return {
        "include_media": include_media,
        "file_count": len(manifest_files),
        "files": manifest_files,
    }
