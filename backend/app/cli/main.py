"""mpp — CLI entry point for MediaProcessPipeline."""

from __future__ import annotations

from typing import Optional

import typer

app = typer.Typer(
    name="mpp",
    help="MediaProcessPipeline — 将音视频转化为结构化知识",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# mpp serve
# ---------------------------------------------------------------------------

@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address"),
    port: int = typer.Option(18000, help="Port"),
    reload: bool = typer.Option(False, help="Enable auto-reload"),
):
    """启动 daemon 服务。"""
    from app.cli.serve import run_server
    run_server(host=host, port=port, reload=reload)


# ---------------------------------------------------------------------------
# mpp run
# ---------------------------------------------------------------------------

@app.command()
def run(
    source: str = typer.Argument(..., help="媒体文件路径或 URL"),
    skip_separation: bool = typer.Option(False, "--no-sep", help="跳过人声分离"),
):
    """提交任务并实时显示进度。"""
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
    from app.cli.client import MppClient

    console = Console()
    client = MppClient()

    if not client.ping():
        console.print("[red]Daemon 未运行，请先执行 mpp serve[/red]")
        raise typer.Exit(1)

    options = {}
    if skip_separation:
        options["skip_separation"] = True

    task = client.create_task(source, options=options)
    task_id = task["id"]
    console.print(f"Task [bold]{task_id[:8]}[/bold] submitted")

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=30),
        TextColumn("{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        bar = progress.add_task(source if len(source) <= 40 else "..." + source[-37:], total=100)

        try:
            for event in client.stream_task_events(task_id):
                etype = event.get("type", "")
                data = event.get("data", {})

                if etype == "step":
                    pct = data.get("progress", 0) * 100
                    msg = data.get("message", "")
                    progress.update(bar, completed=pct, description=msg)
                elif etype == "completed":
                    progress.update(bar, completed=100, description="完成")
                    break
                elif etype == "failed":
                    progress.update(bar, description="[red]失败[/red]")
                    break
                elif etype == "cancelled":
                    progress.update(bar, description="[dim]已取消[/dim]")
                    break
        except KeyboardInterrupt:
            console.print("\n[yellow]中断，任务仍在后台运行[/yellow]")
            raise typer.Exit(0)

    # Final status
    final = client.get_task(task_id)
    status = final.get("status", "")
    if status == "completed":
        output = final.get("result", {}).get("output_dir", "")
        console.print(f"[green]✓[/green] 完成  {output}")
    elif status == "failed":
        console.print(f"[red]✗[/red] 失败: {final.get('error', '')}")


# ---------------------------------------------------------------------------
# mpp status
# ---------------------------------------------------------------------------

@app.command()
def status():
    """查看队列和活跃任务。"""
    from app.cli.client import MppClient
    from app.cli.display import console, print_task_table

    client = MppClient()
    if not client.ping():
        console.print("[red]Daemon 未运行[/red]")
        raise typer.Exit(1)

    stats = client.get_stats()
    console.print(f"[bold]Total:[/bold] {stats.get('total', 0)}  "
                  f"[blue]processing: {stats.get('processing', 0)}[/blue]  "
                  f"[yellow]queued: {stats.get('queued', 0)}[/yellow]  "
                  f"[green]completed: {stats.get('completed', 0)}[/green]  "
                  f"[red]failed: {stats.get('failed', 0)}[/red]")

    # Show active + queued tasks
    active = client.list_tasks(status="processing")
    queued = client.list_tasks(status="queued")
    if active or queued:
        print_task_table(active + queued)


# ---------------------------------------------------------------------------
# mpp list
# ---------------------------------------------------------------------------

@app.command(name="list")
def list_tasks(
    status_filter: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
    limit: int = typer.Option(20, "--limit", "-n"),
):
    """查看历史任务。"""
    from app.cli.client import MppClient
    from app.cli.display import console, print_task_table

    client = MppClient()
    if not client.ping():
        # Fallback: read SQLite directly
        try:
            _list_from_db(status_filter, limit)
            return
        except Exception:
            console.print("[red]Daemon 未运行且无法读取数据库[/red]")
            raise typer.Exit(1)

    tasks = client.list_tasks(status=status_filter, limit=limit)
    print_task_table(tasks)


def _list_from_db(status_filter: str | None, limit: int) -> None:
    """Directly read SQLite when daemon is not running."""
    from app.core.database import get_task_store, init_db
    from app.cli.display import console, print_task_table

    init_db()
    store = get_task_store()
    tasks = store.list(status=status_filter, limit=limit)
    console.print("[dim](offline — reading from SQLite)[/dim]")
    print_task_table([t.model_dump(mode="json") for t in tasks])


# ---------------------------------------------------------------------------
# mpp show
# ---------------------------------------------------------------------------

@app.command()
def show(task_id: str = typer.Argument(..., help="Task ID (prefix match)")):
    """查看单个任务详情。"""
    from app.cli.client import MppClient
    from app.cli.display import console, print_task_detail

    client = MppClient()
    if not client.ping():
        console.print("[red]Daemon 未运行[/red]")
        raise typer.Exit(1)

    # Support prefix matching
    tasks = client.list_tasks(limit=200)
    matches = [t for t in tasks if t["id"].startswith(task_id)]
    if not matches:
        console.print(f"[red]No task matching '{task_id}'[/red]")
        raise typer.Exit(1)
    if len(matches) > 1:
        console.print(f"[yellow]Ambiguous prefix, {len(matches)} matches. Showing first.[/yellow]")

    print_task_detail(matches[0])


# ---------------------------------------------------------------------------
# mpp cancel
# ---------------------------------------------------------------------------

@app.command()
def cancel(task_id: str = typer.Argument(..., help="Task ID to cancel")):
    """取消任务。"""
    from app.cli.client import MppClient
    from app.cli.display import console

    client = MppClient()
    if not client.ping():
        console.print("[red]Daemon 未运行[/red]")
        raise typer.Exit(1)

    # Prefix match
    tasks = client.list_tasks(limit=200)
    matches = [t for t in tasks if t["id"].startswith(task_id)]
    if not matches:
        console.print(f"[red]No task matching '{task_id}'[/red]")
        raise typer.Exit(1)

    result = client.cancel_task(matches[0]["id"])
    console.print(f"[green]Cancelled[/green] {matches[0]['id'][:8]}")


# ---------------------------------------------------------------------------
# mpp config
# ---------------------------------------------------------------------------

@app.command()
def config(
    key: Optional[str] = typer.Argument(None, help="Setting key"),
    value: Optional[str] = typer.Argument(None, help="New value"),
):
    """查看/修改配置。无参数显示全部，一个参数查看，两个参数修改。"""
    from app.cli.display import console

    if key is None:
        # Show all settings
        _config_show_all()
        return

    if value is None:
        # Show single key
        settings = _config_read()
        if key in settings:
            console.print(f"[bold]{key}[/bold] = {settings[key]}")
        else:
            console.print(f"[red]Unknown key: {key}[/red]")
            raise typer.Exit(1)
        return

    # Set value
    from app.cli.client import MppClient
    client = MppClient()

    # Coerce types
    if value.lower() in ("true", "false"):
        typed_value: str | bool | int | float = value.lower() == "true"
    else:
        try:
            typed_value = int(value)
        except ValueError:
            try:
                typed_value = float(value)
            except ValueError:
                typed_value = value

    if client.ping():
        client.patch_settings({key: typed_value})
    else:
        # Direct file write
        from app.core.settings import get_runtime_settings, patch_runtime_settings
        patch_runtime_settings({key: typed_value})

    console.print(f"[green]✓[/green] {key} = {typed_value}")


def _config_read() -> dict:
    """Read settings, preferring daemon, falling back to file."""
    from app.cli.client import MppClient
    client = MppClient()
    if client.ping():
        return client.get_settings()
    from app.core.settings import get_runtime_settings
    return get_runtime_settings().model_dump()


def _config_show_all() -> None:
    from app.cli.display import console
    from rich.table import Table
    settings = _config_read()
    table = Table(show_header=True, header_style="bold")
    table.add_column("Key", style="cyan")
    table.add_column("Value")
    for k, v in settings.items():
        display = str(v)
        # Mask API keys
        if "api_key" in k and v:
            display = v[:8] + "..." if len(str(v)) > 8 else "***"
        table.add_row(k, display)
    console.print(table)
