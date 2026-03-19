"""Gradio UI — single-file frontend for MediaProcessPipeline.

Mounted into FastAPI at /ui via gr.mount_gradio_app().
Talks to the same daemon via internal imports (no HTTP round-trip).
"""

from __future__ import annotations

import json
import shutil
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
STEP_IDS = [s["id"] for s in PIPELINE_STEPS]

MEDIA_EXTENSIONS = (
    ".mp4", ".mkv", ".avi", ".webm", ".mov",
    ".mp3", ".wav", ".flac", ".m4a", ".ogg",
)


def _fmt_time(dt_or_iso) -> str:
    if not dt_or_iso:
        return "-"
    if isinstance(dt_or_iso, datetime):
        return dt_or_iso.strftime("%m-%d %H:%M")
    try:
        return datetime.fromisoformat(str(dt_or_iso)).strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(dt_or_iso)


def _fmt_duration(created, completed) -> str:
    if not created or not completed:
        return "-"
    try:
        c = created if isinstance(created, datetime) else datetime.fromisoformat(str(created))
        d = completed if isinstance(completed, datetime) else datetime.fromisoformat(str(completed))
        secs = (d - c).total_seconds()
        if secs < 60:
            return f"{secs:.0f}s"
        return f"{secs / 60:.1f}m"
    except (ValueError, TypeError):
        return "-"


def _status_icon(status: str) -> str:
    return {"queued": "🟡", "processing": "🔵", "completed": "🟢",
            "failed": "🔴", "cancelled": "⚪"}.get(status, "⚪")


def _step_progress_text(task: Task) -> str:
    """Build a step-by-step progress line like: ✓下载 ✓分离 ▶转录 ○分析 ○润色 ○摘要 ○归档"""
    parts = []
    for sid in STEP_IDS:
        name = STEP_NAMES[sid]
        if sid in (task.completed_steps or []):
            parts.append(f"✓{name}")
        elif task.current_step == sid:
            parts.append(f"▶{name}")
        else:
            parts.append(f"○{name}")
    return "  ".join(parts)


def _task_to_row(t: Task) -> list:
    source = t.source
    if len(source) > 45:
        source = "..." + source[-42:]
    return [
        str(t.id)[:8],
        f"{_status_icon(t.status)} {t.status}",
        source,
        f"{int(t.progress * 100)}%",
        _step_progress_text(t) if t.status in ("processing", "queued") else (STEP_NAMES.get(t.current_step, "-") if t.current_step else "-"),
        _fmt_time(t.created_at),
        _fmt_duration(t.created_at, t.completed_at),
    ]


# ---------------------------------------------------------------------------
# Core actions
# ---------------------------------------------------------------------------

async def submit_from_text(source: str, skip_separation: bool) -> str:
    """Submit from URL/path text input."""
    if not source or not source.strip():
        return "⚠ 请输入媒体文件路径或 URL"
    return await _submit(source.strip(), skip_separation)


async def submit_from_file(file, skip_separation: bool) -> str:
    """Submit from uploaded file."""
    if file is None:
        return "⚠ 请选择文件"

    # file is a filepath string from gr.File
    file_path = Path(file)
    if not file_path.exists():
        return f"⚠ 文件不存在: {file}"

    # Copy to uploads dir so pipeline can find it
    rt = get_runtime_settings()
    upload_dir = Path(rt.data_root).resolve() / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / file_path.name
    if not dest.exists():
        shutil.copy2(file_path, dest)

    return await _submit(str(dest), skip_separation)


async def _submit(source: str, skip_separation: bool) -> str:
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

    return f"✓ 已提交任务 `{str(task.id)[:8]}`  — {source[:60]}"


def refresh_tasks() -> list[list]:
    store = get_task_store()
    return [_task_to_row(t) for t in store.list(limit=50)]


