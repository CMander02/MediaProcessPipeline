"""Read-only access to the structured backend log files."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from app.core.logging_setup import get_current_log_file
from app.core.paths import get_workspace_paths

router = APIRouter(prefix="/logs", tags=["logs"])

LOG_DIR: Path | None = None
MAX_READ_BYTES = 10 * 1024 * 1024

_HEADER_RE = re.compile(
    r"^(?P<time>\d{2}:\d{2}:\d{2}\.\d{3})\s+"
    r"(?P<offset>[+-]\d{4})\s+"
    r"(?P<level>[A-Z]+)\s+"
    r"\[t:(?P<task>\S+)\s+w:(?P<worker>\S+)\s*\]\s+"
    r"(?P<module>\S+)"
    r"(?:\s+\((?P<source>[^)]+)\))?\s{2}"
    r"(?P<message>.*)$"
)
_FILE_DATE_RE = re.compile(r"^mpp_(?P<date>\d{8})_\d{6}\.log(?:\.\d+)?$")
_MESSAGE_FIELD_RE = re.compile(r'\bmessage=(?P<value>"(?:\\.|[^"\\])*"|\S+)')
_EVENT_FIELD_RE = re.compile(r"\bevent=(?P<value>\S+)")


def _available_log_files() -> list[Path]:
    log_dir = LOG_DIR or get_workspace_paths().logs
    if not log_dir.is_dir():
        return []
    return sorted(
        (path for path in log_dir.glob("mpp_*.log*") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def _active_log_file(files: list[Path]) -> Path | None:
    current = get_current_log_file()
    if current is not None:
        current_resolved = current.resolve()
        owned = next((path for path in files if path.resolve() == current_resolved), None)
        if owned is not None:
            return owned
    return next((path for path in files if path.suffix == ".log"), None)


def _resolve_log_file(filename: str | None) -> tuple[Path, Path | None]:
    files = _available_log_files()
    active = _active_log_file(files)
    if filename is None:
        if active is None:
            raise HTTPException(404, "当前没有可读取的后端日志")
        return active, active

    matches = {path.name: path for path in files}
    selected = matches.get(filename)
    if selected is None:
        raise HTTPException(404, "日志文件不存在")
    return selected, active


def _file_date(filename: str) -> str:
    match = _FILE_DATE_RE.match(filename)
    if match is None:
        return datetime.now().astimezone().strftime("%Y-%m-%d")
    raw = match.group("date")
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def _display_message(raw_message: str) -> tuple[str, str]:
    event_match = _EVENT_FIELD_RE.search(raw_message)
    event = event_match.group("value") if event_match else "log"
    message_match = _MESSAGE_FIELD_RE.search(raw_message)
    if message_match is None:
        return event, raw_message

    value = message_match.group("value")
    if value.startswith('"'):
        try:
            return event, str(json.loads(value))
        except json.JSONDecodeError:
            return event, value.strip('"')
    return event, value


def parse_log_lines(lines: list[tuple[int, str]], filename: str) -> list[dict[str, object]]:
    """Parse formatter output and attach traceback continuation lines."""
    entries: list[dict[str, object]] = []
    date = _file_date(filename)

    for byte_offset, line in lines:
        text = line.rstrip("\r\n")
        match = _HEADER_RE.match(text)
        if match is None:
            if entries:
                entries[-1]["raw"] = f"{entries[-1]['raw']}\n{text}"
                entries[-1]["message"] = f"{entries[-1]['message']}\n{text.strip()}"
            elif text:
                entries.append(
                    {
                        "id": f"{filename}:{byte_offset}",
                        "timestamp": "",
                        "level": "RAW",
                        "module": "",
                        "task_id": "",
                        "worker": "",
                        "source": "",
                        "event": "log",
                        "message": text,
                        "raw": text,
                    }
                )
            continue

        groups = match.groupdict()
        event, display_message = _display_message(groups["message"])
        task_id = "" if groups["task"] == "--------" else groups["task"]
        worker = "" if groups["worker"] == "----" else groups["worker"]
        entries.append(
            {
                "id": f"{filename}:{byte_offset}",
                "timestamp": f"{date} {groups['time']} {groups['offset']}",
                "level": groups["level"],
                "module": groups["module"],
                "task_id": task_id,
                "worker": worker,
                "source": groups.get("source") or "",
                "event": event,
                "message": display_message,
                "raw": text,
            }
        )

    return entries


def _read_lines(
    path: Path,
    cursor: int | None,
    max_bytes: int,
) -> tuple[list[tuple[int, str]], int, bool, bool]:
    size = path.stat().st_size
    reset = cursor is not None and cursor > size
    if cursor is None:
        start = max(0, size - max_bytes)
        skip_partial_line = start > 0
    else:
        start = 0 if reset else max(0, cursor)
        skip_partial_line = False

    lines: list[tuple[int, str]] = []
    with path.open("rb") as handle:
        handle.seek(start)
        if skip_partial_line:
            handle.readline()
        while handle.tell() < size and handle.tell() - start < max_bytes:
            line_offset = handle.tell()
            raw_line = handle.readline()
            if not raw_line:
                break
            lines.append((line_offset, raw_line.decode("utf-8", errors="replace")))
        next_cursor = handle.tell()

    truncated = (cursor is None and start > 0) or next_cursor < size
    return lines, next_cursor, reset, truncated


@router.get("/files")
async def list_log_files():
    files = _available_log_files()
    active = _active_log_file(files)
    return {
        "active_file": active.name if active else None,
        "files": [
            {
                "name": path.name,
                "size": path.stat().st_size,
                "modified_at": (
                    datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()
                ),
                "active": path == active,
            }
            for path in files[:50]
        ],
    }


@router.get("")
async def read_log(
    file: str | None = Query(default=None),
    cursor: int | None = Query(default=None, ge=0),
    max_bytes: int = Query(default=MAX_READ_BYTES, ge=1024, le=MAX_READ_BYTES),
):
    selected, active = _resolve_log_file(file)
    lines, next_cursor, reset, truncated = _read_lines(selected, cursor, max_bytes)
    return {
        "file": selected.name,
        "active": selected == active,
        "size": selected.stat().st_size,
        "cursor": next_cursor,
        "reset": reset,
        "truncated": truncated,
        "entries": parse_log_lines(lines, selected.name),
    }
