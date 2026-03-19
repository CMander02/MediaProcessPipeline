"""Gradio UI — single-file frontend for MediaProcessPipeline.

Mounted into FastAPI at /ui via gr.mount_gradio_app().
Talks to the same daemon via internal imports (no HTTP round-trip).
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import gradio as gr

from app.core.database import get_task_store
from app.core.queue import get_task_queue
from app.core.settings import get_runtime_settings, patch_runtime_settings
from app.core.pipeline import PIPELINE_STEPS, PipelineStep
from app.models.task import Task, TaskStatus, TaskType


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


def _get_completed_choices():
    """Return gr.update with choices for completed tasks dropdown."""
    store = get_task_store()
    tasks = store.list(status="completed", limit=30)
    choices = []
    for t in tasks:
        src = t.source
        if len(src) > 50:
            src = "..." + src[-47:]
        choices.append(f"{str(t.id)[:8]} | {src}")
    if not choices:
        return gr.update(choices=[], value=None)
    return gr.update(choices=choices, value=None)


def _get_recent_completed(n: int = 3) -> str:
    """Return markdown for the last N completed tasks."""
    store = get_task_store()
    tasks = store.list(status="completed", limit=n)
    if not tasks:
        return "*暂无已完成任务*"
    lines = []
    for t in tasks:
        src = t.source
        if len(src) > 50:
            src = "..." + src[-47:]
        tid = str(t.id)[:8]
        output_dir = ""
        if t.result:
            output_dir = t.result.get("output_dir", "")
        time_str = _fmt_time(t.completed_at or t.updated_at)
        dur = _fmt_duration(t.created_at, t.completed_at)
        line = f"- 🟢 **`{tid}`** {src} — {time_str} ({dur})"
        if output_dir:
            line += f"  `{output_dir}`"
        lines.append(line)
    return "\n".join(lines)


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

    if file_path.suffix.lower() not in MEDIA_EXTENSIONS:
        return f"⚠ 不支持的格式: {file_path.suffix}（支持 {', '.join(MEDIA_EXTENSIONS)}）"

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
    rows = [_task_to_row(t) for t in store.list(limit=50)]
    if not rows:
        return [["", "暂无任务记录，提交任务后将在此显示", "", "", "", "", ""]]
    return rows


def refresh_active() -> tuple[str, list[list], str, str]:
    """Returns: (stats_md, active_rows, progress_detail_md, recently_completed_md)"""
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
    if active:
        rows = [_task_to_row(t) for t in active]
    else:
        rows = [["", "队列空闲，等待新任务...", "", "", "", "", ""]]

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

    # Recently completed
    recent = _get_recent_completed(3)

    return stats, rows, detail, recent


def get_task_detail(task_id_prefix: str) -> str:
    if not task_id_prefix:
        return "输入任务 ID 前缀或从历史列表点击 ID 查看详情"

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
        return "*选择一个已完成的任务查看结果*", "", ""

    # If the input comes from the dropdown, extract the ID prefix
    if " | " in task_id_prefix:
        task_id_prefix = task_id_prefix.split(" | ")[0].strip()

    store = get_task_store()
    tasks = store.list(limit=200)
    matches = [t for t in tasks if str(t.id).startswith(task_id_prefix.strip())]
    if not matches or not matches[0].result:
        return f"未找到 `{task_id_prefix}` 的结果", "", ""

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

    summary = _read("summary.md")
    srt = _read("transcript.srt")

    if not summary and not polished and not srt:
        return "*输出目录存在但未找到结果文件*", "", ""

    return summary, polished, srt


def view_from_dropdown(choice: str) -> tuple[str, str, str]:
    """Load results from the completed-tasks dropdown."""
    if not choice:
        return "*从下拉列表选择一个已完成的任务*", "", ""
    task_id = choice.split(" | ")[0].strip()
    return view_task_output(task_id)


def history_select_and_detail(evt: gr.SelectData, table_data) -> tuple[str, str]:
    """When user clicks a cell in history table, extract task ID and show detail.

    Returns (task_id_prefix, detail_markdown).
    """
    if evt.index is not None and table_data is not None:
        row_idx = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
        try:
            # Get the ID from the first column of the clicked row
            if hasattr(table_data, "values"):
                # pandas DataFrame
                row = table_data.values[row_idx]
            elif isinstance(table_data, list) and len(table_data) > row_idx:
                row = table_data[row_idx]
            else:
                return "", ""
            task_id = str(row[0]).strip()
            if task_id:
                return task_id, get_task_detail(task_id)
        except (IndexError, KeyError, TypeError):
            pass
    return "", ""


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


def switch_asr_backend(backend: str) -> tuple[str, list[list], Any]:
    """Switch ASR backend via radio button."""
    patch_runtime_settings({"asr_backend": backend})
    return f"✓ ASR 后端已切换为 **{backend}**", _render_settings(), get_runtime_settings().model_dump()


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


def _get_current_asr() -> str:
    return get_runtime_settings().asr_backend


def create_ui() -> gr.Blocks:
    with gr.Blocks(title="MediaProcessPipeline") as demo:
        gr.Markdown("# MediaProcessPipeline\n媒体处理管线 — 将音视频转化为结构化知识")

        with gr.Tabs() as tabs:
            # ---------------------------------------------------------------
            # Tab 1: 处理
            # ---------------------------------------------------------------
            with gr.TabItem("处理", id="process"):
                stats_md = gr.Markdown("loading...")

                # --- Input area (compact) ---
                with gr.Accordion("提交新任务", open=True):
                    with gr.Row():
                        source_input = gr.Textbox(
                            placeholder="粘贴视频链接或本地文件路径，回车提交",
                            label="URL / 路径",
                            scale=5,
                        )
                        submit_btn = gr.Button("提交", variant="primary", scale=1)
                    with gr.Accordion("上传文件 / 高级选项", open=False):
                        with gr.Row():
                            file_input = gr.File(
                                label="拖拽上传文件",
                                file_count="single",
                                scale=5,
                            )
                            upload_btn = gr.Button("上传并处理", variant="primary", scale=1)
                        skip_sep = gr.Checkbox(label="跳过人声分离", value=False)

                submit_status = gr.Markdown("")

                # --- Progress detail ---
                progress_md = gr.Markdown("*空闲*")

                # --- Active tasks ---
                active_table = gr.Dataframe(
                    headers=TASK_HEADERS,
                    datatype=["str"] * 7,
                    interactive=False,
                    label="队列",
                )

                # --- Recently completed ---
                with gr.Accordion("最近完成", open=True):
                    recent_md = gr.Markdown("*暂无已完成任务*")

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

                # Auto-refresh (now also updates recent_md)
                timer = gr.Timer(value=2)
                timer.tick(fn=refresh_active, outputs=[stats_md, active_table, progress_md, recent_md])

            # ---------------------------------------------------------------
            # Tab 2: 历史
            # ---------------------------------------------------------------
            with gr.TabItem("历史", id="history"):
                gr.Markdown("*点击表格中的任务 ID（第一列）可自动填入下方查看详情*", elem_id="history-hint")
                history_table = gr.Dataframe(
                    headers=TASK_HEADERS,
                    datatype=["str"] * 7,
                    interactive=False,
                    label="任务历史",
                )
                refresh_history_btn = gr.Button("刷新", size="sm")
                refresh_history_btn.click(fn=refresh_tasks, outputs=history_table)
                demo.load(fn=refresh_tasks, outputs=history_table)

                with gr.Row():
                    detail_input = gr.Textbox(placeholder="任务 ID 前缀（点击上方表格自动填入）", label="任务 ID", scale=3)
                    detail_btn = gr.Button("查看详情", scale=1)
                    cancel_btn = gr.Button("取消任务", variant="stop", scale=1)

                detail_md = gr.Markdown("输入任务 ID 前缀或从历史列表点击 ID 查看详情")
                cancel_status = gr.Markdown("")

                # Click on history table -> auto-fill detail input and show detail
                history_table.select(
                    fn=history_select_and_detail,
                    inputs=history_table,
                    outputs=[detail_input, detail_md],
                )

                detail_btn.click(fn=get_task_detail, inputs=detail_input, outputs=detail_md)
                detail_input.submit(fn=get_task_detail, inputs=detail_input, outputs=detail_md)
                cancel_btn.click(fn=cancel_task, inputs=detail_input, outputs=cancel_status)

            # ---------------------------------------------------------------
            # Tab 3: 结果
            # ---------------------------------------------------------------
            with gr.TabItem("结果", id="results"):
                with gr.Row():
                    result_dropdown = gr.Dropdown(
                        choices=[],
                        label="已完成任务",
                        allow_custom_value=True,
                        scale=4,
                    )
                    refresh_results_btn = gr.Button("刷新列表", size="sm", scale=1)

                # Populate dropdown on load and on refresh
                refresh_results_btn.click(
                    fn=_get_completed_choices,
                    outputs=result_dropdown,
                )
                demo.load(fn=_get_completed_choices, outputs=result_dropdown)

                with gr.Tabs():
                    with gr.TabItem("摘要"):
                        summary_md = gr.Markdown("*选择一个已完成的任务查看结果*")
                    with gr.TabItem("润色字幕"):
                        polished_md = gr.Markdown("")
                    with gr.TabItem("原始 SRT"):
                        srt_text = gr.Textbox(label="SRT", lines=20, interactive=False)

                # Load results when dropdown selection changes
                result_dropdown.change(
                    fn=view_from_dropdown,
                    inputs=result_dropdown,
                    outputs=[summary_md, polished_md, srt_text],
                )

            # ---------------------------------------------------------------
            # Tab 4: 设置
            # ---------------------------------------------------------------
            with gr.TabItem("设置", id="settings"):
                # Quick ASR toggle at top
                gr.Markdown("### ASR 后端")
                with gr.Row():
                    asr_radio = gr.Radio(
                        choices=["qwen3", "whisperx"],
                        value=_get_current_asr,
                        label="当前 ASR 后端",
                        interactive=True,
                        scale=3,
                    )
                    asr_status = gr.Markdown("", scale=2)

                with gr.Accordion("所有设置", open=True):
                    settings_table = gr.Dataframe(
                        headers=["组", "Key", "Value"],
                        datatype=["str", "str", "str"],
                        interactive=False,
                        label="设置一览",
                    )

                    with gr.Row():
                        setting_key = gr.Textbox(label="Key", placeholder="e.g. asr_backend", scale=2)
                        setting_value = gr.Textbox(label="Value", placeholder="e.g. qwen3", scale=2)
                        save_btn = gr.Button("保存", variant="primary", scale=1)

                    save_status = gr.Markdown("")

                with gr.Accordion("全部设置 (JSON)", open=False):
                    all_settings_json = gr.JSON(label="settings.json")

                # ASR radio wiring
                asr_radio.change(
                    fn=switch_asr_backend,
                    inputs=asr_radio,
                    outputs=[asr_status, settings_table, all_settings_json],
                )

                save_btn.click(
                    fn=save_setting, inputs=[setting_key, setting_value], outputs=save_status,
                ).then(fn=_render_settings, outputs=settings_table)

                def _load_all():
                    return _render_settings(), get_runtime_settings().model_dump()

                demo.load(fn=_load_all, outputs=[settings_table, all_settings_json])

    return demo