def refresh_active() -> tuple[str, list[list], str]:
    """Returns: (stats_md, active_rows, progress_detail_md)"""
    store = get_task_store()
    queue = get_task_queue()

    # Stats
    s = store.stats()
    stats = (
        f"**全部** {s.get('total', 0)}　"
        f"🟢 {s.get('completed', 0)}　"
        f"🔵 {s.get('processing', 0)}　"
        f"🟡 {s.get('queued', 0)}　"
        f"🔴 {s.get('failed', 0)}"
    )

    # Active table
    active = store.list_by_statuses([TaskStatus.QUEUED, TaskStatus.PROCESSING])
    rows = [_task_to_row(t) for t in active]

    # Detailed progress for current task
    current_id = queue.current_task_id
    if current_id:
        current = store.get(current_id)
        if current:
            pct = int(current.progress * 100)
            filled = pct // 5
            bar = "█" * filled + "░" * (20 - filled)

            src = current.source
            if len(src) > 60:
                src = "..." + src[-57:]

            detail = (
                f"### ▶ 正在处理\n\n"
                f"**{src}**\n\n"
                f"`{bar}` **{pct}%**\n\n"
                f"{_step_progress_text(current)}\n\n"
                f"*已运行 {_fmt_duration(current.created_at, datetime.now())}*"
            )
        else:
            detail = "*空闲*"
    else:
        pending = queue.pending_count
        if pending > 0:
            detail = f"*队列中有 {pending} 个任务等待处理*"
        else:
            detail = "*空闲 — 提交任务开始处理*"

    return stats, rows, detail


def get_task_detail(task_id_prefix: str) -> str:
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
        f"**状态:** {t.status}　**进度:** {int(t.progress * 100)}%",
        f"**步骤:** {_step_progress_text(t)}",
        f"**创建:** {_fmt_time(t.created_at)}　**耗时:** {_fmt_duration(t.created_at, t.completed_at)}",
    ]

    if t.error:
        lines.append(f"\n> ❌ **错误:** {t.error}")

    if t.result:
        output_dir = t.result.get("output_dir", "")
        if output_dir:
            lines.append(f"\n**输出目录:** `{output_dir}`")

        analysis = t.result.get("analysis", {})
        if analysis:
            lines.append(f"\n| 字段 | 值 |")
            lines.append(f"|---|---|")
            lines.append(f"| 语言 | {analysis.get('language', '-')} |")
            lines.append(f"| 类型 | {analysis.get('content_type', '-')} |")
            topics = analysis.get("main_topics", [])
            if topics:
                lines.append(f"| 话题 | {', '.join(topics)} |")
            keywords = analysis.get("keywords", [])
            if keywords:
                lines.append(f"| 关键词 | {', '.join(keywords)} |")
            nouns = analysis.get("proper_nouns", [])
            if nouns:
                lines.append(f"| 专有名词 | {', '.join(nouns)} |")

    return "\n".join(lines)


def view_task_output(task_id_prefix: str) -> tuple[str, str, str]:
    if not task_id_prefix:
        return "", "", ""

    store = get_task_store()
    tasks = store.list(limit=200)
    matches = [t for t in tasks if str(t.id).startswith(task_id_prefix.strip())]
    if not matches or not matches[0].result:
        return "未找到结果", "", ""

    output_dir = Path(matches[0].result.get("output_dir", ""))
    if not output_dir.exists():
        return f"输出目录不存在: {output_dir}", "", ""

    def _read(name: str) -> str:
        p = output_dir / name
        return p.read_text(encoding="utf-8") if p.exists() else ""

    # Also try wildcard for polished SRT
    polished = _read("transcript_polished.md")
    if not polished:
        for f in output_dir.glob("*polished*"):
            polished = f.read_text(encoding="utf-8")
            break

    return _read("summary.md"), polished, _read("transcript.srt")


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
    return f"✓ 已取消 {str(matches[0].id)[:8]}" if ok else f"⚠ 无法取消 (状态: {matches[0].status})"


def save_setting(key: str, value: str) -> str:
    if not key:
        return "请选择设置项"
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
    return f"✓ {key} = {typed}"


# ---------------------------------------------------------------------------
# Build Gradio UI
# ---------------------------------------------------------------------------

TASK_HEADERS = ["ID", "状态", "来源", "进度", "步骤", "创建", "耗时"]

SETTING_GROUPS = {
    "ASR": ["asr_backend", "qwen3_device", "qwen3_asr_model_path", "whisper_model", "whisper_device"],
    "LLM": ["llm_provider", "custom_name", "custom_model", "custom_api_base", "anthropic_model", "openai_model"],
    "处理": ["uvr_model", "uvr_device", "enable_diarization", "enable_alignment"],
    "路径": ["data_root", "obsidian_vault_path", "uvr_model_dir"],
}


def _render_settings():
    s = get_runtime_settings().model_dump()
    rows = []
    for group, keys in SETTING_GROUPS.items():
        for k in keys:
            v = s.get(k, "")
            display = str(v)
            if "api_key" in k and v:
                display = display[:8] + "..." if len(display) > 8 else "***"
            rows.append([group, k, display])
    return rows


