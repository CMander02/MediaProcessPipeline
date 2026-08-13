"""Archive, transcript, and speaker commands."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

import typer

from app.cli.commands.common import api_call, client, resolve_archive_ref, resolve_task_ref
from app.cli.context import get_cli_context
from app.cli.output import confirm_action, emit, emit_error

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


def _archive_text(item: dict[str, Any]) -> str:
    media = ",".join(
        name
        for name, enabled in (
            ("video", item.get("has_video")),
            ("audio", item.get("has_audio")),
            ("image", item.get("has_image")),
        )
        if enabled
    )
    return "\t".join(
        [
            str(item.get("task_id") or "")[:8],
            str(item.get("created_at") or item.get("date") or ""),
            media or "text",
            _archive_source(item),
            str(item.get("title") or ""),
            str(item.get("path") or ""),
        ]
    )


@archive_app.command("list")
def list_archives(
    media: Optional[str] = typer.Option(None, "--media", help="video/audio/image/text"),
    source: Optional[str] = typer.Option(None, "--source"),
    sort: str = typer.Option("created_desc", "--sort", help="created_desc/created_asc/title_asc"),
    limit: int = typer.Option(50, "--limit", min=1, max=10000),
):
    """列出归档并按媒体、来源和排序过滤。"""
    api = client()
    items = api_call(lambda: api.list_archives(lite=True))
    if media:
        key = {
            "video": "has_video",
            "audio": "has_audio",
            "image": "has_image",
        }.get(media)
        if media == "text":
            items = [
                item
                for item in items
                if not any(item.get(k) for k in ("has_video", "has_audio", "has_image"))
            ]
        elif key:
            items = [item for item in items if item.get(key)]
        else:
            emit_error("invalid_media_filter", f"Unknown media filter: {media}", exit_code=2)
    if source:
        needle = source.lower()
        items = [item for item in items if needle in _archive_source(item)]
    if sort == "created_asc":
        items.sort(key=lambda item: str(item.get("created_at") or ""))
    elif sort == "title_asc":
        items.sort(key=lambda item: str(item.get("title") or "").casefold())
    elif sort == "created_desc":
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    else:
        emit_error("invalid_sort", f"Unknown archive sort: {sort}", exit_code=2)
    items = items[:limit]
    text = "ID\tCREATED\tMEDIA\tSOURCE\tTITLE\tPATH\n" + "\n".join(
        _archive_text(item) for item in items
    )
    emit(items, text=text)


@archive_app.command("show")
def show(ref: str = typer.Argument(...)):
    """显示完整归档元数据与分析结果。"""
    api = client()
    archive = api_call(lambda: resolve_archive_ref(ref, api))
    emit(archive, text=json.dumps(archive, ensure_ascii=False, indent=2, default=str))


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
}


def _read_archive_file(api, archive: dict[str, Any], selector: str) -> tuple[str, str]:
    root = str(archive["path"]).rstrip("/\\")
    separator = "\\" if "\\" in root else "/"
    candidates = _FILE_SELECTORS.get(selector, [selector])
    for name in candidates:
        path = f"{root}{separator}{name}"
        result = api_call(lambda path=path: api.fs_read(path))
        if result.get("success"):
            return path, str(result.get("content") or "")
    emit_error("archive_file_not_found", f"No {selector!r} file exists in {root}.", exit_code=4)


@archive_app.command("cat")
def cat(
    ref: str = typer.Argument(...),
    file: str = typer.Option(
        ..., "--file", help="summary/transcript/analysis/metadata/mindmap/filename"
    ),
):
    """输出归档文本文件到 stdout。"""
    api = client()
    archive = api_call(lambda: resolve_archive_ref(ref, api))
    path, content = _read_archive_file(api, archive, file)
    emit({"path": path, "content": content}, text=content)


@archive_app.command("export")
def export(
    ref: str = typer.Argument(...),
    file: str = typer.Option(..., "--file"),
    output: Path = typer.Option(..., "--output", "-o"),
):
    """导出一个归档文件到本机路径。"""
    api = client()
    archive = api_call(lambda: resolve_archive_ref(ref, api))
    root = str(archive["path"]).rstrip("/\\")
    separator = "\\" if "\\" in root else "/"
    names = _FILE_SELECTORS.get(file, [file])
    data: bytes | None = None
    selected = ""
    for name in names:
        selected = f"{root}{separator}{name}"
        read = api_call(lambda selected=selected: api.fs_read(selected))
        if read.get("success"):
            data = str(read.get("content") or "").encode("utf-8")
            break
        try:
            if archive.get("archive_id"):
                data = api.sync_file(str(archive["archive_id"]), name)
            else:
                data = api.fs_download(selected)
            break
        except Exception:
            data = None
    if data is None:
        emit_error("archive_file_not_found", f"No {file!r} file exists in {root}.", exit_code=4)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    emit({"source": selected, "output": str(output), "size": len(data)}, text=str(output))


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
    archive = api_call(lambda: resolve_archive_ref(ref, api))
    path, content = _read_archive_file(api, archive, "transcript")
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
