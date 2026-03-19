"""Gradio UI — single-file frontend for MediaProcessPipeline.

Mounted into FastAPI at /ui via gr.mount_gradio_app().
Talks to the same daemon via internal imports (no HTTP round-trip).
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import gradio as gr

from app.core.database import get_task_store
from app.core.events import TaskEvent, get_event_bus
from app.core.queue import get_task_queue
from app.core.settings import get_runtime_settings, patch_runtime_settings
from app.core.pipeline import PIPELINE_STEPS, PipelineStep
from app.models.task import Task, TaskCreate, TaskStatus, TaskType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

STEP_NAMES = {s["id"]: s["name"] for s in PIPELINE_STEPS}
STEP_NAMES_EN = {s["id"]: s["name_en"] for s in PIPELINE_STEPS}


def _fmt_time(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(iso)


def _fmt_duration(created: str | None, completed: str | None) -> str:
    if not created or not completed:
        return "-"
    try:
        d = (datetime.fromisoformat(completed) - datetime.fromisoformat(created)).total_seconds()
        if d < 60:
            return f"{d:.0f}s"
        return f"{d / 60:.1f}m"
    except (ValueError, TypeError):
        return "-"


def _status_icon(status: str) -> str:
    return {
        "queued": "🟡",
        "processing": "🔵",
        "completed": "🟢",
        "failed": "🔴",
        "cancelled": "⚪",
    }.get(status, "⚪")


def _progress_bar(task: Task) -> str:
    """Build a text progress indicator."""
    pct = int(task.progress * 100)
    filled = pct // 5
    bar = "█" * filled + "░" * (20 - filled)
    step_label = STEP_NAMES.get(task.current_step, "") if task.current_step else ""
    return f"{bar} {pct}%  {step_label}"


def _task_to_row(t: Task) -> list:
    """Convert Task to a table row."""
    source = t.source
    if len(source) > 50:
        source = "..." + source[-47:]
    return [
        str(t.id)[:8],
        f"{_status_icon(t.status)} {t.status}",
        source,
        f"{int(t.progress * 100)}%",
        STEP_NAMES.get(t.current_step, "-") if t.current_step else "-",
        _fmt_time(t.created_at.isoformat() if isinstance(t.created_at, datetime) else t.created_at),
        _fmt_duration(
            t.created_at.isoformat() if isinstance(t.created_at, datetime) else t.created_at,
            t.completed_at.isoformat() if isinstance(t.completed_at, datetime) and t.completed_at else None,
        ),
    ]


# ---------------------------------------------------------------------------
# Core actions
# ---------------------------------------------------------------------------

async def submit_task(source: str, skip_separation: bool) -> str:
    """Submit a new pipeline task."""
    if not source or not source.strip():
        return "请输入媒体文件路径或 URL"

    source = source.strip()
    options = {}
    if skip_separation:
        options["skip_separation"] = True

    task = Task(
        task_type=TaskType.PIPELINE,
        source=source,
        options=options,
        status=TaskStatus.QUEUED,
        current_step=PipelineStep.DOWNLOAD,
        message="等待处理...",
        steps=[s["id"] for s in PIPELINE_STEPS],
        completed_steps=[],
    )

    store = get_task_store()
    store.save(task)

    queue = get_task_queue()
    await queue.submit(task.id)

    return f"已提交任务 {str(task.id)[:8]}"


def refresh_tasks() -> list[list]:
    """Get all tasks as table rows."""
    store = get_task_store()
    tasks = store.list(limit=50)
    return [_task_to_row(t) for t in tasks]


def refresh_active() -> tuple[list[list], str]:
    """Get active tasks table + queue status text."""
    store = get_task_store()
    queue = get_task_queue()

    active = store.list_by_statuses([TaskStatus.QUEUED, TaskStatus.PROCESSING])
    rows = [_task_to_row(t) for t in active]

    current_id = queue.current_task_id
    pending = queue.pending_count
    status_text = f"队列: {pending} 等待"
    if current_id:
        current = store.get(current_id)
        if current:
            status_text += f"  |  正在处理: {current.source[:40]}  {_progress_bar(current)}"
    else:
        status_text += "  |  空闲"

    return rows, status_text


def get_stats() -> str:
    """Get stats as markdown."""
    store = get_task_store()
    s = store.stats()
    return (
        f"**全部** {s.get('total', 0)}　"
        f"**完成** {s.get('completed', 0)}　"
        f"**处理中** {s.get('processing', 0)}　"
        f"**排队** {s.get('queued', 0)}　"
        f"**失败** {s.get('failed', 0)}"
    )


def get_task_detail(task_id_prefix: str) -> str:
    """Get task detail as markdown."""
    if not task_id_prefix:
        return "选择一个任务查看详情"

    store = get_task_store()
    tasks = store.list(limit=200)
    matches = [t for t in tasks if str(t.id).startswith(task_id_prefix.strip())]
    if not matches:
        return f"未找到匹配 `{task_id_prefix}` 的任务"

    t = matches[0]
    lines = [
        f"## {_status_icon(t.status)} {t.source}",
        "",
        f"**ID:** `{t.id}`",
        f"**状态:** {t.status}",
        f"**进度:** {int(t.progress * 100)}%  {STEP_NAMES.get(t.current_step, '-') if t.current_step else '-'}",
        f"**创建:** {_fmt_time(t.created_at.isoformat() if isinstance(t.created_at, datetime) else t.created_at)}",
    ]

    if t.error:
        lines.append(f"\n**错误:** {t.error}")

    if t.result:
        output_dir = t.result.get("output_dir", "")
        if output_dir:
            lines.append(f"\n**输出目录:** `{output_dir}`")

        analysis = t.result.get("analysis", {})
        if analysis:
            lines.append(f"\n**语言:** {analysis.get('language', '-')}")
            lines.append(f"**类型:** {analysis.get('content_type', '-')}")
            topics = analysis.get("main_topics", [])
            if topics:
                lines.append(f"**话题:** {', '.join(topics)}")
            keywords = analysis.get("keywords", [])
            if keywords:
                lines.append(f"**关键词:** {', '.join(keywords)}")

    return "\n".join(lines)


def view_task_output(task_id_prefix: str) -> tuple[str, str, str]:
    """Load transcript, polished transcript, and summary from task output dir."""
    if not task_id_prefix:
        return "", "", ""

    store = get_task_store()
    tasks = store.list(limit=200)
    matches = [t for t in tasks if str(t.id).startswith(task_id_prefix.strip())]
    if not matches or not matches[0].result:
        return "", "", ""

    output_dir = Path(matches[0].result.get("output_dir", ""))
    if not output_dir.exists():
        return "", "", ""

    def _read(name: str) -> str:
        p = output_dir / name
        if p.exists():
            return p.read_text(encoding="utf-8")
        return ""

    srt = _read("transcript.srt")
    polished = _read("transcript_polished.md")
    summary = _read("summary.md")

    return srt, polished, summary


async def cancel_task(task_id_prefix: str) -> str:
    if not task_id_prefix:
        return "请输入任务 ID"
    store = get_task_store()
    tasks = store.list(limit=200)
    matches = [t for t in tasks if str(t.id).startswith(task_id_prefix.strip())]
    if not matches:
        return f"未找到 {task_id_prefix}"

    queue = get_task_queue()
    ok = await queue.cancel(matches[0].id)
    return f"已取消 {str(matches[0].id)[:8]}" if ok else f"无法取消 (状态: {matches[0].status})"


def load_settings() -> dict:
    rt = get_runtime_settings()
    return rt.model_dump()


def save_setting(key: str, value: str) -> str:
    if not key:
        return "请选择设置项"
    # Type coercion
    if value.lower() in ("true", "false"):
        typed: Any = value.lower() == "true"
    else:
        try:
            typed = int(value)
        except ValueError:
            try:
                typed = float(value)
            except ValueError:
                typed = value
    patch_runtime_settings({key: typed})
    return f"已保存: {key} = {typed}"


# ---------------------------------------------------------------------------
# Build Gradio UI
# ---------------------------------------------------------------------------

TASK_TABLE_HEADERS = ["ID", "状态", "来源", "进度", "步骤", "创建时间", "耗时"]

# Settings that are commonly edited
SETTING_GROUPS = {
    "ASR": ["asr_backend", "qwen3_device", "qwen3_asr_model_path", "whisper_model", "whisper_device"],
    "LLM": ["llm_provider", "custom_name", "custom_model", "custom_api_base", "anthropic_model", "openai_model"],
    "处理": ["uvr_model", "uvr_device", "enable_diarization", "enable_alignment"],
    "路径": ["data_root", "obsidian_vault_path", "uvr_model_dir"],
}


def create_ui() -> gr.Blocks:
    with gr.Blocks(title="MediaProcessPipeline") as demo:
        gr.Markdown("# MediaProcessPipeline")

        with gr.Tabs() as tabs:
            # ---------------------------------------------------------------
            # Tab 1: 处理
            # ---------------------------------------------------------------
            with gr.TabItem("处理", id="process"):
                stats_md = gr.Markdown(get_stats())

                with gr.Row():
                    source_input = gr.Textbox(
                        placeholder="粘贴视频链接或本地文件路径",
                        label="媒体来源",
                        scale=5,
                    )
                    skip_sep = gr.Checkbox(label="跳过人声分离", value=False, scale=1)
                    submit_btn = gr.Button("开始处理", variant="primary", scale=1)

                submit_status = gr.Markdown("")

                gr.Markdown("### 活跃任务")
                queue_status = gr.Markdown("队列: 0 等待  |  空闲")
                active_table = gr.Dataframe(
                    headers=TASK_TABLE_HEADERS,
                    datatype=["str"] * 7,
                    interactive=False,
                    elem_classes="task-table",
                )

                refresh_btn = gr.Button("刷新", size="sm")

                # Wiring
                submit_btn.click(
                    fn=submit_task,
                    inputs=[source_input, skip_sep],
                    outputs=submit_status,
                ).then(
                    fn=lambda: "",
                    outputs=source_input,
                ).then(
                    fn=refresh_active,
                    outputs=[active_table, queue_status],
                ).then(
                    fn=get_stats,
                    outputs=stats_md,
                )

                refresh_btn.click(
                    fn=refresh_active,
                    outputs=[active_table, queue_status],
                ).then(
                    fn=get_stats,
                    outputs=stats_md,
                )

                # Auto-refresh every 3 seconds via Timer
                timer = gr.Timer(value=3)
                timer.tick(
                    fn=refresh_active,
                    outputs=[active_table, queue_status],
                )

            # ---------------------------------------------------------------
            # Tab 2: 历史
            # ---------------------------------------------------------------
            with gr.TabItem("历史", id="history"):
                history_table = gr.Dataframe(
                    headers=TASK_TABLE_HEADERS,
                    datatype=["str"] * 7,
                    interactive=False,
                    elem_classes="task-table",
                )
                refresh_history_btn = gr.Button("刷新", size="sm")
                refresh_history_btn.click(fn=refresh_tasks, outputs=history_table)
                demo.load(fn=refresh_tasks, outputs=history_table)

                gr.Markdown("---")
                gr.Markdown("### 任务详情")
                with gr.Row():
                    detail_input = gr.Textbox(
                        placeholder="输入任务 ID 前缀",
                        label="任务 ID",
                        scale=3,
                    )
                    detail_btn = gr.Button("查看", scale=1)
                    cancel_btn = gr.Button("取消任务", variant="stop", scale=1)

                detail_md = gr.Markdown("")
                cancel_status = gr.Markdown("")

                detail_btn.click(fn=get_task_detail, inputs=detail_input, outputs=detail_md)
                cancel_btn.click(fn=cancel_task, inputs=detail_input, outputs=cancel_status)

            # ---------------------------------------------------------------
            # Tab 3: 查看结果
            # ---------------------------------------------------------------
            with gr.TabItem("结果", id="results"):
                with gr.Row():
                    result_task_id = gr.Textbox(
                        placeholder="输入已完成任务的 ID 前缀",
                        label="任务 ID",
                        scale=3,
                    )
                    load_result_btn = gr.Button("加载", variant="primary", scale=1)

                with gr.Tabs():
                    with gr.TabItem("摘要"):
                        summary_md = gr.Markdown("")
                    with gr.TabItem("润色字幕"):
                        polished_md = gr.Markdown("")
                    with gr.TabItem("原始转录 (SRT)"):
                        srt_text = gr.Textbox(
                            label="SRT",
                            lines=20,
                            interactive=False,
                        )

                load_result_btn.click(
                    fn=view_task_output,
                    inputs=result_task_id,
                    outputs=[srt_text, polished_md, summary_md],
                )

            # ---------------------------------------------------------------
            # Tab 4: 设置
            # ---------------------------------------------------------------
            with gr.TabItem("设置", id="settings"):
                settings_state = gr.State({})

                def render_settings():
                    s = load_settings()
                    rows = []
                    for group, keys in SETTING_GROUPS.items():
                        for k in keys:
                            v = s.get(k, "")
                            display = str(v)
                            if "api_key" in k and v:
                                display = display[:8] + "..." if len(display) > 8 else "***"
                            rows.append([group, k, display])
                    return rows

                gr.Markdown("### 常用设置")
                settings_table = gr.Dataframe(
                    headers=["组", "Key", "Value"],
                    datatype=["str", "str", "str"],
                    interactive=False,
                )

                with gr.Row():
                    setting_key = gr.Textbox(label="Key", placeholder="e.g. asr_backend", scale=2)
                    setting_value = gr.Textbox(label="Value", placeholder="e.g. qwen3", scale=2)
                    save_btn = gr.Button("保存", variant="primary", scale=1)

                save_status = gr.Markdown("")

                save_btn.click(
                    fn=save_setting,
                    inputs=[setting_key, setting_value],
                    outputs=save_status,
                ).then(
                    fn=render_settings,
                    outputs=settings_table,
                )

                gr.Markdown("### 全部设置")
                all_settings_json = gr.JSON(label="settings.json")

                def load_all():
                    return render_settings(), load_settings()

                demo.load(fn=load_all, outputs=[settings_table, all_settings_json])

    return demo
