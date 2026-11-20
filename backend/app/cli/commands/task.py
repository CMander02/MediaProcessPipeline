"""Task inspection and lifecycle commands."""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Optional

import typer

from app.cli.client import MppClientError
from app.cli.commands.common import (
    api_call,
    client,
    emit_client_error,
    resolve_many_task_refs,
    resolve_task_ref,
)
from app.cli.context import get_cli_context
from app.cli.output import confirm_action, emit, emit_error, emit_event
from app.cli.renderers import render_task, render_timeline, render_timeline_event

task_app = typer.Typer(help="任务查询、观察与生命周期控制", no_args_is_help=True)

_ACTIVE = ["pending", "queued", "processing", "paused"]


class RerunStep(str, Enum):
    speaker_review = "speaker_review"
    polish = "polish"
    analyze = "analyze"


class DeletableStatus(str, Enum):
    failed = "failed"
    cancelled = "cancelled"


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
    except MppClientError as exc:
        emit_client_error(exc)
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
    emit(result, text=lambda: render_task(result))


@task_app.command("timeline")
def timeline(
    ref: str = typer.Argument(...),
    limit: int = typer.Option(1000, "--limit", min=1, max=10000),
    follow: bool = typer.Option(False, "--follow"),
):
    """输出持久化任务时间线，并可继续观察实时事件。"""
    if follow and get_cli_context().output_mode == "json":
        emit_error("jsonl_required", "Use --jsonl with --follow.", exit_code=2)
    api = client()
    task_id = api_call(lambda: resolve_task_ref(ref, api))
    data = api_call(lambda: api.task_timeline(task_id, limit))
    events = data.get("events", [])
    emit(events, text=lambda: render_timeline(events))
    if follow:
        current = api_call(lambda: api.get_task(task_id))
        if current.get("status") in {"completed", "failed", "cancelled"}:
            return
        try:
            for event in api.stream_task_events(task_id):
                emit_event(event)
                if get_cli_context().output_mode == "text":
                    typer.echo(render_timeline_event(event))
                if event.get("type") in {"completed", "failed", "cancelled", "deleted"}:
                    break
        except MppClientError as exc:
            emit_client_error(exc)
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
    except MppClientError as exc:
        emit_client_error(exc)
    except KeyboardInterrupt:
        raise typer.Exit(130)
    if terminal or get_cli_context().output_mode == "json":
        task = api_call(lambda: api.get_task(task_id))
        emit(task, text=_task_text(task))
        if task.get("status") != "completed":
            raise typer.Exit(1)


def select_task_ids(api, refs: list[str], statuses: list[str] | None = None) -> list[str]:
    if not refs and not statuses:
        emit_error(
            "task_selection_required", "Provide task references or a bulk selector.", exit_code=2
        )
    task_ids = resolve_many_task_refs(refs, api)
    if statuses:
        offset = 0
        while True:
            page = api.list_tasks(statuses=statuses, limit=500, offset=offset)
            task_ids.extend(str(task["id"]) for task in page)
            if len(page) < 500:
                break
            offset += len(page)
    return list(dict.fromkeys(task_ids))


def _action(action: str, refs: list[str], statuses: list[str] | None = None) -> None:
    api = client()
    task_ids = api_call(lambda: select_task_ids(api, refs, statuses))
    results = apply_task_actions(task_ids, lambda task_id: api.task_action(task_id, action), action)
    emit(
        results,
        text="\n".join(f"{action}\t{str(item.get('task_id', ''))[:8]}" for item in results)
        or "No matching tasks.",
    )


def apply_task_actions(
    task_ids: list[str], operation: Callable[[str], dict], action: str,
) -> list[dict]:
    if len(task_ids) == 1:
        return [api_call(lambda: operation(task_ids[0]))]
    results, errors = [], []
    for task_id in task_ids:
        try:
            results.append(operation(task_id))
        except MppClientError as exc:
            errors.append({"task_id": task_id, "code": exc.code, "message": exc.message})
    if errors:
        rows = [f"{action}\t{item['task_id']}" for item in results]
        rows += [f"failed\t{item['task_id']}\t{item['message']}" for item in errors]
        emit({"applied": results, "errors": errors}, text="\n".join(rows))
        raise typer.Exit(5 if results else 1)
    return results


