"""Read archived text and task artifacts across daemon and offline modes."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import typer

from app.cli.client import MppClient, MppClientError
from app.cli.commands.common import (
    _valid_uuid,
    api_call,
    client,
    emit_client_error,
    match_archive_ref,
    resolve_archive_ref,
    resolve_task_ref,
)
from app.cli.context import get_cli_context
from app.cli.output import emit, emit_error
from app.cli.renderers import (
    fmt_ms_timestamp,
    render_chapter_table,
    render_fields,
    render_mindmap_tree,
)
from app.services.analysis.transcript_outputs import (
    _parse_srt,
    _split_speaker_prefix,
    _timestamp_bounds,
    _timestamp_to_seconds,
)

TRANSCRIPT_FILES = ("transcript_polished.srt", "transcript.srt")
_IMAGE_LINK = re.compile(
    r"!?\[[^\]\n]*\]\(<?(?:\./)?images/(\d+)\.[\w]+>?(?:\s+\"[^\"]*\")?\)"
    r"|(?<![\w/])(?:\./)?images/(\d+)\.[\w]+"
)


def require_local() -> None:
    ctx = get_cli_context()
    if not ctx.is_local:
        emit_error("daemon_unavailable", f"Cannot reach {ctx.server_url}.", exit_code=3)


def local_archive_index():
    from app.core.archive_sync import get_archive_sync_service
    from app.core.database import init_db

    init_db()
    index = get_archive_sync_service()
    if not index.index_status().get("last_reconciled_at"):
        index.reconcile()
    return index


def resolve_local_archive(ref: str) -> dict | None:
    from app.services.archiving.archive import get_archive_service

    index = local_archive_index()
    archives = index.list_all()
    match = match_archive_ref(ref, archives)
    path = Path(match["path"] if match else ref.strip()).expanduser()
    if not match and not path.is_dir():
        return None
    details = get_archive_service().get_archive(path.resolve())
    if not details:
        return match
    item = {**(match or {}), **details}
    try:
        from app.core.library import get_library_service

        return get_library_service().decorate(item)
    except ValueError:
        return item


class _LocalTasks:
    def list_tasks(self, *, limit: int, offset: int = 0, statuses=None) -> list[dict]:
        from app.core.database import get_task_store

        return [
            item.model_dump(mode="json")
            for item in get_task_store().list(limit=limit, offset=offset, statuses=statuses)
        ]

    def get_task(self, task_id: str) -> dict:
        from app.core.database import get_task_store

        task = get_task_store().get(UUID(task_id))
        if task is None:
            emit_error("task_not_found", f"No task matches {task_id}.", exit_code=4)
        return task.model_dump(mode="json")


def resolve_reader_task(ref: str, api: MppClient | None) -> dict:
    if api is None:
        from app.core.database import init_db

        require_local()
        init_db()
    tasks = api if api is not None else _LocalTasks()
    return api_call(lambda: tasks.get_task(resolve_task_ref(ref, tasks)))


@dataclass
class ArtifactReader:
    api: MppClient | None
    archive: dict
    task: dict | None = None

    @classmethod
    def for_task(cls, api: MppClient | None, task: dict) -> ArtifactReader:
        result = task.get("result") or {}
        archive = dict(result.get("archive") or {})
        if not archive.get("archive_id"):
            archive.pop("archive_id", None)
        archive.update(
            task_id=task.get("id"),
            path=(
                archive.get("output_dir") or result.get("output_dir") or archive.get("path")
                or task.get("output_dir")
            ),
        )
        return cls(api, archive, task)

    @property
    def task_id(self) -> str | None:
        value = self.archive.get("task_id")
        return str(value) if value else None

    @property
    def owner_id(self) -> str | None:
        if task_id := self.task_id:
            try:
                return str(UUID(task_id))
            except ValueError:
                pass
        return None

    @property
    def archive_id(self) -> str | None:
        value = self.archive.get("archive_id")
        return str(value) if value else None

    def sync_id(self) -> str | None:
        if "archive_id" not in self.archive and self.task is not None and self.api is not None:
            archives = self.api.list_archives(lite=True)
            item = next((item for item in archives if item.get("path") == self.root), None)
            self.archive["archive_id"] = item.get("archive_id") if item else None
        return self.archive_id

    @property
    def root(self) -> str:
        return str(self.archive.get("path") or "")

    def path(self, name: str) -> str:
        relative = PurePosixPath(name.replace("\\", "/"))
        windows = PureWindowsPath(self.root)
        if relative.is_absolute() or ".." in relative.parts or PureWindowsPath(name).drive:
            emit_error("invalid_artifact_path", f"Invalid artifact filename {name!r}.", exit_code=2)
        if windows.drive or "\\" in self.root:
            return str(windows.joinpath(*relative.parts))
        return str(PurePosixPath(self.root) / relative)

    def read(self, name: str, *, baseline: bool = False) -> str | None:
        if not self.root:
            return None
        path = self.path(name)
        if self.api is None:
            from app.core.artifacts import get_artifact_store

            try:
                return get_artifact_store().read(self.task_id, self.root, name)["content"]
            except FileNotFoundError:
                return None
        result = {}
        try:
            owner = {"task_id": task_id} if (task_id := self.owner_id) else {}
            result = self.api.fs_read(path, **owner)
            if result.get("success"):
                return str(result.get("content") or "")
        except MppClientError as exc:
            if exc.status_code not in {403, 404}:
                raise
            if exc.status_code == 403 and not self.sync_id():
                raise
        if archive_id := self.sync_id():
            try:
                raw = self.api.sync_file(archive_id, name)
                if baseline:
                    return raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                return raw.decode("utf-8-sig")
            except MppClientError as exc:
                if exc.status_code != 404:
                    raise
        if result.get("error") in {"Task not found", "File does not belong to this task"}:
            result = self.api.fs_read(path)
            if result.get("success"):
                return str(result.get("content") or "")
        return None

    def first(self, *names: str, baseline: bool = False) -> tuple[str, str] | None:
        for name in names:
            content = api_call(lambda: self.read(name, baseline=baseline))
            if content is not None:
                return name, content
        return None

    def required(
        self, *names: str, code: str = "archive_file_not_found", baseline: bool = False,
    ) -> tuple[str, str]:
        found = self.first(*names, baseline=baseline)
        if found is None:
            emit_error(code, f"No readable artifact matches {', '.join(names)}.", exit_code=4)
        return found

    def names(self, directory: str = "") -> list[str]:
        if not self.root:
            return []
        prefix = f"{directory}/" if directory else ""
        if self.api is None:
            from app.core.database import get_task_store

            local = Path(self.path(directory)) if directory else Path(self.root)
            names = {prefix + item.name for item in local.iterdir() if item.is_file()} if (
                local.is_dir() and local.resolve().is_relative_to(Path(self.root).resolve())
            ) else set()
            if self.task_id:
                names.update(
                    item["filename"] for item in get_task_store().list_artifacts(self.task_id)
                    if item["filename"].startswith(prefix)
                )
            return sorted(names)
        if api_call(self.api.capabilities).get("filesystem_browse"):
            path = self.path(directory) if directory else self.root
            try:
                listing = self.api.fs_list(path, "file")
                if listing.get("success", True):
                    return sorted(
                        prefix + item["name"] for item in listing.get("items", [])
                        if item.get("name") and not item.get("is_dir")
                    )
            except MppClientError as exc:
                if exc.status_code not in {403, 404}:
                    emit_client_error(exc)
        if archive_id := api_call(self.sync_id):
            try:
                manifest = self.api.sync_manifest(archive_id)
            except MppClientError as exc:
                if exc.status_code != 404:
                    emit_client_error(exc)
                return []
            return sorted(
                name for item in manifest.get("files") or []
                if (name := item.get("relative_path")) and name.startswith(prefix)
            )
        return []

    def json(self, name: str, *, required: bool = False) -> dict:
        found = self.required(name) if required else self.first(name)
        if found is None:
            return {}
        try:
            data = json.loads(found[1].removeprefix("\ufeff"))
        except ValueError as exc:
            emit_error("invalid_artifact", f"{name} contains invalid JSON.", detail=str(exc),
                       exit_code=4)
        if not isinstance(data, dict):
            emit_error("invalid_artifact", f"{name} must contain a JSON object.", exit_code=4)
        return data

    def metadata(self) -> dict:
        result = (self.task or {}).get("result") or {}
        source = (self.task or {}).get("source") or ""
        result_metadata = result.get("metadata") or {}
        archive_metadata = self.archive.get("metadata") or {}
        file_metadata = self.json("metadata.json")
        data = {**result_metadata, **archive_metadata, **file_metadata}
        data["extra"] = {
            **(result_metadata.get("extra") or {}),
            **(archive_metadata.get("extra") or {}),
            **(file_metadata.get("extra") or {}),
        }
        return {
            **data,
            "title": data.get("title") or self.archive.get("title"),
            "duration": data.get("duration_seconds") or data.get("duration")
            or self.archive.get("duration_seconds"),
            "folder_id": self.archive.get("folder_id") or data.get("folder_id"),
            "source_url": data.get("source_url") or (source if "://" in source else None),
        }

    def task_result(self) -> dict:
        if self.task is not None:
            return self.task.get("result") or {}
        if not self.task_id:
            return {}
        if self.api is None:
            from app.core.database import get_task_store

            try:
                task = get_task_store().get(UUID(self.task_id))
            except ValueError:
                return {}
            return task.result or {} if task else {}
        try:
            return self.api.get_task(self.task_id).get("result") or {}
        except MppClientError as exc:
            if exc.status_code not in {403, 404, 422}:
                raise
            return {}


def archive_reader(ref: str, api: MppClient, *, fallback_task: bool = False) -> ArtifactReader:
    online = api.ping()
    if online:
        archive = api_call(lambda: resolve_archive_ref(ref, api, missing_ok=True))
    else:
        require_local()
        archive = api_call(lambda: resolve_local_archive(ref))
    connected = api if online else None
    if archive is not None:
        return ArtifactReader(connected, archive)
    if fallback_task:
        return ArtifactReader.for_task(connected, resolve_reader_task(ref, connected))
    emit_error("archive_not_found", f"No archive matches {ref!r}.", exit_code=4)


def task_archive_reader(ref: str, api: MppClient | None) -> ArtifactReader:
    normalized = ref.strip()
    if normalized.startswith("@"):
        return ArtifactReader.for_task(api, resolve_reader_task(normalized, api))
    if api is not None and _valid_uuid(normalized):

        def find_task():
            try:
                return api.get_task(normalized)
            except MppClientError as exc:
                if exc.status_code != 404:
                    raise
                return None

        if task := api_call(find_task):
            return ArtifactReader.for_task(api, task)
    if api is None:
        require_local()
        archive = api_call(lambda: resolve_local_archive(normalized))
    else:
        archive = api_call(lambda: resolve_archive_ref(normalized, api, missing_ok=True))
    if archive is not None:
        reader = ArtifactReader(api, archive)
        if reader.task_id:
            return reader
        emit_error("archive_task_not_found", "This archive has no linked task.", exit_code=4)
    return ArtifactReader.for_task(api, resolve_reader_task(normalized, api))


def seconds(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    number = _timestamp_to_seconds(str(value))
    return number if number is not None and math.isfinite(number) and number >= 0 else None


def parse_time(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    number = seconds(value)
    if (
        number is None
        or not re.fullmatch(r"(?:\d+:){0,2}\d+(?:[.,]\d+)?", value)
        or any(float(part) >= 60 for part in parts[1:])
    ):
        emit_error("invalid_time", f"Invalid time {value!r}. Use SS, MM:SS or HH:MM:SS.",
                   exit_code=2)
    return number


def clock_time(value: float) -> str:
    return fmt_ms_timestamp(round(value * 1000)).split(".", 1)[0]


@dataclass
class Cue:
    index: int
    timestamp: str
    start: float
    end: float
    text: str

    @property
    def speaker(self) -> str | None:
        return _split_speaker_prefix(self.text)[0]


def parse_cues(content: str, *, strict: bool = False) -> list[Cue]:
    normalized = content.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\n[ \t]+(?=\n)", "\n", normalized).strip()
    cues = []
    for segment in _parse_srt(normalized):
        timestamp = segment["timestamp"]
        start, end = _timestamp_bounds(timestamp)
        first, last = seconds(start), seconds(end.split()[0]) if end else None
        if "-->" in timestamp and first is not None and last is not None and first <= last:
            cues.append(Cue(segment["index"], timestamp, first, last, segment["text"]))
    if strict and (not cues or len(cues) != len(re.split(r"\n\n+", normalized))):
        emit_error("invalid_srt", "The transcript contains an invalid SRT cue.", exit_code=2)
    return cues


def _chapter_time(node: dict, field: str) -> float | None:
    milliseconds = seconds(node.get(f"{field}_ms"))
    if milliseconds is not None:
        return milliseconds / 1000
    return seconds(node.get(field, node.get(f"{field}_time")))


def chapters(reader: ArtifactReader, metadata: dict) -> list[dict]:
    nodes = (
        reader.json("summary.json").get("timeline")
        or reader.json("source_context.json").get("timeline")
        or metadata.get("chapters")
    )
    if not nodes:
        root = reader.json("mindmap.json")
        nodes = root.get("children") or ([root] if _chapter_time(root, "start") is not None else [])
    rows: list[dict] = []

    def visit(items: list, parent_end: float | None, depth: int = 0) -> None:
        valid = [node for node in items if isinstance(node, dict)]
        for i, node in enumerate(valid):
            start = _chapter_time(node, "start")
            end = _chapter_time(node, "end")
            if end is None:
                end = next(
                    (bound for later in valid[i + 1:] if
                     (bound := _chapter_time(later, "start")) is not None
                     and (start is None or bound > start)),
                    parent_end,
                )
            rows.append({
                "index": len(rows) + 1, "depth": depth, "start": start, "end": end,
                "title": node.get("title"), "summary": node.get("summary"),
            })
            visit(node.get("children") or [], end, depth + 1)

    visit(nodes, seconds(metadata.get("duration")))
    return rows


def mindmap(
    reader: ArtifactReader, *, plain: bool = False, depth: int | None = None,
    timestamps: bool = True, raw_md: bool = False, code: str = "archive_file_not_found",
) -> str:
    tree = {} if raw_md else reader.json("mindmap.json")
    if tree:
        return render_mindmap_tree(tree, plain=plain, depth=depth, timestamps=timestamps)
    return reader.required("mindmap.md", code=code)[1]


def _language_names(available: list[str], language: str) -> tuple[str, ...]:
    if not re.fullmatch(r"[a-zA-Z0-9]+(?:[-_][a-zA-Z0-9]+)*", language):
        emit_error("invalid_language", f"Invalid language tag {language!r}.", exit_code=2)
    nominal = f"transcript.{language}.srt"
    names = [name for name in available if name.casefold() == nominal.casefold()]
    return tuple(dict.fromkeys([nominal, *names]))


def _overlap(cue: Cue, start: float, end: float) -> bool:
    return cue.end >= start and cue.start <= end


def _cue_range(spec: str) -> tuple[int, int]:
    parts = re.fullmatch(r"(\d+)(?:-(\d+))?", spec.strip())
    if parts:
        start, end = int(parts[1]), int(parts[2] or parts[1])
        if 0 < start <= end:
            return start, end
    emit_error("invalid_cue_range", f"Invalid cue range {spec!r}.", exit_code=2)


def _transcript(
    reader: ArtifactReader, *, raw: bool, language: str | None, bilingual: str | None,
    timestamps: bool, chapter: int | None, at: str | None, window: float,
    time_range: str | None, cue: str | None, speaker: str | None, grep: str | None,
    context: int,
) -> str:
    secondary: list[Cue] = []
    secondary_language = None
    if bilingual is not None:
        languages = [part.strip() for part in bilingual.split(",")]
        if len(languages) != 2 or any(not part for part in languages):
            emit_error("invalid_bilingual", "Pass two language tags as PRIMARY,SECONDARY.",
                       exit_code=2)
        language, secondary_language = languages
    if language:
        available = reader.names()
        names = _language_names(available, language)
        if secondary_language:
            secondary_names = _language_names(available, secondary_language)
            secondary = parse_cues(reader.required(*secondary_names)[1])
    else:
        names = ("transcript.srt",) if raw else TRANSCRIPT_FILES
    cues = parse_cues(reader.required(*names)[1])
    bounds: list[tuple[float, float]] = []
    if chapter is not None:
        rows = chapters(reader, reader.metadata())
        if chapter > len(rows) or rows[chapter - 1]["start"] is None:
            emit_error("chapter_not_found", f"No timed chapter has index {chapter}.", exit_code=4)
        row = rows[chapter - 1]
        bounds.append((row["start"], row["end"] if row["end"] is not None else math.inf))
    if at is not None:
        center = parse_time(at)
        bounds.append((center - window / 2, center + window / 2))
    if time_range is not None:
        parts = time_range.split("-")
        if len(parts) != 2:
            emit_error("invalid_time_range", f"Invalid time range {time_range!r}.", exit_code=2)
        first, last = (parse_time(part.strip()) for part in parts)
        if first > last:
            emit_error("invalid_time_range", "Start time must precede end time.", exit_code=2)
        bounds.append((first, last))
    first_cue, last_cue = _cue_range(cue) if cue is not None else (1, len(cues))
    selected = {
        i for i, item in enumerate(cues)
        if first_cue <= i + 1 <= last_cue
        and all(_overlap(item, start, end) for start, end in bounds)
    }
    matched = {
        i for i in selected
        if (speaker is None or (cues[i].speaker or "").casefold() == speaker.casefold())
        and (grep is None or grep.casefold() in cues[i].text.casefold())
    }
    if grep is not None and context:
        matched = selected.intersection(
            j for i in matched for j in range(max(0, i - context), min(len(cues), i + context + 1))
        )
    rendered: dict[int, list[str]] = {}
    remaining = sorted(secondary, key=lambda item: item.start)
    active: list[Cue] = []
    cursor = 0
    for i in sorted(matched, key=lambda position: cues[position].start):
        item = cues[i]
        prefix = f"[{clock_time(item.start)}] " if timestamps else ""
        rows = [prefix + item.text]
        active = [other for other in active if other.end > item.start]
        while cursor < len(remaining) and remaining[cursor].start < item.end:
            active.append(remaining[cursor])
            cursor += 1
        for other in active:
            if other.start < item.end and other.end > item.start:
                text = _split_speaker_prefix(other.text)[1]
                rows.extend(f"    {line}" for line in text.splitlines())
        rendered[i] = rows
    return "\n".join(line for i in sorted(rendered) for line in rendered[i])


def _notes(reader: ArtifactReader) -> str:
    source = reader.required("source.md")[1]
    descriptions = {}
    names = set(reader.names("descriptions"))
    names.update(
        f"descriptions/{int(match[1] or match[2]):02d}.md" for match in _IMAGE_LINK.finditer(source)
    )
    for name in sorted(names):
        if match := re.fullmatch(r"descriptions/(\d+)\.md", name):
            found = reader.first(name)
            if found:
                descriptions[int(match[1])] = found[1].strip()
    for item in api_call(reader.task_result).get("image_descriptions") or []:
        if item.get("index") is not None and item.get("text"):
            descriptions.setdefault(int(item["index"]), item["text"].strip())
    seen: set[int] = set()

    def block(index: int) -> str:
        return f"[配图 {index:02d} | 视觉与 OCR 描述]\n{descriptions[index]}"

    def expand(match: re.Match) -> str:
        index = int(match[1] or match[2])
        if index not in descriptions:
            return match[0]
        seen.add(index)
        return f"\n\n{block(index)}\n\n"

    expanded = _IMAGE_LINK.sub(expand, source)
    return "\n\n".join([expanded.strip(), *(block(i) for i in sorted(descriptions.keys() - seen))])


def _analysis(data: dict) -> str:
    def lines(value: Any) -> list[str]:
        if isinstance(value, dict):
            return [
                line for key, item in value.items()
                for line in [str(key), *(f"  {part}" for part in lines(item))]
            ]
        if isinstance(value, list):
            return [f"- {line}" for item in value for line in lines(item)]
        return str(value).splitlines() if value is not None else []

    return "\n\n".join(
        "\n".join([f"## {key.replace('_', ' ')}", *lines(value)])
        for key, value in data.items() if value is not None
    )


def _overview(reader: ArtifactReader) -> str:
    metadata = reader.metadata()
    extra = metadata["extra"]
    transcript = reader.first(*TRANSCRIPT_FILES)
    cues = parse_cues(transcript[1]) if transcript else []
    speakers = [item.speaker for item in cues] if cues else extra.get("speakers") or []
    names = {speaker.casefold() for speaker in speakers if isinstance(speaker, str)}
    quality = extra.get("speaker_quality") or {}
    fields = {
        key: metadata.get(key)
        for key in ("title", "platform", "uploader", "duration", "folder_id", "source_url")
    }
    fields["speaker_count"] = extra.get("speaker_count", len(names - {"unknown"}))
    fields["pending_unknown_cues"] = (
        sum((item.speaker or "").casefold() == "unknown" for item in cues)
        if cues else quality.get("unassigned_segments", 0)
    )
    sections = [render_fields(fields)]
    if extra.get("derived_content_stale"):
        reason = extra.get("derived_content_stale_reason")
        sections.append("[摘要待更新] Derived content is stale." + (f" {reason}" if reason else ""))
    if rows := chapters(reader, metadata):
        sections.append(render_chapter_table(rows))
    if content := reader.first("summary.md", "source.md"):
        sections.append(content[1])
    return "\n\n".join(sections)


def _source_link(reader: ArtifactReader, value: str) -> str:
    position = int(parse_time(value))
    metadata = reader.metadata()
    url = metadata.get("source_url")
    if not url:
        emit_error("source_url_missing", "This archive has no source URL.", exit_code=4)
    parts = api_call(lambda: urlsplit(url))
    host = parts.hostname or ""
    platform = str(metadata.get("platform") or "").casefold()
    youtube = host in {"youtu.be", "youtube.com"} or host.endswith(".youtube.com")
    bilibili = host in {"b23.tv", "bilibili.com"} or host.endswith(".bilibili.com")
    if platform == "youtube" or youtube:
        suffix = "s"
    elif platform == "bilibili" or bilibili:
        suffix = ""
    else:
        return urlunsplit(parts._replace(fragment=f"t={position}"))
    query = [item for item in parts.query.split("&") if item and item.split("=", 1)[0] != "t"]
    return urlunsplit(parts._replace(query="&".join([*query, f"t={position}{suffix}"])))


def read(
    ref: str = typer.Argument(...),
    view: str = typer.Option("overview", "--view", "-v"),
    depth: Optional[int] = typer.Option(None, "--depth", min=0),
    timestamps: bool = typer.Option(True, "--timestamps/--no-timestamps"),
    raw_md: bool = typer.Option(False, "--raw-md"),
    plain: bool = typer.Option(False, "--plain"),
    chapter: Optional[int] = typer.Option(None, "--chapter", min=1),
    at: Optional[str] = typer.Option(None, "--at"),
    window: float = typer.Option(60.0, "--window", min=0.0),
    time_range: Optional[str] = typer.Option(None, "--range"),
    cue: Optional[str] = typer.Option(None, "--cue"),
    speaker: Optional[str] = typer.Option(None, "--speaker"),
    grep: Optional[str] = typer.Option(None, "--grep"),
    context: int = typer.Option(0, "--context", "-C", min=0),
    language: Optional[str] = typer.Option(None, "--lang"),
    bilingual: Optional[str] = typer.Option(None, "--bilingual"),
    url_at: Optional[str] = typer.Option(None, "--url-at"),
):
    """阅读归档摘要、章节、导图、字幕或图文笔记。"""
    views = {"overview", "summary", "detail", "mindmap", "chapters",
             "transcript", "raw", "notes", "source", "analysis"}
    if view not in views:
        emit_error("invalid_view", f"Choose a view from {', '.join(sorted(views))}.", exit_code=2)
    if not math.isfinite(window):
        emit_error("invalid_window", "The time window must be finite.", exit_code=2)
    selectors = (chapter, at, time_range, cue, speaker, grep, language, bilingual)
    if view == "overview" and any(value is not None for value in selectors):
        view = "transcript"
    reader = archive_reader(ref, client(), fallback_task=True)
    if url_at is not None:
        url = _source_link(reader, url_at)
        emit({"url": url}, text=url)
        return
    if view == "overview":
        content = _overview(reader)
    elif view in {"summary", "detail", "source"}:
        content = reader.required(f"{view}.md")[1]
    elif view == "mindmap":
        content = mindmap(reader, plain=plain or get_cli_context().plain, depth=depth,
                          timestamps=timestamps, raw_md=raw_md)
    elif view == "chapters":
        content = render_chapter_table(chapters(reader, reader.metadata()), timestamps=timestamps)
    elif view in {"transcript", "raw"}:
        content = _transcript(
            reader, raw=view == "raw", language=language, bilingual=bilingual,
            timestamps=timestamps,
            chapter=chapter, at=at, window=window, time_range=time_range, cue=cue, speaker=speaker,
            grep=grep, context=context,
        )
    elif view == "notes":
        content = _notes(reader)
    else:
        content = _analysis(reader.json("analysis.json", required=True))
    content = content.removeprefix("\ufeff")
    emit({"view": view, "content": content}, text=content)
