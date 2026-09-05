"""Direct execution for the mpp CLI."""

from __future__ import annotations

import typer
from app.cli.context import get_cli_context as _command_context


def _build_options(
    no_sep: bool = False,
    speakers: int | None = None,
    hotwords: str | None = None,
    force_asr: bool = False,
    api_flow: bool = False,
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
    if api_flow:
        opts["api_flow"] = True
        opts["skip_separation"] = True
        opts["asr_provider"] = "siliconflow"
        opts["asr_chunk_strategy"] = "ffmpeg"
        opts["disable_diarization"] = True
        opts["disable_voiceprint"] = True
    return opts


def _run_direct(source: str, options: dict, quiet: bool = False) -> dict:
    """Run a pipeline task in the current CLI process without uvicorn/daemon."""
    import asyncio

    return asyncio.run(_run_direct_async(source, options=options, quiet=quiet))


async def _run_direct_async(source: str, options: dict, quiet: bool = False) -> dict:
    import asyncio

    from app.cli.display import console
    from app.core.database import close_db, get_task_store, init_db
    from app.core.events import TaskEvent, get_event_bus
    from app.core.pipeline import process_task

    _apply_direct_runtime_overrides(options)
    init_db()

    task = _create_direct_task(source, options)
    bus = get_event_bus()
    q = bus.subscribe_task(task.id)

    if not quiet and not (_command_context().output_mode in {"json", "jsonl"}):
        console.print(f"direct  [bold]{str(task.id)[:8]}[/bold]")

    await bus.publish(TaskEvent(task.id, "queued"))
    runner = asyncio.create_task(process_task(task.id, _download_worker_call=False))

    try:
        await _stream_direct_events(q, runner, quiet=quiet)
        await runner
        final = get_task_store().get(task.id)
        return final.model_dump(mode="json") if final else task.model_dump(mode="json")
    finally:
        await bus.unsubscribe_task(task.id, q)
        close_db()


def _apply_direct_runtime_overrides(options: dict) -> None:
    """Apply process-local runtime settings for one-shot direct flows."""
    if not options.get("api_flow"):
        return

    from app.cli.display import console
    from app.core.settings import get_runtime_settings, replace_runtime_settings_for_process

    rt = get_runtime_settings()
    if rt.llm_provider == "local":
        console.print(
            "[red]--api-flow 要求 LLM 也走 API。请先把 llm_provider 设为 "
            "anthropic/openai/deepseek/custom。[/red]"
        )
        raise typer.Exit(1)

    updates = {
        "asr_provider": "siliconflow",
        "siliconflow_asr_chunk_strategy": "ffmpeg",
        "enable_diarization": False,
        "enable_voiceprint": False,
    }
    if rt.polish_provider == "local":
        # Empty means "follow llm_provider"; keeps API flow from loading local HF.
        updates["polish_provider"] = ""

    replace_runtime_settings_for_process(rt.model_copy(update=updates))


def _create_direct_task(source: str, options: dict):
    from pathlib import Path

    from app.core.database import get_task_store
    from app.core.pipeline import (
        PIPELINE_STEPS,
        PipelineStep,
        _clean_source_path,
        _looks_like_local_path,
        create_task_dir,
        write_metadata_json,
    )
    from app.models import Task, TaskStatus, TaskType

    clean_source = _clean_source_path(source)
    task = Task(
        task_type=TaskType.PIPELINE,
        source=clean_source,
        options=options,
        status=TaskStatus.QUEUED,
        current_step=PipelineStep.DOWNLOAD,
        message="等待处理...",
        steps=[s["id"] for s in PIPELINE_STEPS],
        completed_steps=[],
    )

    if _looks_like_local_path(clean_source):
        path = Path(clean_source)
        title = path.stem
        media_type = (
            "video" if path.suffix.lower() in {".mp4", ".mkv", ".avi", ".webm", ".mov"} else "audio"
        )
    else:
        title = str(task.id)
        media_type = "unknown"

    task_dir = create_task_dir(task.id, title)
    write_metadata_json(
        task_dir,
        {
            "title": title,
            "source_url": clean_source,
            "media_type": media_type,
        },
        status="queued",
    )
    task.result = {"output_dir": str(task_dir)}

    store = get_task_store()
    store.save(task)
    return task


async def _stream_direct_events(q, runner, quiet: bool = False) -> None:
    if quiet or (_command_context().output_mode in {"json", "jsonl"}):
        await runner
        return

    import asyncio
    import time as _time

    from app.cli.display import STEP_LABELS, console

    ok_char = "+" if _command_context().plain else "✓"
    err_char = "x" if _command_context().plain else "✗"
    run_char = ">" if _command_context().plain else "▶"
    started: dict[str, float] = {}
    printed: set[str] = set()

    def _label(step) -> str:
        key = str(step)
        return STEP_LABELS.get(key, key)

    while True:
        if runner.done() and q.empty():
            return
        try:
            event = await asyncio.wait_for(q.get(), timeout=0.2)
        except asyncio.TimeoutError:
            continue

        etype = event.event_type
        data = event.data or {}

        if etype == "step":
            step = str(data.get("step", ""))
            completed = bool(data.get("completed", False))
            if not step:
                continue
            if completed:
                if step in printed:
                    continue
                elapsed = _time.monotonic() - started.get(step, _time.monotonic())
                console.print(
                    f"  [green]{ok_char}[/green] {_label(step)}  [dim]{elapsed:.1f}s[/dim]"
                )
                printed.add(step)
            elif step not in started and step not in printed:
                started[step] = _time.monotonic()
                console.print(f"  [blue]{run_char}[/blue] {_label(step)}")
        elif etype == "failed":
            console.print(f"  [red]{err_char}[/red] 失败: {data.get('error', '')}")
            return
        elif etype in ("completed", "cancelled"):
            return
