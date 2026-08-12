"""Access, diagnostics, storage, filesystem, sync, and atomic pipeline commands."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

import typer

from app.cli.commands.common import api_call, client, resolve_task_ref
from app.cli.context import get_cli_context
from app.cli.daemon import daemon_status, start_daemon, stop_daemon
from app.cli.output import confirm_action, emit, emit_error

server_app = typer.Typer(help="本地 daemon 进程管理", no_args_is_help=True)
auth_app = typer.Typer(help="API 认证检查", no_args_is_help=True)
stage_app = typer.Typer(help="远程文件 staging 管理", no_args_is_help=True)
kb_app = typer.Typer(help="知识库检索与索引管理", no_args_is_help=True)
voiceprint_app = typer.Typer(help="声纹人员与样本管理", no_args_is_help=True)
logs_app = typer.Typer(help="后端日志查询与跟踪", no_args_is_help=True)
storage_app = typer.Typer(help="数据目录使用量与清理", no_args_is_help=True)
fs_app = typer.Typer(help="服务端文件系统高级操作", no_args_is_help=True)
sync_app = typer.Typer(help="移动端归档同步管理", no_args_is_help=True)
pipeline_app = typer.Typer(help="原子媒体处理操作", no_args_is_help=True)


@server_app.command("status")
def server_status():
    """显示 daemon 健康状态与 CLI 管理的 PID。"""
    ctx = get_cli_context()
    result = daemon_status(ctx.server_url, ctx.api_token)
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2, default=str))


@server_app.command("start")
def server_start(timeout: float = typer.Option(20.0, "--timeout", min=1.0, max=300.0)):
    """在后台启动独立本地 daemon。"""
    ctx = get_cli_context()
    try:
        result = start_daemon(ctx.server_url, api_token=ctx.api_token, timeout=timeout)
    except RuntimeError as exc:
        emit_error("daemon_start_failed", str(exc), retryable=True, exit_code=3)
    emit(result, text=f"online\t{result.get('server')}\tpid={result.get('pid') or '-'}")


@server_app.command("stop")
def server_stop(
    yes: bool = typer.Option(False, "--yes"),
    timeout: float = typer.Option(10.0, "--timeout", min=1.0, max=120.0),
):
    """停止经 PID 与命令行校验的 CLI 管理 daemon。"""
    confirm_action("Stop the CLI-managed daemon?", explicit_yes=yes)
    ctx = get_cli_context()
    try:
        result = stop_daemon(ctx.server_url, api_token=ctx.api_token, timeout=timeout)
    except RuntimeError as exc:
        emit_error("daemon_stop_failed", str(exc), exit_code=4)
    emit(result, text=f"offline\t{result.get('server')}")


@auth_app.command("check")
def auth_check():
    """检查认证状态，并读取能力清单验证当前 token。"""
    api = client()
    status = api_call(api.auth_status)
    capabilities = api_call(api.capabilities)
    result = {"auth": status, "capabilities": capabilities}
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))


@auth_app.command("unlock")
def auth_unlock(token: Optional[str] = typer.Option(None, "--token", prompt=False)):
    """验证 token 并创建 daemon 会话。"""
    value = token or get_cli_context().api_token
    if not value:
        emit_error("token_required", "Provide --token or MPP_API_TOKEN.", exit_code=2)
    api = client()
    result = api_call(lambda: api.auth_unlock(value))
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2), redact_secrets=True)


@auth_app.command("logout")
def auth_logout():
    """清除 daemon 返回的浏览器会话 cookie。"""
    api = client()
    result = api_call(api.auth_logout)
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))


@stage_app.command("upload")
def stage_upload(path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True)):
    """上传本机媒体到 daemon staging。"""
    api = client()
    result = api_call(lambda: api.stage_file(path.resolve()))
    emit(result, text=f"{result.get('staging_id')}\t{result.get('path')}")


@stage_app.command("delete")
def stage_delete(staging_id: str = typer.Argument(...)):
    """删除一个 staging 目录。"""
    api = client()
    result = api_call(lambda: api.delete_staged(staging_id))
    emit(result, text=staging_id)


@kb_app.command("search")
def kb_search(
    query: str = typer.Argument(...),
    top_k: int = typer.Option(10, "--top-k", min=1, max=50),
    platform: Optional[str] = typer.Option(None, "--platform"),
    uploader: Optional[str] = typer.Option(None, "--uploader"),
):
    """执行语义知识库检索。"""
    api = client()
    result = api_call(lambda: api.kb_search(query, top_k, platform, uploader))
    entries = result.get("results") or []
    text = "\n\n".join(
        f"[{index}] score={item.get('score')} task={item.get('task_id')}\n"
        f"{item.get('text') or item.get('content') or ''}"
        for index, item in enumerate(entries, 1)
    )
    emit(result, text=text)


@kb_app.command("stats")
def kb_stats():
    """显示知识库 chunk 数量。"""
    api = client()
    result = api_call(api.kb_stats)
    emit(result, text=json.dumps(result, ensure_ascii=False))


@kb_app.command("reindex")
def kb_reindex(yes: bool = typer.Option(False, "--yes")):
    """重建全部已完成任务的知识库索引。"""
    confirm_action("Reindex all completed tasks?", explicit_yes=yes)
    api = client()
    result = api_call(api.kb_reindex)
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))


@voiceprint_app.command("list")
def voiceprint_list():
    """列出声纹人员。"""
    api = client()
    items = api_call(api.voiceprint_persons)
    text = "ID\tSAMPLES\tNAME\tNOTES\n" + "\n".join(
        f"{item.get('id')}\t{item.get('sample_count')}\t{item.get('name')}\t{item.get('notes')}"
        for item in items
    )
    emit(items, text=text)


def _voiceprint_person(api, person_id: str) -> dict[str, Any]:
    for item in api_call(api.voiceprint_persons):
        if str(item.get("id")) == person_id:
            return item
    emit_error("voiceprint_person_not_found", f"Person {person_id!r} was not found.", exit_code=4)


@voiceprint_app.command("show")
def voiceprint_show(person_id: str = typer.Argument(...)):
    """显示一个声纹人员。"""
    api = client()
    result = _voiceprint_person(api, person_id)
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))


@voiceprint_app.command("update")
def voiceprint_update(
    person_id: str = typer.Argument(...),
    name: Optional[str] = typer.Option(None, "--name"),
    notes: Optional[str] = typer.Option(None, "--notes"),
):
    """修改声纹人员名称或备注。"""
    patch = {
        key: value for key, value in {"name": name, "notes": notes}.items() if value is not None
    }
    if not patch:
        emit_error("empty_patch", "Provide --name or --notes.", exit_code=2)
    api = client()
    result = api_call(lambda: api.update_voiceprint_person(person_id, patch))
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))


@voiceprint_app.command("merge")
def voiceprint_merge(
    dst_id: str = typer.Argument(...),
    src_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes"),
):
    """把源人员的样本合并到目标人员。"""
    confirm_action(f"Merge voiceprint person {src_id} into {dst_id}?", explicit_yes=yes)
    api = client()
    result = api_call(lambda: api.merge_voiceprint_persons(dst_id, src_id))
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))


@voiceprint_app.command("delete")
def voiceprint_delete(
    person_id: str = typer.Argument(...), yes: bool = typer.Option(False, "--yes")
):
    """删除声纹人员和关联样本记录。"""
    api = client()
    person = _voiceprint_person(api, person_id)
    confirm_action(
        f"Delete voiceprint person {person.get('name')} "
        f"with {person.get('sample_count')} sample(s)?",
        explicit_yes=yes,
    )
    result = api_call(lambda: api.delete_voiceprint_person(person_id))
    emit(result, text=person_id)


@voiceprint_app.command("sample")
def voiceprint_sample(
    sample_id: str = typer.Argument(...), output: Path = typer.Option(..., "--output", "-o")
):
    """下载一个声纹样本 WAV。"""
    api = client()
    data = api_call(lambda: api.voiceprint_sample(sample_id))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    emit({"sample_id": sample_id, "output": str(output), "size": len(data)}, text=str(output))


def _log_text(payload: dict[str, Any]) -> str:
    lines = []
    for item in payload.get("entries") or []:
        lines.append(
            "\t".join(
                str(item.get(key) or "") for key in ("timestamp", "level", "event", "message")
            )
        )
    return "\n".join(lines)


@logs_app.command("list")
def logs_list():
    """列出后端日志文件。"""
    api = client()
    result = api_call(api.log_files)
    text = "\n".join(
        "\t".join(
            [
                "*" if item.get("active") else " ",
                str(item.get("size")),
                str(item.get("modified_at")),
                str(item.get("name")),
            ]
        )
        for item in result.get("files") or []
    )
    emit(result, text=text)


@logs_app.command("show")
def logs_show(
    file: Optional[str] = typer.Argument(None),
    cursor: Optional[int] = typer.Option(None, "--cursor", min=0),
    max_bytes: Optional[int] = typer.Option(None, "--max-bytes", min=1024),
):
    """读取日志，支持 cursor 增量读取。"""
    api = client()
    result = api_call(lambda: api.read_logs(file, cursor, max_bytes))
    emit(result, text=_log_text(result))


@logs_app.command("tail")
def logs_tail(
    file: Optional[str] = typer.Argument(None),
    follow: bool = typer.Option(False, "--follow", "-f"),
    interval: float = typer.Option(1.0, "--interval", min=0.1, max=60.0),
):
    """从日志尾部读取，并可持续跟踪。"""
    if follow and get_cli_context().output_mode == "json":
        emit_error("jsonl_required", "Use --jsonl with --follow.", exit_code=2)
    api = client()
    cursor: int | None = None
    try:
        while True:
            result = api_call(lambda: api.read_logs(file, cursor, None))
            text = _log_text(result)
            if get_cli_context().output_mode in {"json", "jsonl"}:
                emit(result)
            elif text:
                typer.echo(text)
            cursor = int(result.get("cursor") or 0)
            if not follow:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        raise typer.Exit(130)


@storage_app.command("usage")
def storage_usage():
    """统计 data_root 文件数量与类型占用。"""
    api = client()
    result = api_call(api.disk_usage)
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))


@storage_app.command("clean")
def storage_clean(
    task: Optional[str] = typer.Option(None, "--task"),
    older_than: Optional[int] = typer.Option(None, "--older-than", min=1),
    dry_run: bool = typer.Option(True, "--dry-run/--apply"),
    yes: bool = typer.Option(False, "--yes"),
):
    """预览或清理任务临时目录与过期孤儿目录。"""
    if task and older_than is not None:
        emit_error("invalid_options", "Use one of --task or --older-than.", exit_code=2)
    api = client()
    task_id = api_call(lambda: resolve_task_ref(task, api)) if task else None
    if not dry_run:
        confirm_action("Delete the selected temporary data?", explicit_yes=yes)
    if task_id:
        result = api_call(lambda: api.cleanup_task(task_id, dry_run=dry_run))
    else:
        result = api_call(lambda: api.cleanup_all(older_than or 24, dry_run=dry_run))
    errors = result.get("errors") or []
    if errors and not (result.get("candidates") or result.get("cleaned")):
        emit_error(
            "cleanup_refused", "No eligible cleanup target was found.", detail=errors, exit_code=4
        )
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))
    if errors:
        raise typer.Exit(5)


def _require_fs_capability(api, capability: str = "filesystem_browse") -> None:
    capabilities = api_call(api.capabilities)
    if not capabilities.get(capability):
        emit_error(
            "capability_denied",
            f"Server capability {capability!r} is disabled.",
            detail=capabilities,
            exit_code=4,
        )


@fs_app.command("drives")
def fs_drives():
    """列出 daemon 主机的驱动器或常用根目录。"""
    api = client()
    _require_fs_capability(api)
    result = api_call(api.fs_drives)
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))


@fs_app.command("list")
def fs_list(path: str = typer.Argument(...), mode: str = typer.Option("all", "--mode")):
    """浏览 daemon 主机目录。"""
    if mode not in {"file", "directory", "all"}:
        emit_error("invalid_mode", f"Unknown browse mode: {mode}", exit_code=2)
    api = client()
    _require_fs_capability(api)
    result = api_call(lambda: api.fs_list(path, mode))
    emit(
        result,
        text="\n".join(
            f"{'dir' if item.get('is_dir') else 'file'}\t{item.get('path')}"
            for item in result.get("items") or []
        ),
    )


@fs_app.command("scan")
def fs_scan(
    path: str = typer.Argument(...),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive"),
):
    """扫描 daemon 主机目录中的媒体文件。"""
    api = client()
    _require_fs_capability(api)
    result = api_call(lambda: api.fs_scan(path, recursive))
    emit(result, text="\n".join(str(item.get("path")) for item in result.get("files") or []))


@fs_app.command("read")
def fs_read(path: str = typer.Argument(...)):
    """读取 data_root 内 UTF-8 文本。"""
    api = client()
    result = api_call(lambda: api.fs_read(path))
    if not result.get("success"):
        emit_error("file_read_failed", str(result.get("error") or "Read failed."), exit_code=4)
    emit(result, text=str(result.get("content") or ""))


@fs_app.command("write")
def fs_write(
    path: str = typer.Argument(...),
    source_file: Path = typer.Option(..., "--from", exists=True, dir_okay=False, readable=True),
    dry_run: bool = typer.Option(False, "--dry-run"),
    yes: bool = typer.Option(False, "--yes"),
):
    """把本机 UTF-8 文本写入 daemon data_root。"""
    content = source_file.read_text(encoding="utf-8")
    preview = {
        "path": path,
        "source": str(source_file),
        "characters": len(content),
        "lines": content.count("\n") + 1,
    }
    if dry_run:
        emit(preview, text=json.dumps(preview, ensure_ascii=False, indent=2))
        return
    confirm_action(f"Write {len(content)} characters to {path}?", explicit_yes=yes)
    api = client()
    result = api_call(lambda: api.fs_write(path, content))
    if not result.get("success"):
        emit_error("file_write_failed", str(result.get("error") or "Write failed."), exit_code=4)
    emit({**preview, **result}, text=path)


@fs_app.command("download")
def fs_download(
    path: str = typer.Argument(...), output: Path = typer.Option(..., "--output", "-o")
):
    """下载 daemon data_root 内媒体文件。"""
    api = client()
    data = api_call(lambda: api.fs_download(path))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    emit({"source": path, "output": str(output), "size": len(data)}, text=str(output))


@fs_app.command("open")
def fs_open(path: str = typer.Argument(...)):
    """在 daemon 主机文件管理器中打开 data_root 路径。"""
    api = client()
    _require_fs_capability(api, "open_local_folder")
    result = api_call(lambda: api.fs_open(path))
    emit(result, text=str(result.get("path") or path))


@sync_app.command("changes")
def sync_changes(
    cursor: int = typer.Option(0, "--cursor", min=0),
    limit: int = typer.Option(100, "--limit", min=1, max=500),
):
    """读取归档同步变更。"""
    api = client()
    result = api_call(lambda: api.sync_changes(cursor, limit))
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))


@sync_app.command("status")
def sync_status():
    """显示服务端归档同步 revision 和待同步变更概况。"""
    api = client()
    result = api_call(lambda: api.sync_changes(0, 1))
    summary = {
        "server_revision": result.get("server_revision", 0),
        "has_changes": bool(result.get("changes")),
        "has_more": bool(result.get("has_more")),
    }
    emit(summary, text=json.dumps(summary, ensure_ascii=False, indent=2))


@sync_app.command("manifest")
def sync_manifest(archive_id: str = typer.Argument(...)):
    """读取一个同步归档的文件 manifest。"""
    api = client()
    result = api_call(lambda: api.sync_manifest(archive_id))
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))


@sync_app.command("download")
def sync_download(
    archive_id: str = typer.Argument(...),
    relative_path: str = typer.Argument(...),
    output: Path = typer.Option(..., "--output", "-o"),
):
    """下载同步 manifest 声明的归档文件。"""
    api = client()
    data = api_call(lambda: api.sync_file(archive_id, relative_path))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    emit(
        {
            "archive_id": archive_id,
            "relative_path": relative_path,
            "output": str(output),
            "size": len(data),
        },
        text=str(output),
    )


@sync_app.command("rebuild")
def sync_rebuild(yes: bool = typer.Option(False, "--yes")):
    """重建服务端归档同步索引。"""
    confirm_action("Rebuild the archive sync index?", explicit_yes=yes)
    api = client()
    result = api_call(api.sync_rebuild)
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))


def _input_text(text: Optional[str], source_file: Optional[Path]) -> str:
    if text is not None and source_file is not None:
        emit_error("invalid_input", "Use one of --text or --from.", exit_code=2)
    if source_file is not None:
        return source_file.read_text(encoding="utf-8")
    if text is not None:
        return text
    if not sys.stdin.isatty():
        return sys.stdin.read()
    emit_error("text_required", "Provide --text, --from, or stdin.", exit_code=2)


@pipeline_app.command("download")
def pipeline_download(url: str = typer.Argument(...)):
    """调用原子媒体下载操作。"""
    api = client()
    result = api_call(
        lambda: api.request(
            "POST", "/api/pipeline/download", json_body={"url": url}, timeout=max(api.timeout, 3600)
        )
    )
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2, default=str))


@pipeline_app.command("scan")
def pipeline_scan():
    """扫描 daemon inbox。"""
    api = client()
    result = api_call(lambda: api.request("POST", "/api/pipeline/scan", json_body={}))
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2, default=str))


@pipeline_app.command("separate")
def pipeline_separate(audio_path: str = typer.Argument(...)):
    """执行原子人声分离。"""
    api = client()
    result = api_call(
        lambda: api.request(
            "POST",
            "/api/pipeline/separate",
            params={"audio_path": audio_path},
            timeout=max(api.timeout, 3600),
        )
    )
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2, default=str))


@pipeline_app.command("transcribe")
def pipeline_transcribe(
    audio_path: str = typer.Argument(...),
    language: Optional[str] = typer.Option(None, "--language"),
):
    """执行原子音频转录。"""
    api = client()
    result = api_call(
        lambda: api.request(
            "POST",
            "/api/pipeline/transcribe",
            json_body={"audio_path": audio_path, "language": language},
            timeout=max(api.timeout, 3600),
        )
    )
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2, default=str))


def _analysis_action(
    action: str, text: Optional[str], source_file: Optional[Path], language: Optional[str]
) -> None:
    content = _input_text(text, source_file)
    api = client()
    result = api_call(
        lambda: api.request(
            "POST",
            f"/api/pipeline/{action}",
            json_body={"text": content, "language": language},
            timeout=max(api.timeout, 3600),
        )
    )
    if isinstance(result, dict):
        rendered = str(
            result.get("polished")
            or result.get("markdown")
            or json.dumps(result, ensure_ascii=False, indent=2)
        )
    else:
        rendered = str(result)
    emit(result, text=rendered)


@pipeline_app.command("polish")
def pipeline_polish(
    text: Optional[str] = typer.Option(None, "--text"),
    source_file: Optional[Path] = typer.Option(
        None, "--from", exists=True, dir_okay=False, readable=True
    ),
    language: Optional[str] = typer.Option(None, "--language"),
):
    """润色输入文本。"""
    _analysis_action("polish", text, source_file, language)


@pipeline_app.command("summarize")
def pipeline_summarize(
    text: Optional[str] = typer.Option(None, "--text"),
    source_file: Optional[Path] = typer.Option(
        None, "--from", exists=True, dir_okay=False, readable=True
    ),
    language: Optional[str] = typer.Option(None, "--language"),
):
    """生成输入文本摘要。"""
    _analysis_action("summarize", text, source_file, language)


@pipeline_app.command("mindmap")
def pipeline_mindmap(
    text: Optional[str] = typer.Option(None, "--text"),
    source_file: Optional[Path] = typer.Option(
        None, "--from", exists=True, dir_okay=False, readable=True
    ),
    language: Optional[str] = typer.Option(None, "--language"),
):
    """生成输入文本思维导图。"""
    _analysis_action("mindmap", text, source_file, language)


def register_misc_commands(app: typer.Typer) -> None:
    @app.command("capabilities")
    def capabilities():
        """显示当前连接的服务端能力。"""
        api = client()
        result = api_call(api.capabilities)
        emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))
