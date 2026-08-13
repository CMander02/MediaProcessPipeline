"""Task inspection and lifecycle commands."""

from __future__ import annotations

import json
from typing import Any, Optional

import typer

from app.cli.commands.common import api_call, client, resolve_many_task_refs, resolve_task_ref
from app.cli.context import get_cli_context
from app.cli.output import confirm_action, emit, emit_event

task_app = typer.Typer(help="任务查询、观察与生命周期控制", no_args_is_help=True)


def _task_text(task: dict[str, Any]) -> str:
    return "\t".join(
        [
            str(task.get("id", ""))[:8],
            str(task.get("status", "")),
            f"{float(task.get('progress', 0) or 0) * 100:.0f}%",
            str(task.get("source", "")),
        ]
    )


@task_app.command("list")
def list_tasks(
    status: Optional[list[str]] = typer.Option(None, "--status", "-s", help="状态，可重复"),
    active: bool = typer.Option(False, "--active", help="仅显示活跃状态"),
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=10000),
    offset: int = typer.Option(0, "--offset", min=0),
    watch: bool = typer.Option(False, "--watch", "-w"),
):
    """列出任务，支持状态过滤、分页和事件观察。"""
    if watch and get_cli_context().output_mode == "json":
        from app.cli.output import emit_error

        emit_error("jsonl_required", "Use --jsonl with --watch.", exit_code=2)
    api = client()
    statuses = ["pending", "queued", "processing", "paused"] if active else (status or None)
    online = api.ping()
    if online:
        tasks = api_call(lambda: api.list_tasks(limit=limit, offset=offset, statuses=statuses))
    elif get_cli_context().is_local:
        from app.core.database import get_task_store, init_db

        init_db()
        tasks = [
            item.model_dump(mode="json")
            for item in get_task_store().list(limit=limit, offset=offset, statuses=statuses)
        ]
    else:
        from app.cli.output import emit_error

        emit_error(
            "daemon_unavailable", f"Cannot reach {get_cli_context().server_url}.", exit_code=3
        )
    if get_cli_context().output_mode in {"json", "jsonl"}:
        emit(tasks)
    else:
        typer.echo("ID\tSTATUS\tPROGRESS\tSOURCE")
        for task in tasks:
            typer.echo(_task_text(task))
    if not watch:
        return
    try:
        for event in api.stream_all_events():
            emit_event(event)
            if get_cli_context().output_mode == "text":
                data = event.get("data") or {}
                typer.echo(
                    f"{event.get('type', '')}\t"
                    f"{str(event.get('task_id', ''))[:8]}\t{data.get('message', '')}"
                )
    except KeyboardInterrupt:
        raise typer.Exit(130)


@task_app.command("stats")
def stats():
    """显示任务状态统计。"""
    api = client()
    if api.ping():
        data = api_call(api.task_stats)
    elif get_cli_context().is_local:
        from app.core.database import get_task_store, init_db

        init_db()
        data = get_task_store().stats()
        data["total"] = sum(data.values())
    else:
        from app.cli.output import emit_error

        emit_error(
            "daemon_unavailable", f"Cannot reach {get_cli_context().server_url}.", exit_code=3
        )
    text = "\n".join(f"{key}: {value}" for key, value in data.items())
    emit(data, text=text)


@task_app.command("history")
def history(
    status: Optional[str] = typer.Option(None, "--status", "-s"),
    limit: int = typer.Option(50, "--limit", "-n", min=1, max=10000),
    offset: int = typer.Option(0, "--offset", min=0),
):
    """列出终态任务历史及统计。"""
    api = client()
    data = api_call(lambda: api.task_history(status=status, limit=limit, offset=offset))
    tasks = data.get("tasks") or []
    text = "ID\tSTATUS\tPROGRESS\tSOURCE\n" + "\n".join(_task_text(task) for task in tasks)
    emit(data, text=text)


@task_app.command("steps")
def steps():
    """显示服务端任务步骤 schema。"""
    api = client()
    data = api_call(api.task_steps)
    items = data.get("steps") or []
    text = "\n".join(
        f"{item.get('id')}\t{item.get('name') or item.get('label') or ''}" for item in items
    )
    emit(data, text=text)


@task_app.command("show")
def show(
    ref: str = typer.Argument(...),
    timeline: bool = typer.Option(False, "--timeline"),
    files: bool = typer.Option(False, "--files"),
):
    """显示任务详情，可附带时间线和输出文件。"""
    api = client()
    task_id = api_call(lambda: resolve_task_ref(ref, api))
    task = api_call(lambda: api.get_task(task_id))
    result: dict[str, Any] = {"task": task}
    if timeline:
        result["timeline"] = api_call(lambda: api.task_timeline(task_id)).get("events", [])
    if files:
        output_dir = (task.get("result") or {}).get("output_dir")
        if output_dir:
            capabilities = api_call(api.capabilities)
            if capabilities.get("filesystem_browse"):
                listing = api_call(lambda: api.fs_list(output_dir, "file"))
                result["files"] = listing.get("items", [])
            else:
                archives = api_call(lambda: api.list_archives(lite=True))
                archive = next(
                    (
                        item
                        for item in archives
                        if str(item.get("task_id") or "") == task_id
                        or str(item.get("path") or "") == str(output_dir)
                    ),
                    None,
                )
                if archive and archive.get("archive_id"):
                    manifest = api_call(lambda: api.sync_manifest(str(archive["archive_id"])))
                    result["files"] = manifest.get("files", [])
                else:
                    result["files"] = []
        else:
            result["files"] = []
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2, default=str))