@task_app.command("cancel")
def cancel(
    refs: list[str] = typer.Argument(None),
    all_active: bool = typer.Option(False, "--all-active"),
):
    """取消 pending、queued、processing 或 paused 任务。"""
    _action("cancel", refs or [], _ACTIVE if all_active else None)


@task_app.command("pause")
def pause(refs: list[str] = typer.Argument(...)):
    """暂停 pending、queued 或 processing 任务。"""
    _action("pause", refs)


@task_app.command("resume")
def resume(
    refs: list[str] = typer.Argument(None),
    all_paused: bool = typer.Option(False, "--all-paused"),
):
    """从 paused 或 failed 任务的 checkpoint 续做。"""
    _action("resume", refs or [], ["paused"] if all_paused else None)


@task_app.command("rerun")
def rerun(
    ref: str = typer.Argument(...),
    checkpoint: bool = typer.Option(False, "--checkpoint", help="在原任务上复用 checkpoint"),
    full: bool = typer.Option(False, "--full", help="在原文件夹完整运行"),
    from_step: Optional[RerunStep] = typer.Option(None, "--from"),
    wait: bool = typer.Option(False, "--wait"),
):
    """在原文件夹执行 checkpoint 重跑或完整重跑。"""
    if full and (checkpoint or from_step):
        emit_error(
            "invalid_options", "--full cannot be combined with --checkpoint or --from.", exit_code=2
        )
    api = client()
    task_id = api_call(lambda: resolve_task_ref(ref, api))
    if full:
        result = api_call(lambda: api.full_rerun_task(task_id))
    elif from_step:
        result = api_call(lambda: api.checkpoint_rerun(task_id, from_step=from_step.value))
    else:
        result = api_call(lambda: api.checkpoint_rerun_task(task_id))
    rerun_id = task_id
    if wait and get_cli_context().output_mode == "json":
        try:
            for event in api.stream_task_events(rerun_id):
                if event.get("type") in {"completed", "failed", "cancelled", "deleted"}:
                    break
        except MppClientError as exc:
            emit_client_error(exc)
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
    refs: list[str] = typer.Argument(None),
    status: Optional[DeletableStatus] = typer.Option(None, "--status"),
    yes: bool = typer.Option(False, "--yes", help="确认删除任务及其输出目录"),
):
    """删除任务记录与 data_root 内输出目录。"""
    if status is None and refs:
        confirm_action(
            f"Delete {len(refs)} task(s) and their output directories?", explicit_yes=yes
        )
    api = client()
    task_ids = api_call(
        lambda: select_task_ids(api, refs or [], [status.value] if status else None)
    )
    if not task_ids:
        emit([], text="No matching tasks.")
        return
    if status:
        confirm_action(
            f"Delete {len(task_ids)} task(s) and their output directories?", explicit_yes=yes
        )
    results = apply_task_actions(task_ids, api.delete_task, "deleted")
    emit(
        results, text="\n".join(f"deleted\t{str(item.get('task_id', ''))[:8]}" for item in results)
    )


@task_app.command("cleanup-duplicates")
def cleanup_duplicates(
    apply: bool = typer.Option(False, "--apply"),
    yes: bool = typer.Option(False, "--yes"),
):
    """预览或删除已有成功归档的失败任务。"""
    api = client()
    preview = api_call(api.duplicate_cleanup_preview)
    if not apply or not preview.get("count"):
        emit(
            preview,
            text="\n".join(
                [f"Duplicate failed attempts\t{preview.get('count', 0)}"]
                + [
                    f"{item.get('title', '')}\t{item.get('path', '')}"
                    for item in preview.get("items", [])
                ]
            ),
        )
        return
    confirm_action(f"Delete {preview['count']} duplicate failed attempts?", explicit_yes=yes)
    result = api_call(api.duplicate_cleanup_apply)
    errors = result.get("errors") or []
    emit(result, text="\n".join(
        [f"Deleted\t{result.get('deleted', 0)}"]
        + [f"{item.get('path', '')}\t{item.get('error', '')}" for item in errors]
    ))
    if errors:
        raise typer.Exit(5 if result.get("deleted") else 1)
