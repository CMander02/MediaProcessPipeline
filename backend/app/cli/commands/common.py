"""Shared helpers for grouped CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, TypeVar
from uuid import UUID

from app.cli.client import MppClient, MppClientError
from app.cli.output import emit_error, print_debug_exception

T = TypeVar("T")


def client() -> MppClient:
    return MppClient()


def api_call(operation: Callable[[], T]) -> T:
    try:
        return operation()
    except MppClientError as exc:
        print_debug_exception(exc)
        emit_error(
            exc.code,
            exc.message,
            detail=exc.detail,
            retryable=exc.retryable,
            exit_code=exc.exit_code,
        )
    except (OSError, ValueError) as exc:
        print_debug_exception(exc)
        emit_error("local_error", str(exc), exit_code=2)


def _valid_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except ValueError:
        return False


def all_tasks(api: MppClient, page_size: int = 500, max_items: int = 10000) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    offset = 0
    while len(result) < max_items:
        page = api.list_tasks(limit=min(page_size, max_items - len(result)), offset=offset)
        result.extend(page)
        if len(page) < page_size:
            break
        offset += len(page)
    return result


def resolve_task_ref(ref: str, api: MppClient) -> str:
    normalized = ref.strip()
    if not normalized:
        emit_error("invalid_task_ref", "Task reference is empty.", exit_code=2)

    aliases: dict[str, list[str] | None] = {
        "@last": None,
        "@fail": ["failed"],
        "@run": ["processing"],
        "@queued": ["queued"],
        "@paused": ["paused"],
        "@completed": ["completed"],
        "@active": ["processing", "queued", "paused", "pending"],
    }
    if normalized.startswith("@"):
        if normalized not in aliases:
            emit_error(
                "invalid_task_ref",
                f"Unknown task reference {normalized}. Supported: {', '.join(aliases)}",
                exit_code=2,
            )
        statuses = aliases[normalized]
        tasks = api.list_tasks(limit=1, statuses=statuses) if statuses else api.list_tasks(limit=1)
        if not tasks:
            emit_error("task_not_found", f"No task matches {normalized}.", exit_code=4)
        return str(tasks[0]["id"])

    if _valid_uuid(normalized):
        api.get_task(normalized)
        return normalized

    matches = [task for task in all_tasks(api) if str(task.get("id", "")).startswith(normalized)]
    if not matches:
        emit_error("task_not_found", f"No task ID starts with {normalized!r}.", exit_code=4)
    if len(matches) > 1:
        emit_error(
            "ambiguous_task_ref",
            f"Task prefix {normalized!r} matches {len(matches)} tasks.",
            detail=[
                {"id": item.get("id"), "status": item.get("status"), "source": item.get("source")}
                for item in matches[:20]
            ],
            exit_code=4,
        )
    return str(matches[0]["id"])


def resolve_many_task_refs(refs: list[str], api: MppClient) -> list[str]:
    resolved: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        task_id = resolve_task_ref(ref, api)
        if task_id not in seen:
            seen.add(task_id)
            resolved.append(task_id)
    return resolved


def resolve_archive_ref(ref: str, api: MppClient) -> dict[str, Any]:
    archives = api.list_archives(lite=True)
    if not archives:
        emit_error("archive_not_found", "No archives are available.", exit_code=4)
    normalized = ref.strip()
    if normalized == "@last":
        return api.get_archive(str(archives[0]["path"]))

    exact: list[dict[str, Any]] = []
    prefix: list[dict[str, Any]] = []
    for archive in archives:
        path = str(archive.get("path", ""))
        archive_id = str(archive.get("archive_id", ""))
        task_id = str(archive.get("task_id", ""))
        title = str(archive.get("title", ""))
        if normalized in {path, archive_id, task_id, title}:
            exact.append(archive)
        elif (archive_id and archive_id.startswith(normalized)) or (
            task_id and task_id.startswith(normalized)
        ):
            prefix.append(archive)
    matches = exact or prefix
    if not matches:
        candidate = Path(normalized).expanduser()
        if candidate.is_absolute():
            return api.get_archive(str(candidate))
        emit_error("archive_not_found", f"No archive matches {normalized!r}.", exit_code=4)
    if len(matches) > 1:
        emit_error(
            "ambiguous_archive_ref",
            f"Archive reference {normalized!r} matches {len(matches)} archives.",
            detail=[
                {
                    "path": item.get("path"),
                    "title": item.get("title"),
                    "task_id": item.get("task_id"),
                }
                for item in matches[:20]
            ],
            exit_code=4,
        )
    return api.get_archive(str(matches[0]["path"]))


ARCHIVE_FILES = {
    "summary": ["summary.md"],
    "transcript": ["transcript_polished.srt", "transcript.srt", "transcript_polished.md"],
    "analysis": ["analysis.json"],
    "metadata": ["metadata.json"],
    "mindmap": ["mindmap.md", "mindmap.json"],
}


def archive_file_path(archive: dict[str, Any], selector: str) -> str:
    root = Path(str(archive.get("path", "")))
    names = ARCHIVE_FILES.get(selector, [selector])
    for name in names:
        candidate = root / name
        result = str(candidate)
        return result
    emit_error(
        "archive_file_not_found", f"No file selector is defined for {selector}.", exit_code=4
    )


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)