@task_app.command("timeline")
def timeline(
    ref: str = typer.Argument(...),
    limit: int = typer.Option(1000, "--limit", min=1, max=10000),
    follow: bool = typer.Option(False, "--follow"),
):
    """输出持久化任务时间线，并可继续观察实时事件。"""
    if follow and get_cli_context().output_mode == "json":
        from app.cli.output import emit_error

        emit_error("jsonl_required", "Use --jsonl with --follow.", exit_code=2)
    api = client()
    task_id = api_call(lambda: resolve_task_ref(ref, api))
    data = api_call(lambda: api.task_timeline(task_id, limit))
    events = data.get("events", [])
    emit(
        events, text="\n".join(json.dumps(item, ensure_ascii=False, default=str) for item in events)
    )
    if follow:
        current = api_call(lambda: api.get_task(task_id))
        if current.get("status") in {"completed", "failed", "cancelled"}:
            return
        try:
            for event in api.stream_task_events(task_id):
                emit_event(event)
                if get_cli_context().output_mode == "text":
                    typer.echo(json.dumps(event, ensure_ascii=False, default=str))
                if event.get("type") in {"completed", "failed", "cancelled", "deleted"}:
                    break
        except KeyboardInterrupt:
            raise typer.Exit(130)


@task_app.command("watch")
def watch(ref: str = typer.Argument(...)):
    """观察任务实时事件，终态后输出最新任务。"""
    api = client()
    task_id = api_call(lambda: resolve_task_ref(ref, api))
    current = api_call(lambda: api.get_task(task_id))
    if current.get("status") in {"completed", "failed", "cancelled"}:
        emit(current, text=_task_text(current))
        if current.get("status") != "completed":
            raise typer.Exit(1)
        return
    terminal = False
    try:
        for event in api.stream_task_events(task_id):
            emit_event(event)
            if get_cli_context().output_mode == "text":
                data = event.get("data") or {}
                typer.echo(f"{event.get('type', '')}\t{data.get('message', '')}")
            if event.get("type") in {"completed", "failed", "cancelled", "deleted"}:
                terminal = True
                break
    except KeyboardInterrupt:
        raise typer.Exit(130)
    if terminal or get_cli_context().output_mode == "json":
        task = api_call(lambda: api.get_task(task_id))
        emit(task, text=_task_text(task))
        if task.get("status") != "completed":
            raise typer.Exit(1)


def _action(action: str, refs: list[str]) -> None:
    api = client()
    task_ids = api_call(lambda: resolve_many_task_refs(refs, api))
    results = []
    for task_id in task_ids:
        results.append(api_call(lambda task_id=task_id: api.task_action(task_id, action)))
    emit(
        results, text="\n".join(f"{action}\t{str(item.get('task_id', ''))[:8]}" for item in results)
    )


@task_app.command("cancel")
def cancel(refs: list[str] = typer.Argument(...)):
    """取消 pending、queued、processing 或 paused 任务。"""
    _action("cancel", refs)


@task_app.command("pause")
def pause(refs: list[str] = typer.Argument(...)):
    """暂停 pending、queued 或 processing 任务。"""
    _action("pause", refs)


@task_app.command("resume")
def resume(refs: list[str] = typer.Argument(...)):
    """从 paused 或 failed 任务的 checkpoint 续做。"""
    _action("resume", refs)


@task_app.command("rerun")
def rerun(
    ref: str = typer.Argument(...),
    checkpoint: bool = typer.Option(False, "--checkpoint", help="在原任务上复用 checkpoint"),
    full: bool = typer.Option(False, "--full", help="创建新任务完整运行"),
    wait: bool = typer.Option(False, "--wait"),
):
    """执行 checkpoint 重跑或创建新任务完整重跑。"""
    if checkpoint and full:
        from app.cli.output import emit_error

        emit_error("invalid_options", "Choose one of --checkpoint or --full.", exit_code=2)
    api = client()
    task_id = api_call(lambda: resolve_task_ref(ref, api))
    if full:
        original = api_call(lambda: api.get_task(task_id))
        result = api_call(
            lambda: api.create_task(
                str(original.get("source", "")),
                original.get("options") or {},
                original.get("webhook_url"),
            )
        )
        rerun_id = str(result["id"])
    else:
        result = api_call(lambda: api.checkpoint_rerun_task(task_id))
        rerun_id = task_id
    if wait and get_cli_context().output_mode == "json":
        try:
            for event in api.stream_task_events(rerun_id):
                if event.get("type") in {"completed", "failed", "cancelled", "deleted"}:
                    break
        except KeyboardInterrupt:
            raise typer.Exit(130)
        final = api_call(lambda: api.get_task(rerun_id))
        emit(final, text=_task_text(final))
        if final.get("status") != "completed":
            raise typer.Exit(1)
        return
    emit(result, text=f"queued\t{rerun_id[:8]}")
    if wait:
        watch(rerun_id)


@task_app.command("delete")
def delete(
    refs: list[str] = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", help="确认删除任务及其输出目录"),
):
    """删除任务记录与 data_root 内输出目录。"""
    confirm_action(f"Delete {len(refs)} task(s) and their output directories?", explicit_yes=yes)
    api = client()
    task_ids = api_call(lambda: resolve_many_task_refs(refs, api))
    results = [api_call(lambda task_id=task_id: api.delete_task(task_id)) for task_id in task_ids]
    emit(
        results, text="\n".join(f"deleted\t{str(item.get('task_id', ''))[:8]}" for item in results)
    )
