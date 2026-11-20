"""Shared source expansion, upload, option, and batch submission helpers."""

from __future__ import annotations

import glob
import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.cli.client import MppClient, MppClientError
from app.cli.context import configure_cli_context, get_cli_context, route_to_configured_server
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


def _is_bilibili_source(source: str) -> bool:
    host = urlsplit(source).hostname or "" if _is_url(source) else ""
    return host in {"bilibili.com", "b23.tv"} or host.endswith(".bilibili.com")


@dataclass(frozen=True)
class ImportOptions:
    folder: str | None = None
    move_existing: bool = False
    collection_folder: bool = False
    force_duplicate: bool = False
    episodes: str | None = None


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
        if _is_url(source):
            expanded.append(source)
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
        if not path.is_file() and (path.is_absolute() or path.suffix.lower() in MEDIA_EXTENSIONS):
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
    workflow: dict[str, bool | None] | None = None,
    only_transcribe: bool = False,
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
    overrides = {key: value for key, value in (workflow or {}).items() if value is not None}
    if only_transcribe:
        overrides.update(
            dict.fromkeys(("polish", "analyze", "summary", "mindmap", "detail"), False)
        )
    if overrides:
        existing = options.get("workflow", {})
        if not isinstance(existing, dict):
            emit_error("invalid_workflow", "The workflow option must be an object.", exit_code=2)
        options["workflow"] = {**existing, **overrides}
    return options


