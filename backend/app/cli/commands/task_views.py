"""Task views for the mpp CLI."""

from __future__ import annotations

import subprocess
import sys
from typing import Optional

import typer

from app.cli.commands.root_support import (
    _emit_json_compat,
    _get_client,
    _require_daemon,
    _resolve_ref,
)
from app.cli.context import get_cli_context as _command_context


def tasks(
    watch: bool = typer.Option(False, "--watch", "-w", help="实时刷新（每 2 秒）"),
    all_tasks: bool = typer.Option(False, "--all", help="显示所有历史记录"),
    status_filter: Optional[str] = typer.Option(None, "--status", "-s", help="按状态筛选"),
    limit: int = typer.Option(20, "--limit", "-n", help="最多显示条数"),
    json_out: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """查看任务队列和历史（替代 status + list）。"""
    from app.cli.commands.task import list_tasks as command_task_list
    from app.cli.context import configure_cli_context, get_cli_context

    previous_mode = get_cli_context().output_mode
    if json_out and previous_mode == "text":
        configure_cli_context(output_mode="json")
    try:
        command_task_list(
            status=[status_filter] if status_filter else None,
            active=not all_tasks and not status_filter,
            limit=limit,
            offset=0,
            watch=watch,
        )
    finally:
        configure_cli_context(output_mode=previous_mode)


def list_alias(
    status_filter: Optional[list[str]] = typer.Option(None, "--status", "-s", help="状态，可重复"),
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=10000),
    offset: int = typer.Option(0, "--offset", min=0),
):
    """列出任务，兼容旧版 mpp list。"""
    from app.cli.commands.task import list_tasks as command_task_list

    command_task_list(status=status_filter, active=False, limit=limit, offset=offset, watch=False)


def status_alias():
    """显示任务统计和当前活跃任务。"""
    from app.cli.commands.common import api_call, client
    from app.cli.context import get_cli_context
    from app.cli.output import emit, emit_error

    api = client()
    active_statuses = ["pending", "queued", "processing", "paused"]
    if api.ping():
        stats_data = api_call(api.task_stats)
        active_tasks = api_call(lambda: api.list_tasks(limit=100, statuses=active_statuses))
    elif get_cli_context().is_local:
        from app.core.database import get_task_store, init_db

        init_db()
        store = get_task_store()
        stats_data = store.stats()
        stats_data["total"] = sum(stats_data.values())
        active_tasks = [
            item.model_dump(mode="json") for item in store.list(limit=100, statuses=active_statuses)
        ]
    else:
        emit_error(
            "daemon_unavailable", f"Cannot reach {get_cli_context().server_url}.", exit_code=3
        )

    stats_text = "  ".join(f"{key}={value}" for key, value in stats_data.items())
    rows = [stats_text, "ID\tSTATUS\tPROGRESS\tSOURCE"]
    for item in active_tasks:
        rows.append(
            f"{str(item.get('id', ''))[:8]}\t{item.get('status', '')}\t"
            f"{float(item.get('progress', 0) or 0) * 100:.0f}%\t{item.get('source', '')}"
        )
    emit({"stats": stats_data, "active": active_tasks}, text="\n".join(rows))


def _tasks_watch(status_filter: str | None = None, limit: int = 20) -> None:
    """Live-refresh task list using Rich Live + global SSE stream."""
    from rich.live import Live
    from rich.table import Table

    from app.cli.display import console, styled_status, time_ago

    client = _require_daemon()

    def _make_table(task_list: list[dict]) -> Table:
        table = Table(show_header=True, header_style="bold")
        table.add_column("ID", width=8)
        table.add_column("Status", width=14)
        table.add_column("Source", max_width=48, overflow="ellipsis")
        table.add_column("Progress", width=8, justify="right")
        table.add_column("Updated", width=10)
        for t in task_list:
            src = t.get("source", "")
            if len(src) > 48:
                src = "..." + src[-45:]
            table.add_row(
                t.get("id", "")[:8],
                styled_status(t.get("status", "")),
                src,
                f"{t.get('progress', 0) * 100:.0f}%",
                time_ago(t.get("updated_at") or t.get("created_at")),
            )
        return table

    current_tasks: list[dict] = []

    def _refresh() -> list[dict]:
        if status_filter:
            return client.list_tasks(status=status_filter, limit=limit)
        active = client.list_tasks(status="processing", limit=50)
        queued = client.list_tasks(status="queued", limit=50)
        recent = client.list_tasks(limit=limit)
        seen: set[str] = set()
        merged: list[dict] = []
        for t in active + queued + recent:
            if t["id"] not in seen:
                seen.add(t["id"])
                merged.append(t)
        return merged[:limit]

    try:
        current_tasks = _refresh()
        with Live(_make_table(current_tasks), console=console, refresh_per_second=1) as live:
            for event in client.stream_all_events():
                etype = event.get("type", "")
                if etype in ("step", "completed", "failed", "cancelled", "queued"):
                    current_tasks = _refresh()
                    live.update(_make_table(current_tasks))
    except KeyboardInterrupt:
        pass