def create_ui() -> gr.Blocks:
    with gr.Blocks(title="MediaProcessPipeline") as demo:
        gr.Markdown("# MediaProcessPipeline")

        with gr.Tabs():
            # ---------------------------------------------------------------
            # Tab 1: 处理
            # ---------------------------------------------------------------
            with gr.TabItem("处理", id="process"):
                stats_md = gr.Markdown("loading...")

                # --- Input area ---
                gr.Markdown("### 提交任务")
                with gr.Row():
                    source_input = gr.Textbox(
                        placeholder="粘贴视频链接或本地文件路径，回车提交",
                        label="URL / 路径",
                        scale=5,
                    )
                    submit_btn = gr.Button("提交", variant="primary", scale=1)

                with gr.Row():
                    file_input = gr.File(
                        label="或拖拽上传文件",
                        file_types=[f"*{ext}" for ext in MEDIA_EXTENSIONS],
                        scale=5,
                    )
                    upload_btn = gr.Button("上传并处理", variant="primary", scale=1)

                with gr.Row():
                    skip_sep = gr.Checkbox(label="跳过人声分离", value=False)

                submit_status = gr.Markdown("")

                # --- Progress detail ---
                progress_md = gr.Markdown("*空闲*")

                # --- Active tasks ---
                gr.Markdown("### 队列")
                active_table = gr.Dataframe(
                    headers=TASK_HEADERS,
                    datatype=["str"] * 7,
                    interactive=False,
                )

                # --- Wiring ---
                submit_btn.click(
                    fn=submit_from_text, inputs=[source_input, skip_sep], outputs=submit_status,
                ).then(fn=lambda: "", outputs=source_input)

                source_input.submit(
                    fn=submit_from_text, inputs=[source_input, skip_sep], outputs=submit_status,
                ).then(fn=lambda: "", outputs=source_input)

                upload_btn.click(
                    fn=submit_from_file, inputs=[file_input, skip_sep], outputs=submit_status,
                ).then(fn=lambda: None, outputs=file_input)

                # Auto-refresh
                timer = gr.Timer(value=2)
                timer.tick(fn=refresh_active, outputs=[stats_md, active_table, progress_md])

            # ---------------------------------------------------------------
            # Tab 2: 历史
            # ---------------------------------------------------------------
            with gr.TabItem("历史", id="history"):
                history_table = gr.Dataframe(
                    headers=TASK_HEADERS,
                    datatype=["str"] * 7,
                    interactive=False,
                )
                refresh_history_btn = gr.Button("刷新", size="sm")
                refresh_history_btn.click(fn=refresh_tasks, outputs=history_table)
                demo.load(fn=refresh_tasks, outputs=history_table)

                gr.Markdown("---")
                with gr.Row():
                    detail_input = gr.Textbox(placeholder="任务 ID 前缀", label="任务 ID", scale=3)
                    detail_btn = gr.Button("查看详情", scale=1)
                    cancel_btn = gr.Button("取消任务", variant="stop", scale=1)

                detail_md = gr.Markdown("")
                cancel_status = gr.Markdown("")

                detail_btn.click(fn=get_task_detail, inputs=detail_input, outputs=detail_md)
                cancel_btn.click(fn=cancel_task, inputs=detail_input, outputs=cancel_status)

            # ---------------------------------------------------------------
            # Tab 3: 结果
            # ---------------------------------------------------------------
            with gr.TabItem("结果", id="results"):
                with gr.Row():
                    result_task_id = gr.Textbox(placeholder="已完成任务 ID 前缀", label="任务 ID", scale=3)
                    load_result_btn = gr.Button("加载结果", variant="primary", scale=1)

                with gr.Tabs():
                    with gr.TabItem("摘要"):
                        summary_md = gr.Markdown("")
                    with gr.TabItem("润色字幕"):
                        polished_md = gr.Markdown("")
                    with gr.TabItem("原始 SRT"):
                        srt_text = gr.Textbox(label="SRT", lines=20, interactive=False)

                load_result_btn.click(
                    fn=view_task_output,
                    inputs=result_task_id,
                    outputs=[summary_md, polished_md, srt_text],
                )

            # ---------------------------------------------------------------
            # Tab 4: 设置
            # ---------------------------------------------------------------
            with gr.TabItem("设置", id="settings"):
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
                    fn=save_setting, inputs=[setting_key, setting_value], outputs=save_status,
                ).then(fn=_render_settings, outputs=settings_table)

                gr.Markdown("### 全部设置 (JSON)")
                all_settings_json = gr.JSON(label="settings.json")

                def _load_all():
                    return _render_settings(), get_runtime_settings().model_dump()

                demo.load(fn=_load_all, outputs=[settings_table, all_settings_json])

    return demo