def expand_bilibili_collections(
    api: MppClient, sources: list[str], selection: str | None, *, positional: bool = False,
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
        if not _is_bilibili_source(source):
            result.append(source)
            continue
        collection = api.bilibili_collection(source)
        items = collection.get("items") or []
        if not positional and (not collection.get("is_collection") or len(items) <= 1):
            result.append(source)
            continue
        if wanted is None and not items:
            result.append(source)
            continue
        indices = set(range(len(items))) if wanted is None else set()
        ids = {str(item["id"]): index for index, item in enumerate(items)}
        for token in sorted(wanted or ()):
            if token in ids:
                indices.add(ids[token])
                continue
            bounds = re.fullmatch(r"(\d+)(?:-(\d+))?", token) if positional else None
            if bounds:
                start, end = int(bounds[1]), int(bounds[2] or bounds[1])
                if 1 <= start <= end <= len(items):
                    indices.update(range(start - 1, end))
                    continue
            if token:
                emit_error(
                    "collection_item_not_found",
                    f"Unknown collection item or range: {token}",
                    detail={"available": list(ids)},
                    exit_code=2,
                )
        result.extend(
            str(item["url"])
            for index, item in enumerate(items)
            if index in indices and item.get("url")
        )
    if not result:
        emit_error(
            "empty_collection_selection", "The collection selection contains no items.", exit_code=2
        )
    return result


def submit_to_servers(
    connect: Callable[[], MppClient],
    sources: list[str],
    *,
    options: dict[str, Any],
    webhook_url: str | None,
    upload_mode: str,
    collection: str | None,
    raw_target: str | None,
    remote: bool | None,
    import_options: ImportOptions = ImportOptions(),
) -> tuple[list[tuple[dict[str, Any], MppClient]], list[dict[str, Any]]]:
    from app.core.settings import get_runtime_settings

    settings = get_runtime_settings()
    ctx = get_cli_context()
    effective_remote = remote if remote is not None else ctx.remote_preference
    target = (raw_target if raw_target is not None else settings.cli_default_worker_id).strip()
    stay_local = (
        not ctx.explicit_server
        and effective_remote is None
        and raw_target is None
        and settings.cli_use_remote_server
        and settings.cli_local_files_stay_local
        and upload_mode == "auto"
    )
    local_sources = set()
    if stay_local:
        local_sources = {
            source for source in sources if not _is_url(source) and Path(source).is_file()
        }
    local_only = bool(local_sources) and len(local_sources) == len(sources)
    if local_only:
        effective_remote, target, upload_mode = False, "local", "never"
    route_to_configured_server(remote=effective_remote, target=target, settings=settings)
    api = connect()
    if not local_sources or local_only:
        tasks, errors = submit_sources(
            api, sources, options=options, webhook_url=webhook_url,
            upload_mode=upload_mode, collection=collection, target_worker=target,
            import_options=import_options,
        )
        return [(task, api) for task in tasks], errors

    target_worker_id = resolve_worker_target(api, target)
    collection_folders = _collection_folders(sources, import_options)
    sources = _expand_selected(api, sources, collection, import_options)
    _check_expanded_batch_size(sources)
    local_indices = [index for index, source in enumerate(sources) if source in local_sources]
    remote_indices = [index for index, source in enumerate(sources) if source not in local_sources]
    remote_folder_id = _import_folder_id(api, import_options.folder) if remote_indices else None
    coordinator = get_cli_context()
    route_to_configured_server(remote=False, settings=settings)
    try:
        local_api = connect()
        local_folder_id = _import_folder_id(local_api, import_options.folder)
        local_tasks, local_errors = _submit_expanded_sources(
            local_api, [sources[index] for index in local_indices],
            options=options, webhook_url=webhook_url, upload_mode="never", target_worker_id="local",
            import_options=import_options, collection_folders=[], folder_id=local_folder_id,
        )
    finally:
        configure_cli_context(server_url=coordinator.server_url, api_token=coordinator.api_token)

    ordered = {local_indices[index]: (task, local_api) for index, task in local_tasks.items()}
    remote_errors = []
    if remote_indices:
        try:
            remote_tasks, remote_errors = _submit_expanded_sources(
                api, [sources[index] for index in remote_indices],
                options=options, webhook_url=webhook_url,
                upload_mode=upload_mode, target_worker_id=target_worker_id,
                import_options=import_options, collection_folders=collection_folders,
                folder_id=remote_folder_id,
            )
        except MppClientError as exc:
            exc.detail = {
                "remote_error": exc.detail,
                "local_tasks": [
                    {"id": task.get("id"), "source": task.get("source")}
                    for task in local_tasks.values()
                ],
            }
            raise
        ordered.update({remote_indices[index]: (task, api) for index, task in remote_tasks.items()})
    return [ordered[index] for index in sorted(ordered)], local_errors + remote_errors


def resolve_worker_target(api: MppClient, raw_target: str, *, required: bool = False) -> str:
    target = raw_target.strip()
    if not target or target == "local":
        return target
    try:
        nodes = api.execution_nodes().get("nodes") or []
    except MppClientError:
        if required:
            raise
        return target
    if any(node["node_id"] == target for node in nodes):
        return target
    folded = target.casefold()
    candidates = sorted({
        node["node_id"]
        for node in nodes
        if folded in {node["node_id"].casefold(), node["display_name"].casefold()}
    })
    if len(candidates) > 1:
        emit_error(
            "ambiguous_worker_target",
            f"Worker {target!r} matches multiple nodes. Use a node ID: {', '.join(candidates)}.",
            detail={"candidate_node_ids": candidates},
            exit_code=2,
        )
    if required and not candidates:
        emit_error("worker_not_found", f"No worker matches {target!r}.", exit_code=4)
    return candidates[0] if candidates else target


def prepare_sources_for_server(
    api: MppClient, sources: list[str], upload_mode: str = "auto", *, target_worker_id: str = ""
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    if upload_mode not in {"auto", "always", "never"}:
        emit_error("invalid_upload_mode", f"Unknown upload mode: {upload_mode}", exit_code=2)
    capabilities = api.capabilities() if upload_mode != "never" else {}
    prepared: list[str] = []
    staged: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for source in sources:
        path = Path(source)
        if _is_url(source) or not path.is_file():
            prepared.append(source)
            continue
        should_upload = upload_mode == "always" or (
            upload_mode == "auto"
            and (
                target_worker_id not in {"", "local"}
                or not get_cli_context().is_local
                or not capabilities.get("local_path_submission")
            )
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


def _check_expanded_batch_size(sources: list[str]) -> None:
    if len(sources) > 100:
        emit_error(
            "too_many_sources",
            f"A collection-expanded batch accepts at most 100 sources; received {len(sources)}.",
            exit_code=2,
        )


def submit_sources(
    api: MppClient,
    sources: list[str],
    *,
    options: dict[str, Any],
    webhook_url: str | None = None,
    upload_mode: str = "auto",
    collection: str | None = None,
    target_worker: str = "",
    import_options: ImportOptions = ImportOptions(),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    target_worker_id = resolve_worker_target(api, target_worker)
    expanded = _expand_selected(api, sources, collection, import_options)
    _check_expanded_batch_size(expanded)
    tasks, errors = _submit_expanded_sources(
        api,
        expanded,
        options=options,
        webhook_url=webhook_url,
        upload_mode=upload_mode,
        target_worker_id=target_worker_id,
        import_options=import_options,
        collection_folders=_collection_folders(sources, import_options),
        folder_id=_import_folder_id(api, import_options.folder),
    )
    return list(tasks.values()), errors


def _expand_selected(api, sources, collection, import_options):
    if collection is not None and import_options.episodes is not None:
        emit_error("invalid_options", "Choose one of --collection or --episodes.", exit_code=2)
    return expand_bilibili_collections(
        api,
        sources,
        import_options.episodes or collection,
        positional=import_options.episodes is not None,
    )


def _collection_folders(sources: list[str], options: ImportOptions) -> list[dict[str, Any]]:
    return [
        {"source": source, "create_folder": True}
        for source in sources if options.collection_folder and _is_bilibili_source(source)
    ]


def _import_folder_id(api: MppClient, folder: str | None) -> str | None:
    from app.cli.commands.library import resolve_folder_ref

    if folder:
        return resolve_folder_ref(folder, api.list_library_folders())["id"]
    return None


def _submit_expanded_sources(
    api: MppClient,
    sources: list[str],
    *,
    options: dict[str, Any],
    webhook_url: str | None,
    upload_mode: str,
    target_worker_id: str,
    import_options: ImportOptions,
    collection_folders: list[dict[str, Any]],
    folder_id: str | None,
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    routing = {"target_worker_id": target_worker_id} if target_worker_id else {}
    classification = {}
    if folder_id is not None:
        classification["folder_id"] = folder_id
    if import_options.move_existing:
        classification["move_existing"] = True
    if collection_folders:
        classification["collection_folders"] = collection_folders

    prepared, staged, errors = prepare_sources_for_server(
        api, sources, upload_mode, target_worker_id=target_worker_id,
    )
    if not prepared:
        return {}, errors
    owned: set[str] = set()
    try:
        if not import_options.force_duplicate and hasattr(api, "import_tasks"):
            webhook = {"webhook_url": webhook_url} if webhook_url else {}
            response = api.import_tasks(prepared, options, **webhook, **routing, **classification)
            items = response["items"]
        else:
            batch = api.create_tasks_batch(
                prepared, options, webhook_url, **routing, **classification
            )
            items = [{"outcome": "created", "task": task} for task in batch]

        original_indices = [index for index, source in enumerate(sources)
                            if source not in {error["source"] for error in errors}]
        tasks = {}
        for index, submitted_source, item in zip(original_indices, prepared, items, strict=True):
            task = item.get("task")
            outcome = item["outcome"]
            if task and (outcome == "created" or item.get("reason") == "queue_notification_failed"):
                owned.add(submitted_source)
            if outcome == "failed":
                errors.append({
                    "source": sources[index], "code": item.get("reason") or "import_failed",
                    "message": item.get("error") or "Import failed",
                    **({"task_id": task["id"]} if task else {}),
                })
                continue
            match = item.get("match") or {}
            entry = dict(task) if task else {
                "id": match.get("task_id") or match.get("archive_id"),
                "source": sources[index], "status": match.get("status"),
            }
            if outcome != "created":
                entry.update(
                    outcome=outcome, match=match, reason=item.get("reason"), source=sources[index]
                )
            if item.get("folder_error"):
                entry["folder_error"] = item["folder_error"]
            if item.get("folder_outcome") and classification:
                entry.update(folder_id=item.get("folder_id"), folder_outcome=item["folder_outcome"])
            tasks[index] = entry
        return tasks, errors
    finally:
        for item in staged:
            staging_id = item.get("staging_id")
            if staging_id and str(item["path"]) not in owned:
                with suppress(MppClientError, OSError):
                    api.delete_staged(str(staging_id))


def batch_text(tasks: list[dict[str, Any]]) -> str:
    return "\n".join(
        "\t".join(str(value) for value in (
            task.get("id") or "", task.get("outcome") or task.get("status"),
            task.get("source"), *([task["reason"]] if task.get("reason") else []),
            *([task["folder_error"]] if task.get("folder_error") else []),
        )) for task in tasks
    )
