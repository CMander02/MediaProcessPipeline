"""Rich formatting helpers for CLI output."""

from __future__ import annotations

from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.text import Text

console = Console()

STEP_LABELS = {
    "download": "下载媒体",
    "separate": "分离人声",
    "transcribe": "转录音频",
    "analyze": "分析内容",
    "polish": "润色字幕",
    "summarize": "生成摘要",
    "archive": "归档保存",
}

STATUS_ICONS = {
    "pending": ("dim", "..."),
    "queued": ("yellow", "○"),
    "processing": ("blue bold", "▶"),
    "completed": ("green", "✓"),
    "failed": ("red", "✗"),
    "cancelled": ("dim", "⊘"),
}


def styled_status(status: str) -> Text:
    style, icon = STATUS_ICONS.get(status, ("", "?"))
    return Text(f"{icon} {status}", style=style)


def time_ago(iso_str: str | None) -> str:
    if not iso_str:
        return "-"
    try:
        secs = int((datetime.now() - datetime.fromisoformat(iso_str)).total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except (ValueError, TypeError):
        return str(iso_str)


def print_task_table(tasks: list[dict]) -> None:
    if not tasks:
        console.print("[dim]No tasks.[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", width=8)
    table.add_column("Status", width=14)
    table.add_column("Source", max_width=50, overflow="ellipsis")
    table.add_column("Progress", width=8, justify="right")
    table.add_column("Created", width=10)
    for t in tasks:
        src = t.get("source", "")
        if len(src) > 50:
            src = "..." + src[-47:]
        table.add_row(
            t.get("id", "")[:8],
            styled_status(t.get("status", "")),
            src,
            f"{t.get('progress', 0) * 100:.0f}%",
            time_ago(t.get("created_at")),
        )
    console.print(table)


def print_task_detail(task: dict) -> None:
    style, icon = STATUS_ICONS.get(task.get("status", ""), ("", "?"))
    console.print(f"[bold]ID:[/bold]     {task.get('id', '')}")
    console.print(f"[bold]Status:[/bold] [{style}]{icon} {task.get('status')}[/{style}]")
    console.print(f"[bold]Source:[/bold] {task.get('source', '')}")
    console.print(f"[bold]Step:[/bold]   {task.get('message') or '-'}  ({task.get('progress', 0) * 100:.0f}%)")
    if task.get("error"):
        console.print(f"[bold red]Error:[/bold red]  {task['error']}")
    if task.get("result", {}).get("output_dir"):
        console.print(f"[bold]Output:[/bold] {task['result']['output_dir']}")