def show(
    task_ref: str = typer.Argument(..., help="Task ID、前缀或 @last / @fail / @run"),
    summary: bool = typer.Option(False, "--summary", help="打印摘要文件到 stdout"),
    transcript: bool = typer.Option(False, "--transcript", help="打印字幕/转录到 stdout"),
    detail: bool = typer.Option(False, "--detail", help="打印详细摘要到 stdout"),
    mindmap: bool = typer.Option(False, "--mindmap", help="打印导图文本到 stdout"),
    json_out: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """查看任务详情（步骤、输出文件、选项）。"""
    from app.cli.commands.read import resolve_reader_task
    from app.cli.context import configure_cli_context
    from app.cli.display import console, print_task_detail

    previous_mode = _command_context().output_mode
    if json_out and previous_mode == "text":
        configure_cli_context(output_mode="json")
    try:
        api = _get_client()
        connected = api if api.ping() else None
        task = resolve_reader_task(task_ref, connected)
        flags = {
            name for name, enabled in (
                ("summary", summary), ("transcript", transcript), ("detail", detail),
                ("mindmap", mindmap),
            ) if enabled
        }
        artifacts = _cat_task(task, flags, connected) if flags else {}
        if _command_context().output_mode in {"json", "jsonl"}:
            _emit_json_compat({**task, "artifacts": artifacts} if flags else task)
        elif flags:
            typer.echo("\n".join(artifacts.values()))
        else:
            if connected is None:
                console.print("[dim](offline)[/dim]")
            print_task_detail(task)
    finally:
        configure_cli_context(output_mode=previous_mode)


def _cat_task(task: dict, flags: set[str], api=None) -> dict[str, str]:
    from app.cli.commands.common import api_call
    from app.cli.commands.read import ArtifactReader, mindmap
    from app.cli.output import emit_error

    reader = ArtifactReader.for_task(api, task)
    if not reader.root:
        emit_error("task_output_missing", "The task has no output directory.", exit_code=4)
    artifacts = {}
    code = "task_artifact_missing"
    candidates = {
        "summary": ("summary.md",),
        "transcript": ("transcript_polished.srt", "transcript_polished.md", "transcript.srt"),
        "detail": ("detail.md",),
    }
    available = None
    for name, files in candidates.items():
        if name not in flags:
            continue
        found = reader.first(*files)
        if found is None and name in {"summary", "transcript"}:
            if available is None:
                available = api_call(reader.names)
            legacy = [path for path in available if path not in files and "/" not in path]
            if name == "summary":
                matches = [path for path in legacy
                           if "summary" in path.lower() and path.lower().endswith(".md")]
                matches.sort(key=lambda path: not path.lower().endswith("_summary.md"))
            else:
                matches = [path for path in legacy if path.lower().endswith(".srt")]
                matches.extend(path for path in legacy if "transcript" in path.lower()
                               and path.lower().endswith((".txt", ".md")))
            found = reader.first(*matches)
        if found is None:
            emit_error(code, f"No readable {name} artifact was found.", exit_code=4)
        artifacts[name] = found[1].removeprefix("\ufeff")
    if "mindmap" in flags:
        artifacts["mindmap"] = mindmap(
            reader, plain=_command_context().plain, code=code,
        ).removeprefix("\ufeff")
    return artifacts


def open_output(
    task_ref: str = typer.Argument(..., help="Task ID、前缀或 @last"),
):
    """在文件管理器中打开任务输出目录。"""
    client = _require_daemon()
    from app.cli.commands.common import api_call

    task_id = api_call(lambda: _resolve_ref(task_ref, client=client))
    task = api_call(lambda: client.get_task(task_id))

    result = task.get("result") or {}
    output_dir = result.get("output_dir") or task.get("output_dir")
    if not output_dir:
        from app.cli.output import emit_error

        emit_error("task_output_missing", "The task has no output directory.", exit_code=4)

    from app.cli.context import get_cli_context
    from app.cli.output import emit

    if not get_cli_context().is_local:
        emit({"opened": False, "path": output_dir}, text=output_dir)
        return

    import pathlib

    od = pathlib.Path(output_dir)
    if not od.exists():
        from app.cli.output import emit_error

        emit_error("task_output_missing", f"Directory does not exist: {output_dir}", exit_code=4)

    if sys.platform == "win32":
        subprocess.run(["explorer", str(od)], check=False)
    elif sys.platform == "darwin":
        subprocess.run(["open", str(od)], check=False)
    else:
        subprocess.run(["xdg-open", str(od)], check=False)

    emit({"opened": True, "path": output_dir}, text=f"opened\t{output_dir}")


def cancel(
    task_ref: str = typer.Argument(..., help="Task ID、前缀或 @last / @run"),
):
    """取消任务。"""
    from app.cli.commands.task import cancel as command_cancel

    command_cancel([task_ref])
