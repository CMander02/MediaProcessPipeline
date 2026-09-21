"""Shared source expansion, upload, option, and batch submission helpers."""

from __future__ import annotations

import glob
import re
from pathlib import Path
from typing import Any

from app.cli.client import MppClient, MppClientError
from app.cli.context import get_cli_context
from app.cli.output import emit_error, parse_assignments

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
}


def _is_url(value: str) -> bool:
    return value.lower().startswith(("http://", "https://"))


def expand_sources(
    values: list[str],
    *,
    from_file: Path | None = None,
    recursive: bool = False,
) -> list[str]:
    raw = list(values)
    if from_file:
        try:
            raw.extend(
                line.strip()
                for line in from_file.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            )
        except OSError as exc:
            emit_error("source_file_error", str(exc), exit_code=2)
    expanded: list[str] = []
    for source in raw:
        source = source.strip()
        if not source:
            continue
        path = Path(source).expanduser()
        if path.is_dir():
            iterator = path.rglob("*") if recursive else path.glob("*")
            expanded.extend(
                str(item.resolve())
                for item in iterator
                if item.is_file() and item.suffix.lower() in MEDIA_EXTENSIONS
            )
            continue
        if glob.has_magic(source):
            matches = [Path(item) for item in glob.glob(source, recursive=recursive)]
            for item in matches:
                if item.is_dir():
                    iterator = item.rglob("*") if recursive else item.glob("*")
                    expanded.extend(
                        str(child.resolve())
                        for child in iterator
                        if child.is_file() and child.suffix.lower() in MEDIA_EXTENSIONS
                    )
                elif item.is_file() and item.suffix.lower() in MEDIA_EXTENSIONS:
                    expanded.append(str(item.resolve()))
            continue
        if (
            not _is_url(source)
            and not path.is_file()
            and (path.is_absolute() or path.suffix.lower() in MEDIA_EXTENSIONS)
        ):
            emit_error(
                "source_not_found", f"Local media source does not exist: {source}", exit_code=2
            )
        expanded.append(str(path.resolve()) if path.is_file() else source)
    unique: list[str] = []
    seen: set[str] = set()
    for source in expanded:
        key = source.casefold() if not _is_url(source) else source
        if key not in seen:
            seen.add(key)
            unique.append(source)
    if not unique:
        emit_error("no_sources", "No valid sources were found.", exit_code=2)
    if len(unique) > 100:
        emit_error(
            "too_many_sources",
            f"A batch accepts at most 100 sources; received {len(unique)}.",
            exit_code=2,
        )
    return unique


def task_options(
    *,
    force_asr: bool = False,
    prefer_subtitles: bool = False,
    skip_separation: bool = False,
    speakers: int | None = None,
    hotwords: list[str] | None = None,
    legacy_hotwords: str | None = None,
    api_flow: bool = False,
    assignments: list[str] | None = None,
) -> dict[str, Any]:
    options = parse_assignments(assignments or [])
    if force_asr and prefer_subtitles:
        emit_error(
            "invalid_options",
            "Use one of --force-asr or --prefer-subtitles.",
            exit_code=2,
        )
    if force_asr:
        options["force_asr"] = True
    if prefer_subtitles:
        options["force_asr"] = False
    if skip_separation:
        options["skip_separation"] = True
    if speakers is not None:
        if speakers < 1:
            emit_error("invalid_speakers", "--speakers must be at least 1.", exit_code=2)
        options["num_speakers"] = speakers
    words = [word.strip() for word in (hotwords or []) if word.strip()]
    if legacy_hotwords:
        words.extend(word.strip() for word in re.split(r"[,，、]", legacy_hotwords) if word.strip())
    if words:
        options["hotwords"] = list(dict.fromkeys(words))
    if api_flow:
        options.update(
            {
                "api_flow": True,
                "skip_separation": True,
                "asr_provider": "siliconflow",
                "asr_chunk_strategy": "ffmpeg",
                "disable_diarization": True,
                "disable_voiceprint": True,
            }
        )
    return options


def expand_bilibili_collections(
    api: MppClient, sources: list[str], selection: str | None
) -> list[str]:
    if not selection:
        return sources
    result: list[str] = []
    wanted = (
        {item.strip() for item in selection.split(",") if item.strip()}
        if selection != "all"
        else None
    )
    for source in sources:
        if not _is_url(source) or not any(
            host in source.lower() for host in ("bilibili.com", "b23.tv")
        ):
            result.append(source)
            continue
        collection = api.bilibili_collection(source)
        items = collection.get("items") or []
        if not collection.get("is_collection") or len(items) <= 1:
            result.append(source)
            continue
        selected = [item for item in items if wanted is None or str(item.get("id")) in wanted]
        if wanted is not None:
            missing = wanted - {str(item.get("id")) for item in selected}
            if missing:
                emit_error(
                    "collection_item_not_found",
                    f"Unknown collection item IDs: {', '.join(sorted(missing))}",
                    detail={"available": [item.get("id") for item in items]},
                    exit_code=2,
                )
        result.extend(str(item.get("url")) for item in selected if item.get("url"))
    if not result:
        emit_error(
            "empty_collection_selection", "The collection selection contains no items.", exit_code=2
        )
    return result


def prepare_sources_for_server(
    api: MppClient, sources: list[str], upload_mode: str = "auto"
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    if upload_mode not in {"auto", "always", "never"}:
        emit_error("invalid_upload_mode", f"Unknown upload mode: {upload_mode}", exit_code=2)
    capabilities = api.capabilities()
    prepared: list[str] = []
    staged: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for source in sources:
        path = Path(source)
        if not path.is_file():
            prepared.append(source)
            continue
        should_upload = upload_mode == "always" or (
            upload_mode == "auto"
            and (not get_cli_context().is_local or not capabilities.get("local_path_submission"))
        )
        if should_upload:
            try:
                item = api.stage_file(path)
            except (MppClientError, OSError) as exc:
                errors.append(
                    {
                        "source": source,
                        "code": getattr(exc, "code", "upload_failed"),
                        "message": str(exc),
                    }
                )
                continue
            staged.append(item)
            prepared.append(str(item["path"]))
        else:
            prepared.append(str(path.resolve()))
    return prepared, staged, errors


def submit_sources(
    api: MppClient,
    sources: list[str],
    *,
    options: dict[str, Any],
    webhook_url: str | None = None,
    upload_mode: str = "auto",
    collection: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    expanded = expand_bilibili_collections(api, sources, collection)
    if len(expanded) > 100:
        emit_error(
            "too_many_sources",
            f"A collection-expanded batch accepts at most 100 sources; received {len(expanded)}.",
            exit_code=2,
        )
    prepared, staged, errors = prepare_sources_for_server(api, expanded, upload_mode)
    if not prepared:
        emit_error(
            "staging_failed",
            "Every local source failed to upload.",
            detail=errors,
            exit_code=1,
        )
    try:
        return api.create_tasks_batch(prepared, options, webhook_url), errors
    except Exception:
        for item in staged:
            staging_id = item.get("staging_id")
            if staging_id:
                try:
                    api.delete_staged(str(staging_id))
                except Exception:
                    pass
        raise


def batch_text(tasks: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{task.get('id')}\t{task.get('status')}\t{task.get('source')}" for task in tasks
    )
