"""Rich formatting helpers for CLI output."""

from __future__ import annotations

import os
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.text import Text

# ---------------------------------------------------------------------------
# Plain-mode detection
# ---------------------------------------------------------------------------
# MPP_PLAIN_OUTPUT is set by the encoding-fix bootstrap when Windows stdout
# cannot handle Unicode, or explicitly by --plain / --no-color flags.

_plain: bool = os.environ.get("MPP_PLAIN_OUTPUT", "0") == "1"
_no_color: bool = os.environ.get("MPP_NO_COLOR", "0") == "1"

console = Console(
    highlight=False,
    no_color=_no_color or _plain,
)


def set_plain(value: bool) -> None:
    """Called by CLI entry when --plain is passed."""
    global _plain, console
    _plain = value
    if value:
        os.environ["MPP_PLAIN_OUTPUT"] = "1"
        os.environ["MPP_NO_COLOR"] = "1"
    console = Console(highlight=False, no_color=_plain)


def set_no_color(value: bool) -> None:
    global _no_color, console
    _no_color = value
    if value:
        os.environ["MPP_NO_COLOR"] = "1"
    console = Console(highlight=False, no_color=_no_color or _plain)


# ---------------------------------------------------------------------------
# Icons — ASCII fallback when plain mode is active
# ---------------------------------------------------------------------------

def _icon(unicode_char: str, ascii_char: str) -> str:
    return ascii_char if _plain else unicode_char


STEP_LABELS = {
    "download": "下载媒体",
    "separate": "分离人声",
    "transcribe": "转录音频",
    "analyze": "分析内容",
    "polish": "润色字幕",
    "summarize": "生成摘要",
    "archive": "归档保存",
}

_STATUS_ICONS_UNICODE = {
    "pending":    ("dim",        "…"),
    "queued":     ("yellow",     "○"),
    "processing": ("blue bold",  "▶"),
    "completed":  ("green",      "✓"),
    "failed":     ("red",        "✗"),
    "cancelled":  ("dim",        "⊘"),
}

_STATUS_ICONS_ASCII = {
    "pending":    ("dim",        "..."),
    "queued":     ("yellow",     "o"),
    "processing": ("blue bold",  ">"),
    "completed":  ("green",      "+"),
    "failed":     ("red",        "x"),
    "cancelled":  ("dim",        "-"),
}


def _status_icons() -> dict:
    return _STATUS_ICONS_ASCII if _plain else _STATUS_ICONS_UNICODE


def styled_status(status: str) -> Text:
    icons = _status_icons()
    style, icon = icons.get(status, ("", "?"))
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


def fmt_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "-"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


# ---------------------------------------------------------------------------
# Task table (list view)
# ---------------------------------------------------------------------------

def print_task_table(tasks: list[dict]) -> None:
    if not tasks:
        console.print("[dim]No tasks.[/dim]")
        return
    table = Table(show_header=True, header_style="bold", box=None if _plain else None)
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


# ---------------------------------------------------------------------------
# Task detail (show view)
# ---------------------------------------------------------------------------

def print_task_detail(task: dict) -> None:
    icons = _status_icons()
    status = task.get("status", "")
    style, icon = icons.get(status, ("", "?"))

    ok  = _icon("✓", "+")
    err = _icon("✗", "x")
    arr = _icon("→", "->")

    console.print(f"[bold]任务[/bold]  {task.get('id', '')[:8]}")
    console.print(f"  状态    [{style}]{icon} {status}[/{style}]")
    console.print(f"  来源    {task.get('source', '')}")

    # Options
    opts = task.get("options") or {}
    if opts:
        opts_str = "  ".join(f"{k}={v}" for k, v in opts.items())
        console.print(f"  选项    {opts_str}")

    # Steps — stored as list[str] (step names); completed_steps is also list[str]
    steps = task.get("steps") or []
    completed_steps = set(task.get("completed_steps") or [])
    current_step = task.get("current_step") or ""
    task_status = task.get("status", "")

    if steps:
        console.print()
        console.print("  步骤")
        for step_name in steps:
            label = STEP_LABELS.get(step_name, step_name)
            if step_name in completed_steps:
                s_status = "completed"
            elif step_name == current_step and task_status == "processing":
                s_status = "processing"
            elif task_status in ("failed", "cancelled") and step_name == current_step:
                s_status = task_status
            else:
                s_status = "pending"
            s_style, s_icon = icons.get(s_status, ("", "?"))
            console.print(f"  [{s_style}]{s_icon}[/{s_style}] {label}")
    else:
        # Fallback: show current step/progress
        msg = task.get("message") or "-"
        pct = f"{task.get('progress', 0) * 100:.0f}%"
        console.print(f"  步骤    {msg}  ({pct})")

    # Error
    if task.get("error"):
        console.print(f"\n  [{style}]{err} 错误[/{style}]  {task['error']}")

    # Output
    result = task.get("result") or {}
    output_dir = result.get("output_dir") or task.get("output_dir")
    if output_dir:
        console.print(f"\n  输出  {arr}  {output_dir}")
        # List output files if available
        output_files = result.get("output_files") or result.get("files") or {}
        if output_files:
            for ftype, fpath in output_files.items():
                if fpath:
                    import pathlib
                    fname = pathlib.Path(fpath).name
                    console.print(f"    {fname:<40} ({ftype})")
