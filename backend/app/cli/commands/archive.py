"""Archive, transcript, and speaker commands."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

import typer

from app.cli.client import MppClient, MppClientError
from app.cli.commands.common import api_call, client, resolve_archive_ref, resolve_task_ref
from app.cli.commands.read import (
    TRANSCRIPT_FILES,
    ArtifactReader,
    archive_reader,
    local_archive_index,
    parse_cues,
    require_local,
)
from app.cli.context import configure_cli_context, get_cli_context
from app.cli.output import confirm_action, emit, emit_error
from app.cli.renderers import render_archive, render_fields, render_storage_media
from app.services.analysis.transcript_outputs import _segments_to_srt, _split_speaker_prefix

archive_app = typer.Typer(help="归档浏览、读取、导出与修改", no_args_is_help=True)
transcript_app = typer.Typer(help="归档字幕导入与导出", no_args_is_help=True)
speaker_app = typer.Typer(help="任务说话人修改", no_args_is_help=True)
archive_app.add_typer(transcript_app, name="transcript")


def _archive_source(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") or {}
    return str(
        metadata.get("platform")
        or metadata.get("source_type")
        or metadata.get("media_type")
        or "other"
    ).lower()


def _archive_badges(item: dict) -> list[str]:
    metadata = item.get("metadata") or {}
    extra = metadata.get("extra") or {}
    quality = extra.get("speaker_quality") or {}
    badges = []
    if (
        metadata.get("media_removed")
        or item.get("text_only")
        or (item.get("media_is_external") and not item.get("media_file"))
        or (metadata.get("media_retention_applied") and not (
            item.get("has_video") or item.get("has_audio")
        ))
    ):
        badges.append("[仅文本]")
    if quality.get("unassigned_segments") or extra.get("pending_unknown_cues"):
        badges.append("[待定人物]")
    if extra.get("derived_content_stale"):
        badges.append("[摘要待更新]")
    attempts = item.get("attempts") or item.get("duplicate_count") or 0
    if attempts > 1:
        badges.append(f"[x{attempts}]")
    return badges


def _archive_text(item: dict[str, Any]) -> str:
    media = ",".join(
        name for name in ("video", "audio", "image") if item.get(f"has_{name}")
    )
    title = " ".join([str(item.get("title") or ""), *_archive_badges(item)])
    return "\t".join(
        [
            str(item.get("task_id") or item.get("archive_id") or "")[:8],
            str(item.get("created_at") or item.get("date") or ""),
            media or "text",
            _archive_source(item),
            title,
            str(item.get("path") or ""),
        ]
    )


def _folder_id(value: str, directory: dict) -> str:
    folders = directory.get("folders") or []
    for folder in folders:
        if value == folder["id"]:
            return value
    matches = [folder["id"] for folder in folders if value == folder.get("name")]
    if len(matches) == 1:
        return matches[0]
    if matches:
        emit_error("ambiguous_folder", f"Multiple folders are named {value!r}.", exit_code=4)
    emit_error("folder_not_found", f"No folder matches {value!r}.", exit_code=4)


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _long_archive_text(reader: ArtifactReader) -> str:
    summary = reader.json("summary.json") or reader.archive.get("summary") or {}
    if isinstance(summary, str):
        summary = {"tldr": summary}
    text = _one_line(summary.get("tldr"))
    if not text and (item := reader.first("summary.md")):
        content = re.sub(r"(?s)^\ufeff?---[^\S\n]*\n.*?\n---[^\S\n]*\n", "", item[1])
        text = next((_one_line(line) for line in content.splitlines()
                     if line.strip() and not line.lstrip().startswith("#")), "")
    topics = [
        _one_line(item.get("name") or item.get("title")) if isinstance(item, dict)
        else _one_line(item) for item in summary.get("topics") or []
    ]
    return " | ".join(part for part in (text, ", ".join(filter(None, topics))) if part)


@archive_app.command("list")
def list_archives(
    search: str = typer.Option("", "--search", "-q"),
    folder: Optional[str] = typer.Option(None, "--folder"),
    unclassified: bool = typer.Option(False, "--unclassified"),
    media: str = typer.Option("all", "--media", help="video/audio/image/text"),
    source: str = typer.Option("all", "--source"),
    status: str = typer.Option("all", "--status"),
    duplicates: bool = typer.Option(False, "--duplicates"),
    sort: str = typer.Option("processed_desc", "--sort"),
    page: int = typer.Option(1, "--page", min=1),
    limit: int = typer.Option(50, "--limit", "--page-size", min=1, max=500),
    facets: bool = typer.Option(False, "--facets"),
    long: bool = typer.Option(False, "--long"),
):
    """分页列出归档并按关键词、媒体、文件夹和状态过滤。"""
    if folder and unclassified:
        emit_error("invalid_folder_filter", "Choose --folder or --unclassified.", exit_code=2)
    if sort not in {"processed_desc", "created_desc", "created_asc", "published_desc", "title_asc"}:
        emit_error("invalid_sort", f"Unknown archive sort {sort!r}.", exit_code=2)
    api = client()
    online = api.ping()
    folder_id = None
    if online:
        list_page = api.list_archives_paged
        if folder:
            folder_id = _folder_id(folder, api_call(api.list_library_folders))
    else:
        require_local()
        from app.core.library import get_library_service

        list_page = api_call(local_archive_index).list_page
        if folder:
            folder_id = _folder_id(folder, api_call(get_library_service().directory))
    params = {
        "page": page, "page_size": limit, "search": search, "media": media, "source": source,
        "status": status, "duplicates": duplicates, "sort": sort, "unclassified": unclassified,
    }
    if folder_id is not None:
        params["folder_id"] = folder_id
    data = api_call(lambda: list_page(**params))
    items = data.get("archives") or []

    def render() -> str:
        rows = ["ID\tCREATED\tMEDIA\tSOURCE\tTITLE\tPATH"]
        for item in items:
            reader = ArtifactReader(api if online else None, item)
            metadata = dict(item.get("metadata") or {})
            if "derived_content_stale" not in (metadata.get("extra") or {}):
                full = reader.json("metadata.json")
                metadata = {**metadata, **full}
            rows.append(_archive_text({**item, "metadata": metadata}))
            if long and (summary := _long_archive_text(reader)):
                rows.append(f"  {summary}")
        if facets:
            rows.extend(["", render_fields(data.get("facets") or {}, "facets")])
        return "\n".join(rows)

    emit(data if facets else items, text=render)


@archive_app.command("show")
def show(ref: str = typer.Argument(...)):
    """显示完整归档元数据与分析结果。"""
    api = client()
    archive = api_call(lambda: resolve_archive_ref(ref, api))
    emit(archive, text=lambda: render_archive(archive))


@archive_app.command("files")
def files(ref: str = typer.Argument(...)):
    """列出归档目录文件。"""
    api = client()
    archive = api_call(lambda: resolve_archive_ref(ref, api))
    capabilities = api_call(api.capabilities)
    if capabilities.get("filesystem_browse"):
        listing = api_call(lambda: api.fs_list(str(archive["path"]), "all"))
        items = listing.get("items", [])
    elif archive.get("archive_id"):
        manifest = api_call(lambda: api.sync_manifest(str(archive["archive_id"])))
        items = [
            {"name": item.get("relative_path"), "size": item.get("size"), "is_dir": False}
            for item in manifest.get("files") or []
        ]
    else:
        emit_error(
            "capability_denied",
            "The server does not expose archive directory listings.",
            exit_code=4,
        )
    text = "\n".join(
        f"{'dir' if item.get('is_dir') else 'file'}\t{item.get('size') or 0}\t{item.get('name')}"
        for item in items
    )
    emit(items, text=text)


_FILE_SELECTORS = {
    "summary": ["summary.md"],
    "transcript": ["transcript_polished.srt", "transcript.srt", "transcript_polished.md"],
    "analysis": ["analysis.json"],
    "metadata": ["metadata.json"],
    "mindmap": ["mindmap.md", "mindmap.json"],
    "detail": ["detail.md"],
    "source": ["source.md"],
    "polished-md": ["transcript_polished.md"],
    "raw-srt": ["transcript.srt"],
    "speakers": ["speaker_identity.json", "speaker_map.json"],
    "participants": ["participant_evidence.json"],
}


def _read_archive_file(reader: ArtifactReader, selector: str) -> tuple[str, str]:
    name, content = reader.required(*_FILE_SELECTORS.get(selector, [selector]))
    return reader.path(name), content


@archive_app.command("cat")
def cat(
    ref: str = typer.Argument(...),
    file: str = typer.Option(..., "--file", help="归档文件名或文件类型"),
):
    """输出归档文本文件到 stdout。"""
    path, content = _read_archive_file(archive_reader(ref, client()), file)
    emit({"path": path, "content": content}, text=content)


def _export_file(reader: ArtifactReader, selector: str) -> tuple[str, bytes]:
    from app.core.artifacts import TEXT_SUFFIXES
    from app.core.paths import managed_child

    names = _FILE_SELECTORS.get(selector, [selector])
    for name in names:
        path = reader.path(name)
        if reader.api is not None or Path(name).suffix.lower() in TEXT_SUFFIXES:
            try:
                content = reader.read(name)
            except UnicodeError:
                content = None
            if content is not None:
                return path, content.encode("utf-8")
        try:
            if reader.api is None:
                return path, managed_child(path, reader.root).read_bytes()
            if reader.archive_id:
                return path, reader.api.sync_file(reader.archive_id, name)
            return path, reader.api.fs_download(path)
        except FileNotFoundError:
            continue
        except MppClientError as exc:
            if exc.status_code not in {403, 404}:
                raise
            if exc.status_code == 403:
                raise
    emit_error("archive_file_not_found", f"No {selector!r} file exists in {reader.root}.",
               exit_code=4)


def _export_markdown(reader: ArtifactReader) -> tuple[str, str]:
    if selected := reader.first("transcript_polished.md"):
        return selected
    from app.services.analysis.transcript_outputs import srt_to_markdown

    name, srt = reader.required(*TRANSCRIPT_FILES)
    metadata = reader.metadata()
    fields = {
        "title": metadata.get("title"),
        "source": metadata.get("source_url"),
        "platform": metadata.get("platform"),
        "duration": metadata.get("duration"),
    }
    frontmatter = ["---", *(f"{key}: {json.dumps(value, ensure_ascii=False)}"
                            for key, value in fields.items() if value is not None), "---"]
    body = srt_to_markdown(srt.removeprefix("\ufeff"), str(fields["title"] or ""))
    return name, "\n".join([*frontmatter, "", body])


@archive_app.command("export")
def export(
    ref: str = typer.Argument(...),
    file: Optional[str] = typer.Option(None, "--file"),
    format: Optional[str] = typer.Option(None, "--format", help="md/srt/txt/zip"),
    output: Path = typer.Option(..., "--output", "-o"),
):
    """导出单个文件、转录文本或便携归档。"""
    if file is None and format not in {"md", "srt", "txt", "zip"}:
        emit_error("invalid_format", "Pass --file or choose --format md, srt, txt, or zip.",
                   exit_code=2)
    reader = archive_reader(ref, client())
    if file is not None:
        source, data = api_call(lambda: _export_file(reader, file))
    elif format == "zip":
        api = _online_reader(reader)
        task_id = _task_id(reader)
        api_call(lambda: output.parent.mkdir(parents=True, exist_ok=True))
        with tempfile.TemporaryDirectory(dir=output.parent, prefix=".mpp-export-") as directory:
            staged = Path(directory) / output.name
            result = api_call(lambda: api.export_task_archive(task_id, staged, include_media=False))
            api_call(lambda: staged.replace(output))
        emit({"source": reader.root, "output": str(output), "size": result.get("size"),
              "format": "zip"}, text=str(output))
        return
    else:
        if format == "md":
            name, content = _export_markdown(reader)
        else:
            name, content = reader.required(*TRANSCRIPT_FILES)
            if format == "txt":
                content = "\n".join(cue.text for cue in parse_cues(content))
        source, data = reader.path(name), content.encode("utf-8")
    api_call(lambda: output.parent.mkdir(parents=True, exist_ok=True))
    api_call(lambda: output.write_bytes(data))
    emit({"source": source, "output": str(output), "size": len(data)}, text=str(output))


def _online_reader(reader: ArtifactReader) -> MppClient:
    if reader.api is None:
        emit_error("daemon_unavailable", "This operation requires a running daemon.", exit_code=3)
    return reader.api


def _task_id(reader: ArtifactReader) -> str:
    if reader.owner_id is None:
        emit_error("task_not_found", "This archive has no associated task.", exit_code=4)
    return reader.owner_id


def _write_artifact(reader: ArtifactReader, name: str, baseline: str, content: str) -> dict:
    api = _online_reader(reader)
    result = api_call(lambda: api.fs_write(
        reader.path(name), content, expected_content=baseline,
    ))
    if not result.get("success"):
        emit_error("artifact_write_failed", str(result.get("error") or "Could not save artifact."),
                   detail=result, exit_code=4)
    return result


def _edit_interactive(name: str, baseline: str) -> str:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not get_cli_context().interactive or not editor:
        emit_error("editor_required", "Pass --from or configure an editor in an interactive shell.",
                   exit_code=2)
    command = api_call(lambda: shlex.split(editor, posix=sys.platform != "win32"))
    if sys.platform == "win32":
        command = [part.strip('"') for part in command]
    if not command:
        emit_error("editor_required", "The configured editor command is empty.", exit_code=2)
    with tempfile.TemporaryDirectory(prefix="mpp-edit-") as directory:
        path = Path(directory) / Path(name).name
        api_call(lambda: path.write_text(baseline, encoding="utf-8"))
        process = api_call(lambda: subprocess.run([*command, str(path)], check=False))
        if process.returncode:
            emit_error("editor_failed", f"The editor exited with status {process.returncode}.",
                       exit_code=2)
        return api_call(lambda: path.read_text(encoding="utf-8"))


@archive_app.command("edit")
def edit(
    ref: str = typer.Argument(...),
    target: str = typer.Argument("transcript"),
    source_file: Optional[Path] = typer.Option(
        None, "--from", exists=True, dir_okay=False, readable=True,
    ),
):
    """编辑归档文本并在保存时检查文件是否已被其他操作修改。"""
    targets = {
        "summary": ("summary.md",),
        "detail": ("detail.md",),
        "transcript": TRANSCRIPT_FILES,
        "source": ("source.md",),
    }
    if target not in targets:
        emit_error("invalid_edit_target", f"Choose from {', '.join(targets)}.", exit_code=2)
    reader = archive_reader(ref, client())
    _online_reader(reader)
    name, baseline = reader.required(*targets[target], baseline=True)
    if source_file:
        content = api_call(lambda: source_file.read_text(encoding="utf-8"))
    else:
        content = _edit_interactive(name, baseline)
    if content == baseline:
        result = {"success": True, "path": reader.path(name), "changed": False}
    else:
        if target == "transcript":
            parse_cues(content, strict=True)
        result = {**_write_artifact(reader, name, baseline, content), "changed": True}
    emit(result, text=reader.path(name))


@archive_app.command("cue")
def cue(
    ref: str = typer.Argument(...),
    cue_index: int = typer.Argument(..., min=1),
    text: Optional[str] = typer.Option(None, "--text"),
    speaker: Optional[str] = typer.Option(None, "--speaker"),
    delete: bool = typer.Option(False, "--delete"),
    yes: bool = typer.Option(False, "--yes"),
):
    """修改或删除第 N 条字幕。"""
    if delete == (text is not None or speaker is not None):
        emit_error("invalid_cue_action", "Pass --delete, --text, or --speaker.", exit_code=2)
    if speaker is not None and (not speaker.strip() or re.search(r"[\[\]\r\n]", speaker)):
        emit_error("invalid_speaker", "Enter a speaker name without brackets or line breaks.",
                   exit_code=2)
    if text is not None:
        text = "\n".join(text.strip().splitlines())
        if not text or re.search(r"\n[ \t]*\n", text):
            emit_error("invalid_cue_text", "Cue text must be nonempty with no blank lines.",
                       exit_code=2)
    reader = archive_reader(ref, client())
    _online_reader(reader)
    name, baseline = reader.required(*TRANSCRIPT_FILES, baseline=True)
    cues = parse_cues(baseline, strict=True)
    if cue_index > len(cues):
        emit_error("cue_not_found", f"No cue has index {cue_index}.", exit_code=4)
    position = cue_index - 1
    if delete:
        confirm_action(f"Delete cue {cue_index}?", explicit_yes=yes)
        del cues[position]
    else:
        item = cues[position]
        old_speaker, body = _split_speaker_prefix(item.text)
        label = speaker.strip() if speaker is not None else old_speaker
        body = text.strip() if text is not None else body
        item.text = f"[{label}] {body}" if label else body
    content = _segments_to_srt([
        {"index": i if delete else item.index, "timestamp": item.timestamp, "text": item.text}
        for i, item in enumerate(cues, 1)
    ])
    if content and baseline.endswith("\n"):
        content += "\n"
    if baseline.startswith("\ufeff"):
        content = "\ufeff" + content
    result = _write_artifact(reader, name, baseline, content)
    emit({**result, "cue": cue_index, "deleted": delete}, text=reader.path(name))


@archive_app.command("slim")
def slim(
    refs: Optional[list[str]] = typer.Argument(None),
    all_archives: bool = typer.Option(False, "--all"),
    policy: str = typer.Option("text", "--policy"),
    apply: bool = typer.Option(False, "--apply"),
    yes: bool = typer.Option(False, "--yes"),
    json_out: bool = typer.Option(False, "--json"),
):
    """预览或执行归档媒体保留策略。"""
    previous_mode = get_cli_context().output_mode
    if json_out and previous_mode == "text":
        configure_cli_context(output_mode="json")
    try:
        _slim(refs or [], all_archives, policy, apply, yes)
    finally:
        configure_cli_context(output_mode=previous_mode)


def _slim(refs: list[str], all_archives: bool, policy: str, apply: bool, yes: bool) -> None:
    if all_archives == bool(refs):
        emit_error("invalid_archive_selection", "Pass archive references or --all.", exit_code=2)
    if policy not in {"text", "playback", "all"}:
        emit_error("invalid_policy", "Choose text, playback, or all.", exit_code=2)
    api = client()
    if all_archives:
        archives = api_call(lambda: api.list_archives(lite=True))
    else:
        archives = [api_call(lambda: resolve_archive_ref(ref, api)) for ref in refs]
    paths = list(dict.fromkeys(str(item["path"]) for item in archives))
    previews = [api_call(lambda: api.media_retention(path, policy)) for path in paths]
    planned = [
        (path, preview["policy"], [item["path"] for item in preview["entries"] if item["delete"]])
        for path, preview in zip(paths, previews)
    ]
    if apply and any(files for _, _, files in planned):
        ctx = get_cli_context()
        if ctx.output_mode == "text" and ctx.interactive and not (yes or ctx.assume_yes):
            typer.echo("\n\n".join(map(render_storage_media, previews)), err=True)
        confirm_action("Delete the media files shown in this preview?", explicit_yes=yes)
    applied_by_path = {
        path: api_call(lambda: api.media_retention(path, selected_policy, files))
        for path, selected_policy, files in planned if apply and files
    }
    data = {"previews": previews, "applied": list(applied_by_path.values())}
    emit(data, text=lambda: "\n\n".join(
        render_storage_media(preview, applied_by_path.get(path))
        for path, preview in zip(paths, previews)
    ) or "No archives.")
    if any(item.get("errors") for item in applied_by_path.values()):
        raise typer.Exit(5)


@archive_app.command("dedupe")
def dedupe(
    apply: bool = typer.Option(False, "--apply"),
    yes: bool = typer.Option(False, "--yes"),
):
    """预览或清理已有成功归档的失败副本。"""
    api = client()
    preview = api_call(api.duplicate_cleanup_preview)
    rows = [f"{item.get('title', '')}\t{item['path']}" for item in preview.get("items") or []]
    if not apply or not preview.get("count"):
        emit(preview, text="\n".join([f"Candidates\t{preview.get('count', 0)}", *rows]))
        return
    ctx = get_cli_context()
    if ctx.output_mode == "text" and ctx.interactive and not (yes or ctx.assume_yes):
        typer.echo("\n".join(rows), err=True)
    confirm_action(f"Delete {preview['count']} failed duplicate(s)?", explicit_yes=yes)
    result = api_call(api.duplicate_cleanup_apply)
    errors = result.get("errors") or []
    emit({"preview": preview, "applied": result}, text=lambda: "\n".join([
        f"Deleted\t{result.get('deleted', 0)}",
        *(f"{item.get('path', '')}\t{item.get('error', '')}" for item in errors),
    ]))
    if errors:
        raise typer.Exit(5)


@archive_app.command("reconcile")
def reconcile(status_only: bool = typer.Option(False, "--status-only")):
    """重扫归档索引或查看索引状态。"""
    api = client()
    result = api_call(api.archive_index_status if status_only else api.reconcile_archives)
    emit(result, text=lambda: render_fields(result))


def _render_artifact_status(result: dict) -> str:
    rows = [f"{item['filename']}\t{item['state']}" for item in result.get("artifacts") or []]
    return "\n".join(["FILE\tSTATE", *rows])


@archive_app.command("artifacts")
def artifacts(
    ref: str = typer.Argument(...),
    repair: Optional[str] = typer.Option(None, "--repair"),
    names: Optional[str] = typer.Option(None, "--names"),
    yes: bool = typer.Option(False, "--yes"),
):
    """查看文本产物的文件和 SQLite 副本状态，或修复指定副本。"""
    if repair is not None and repair not in {"mirror", "restore-file"}:
        emit_error("invalid_repair", "Choose mirror or restore-file.", exit_code=2)
    chosen = list(dict.fromkeys(name.strip() for name in (names or "").split(",") if name.strip()))
    if names is not None and not chosen:
        emit_error("invalid_artifact_names", "Pass one or more comma-separated filenames.",
                   exit_code=2)
    reader = archive_reader(ref, client(), fallback_task=True)
    api = _online_reader(reader)
    task_id = _task_id(reader)
    if repair is None:
        result = api_call(lambda: api.artifacts_status(task_id))
        if chosen:
            result = {**result, "artifacts": [
                item for item in result.get("artifacts") or [] if item["filename"] in chosen
            ]}
        emit(result, text=lambda: _render_artifact_status(result))
        return
    if repair == "restore-file" and not chosen:
        status = api_call(lambda: api.artifacts_status(task_id))
        chosen = [item["filename"] for item in status.get("artifacts") or []
                  if item["state"] == "file_missing"]
        if not chosen:
            emit({"task_id": task_id, "repairs": []}, text="No missing files.")
            return
    confirm_action(f"Run artifact repair ({repair})?", explicit_yes=yes)
    results = []
    for name in chosen or [None]:
        payload = {"task_id": task_id, "action": repair}
        if name is not None:
            payload["filename"] = name
        results.append(api_call(lambda: api.artifacts_repair(payload)))
    emit({"task_id": task_id, "repairs": results}, text=lambda: "\n".join(
        result.get("path") or _render_artifact_status(result) for result in results
    ))
    if any(result.get("success") is False for result in results):
        raise typer.Exit(5)


@archive_app.command("thumbnail")
def thumbnail(
    ref: str = typer.Argument(...),
    output: Path = typer.Option(..., "--output", "-o"),
):
    """生成或下载归档缩略图。"""
    api = client()
    archive = api_call(lambda: resolve_archive_ref(ref, api))
    data = api_call(lambda: api.archive_thumbnail(str(archive["path"])))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    emit(
        {"archive_id": archive.get("archive_id"), "output": str(output), "size": len(data)},
        text=str(output),
    )


@archive_app.command("open")
def open_archive(ref: str = typer.Argument(...)):
    """在 daemon 所在主机打开归档目录。"""
    api = client()
    archive = api_call(lambda: resolve_archive_ref(ref, api))
    if not get_cli_context().is_local:
        emit({"opened": False, "path": archive["path"]}, text=str(archive["path"]))
        return
    result = api_call(lambda: api.fs_open(str(archive["path"])))
    emit(result, text=str(result.get("path") or archive["path"]))


@archive_app.command("rename")
def rename(ref: str = typer.Argument(...), title: str = typer.Argument(...)):
    """修改归档标题。"""
    api = client()
    archive = api_call(lambda: resolve_archive_ref(ref, api))
    result = api_call(lambda: api.rename_archive(str(archive["path"]), title))
    emit(result, text=str(result.get("title") or title))


@archive_app.command("delete")
def delete(
    refs: list[str] = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes"),
):
    """删除归档与关联的非活跃任务记录。"""
    confirm_action(f"Delete {len(refs)} archive(s)?", explicit_yes=yes)
    api = client()
    results = []
    for ref in refs:
        archive = api_call(lambda ref=ref: resolve_archive_ref(ref, api))
        results.append(api_call(lambda archive=archive: api.delete_archive(str(archive["path"]))))
    emit(results, text="\n".join(str(item.get("path") or "") for item in results))


_SRT_BLOCK = re.compile(
    r"(?ms)^\s*(\d+)\s*\n(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*\n(.*?)(?=\n{2,}|\Z)"
)


def _srt_summary(content: str) -> dict[str, Any]:
    matches = list(_SRT_BLOCK.finditer(content.replace("\r\n", "\n")))
    if not matches:
        emit_error("invalid_srt", "The input contains no valid SRT segments.", exit_code=2)
    speakers = sorted(
        {
            match.group(4).split(":", 1)[0].strip()
            for match in matches
            if ":" in match.group(4).splitlines()[0]
        }
    )
    return {
        "segments": len(matches),
        "start": matches[0].group(2),
        "end": matches[-1].group(3),
        "speakers": speakers,
    }


def _srt_plain_text(content: str) -> str:
    matches = list(_SRT_BLOCK.finditer(content.replace("\r\n", "\n")))
    return "\n".join(match.group(4).strip() for match in matches)


@transcript_app.command("export")
def transcript_export(
    ref: str = typer.Argument(...),
    format: str = typer.Option("srt", "--format", help="srt/md/txt"),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
):
    """导出归档字幕。"""
    if format not in {"srt", "md", "txt"}:
        emit_error("invalid_format", f"Unsupported transcript format: {format}", exit_code=2)
    api = client()
    reader = archive_reader(ref, api)
    path, content = _read_archive_file(reader, "transcript")
    rendered = content if format == "srt" else _srt_plain_text(content)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        emit({"source": path, "output": str(output), "format": format}, text=str(output))
    else:
        emit({"source": path, "format": format, "content": rendered}, text=rendered)


@transcript_app.command("import")
def transcript_import(
    ref: str = typer.Argument(...),
    source_file: Path = typer.Option(..., "--from", exists=True, dir_okay=False, readable=True),
    dry_run: bool = typer.Option(False, "--dry-run"),
    yes: bool = typer.Option(False, "--yes"),
):
    """校验并写入归档 SRT，同时更新 SQLite artifact。"""
    content = source_file.read_text(encoding="utf-8")
    summary = _srt_summary(content)
    if dry_run:
        emit(summary, text=json.dumps(summary, ensure_ascii=False, indent=2))
        return
    confirm_action(f"Replace transcript with {summary['segments']} SRT segments?", explicit_yes=yes)
    api = client()
    archive = api_call(lambda: resolve_archive_ref(ref, api))
    root = str(archive["path"]).rstrip("/\\")
    separator = "\\" if "\\" in root else "/"
    filename = "transcript_polished.srt" if archive.get("has_transcript") else "transcript.srt"
    target = f"{root}{separator}{filename}"
    result = api_call(lambda: api.fs_write(target, content))
    emit({**summary, **result, "path": target}, text=target)


@speaker_app.command("rename")
def rename_speaker(
    task_ref: str = typer.Argument(...),
    old_name: str = typer.Argument(...),
    new_name: str = typer.Argument(...),
    on_conflict: str = typer.Option("ask", "--on-conflict", help="ask/merge/new"),
):
    """修改任务说话人名称并同步声纹映射与产物。"""
    if on_conflict not in {"ask", "merge", "new"}:
        emit_error("invalid_conflict_mode", f"Unknown conflict mode: {on_conflict}", exit_code=2)
    api = client()
    task_id = api_call(lambda: resolve_task_ref(task_ref, api))
    result = api_call(lambda: api.rename_task_speaker(task_id, old_name, new_name, on_conflict))
    if result.get("status") == "conflict":
        emit_error(
            "speaker_name_conflict",
            "The target speaker name already belongs to another voiceprint person.",
            detail=result,
            exit_code=4,
        )
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))
