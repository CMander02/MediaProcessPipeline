"""mpp — CLI entry point for MediaProcessPipeline.

Design goals (see agentspace/2026-04-13):
  - 抽一下，动一下：每条命令自洽，CLI 自己处理 daemon 问题
  - @last / @fail / @run 引用语法
  - submit / attach / retry 补齐生命周期
  - config list|get|set 子命令化，未知 key 报错
  - tasks 统一视图（替代 status + list）
  - 全局 --plain / --no-color / --json
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from difflib import get_close_matches
from typing import Optional

import typer

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="mpp",
    help="MediaProcessPipeline — 将音视频转化为结构化知识",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

config_app = typer.Typer(help="查看/修改配置", no_args_is_help=True)
app.add_typer(config_app, name="config")


@app.command(name="help", hidden=True)
def show_help():
    """显示帮助信息（等同于 --help）。"""
    import click
    from typer.main import get_command
    click_app = get_command(app)
    with click.Context(click_app, info_name="mpp") as ctx:
        print(ctx.get_help())

# ---------------------------------------------------------------------------
# Global state (set via callback before any command runs)
# ---------------------------------------------------------------------------

_plain_mode: bool = False
_json_mode: bool = False


@app.callback(invoke_without_command=True)
def _global_options(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="纯文本输出，无颜色，无 Unicode 图标（ASCII）"),
    no_color: bool = typer.Option(False, "--no-color", help="去掉颜色，保留格式结构"),
    json_out: bool = typer.Option(False, "--json", help="机器可读 JSON 输出（stdout）"),
) -> None:
    global _plain_mode, _json_mode
    _plain_mode = plain or (os.environ.get("MPP_PLAIN_OUTPUT") == "1")
    _json_mode = json_out

    # Apply to display module so Rich output adapts
    if plain or _plain_mode:
        from app.cli.display import set_plain
        set_plain(True)
    elif no_color:
        from app.cli.display import set_no_color
        set_no_color(True)


# ---------------------------------------------------------------------------
# Helpers: daemon auto-check
# ---------------------------------------------------------------------------

def _get_client() -> "MppClient":  # noqa: F821
    from app.cli.client import MppClient
    return MppClient()


def _require_daemon(client=None, auto_start: bool = False) -> "MppClient":  # noqa: F821
    """Return a connected client, auto-starting the daemon in background if needed."""
    if client is None:
        client = _get_client()
    if client.ping():
        return client

    if not auto_start:
        from app.cli.display import console
        console.print(
            "[red]Daemon 未运行[/red]  →  先执行 [bold]mpp serve[/bold]，或使用 [bold]mpp ping[/bold] 诊断"
        )
        raise typer.Exit(1)

    from app.cli.display import console
    from app.cli.serve import start_daemon_background

    ok_char = "+" if _plain_mode else "✓"
    console.print("[dim]启动后台服务…[/dim]", end="\r")
    ready = start_daemon_background()
    if not ready:
        console.print("[red]后台服务启动超时，请手动运行 mpp serve[/red]")
        raise typer.Exit(1)

    console.print(f"[green]{ok_char}[/green] 后台服务已启动  (http://127.0.0.1:18000)")
    return client


def _resolve_ref(ref: str, client=None) -> str:
    """Resolve @last / @fail / @run to a real task ID.

    Falls back to SQLite offline read when daemon is not reachable.
    Prefix-match for plain hex IDs.
    """
    if not ref.startswith("@"):
        # Plain ID or prefix — resolve via list
        return _resolve_prefix(ref, client)

    keyword = ref.lstrip("@").lower()
    status_map = {
        "last": None,          # most recent overall
        "fail": "failed",
        "run":  "processing",
    }
    if keyword not in status_map:
        from app.cli.display import console
        console.print(f"[red]未知引用: {ref}  (支持 @last / @fail / @run)[/red]")
        raise typer.Exit(1)

    status_filter = status_map[keyword]

    # Try daemon first, fall back to SQLite
    tasks = _list_tasks_any(status_filter=status_filter, limit=1, client=client)
    if not tasks:
        from app.cli.display import console
        console.print(f"[red]没有匹配 {ref} 的任务[/red]")
        raise typer.Exit(1)
    return tasks[0]["id"]


def _resolve_prefix(prefix: str, client=None) -> str:
    """Resolve a task ID prefix to a full ID."""
    tasks = _list_tasks_any(limit=200, client=client)
    matches = [t for t in tasks if t["id"].startswith(prefix)]
    if not matches:
        from app.cli.display import console
        console.print(f"[red]没有匹配 '{prefix}' 的任务[/red]")
        raise typer.Exit(1)
    if len(matches) > 1:
        from app.cli.display import console
        console.print(f"[yellow]前缀模糊，{len(matches)} 个匹配，使用最近一条[/yellow]")
    return matches[0]["id"]


def _list_tasks_any(
    status_filter: str | None = None,
    limit: int = 50,
    client=None,
) -> list[dict]:
    """List tasks from daemon if available, else from SQLite."""
    if client is None:
        client = _get_client()
    if client.ping():
        return client.list_tasks(status=status_filter, limit=limit)
    # Offline fallback
    try:
        from app.core.database import get_task_store, init_db
        init_db()
        store = get_task_store()
        items = store.list(status=status_filter, limit=limit)
        return [_task_to_dict(t) for t in items]
    except Exception:
        return []


def _task_to_dict(task) -> dict:
    """Convert Task model to plain dict for display."""
    return task.model_dump(mode="json")


# ---------------------------------------------------------------------------
# mpp serve
# ---------------------------------------------------------------------------

@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address"),
    port: int = typer.Option(18000, help="Port"),
    reload: bool = typer.Option(False, help="Enable auto-reload"),
):
    """启动 daemon 服务（前台运行）。"""
    from app.cli.serve import run_server
    run_server(host=host, port=port, reload=reload)


# ---------------------------------------------------------------------------
# mpp ping
# ---------------------------------------------------------------------------

@app.command()
def ping():
    """检查 daemon 是否在线。"""
    from app.cli.display import console
    client = _get_client()
    if client.ping():
        console.print("[green]+[/green] daemon 在线  (http://127.0.0.1:18000)" if _plain_mode
                      else "[green]✓[/green] daemon 在线  (http://127.0.0.1:18000)")
    else:
        console.print("[red]x[/red] daemon 未运行" if _plain_mode else "[red]✗[/red] daemon 未运行")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# mpp run
# ---------------------------------------------------------------------------

@app.command()
def run(
    source: str = typer.Argument(..., help="媒体文件路径或 URL"),
    no_sep: bool = typer.Option(False, "--no-sep", help="跳过人声分离"),
    speakers: int = typer.Option(None, "--speakers", "-s", help="说话人数量（留空自动检测）"),
    hotwords: str = typer.Option(None, "--hotwords", "-w", help="热词，逗号分隔"),
    force_asr: bool = typer.Option(False, "--force-asr", help="强制 ASR，忽略平台字幕"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="只输出结果路径"),
):
    """提交任务并实时显示进度（Ctrl+C 可脱离，任务继续后台运行）。"""
    client = _require_daemon(auto_start=True)
    options = _build_options(no_sep=no_sep, speakers=speakers, hotwords=hotwords, force_asr=force_asr)

    task = client.create_task(source, options=options)
    task_id = task["id"]

    if not quiet and not _json_mode:
        from app.cli.display import console
        console.print(f"已提交  [bold]{task_id[:8]}[/bold]")

    _do_attach(task_id, client=client, quiet=quiet)


def _build_options(
    no_sep: bool = False,
    speakers: int | None = None,
    hotwords: str | None = None,
    force_asr: bool = False,
) -> dict:
    opts: dict = {}
    if no_sep:
        opts["skip_separation"] = True
    if speakers is not None:
        opts["num_speakers"] = speakers
    if hotwords:
        opts["hotwords"] = [w.strip() for w in hotwords.split(",") if w.strip()]
    if force_asr:
        opts["force_asr"] = True
    return opts


# ---------------------------------------------------------------------------
# mpp submit
# ---------------------------------------------------------------------------

@app.command()
def submit(
    source: str = typer.Argument(..., help="媒体文件路径或 URL"),
    no_sep: bool = typer.Option(False, "--no-sep", help="跳过人声分离"),
    speakers: int = typer.Option(None, "--speakers", "-s", help="说话人数量"),
    hotwords: str = typer.Option(None, "--hotwords", "-w", help="热词，逗号分隔"),
    force_asr: bool = typer.Option(False, "--force-asr", help="强制 ASR"),
):
    """纯提交，打印 task_id 后立即返回（供脚本捕获）。

    示例：

    \b
    ID=$(mpp submit video.mp4)
    mpp attach $ID
    """
    client = _require_daemon(auto_start=True)
    options = _build_options(no_sep=no_sep, speakers=speakers, hotwords=hotwords, force_asr=force_asr)
    task = client.create_task(source, options=options)
    task_id = task["id"]

    if _json_mode:
        print(json.dumps({"id": task_id, "status": task.get("status", "queued")}))
    else:
        # stdout only the ID for easy shell capture; status goes to stderr
        print(task_id)
        sys.stderr.write(f"queued  {task_id[:8]}\n")


# ---------------------------------------------------------------------------
# mpp attach
# ---------------------------------------------------------------------------

@app.command()
def attach(
    task_ref: str = typer.Argument(..., help="Task ID、前缀或 @last / @fail / @run"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="只输出最终结果"),
):
    """挂接到任务实时进度流（任务已完成则立即显示结果）。"""
    client = _require_daemon(auto_start=True)
    task_id = _resolve_ref(task_ref, client=client)
    _do_attach(task_id, client=client, quiet=quiet)


def _do_attach(task_id: str, client=None, quiet: bool = False) -> None:
    """Core attach logic: stream SSE events for a task to the terminal."""
    from app.cli.display import console
    if client is None:
        client = _require_daemon(auto_start=True)

    # Snapshot current state first — task may already be done
    current = client.get_task(task_id)
    status = current.get("status", "")

    if status in ("completed", "failed", "cancelled"):
        _print_final(current, quiet=quiet)
        raise typer.Exit(0 if status == "completed" else 1)

    if quiet or _json_mode:
        # Minimal mode: just wait for completion event
        try:
            for event in client.stream_task_events(task_id):
                etype = event.get("type", "")
                if etype in ("completed", "failed", "cancelled"):
                    final = client.get_task(task_id)
                    _print_final(final, quiet=quiet)
                    raise typer.Exit(0 if etype == "completed" else 1)
        except KeyboardInterrupt:
            _print_detach_hint(task_id)
            raise typer.Exit(0)
        return

    # Rich progress display — uv-style: each completed step prints a line,
    # current step shows a live spinner+bar.
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn, MofNCompleteColumn
    from app.cli.display import STEP_LABELS

    src = current.get("source", task_id)
    label = src if len(src) <= 40 else "..." + src[-37:]

    ok_char  = "+" if _plain_mode else "✓"
    err_char = "x" if _plain_mode else "✗"

    # Track which steps have already been printed as completed lines
    printed_steps: set[str] = set()
    step_start_time: dict[str, float] = {}
    import time as _time

    # Pre-fill already-completed steps from snapshot (resume / already-running task)
    for s in (current.get("completed_steps") or []):
        console.print(f"  [green]{ok_char}[/green] {STEP_LABELS.get(s, s)}")
        printed_steps.add(s)

    current_step_name = current.get("current_step") or ""
    if current_step_name and current_step_name not in printed_steps:
        step_start_time[current_step_name] = _time.monotonic()

    with Progress(
        SpinnerColumn(),
        TextColumn("  [bold]{task.description}"),
        BarColumn(bar_width=28),
        TextColumn("{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
        transient=True,   # clears the bar line when a step finishes
    ) as progress:
        step_label = STEP_LABELS.get(current_step_name, current_step_name) if current_step_name else label
        init_pct = current.get("progress", 0) * 100
        # Per-step progress: each step is 1/N of the whole; show within-step %
        bar = progress.add_task(step_label, total=100, completed=init_pct)

        def _finish_step(step: str, failed: bool = False) -> None:
            """Print a completed-step line above the live bar."""
            if step in printed_steps:
                return
            elapsed = _time.monotonic() - step_start_time.get(step, _time.monotonic())
            lbl = STEP_LABELS.get(step, step)
            if failed:
                console.print(f"  [red]{err_char}[/red] {lbl}  [dim]{elapsed:.1f}s[/dim]")
            else:
                console.print(f"  [green]{ok_char}[/green] {lbl}  [dim]{elapsed:.1f}s[/dim]")
            printed_steps.add(step)

        try:
            for event in client.stream_task_events(task_id):
                etype = event.get("type", "")
                data  = event.get("data", {})

                if etype == "step":
                    step     = data.get("step", "")
                    completed = data.get("completed", False)
                    msg      = data.get("message", "")
                    overall_pct = data.get("progress", 0) * 100

                    if completed:
                        _finish_step(step)
                    else:
                        # New step starting
                        if step and step not in step_start_time:
                            step_start_time[step] = _time.monotonic()
                        lbl = STEP_LABELS.get(step, step) if step else (msg or label)
                        progress.update(bar, description=lbl, completed=overall_pct)

                elif etype == "completed":
                    # Mark last step complete if not already
                    last_step = data.get("step", "")
                    if last_step:
                        _finish_step(last_step)
                    progress.update(bar, completed=100)
                    break

                elif etype == "failed":
                    last_step = data.get("step", "")
                    if last_step:
                        _finish_step(last_step, failed=True)
                    break

                elif etype == "cancelled":
                    break

        except KeyboardInterrupt:
            console.print()
            _print_detach_hint(task_id)
            _maybe_stop_daemon(console)
            raise typer.Exit(0)

    final = client.get_task(task_id)
    _print_final(final, quiet=quiet)
    if final.get("status") != "completed":
        raise typer.Exit(1)


def _print_final(task: dict, quiet: bool = False) -> None:
    from app.cli.display import console
    status = task.get("status", "")
    ok  = "+" if _plain_mode else "✓"
    err = "x" if _plain_mode else "✗"

    if _json_mode:
        print(json.dumps(task, default=str))
        return

    if status == "completed":
        output = (task.get("result") or {}).get("output_dir", "")
        if quiet:
            print(output)
        else:
            console.print(f"[green]{ok}[/green] 完成  {output}")
    elif status == "failed":
        msg = task.get("error", "")
        if quiet:
            sys.stderr.write(f"failed: {msg}\n")
        else:
            console.print(f"[red]{err}[/red] 失败: {msg}")
    else:
        if not quiet:
            console.print(f"[dim]{status}[/dim]  {task.get('id', '')[:8]}")


def _print_detach_hint(task_id: str) -> None:
    from app.cli.display import console
    console.print(f"\n[yellow]已脱离，任务仍在后台运行[/yellow]")
    console.print(f"  查看进度: [bold]mpp attach {task_id[:8]}[/bold]")
    console.print(f"  查看结果: [bold]mpp show {task_id[:8]}[/bold]")


def _maybe_stop_daemon(console) -> None:
    """If we auto-started the daemon, ask the user whether to shut it down."""
    from app.cli.serve import daemon_was_started_by_cli
    if not daemon_was_started_by_cli():
        return

    try:
        answer = input("\n后台服务由本次 mpp run 启动。是否关闭？[y/N] ").strip().lower()
    except (EOFError, OSError):
        # Non-interactive terminal — leave server running
        return

    if answer in ("y", "yes", "是"):
        import os, signal as _sig
        console.print("[dim]正在关闭后台服务…[/dim]")
        os.kill(os.getpid(), _sig.SIGINT)  # triggers uvicorn graceful shutdown in the bg thread


# ---------------------------------------------------------------------------
# mpp retry
# ---------------------------------------------------------------------------

@app.command()
def retry(
    task_ref: str = typer.Argument(..., help="Task ID、前缀或 @last / @fail"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
):
    """按原参数重新提交失败任务，然后 attach。"""
    from app.cli.display import console
    client = _require_daemon(auto_start=True)
    task_id = _resolve_ref(task_ref, client=client)
    original = client.get_task(task_id)

    source = original.get("source", "")
    options = original.get("options") or {}

    new_task = client.create_task(source, options=options)
    new_id = new_task["id"]

    if not quiet and not _json_mode:
        console.print(f"重新提交  [bold]{new_id[:8]}[/bold]  (原: {task_id[:8]})")

    _do_attach(new_id, client=client, quiet=quiet)


# ---------------------------------------------------------------------------
# mpp tasks  (replaces status + list)
# ---------------------------------------------------------------------------

@app.command()
def tasks(
    watch: bool = typer.Option(False, "--watch", "-w", help="实时刷新（每 2 秒）"),
    all_tasks: bool = typer.Option(False, "--all", help="显示所有历史记录"),
    status_filter: Optional[str] = typer.Option(None, "--status", "-s", help="按状态筛选"),
    limit: int = typer.Option(20, "--limit", "-n", help="最多显示条数"),
    json_out: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """查看任务队列和历史（替代 status + list）。"""
    from app.cli.display import console

    use_json = json_out or _json_mode

    if watch:
        _tasks_watch(status_filter=status_filter, limit=limit)
        return

    offline = False
    client = _get_client()
    if not client.ping():
        offline = True

    if offline:
        try:
            from app.core.database import get_task_store, init_db
            init_db()
            store = get_task_store()
            task_list = store.list(status=status_filter, limit=limit if not all_tasks else 1000)
            task_dicts = [_task_to_dict(t) for t in task_list]
            if not use_json:
                console.print("[dim](offline — 读取本地 SQLite)[/dim]")
        except Exception as e:
            console.print(f"[red]Daemon 未运行且无法读取数据库: {e}[/red]")
            raise typer.Exit(1)
    else:
        if all_tasks:
            task_dicts = client.list_tasks(status=status_filter, limit=1000)
        elif status_filter:
            task_dicts = client.list_tasks(status=status_filter, limit=limit)
        else:
            # Default: active + recent history
            active = client.list_tasks(status="processing", limit=50)
            queued = client.list_tasks(status="queued", limit=50)
            recent = client.list_tasks(limit=limit)
            # Deduplicate, active/queued first
            seen = set()
            task_dicts = []
            for t in active + queued + recent:
                if t["id"] not in seen:
                    seen.add(t["id"])
                    task_dicts.append(t)
            task_dicts = task_dicts[:limit]

    if use_json:
        print(json.dumps(task_dicts, default=str))
        return

    from app.cli.display import print_task_table
    print_task_table(task_dicts)


def _tasks_watch(status_filter: str | None = None, limit: int = 20) -> None:
    """Live-refresh task list using Rich Live + global SSE stream."""
    from app.cli.display import console
    from rich.live import Live
    from rich.table import Table
    from rich.text import Text
    from app.cli.display import styled_status, time_ago

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


# ---------------------------------------------------------------------------
# mpp show
# ---------------------------------------------------------------------------

@app.command()
def show(
    task_ref: str = typer.Argument(..., help="Task ID、前缀或 @last / @fail / @run"),
    summary: bool = typer.Option(False, "--summary", help="打印摘要文件到 stdout"),
    transcript: bool = typer.Option(False, "--transcript", help="打印字幕/转录到 stdout"),
    json_out: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """查看任务详情（步骤、输出文件、选项）。"""
    from app.cli.display import console, print_task_detail

    use_json = json_out or _json_mode

    client = _get_client()
    online = client.ping()

    if online:
        task_id = _resolve_ref(task_ref, client=client)
        task = client.get_task(task_id)
    else:
        # Offline: read from SQLite
        try:
            from app.core.database import get_task_store, init_db
            from uuid import UUID
            init_db()
            store = get_task_store()

            if task_ref.startswith("@"):
                tasks_offline = _list_tasks_any(
                    status_filter="failed" if task_ref == "@fail" else
                                  "processing" if task_ref == "@run" else None,
                    limit=1,
                )
                if not tasks_offline:
                    console.print(f"[red]没有匹配 {task_ref} 的任务（离线）[/red]")
                    raise typer.Exit(1)
                task = tasks_offline[0]
            else:
                all_tasks = _list_tasks_any(limit=200)
                matches = [t for t in all_tasks if t["id"].startswith(task_ref)]
                if not matches:
                    console.print(f"[red]没有匹配 '{task_ref}' 的任务[/red]")
                    raise typer.Exit(1)
                task = matches[0]
            console.print("[dim](offline)[/dim]")
        except typer.Exit:
            raise
        except Exception as e:
            console.print(f"[red]无法读取任务: {e}[/red]")
            raise typer.Exit(1)

    if use_json:
        print(json.dumps(task, default=str))
        return

    if summary or transcript:
        _cat_task(task, summary=summary, transcript=transcript)
        return

    print_task_detail(task)


def _cat_task(task: dict, summary: bool = False, transcript: bool = False) -> None:
    """Print summary or transcript file content to stdout."""
    import pathlib
    result = task.get("result") or {}
    output_dir = result.get("output_dir") or task.get("output_dir")
    if not output_dir:
        sys.stderr.write("No output directory found for this task.\n")
        raise typer.Exit(1)

    od = pathlib.Path(output_dir)
    if not od.exists():
        sys.stderr.write(f"Output directory not found: {output_dir}\n")
        raise typer.Exit(1)

    if summary:
        candidates = sorted(od.glob("*_summary.md")) + sorted(od.glob("*summary*.md"))
        if not candidates:
            sys.stderr.write(f"No summary file found in {output_dir}\n")
            raise typer.Exit(1)
        print(candidates[0].read_text(encoding="utf-8"))

    if transcript:
        # Prefer .srt, then .txt
        candidates = sorted(od.glob("*.srt")) + sorted(od.glob("*transcript*.txt"))
        if not candidates:
            sys.stderr.write(f"No transcript/subtitle file found in {output_dir}\n")
            raise typer.Exit(1)
        print(candidates[0].read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# mpp open
# ---------------------------------------------------------------------------

@app.command(name="open")
def open_output(
    task_ref: str = typer.Argument(..., help="Task ID、前缀或 @last"),
):
    """在文件管理器中打开任务输出目录。"""
    from app.cli.display import console

    task_id = _resolve_ref(task_ref)
    client = _require_daemon()
    task = client.get_task(task_id)

    result = task.get("result") or {}
    output_dir = result.get("output_dir") or task.get("output_dir")
    if not output_dir:
        console.print("[red]该任务没有输出目录（可能未完成）[/red]")
        raise typer.Exit(1)

    import pathlib
    od = pathlib.Path(output_dir)
    if not od.exists():
        console.print(f"[red]目录不存在: {output_dir}[/red]")
        raise typer.Exit(1)

    if sys.platform == "win32":
        subprocess.run(["explorer", str(od)], check=False)
    elif sys.platform == "darwin":
        subprocess.run(["open", str(od)], check=False)
    else:
        subprocess.run(["xdg-open", str(od)], check=False)

    console.print(f"已打开  {output_dir}")


# ---------------------------------------------------------------------------
# mpp cancel
# ---------------------------------------------------------------------------

@app.command()
def cancel(
    task_ref: str = typer.Argument(..., help="Task ID、前缀或 @last / @run"),
):
    """取消任务。"""
    from app.cli.display import console

    client = _require_daemon()
    task_id = _resolve_ref(task_ref, client=client)
    client.cancel_task(task_id)
    ok = "+" if _plain_mode else "✓"
    console.print(f"[green]{ok}[/green] 已取消  {task_id[:8]}")


# ---------------------------------------------------------------------------
# mpp config  (subcommands: list / get / set)
# ---------------------------------------------------------------------------

# --- config group metadata (defined on RuntimeSettings fields) ---

_CONFIG_GROUPS: dict[str, list[str]] = {
    "llm": [
        "llm_provider",
        "anthropic_api_key", "anthropic_api_base", "anthropic_model",
        "openai_api_key", "openai_api_base", "openai_model",
        "custom_api_key", "custom_api_base", "custom_model", "custom_name",
        "local_llm_model_path", "local_llm_n_gpu_layers", "local_llm_n_ctx", "local_llm_n_batch",
        "polish_provider",
    ],
    "asr": [
        "qwen3_asr_model_path", "qwen3_aligner_model_path",
        "qwen3_enable_timestamps", "qwen3_batch_size", "qwen3_max_new_tokens", "qwen3_device",
    ],
    "diarization": [
        "enable_diarization", "hf_token",
        "pyannote_model_path", "pyannote_segmentation_path",
        "diarization_batch_size",
    ],
    "subtitle": [
        "prefer_platform_subtitles", "subtitle_languages", "force_asr",
    ],
    "uvr": [
        "uvr_model", "uvr_device", "uvr_model_dir",
        "uvr_mdx_inst_hq3_path", "uvr_hp_uvr_path", "uvr_denoise_lite_path",
        "uvr_kim_vocal_2_path", "uvr_deecho_dereverb_path", "uvr_htdemucs_path",
    ],
    "paths": [
        "data_root",
        "qwen3_asr_model_path", "qwen3_aligner_model_path",
        "uvr_model_dir",
        "pyannote_model_path", "pyannote_segmentation_path",
        "local_llm_model_path",
    ],
    "security": [
        "api_token",
        "anthropic_api_key", "openai_api_key", "custom_api_key", "hf_token",
        "bilibili_sessdata", "bilibili_bili_jct", "bilibili_dede_user_id",
    ],
    "bilibili": [
        "bilibili_sessdata", "bilibili_bili_jct", "bilibili_dede_user_id",
    ],
    "concurrency": [
        "max_download_concurrency",
    ],
}

_SECRET_KEYS = {
    "anthropic_api_key", "openai_api_key", "custom_api_key",
    "hf_token", "api_token",
    "bilibili_sessdata", "bilibili_bili_jct",
}


def _mask(key: str, value) -> str:
    if key in _SECRET_KEYS and value:
        s = str(value)
        return s[:4] + "..." if len(s) > 4 else "***"
    return str(value)


def _read_settings() -> dict:
    client = _get_client()
    if client.ping():
        return client.get_settings()
    from app.core.settings import get_runtime_settings
    return get_runtime_settings().model_dump()


def _all_valid_keys() -> list[str]:
    from app.core.settings import RuntimeSettings
    return list(RuntimeSettings.model_fields.keys())


@config_app.callback(invoke_without_command=True)
def _config_default(ctx: typer.Context):
    """查看/修改配置。子命令: list / get / set"""
    if ctx.invoked_subcommand is None:
        # Bare `mpp config` → show all (same as `mpp config list`)
        _config_list_impl(group=None)


@config_app.command(name="list")
def config_list(
    group: Optional[str] = typer.Option(None, "--group", "-g", help="分组: llm/asr/uvr/diarization/subtitle/paths/security/bilibili/concurrency"),
    json_out: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """列出所有配置（可按组筛选）。"""
    if json_out or _json_mode:
        settings = _read_settings()
        if group:
            keys = _CONFIG_GROUPS.get(group, [])
            settings = {k: settings[k] for k in keys if k in settings}
        print(json.dumps(settings, ensure_ascii=False))
    else:
        _config_list_impl(group=group)


def _config_list_impl(group: str | None) -> None:
    from app.cli.display import console
    from rich.table import Table

    settings = _read_settings()
    valid_keys = _all_valid_keys()

    if group:
        if group not in _CONFIG_GROUPS:
            close = get_close_matches(group, list(_CONFIG_GROUPS.keys()), n=3, cutoff=0.4)
            msg = f"[red]未知分组: {group}[/red]"
            if close:
                msg += f"  建议: {', '.join(close)}"
            console.print(msg)
            raise typer.Exit(1)
        keys_to_show = [k for k in _CONFIG_GROUPS[group] if k in settings]
        title = f"config  [bold]{group}[/bold]"
    else:
        keys_to_show = valid_keys
        title = "config"

    table = Table(title=title, show_header=True, header_style="bold", show_lines=False)
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Value")

    for k in keys_to_show:
        v = settings.get(k, "")
        table.add_row(k, _mask(k, v))

    console.print(table)


@config_app.command(name="get")
def config_get(
    key: str = typer.Argument(..., help="配置项 key"),
    json_out: bool = typer.Option(False, "--json"),
):
    """查看单个配置项的当前值。"""
    from app.cli.display import console
    from app.core.settings import RuntimeSettings

    valid_keys = _all_valid_keys()
    if key not in valid_keys:
        close = get_close_matches(key, valid_keys, n=3, cutoff=0.4)
        msg = f"[red]未知配置项: {key}[/red]"
        if close:
            msg += f"\n  你是指: [bold]{', '.join(close)}[/bold] ?"
        console.print(msg)
        raise typer.Exit(1)

    settings = _read_settings()
    value = settings.get(key, "")

    defaults = RuntimeSettings().model_dump()
    default_val = defaults.get(key)

    if json_out or _json_mode:
        print(json.dumps({"key": key, "value": value, "default": default_val}))
        return

    display_val = _mask(key, value)
    diff_hint = ""
    if str(value) != str(default_val):
        diff_hint = f"  [dim](默认: {_mask(key, default_val)})[/dim]"

    console.print(f"[cyan]{key}[/cyan] = [bold]{display_val}[/bold]{diff_hint}")


@config_app.command(name="set")
def config_set(
    key: str = typer.Argument(..., help="配置项 key"),
    value: str = typer.Argument(..., help="新值"),
):
    """设置配置项。未知 key 报错并提示近似匹配。"""
    from app.cli.display import console

    valid_keys = _all_valid_keys()
    if key not in valid_keys:
        close = get_close_matches(key, valid_keys, n=3, cutoff=0.4)
        msg = f"[red]未知配置项: {key}[/red]"
        if close:
            msg += f"\n  你是指: [bold]{', '.join(close)}[/bold] ?"
        console.print(msg)
        raise typer.Exit(1)

    # Type coercion
    typed_value: str | bool | int | float
    if value.lower() in ("true", "false"):
        typed_value = value.lower() == "true"
    else:
        try:
            typed_value = int(value)
        except ValueError:
            try:
                typed_value = float(value)
            except ValueError:
                typed_value = value

    client = _get_client()
    if client.ping():
        client.patch_settings({key: typed_value})
    else:
        from app.core.settings import patch_runtime_settings
        patch_runtime_settings({key: typed_value})

    ok = "+" if _plain_mode else "✓"
    console.print(f"[green]{ok}[/green]  {key} = {_mask(key, typed_value)}")


# ---------------------------------------------------------------------------
# mpp doctor
# ---------------------------------------------------------------------------

@app.command()
def doctor():
    """检查运行环境（ffmpeg、CUDA、模型文件、API key 等）。"""
    import shutil
    import pathlib
    from app.cli.display import console

    ok  = "[green]+" if _plain_mode else "[green]✓"
    err = "[red]x"   if _plain_mode else "[red]✗"
    warn = "[yellow]!"

    def check(label: str, passed: bool, detail: str = "") -> None:
        icon = ok if passed else err
        style_end = "[/green]" if passed else "[/red]"
        line = f"  {icon}{style_end}  {label:<20}"
        if detail:
            line += f"  [dim]{detail}[/dim]"
        console.print(line)

    # Daemon
    client = _get_client()
    daemon_ok = client.ping()
    check("Daemon", daemon_ok, "http://127.0.0.1:18000" if daemon_ok else "未运行 — mpp serve")

    # ffmpeg
    ff = shutil.which("ffmpeg")
    check("ffmpeg", ff is not None, ff or "未在 PATH 中")

    # CUDA
    try:
        import torch
        cuda_ok = torch.cuda.is_available()
        device_name = torch.cuda.get_device_name(0) if cuda_ok else ""
        check("CUDA", cuda_ok, device_name)
    except ImportError:
        check("CUDA", False, "torch 未安装")

    # Settings
    settings = _read_settings()
    data_root = settings.get("data_root", "")
    dr_ok = pathlib.Path(data_root).exists() if data_root else False
    check("data_root", dr_ok, data_root)

    # API key
    provider = settings.get("llm_provider", "")
    key_field = f"{provider}_api_key" if provider in ("anthropic", "openai", "custom") else ""
    if key_field:
        has_key = bool(settings.get(key_field, ""))
        check(f"LLM key ({provider})", has_key, "已配置" if has_key else f"未设置 — mpp config set {key_field} <key>")
    else:
        check("LLM", True, f"provider={provider}")

    # ASR model
    mp = settings.get("qwen3_asr_model_path", "")
    mp_ok = pathlib.Path(mp).exists() if mp else False
    check("ASR model (qwen3)", mp_ok or not mp,
          mp if mp_ok else ("未配置路径 (将从 HF 下载)" if not mp else f"路径不存在: {mp}"))
