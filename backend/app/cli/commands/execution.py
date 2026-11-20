"""Execution for the mpp CLI."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer

from app.cli.commands.direct_execution import _run_direct
from app.cli.commands.root_support import _require_daemon, _resolve_ref
from app.cli.context import get_cli_context as _command_context
from app.cli.submission import (
    ImportOptions,
    batch_text,
    expand_sources,
    submit_to_servers,
    task_options,
)


def run(
    sources: list[str] = typer.Argument(None, help="媒体文件、目录、glob 或 URL，可指定多个"),
    no_sep: bool = typer.Option(False, "--no-sep", "--skip-separation", help="跳过人声分离"),
    speakers: int = typer.Option(None, "--speakers", "-s", help="说话人数量（留空自动检测）"),
    hotwords: str = typer.Option(None, "--hotwords", "-w", help="热词，逗号分隔"),
    hotword: list[str] = typer.Option(None, "--hotword", help="热词，可重复"),
    force_asr: bool = typer.Option(False, "--force-asr", help="强制 ASR，忽略平台字幕"),
    prefer_subtitles: bool = typer.Option(False, "--prefer-subtitles", help="优先使用平台字幕"),
    from_file: Optional[Path] = typer.Option(
        None, "--from-file", exists=True, dir_okay=False, readable=True
    ),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="递归展开目录和 glob"),
    upload: str = typer.Option("auto", "--upload", help="auto/always/never"),
    collection: Optional[str] = typer.Option(None, "--collection", help="all 或合集条目 ID 列表"),
    folder: Optional[str] = typer.Option(None, "--folder"),
    move_existing: bool = typer.Option(False, "--move-existing"),
    collection_folder: bool = typer.Option(False, "--collection-folder"),
    force_duplicate: bool = typer.Option(False, "--force-duplicate"),
    episodes: Optional[str] = typer.Option(
        None, "--episodes", help="1-based ranges or collection item IDs"
    ),
    diarize: Optional[bool] = typer.Option(None, "--diarize/--no-diarize"),
    speaker_review: Optional[bool] = typer.Option(None, "--speaker-review/--no-speaker-review"),
    polish: Optional[bool] = typer.Option(None, "--polish/--no-polish"),
    summary: Optional[bool] = typer.Option(None, "--summary/--no-summary"),
    mindmap: Optional[bool] = typer.Option(None, "--mindmap/--no-mindmap"),
    detail: Optional[bool] = typer.Option(None, "--detail/--no-detail"),
    only_transcribe: bool = typer.Option(False, "--only-transcribe"),
    webhook: Optional[str] = typer.Option(None, "--webhook"),
    option: list[str] = typer.Option(None, "--option", help="额外任务选项 KEY=VALUE，可重复"),
    detach: bool = typer.Option(False, "--detach", help="提交后立即返回"),
    direct: bool = typer.Option(
        False, "--direct", help="不启动 daemon，在当前 CLI 进程直接跑完整流程"
    ),
    target_worker: Optional[str] = typer.Option(
        None,
        "--target-worker",
        "-W",
        help="节点 ID、显示名称（如 SilentField）或 local",
    ),
    remote: Optional[bool] = typer.Option(
        None,
        "--remote/--local",
        help="本次连接配置的协调服务器或本机 daemon",
    ),
    api_flow: bool = typer.Option(
        False, "--api-flow", help="纯 API 流程：跳过 UVR/本地分离，使用 API ASR"
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="只输出结果路径"),
):
    """提交任务并实时显示进度（Ctrl+C 可脱离，任务继续后台运行）。"""
    from app.cli.commands.common import api_call
    from app.cli.context import get_cli_context
    from app.cli.output import emit

    effective_remote = remote if remote is not None else get_cli_context().remote_preference
    if direct and (target_worker is not None or effective_remote is True):
        from app.cli.output import emit_error

        emit_error(
            "direct_option_conflict",
            "--direct cannot be combined with --target-worker or --remote.",
            exit_code=2,
        )
    expanded = expand_sources(sources or [], from_file=from_file, recursive=recursive)
    options = task_options(
        force_asr=force_asr,
        prefer_subtitles=prefer_subtitles,
        skip_separation=no_sep,
        speakers=speakers,
        hotwords=hotword or [],
        legacy_hotwords=hotwords,
        api_flow=api_flow,
        assignments=option or [],
        workflow=dict(diarize=diarize, speaker_review=speaker_review, polish=polish,
                      summary=summary, mindmap=mindmap, detail=detail),
        only_transcribe=only_transcribe,
    )

    if direct:
        if (
            upload != "auto" or collection or webhook or folder or move_existing
            or collection_folder or episodes or force_duplicate
        ):
            from app.cli.output import emit_error

            emit_error(
                "direct_option_conflict",
                "--direct does not support daemon submission and library options.",
                exit_code=2,
            )
        finals: list[dict] = []
        try:
            for source in expanded:
                finals.append(_run_direct(source, options=options, quiet=quiet))
        except KeyboardInterrupt:
            from app.cli.display import console

            console.print("\n[yellow]direct 任务已中断；当前进程退出后不会继续后台处理[/yellow]")
            raise typer.Exit(130)
        if get_cli_context().output_mode in {"json", "jsonl"} or len(finals) > 1:
            emit(finals, text=batch_text(finals))
        else:
            _print_final(finals[0], quiet=quiet)
        raise typer.Exit(0 if all(item.get("status") == "completed" for item in finals) else 1)

    submitted, submission_errors = api_call(
        lambda: submit_to_servers(
            lambda: _require_daemon(auto_start=True),
            expanded,
            options=options,
            webhook_url=webhook,
            upload_mode=upload,
            collection=collection,
            raw_target=target_worker,
            remote=effective_remote,
            import_options=ImportOptions(
                folder, move_existing, collection_folder, force_duplicate, episodes
            ),
        )
    )
    output_mode = _command_context().output_mode
    if detach or output_mode != "text":
        finals = _submission_finals(submitted, wait=not detach)
        _emit_submission(finals, submission_errors)
    else:
        finals = []
        for task, client in submitted:
            if task.get("outcome"):
                typer.echo(batch_text([task]))
            elif _waitable_submission(task):
                if not quiet and len(submitted) == 1:
                    typer.echo(f"已提交  {str(task['id'])[:8]}")
                task = api_call(lambda: _do_attach(str(task["id"]), client=client, quiet=quiet))
            else:
                _print_final(task, quiet=quiet)
            finals.append(task)
        _report_submission_errors(submission_errors)
    _check_submission_exit(finals, submission_errors, waited=not detach)


def submit(
    sources: list[str] = typer.Argument(None, help="媒体文件、目录、glob 或 URL，可指定多个"),
    no_sep: bool = typer.Option(False, "--no-sep", "--skip-separation", help="跳过人声分离"),
    speakers: int = typer.Option(None, "--speakers", "-s", help="说话人数量"),
    hotwords: str = typer.Option(None, "--hotwords", "-w", help="热词，逗号分隔"),
    hotword: list[str] = typer.Option(None, "--hotword", help="热词，可重复"),
    force_asr: bool = typer.Option(False, "--force-asr", help="强制 ASR"),
    prefer_subtitles: bool = typer.Option(False, "--prefer-subtitles", help="优先使用平台字幕"),
    from_file: Optional[Path] = typer.Option(
        None, "--from-file", exists=True, dir_okay=False, readable=True
    ),
    recursive: bool = typer.Option(False, "--recursive", "-r"),
    upload: str = typer.Option("auto", "--upload", help="auto/always/never"),
    collection: Optional[str] = typer.Option(None, "--collection", help="all 或合集条目 ID 列表"),
    folder: Optional[str] = typer.Option(None, "--folder"),
    move_existing: bool = typer.Option(False, "--move-existing"),
    collection_folder: bool = typer.Option(False, "--collection-folder"),
    force_duplicate: bool = typer.Option(False, "--force-duplicate"),
    episodes: Optional[str] = typer.Option(
        None, "--episodes", help="1-based ranges or collection item IDs"
    ),
    diarize: Optional[bool] = typer.Option(None, "--diarize/--no-diarize"),
    speaker_review: Optional[bool] = typer.Option(None, "--speaker-review/--no-speaker-review"),
    polish: Optional[bool] = typer.Option(None, "--polish/--no-polish"),
    summary: Optional[bool] = typer.Option(None, "--summary/--no-summary"),
    mindmap: Optional[bool] = typer.Option(None, "--mindmap/--no-mindmap"),
    detail: Optional[bool] = typer.Option(None, "--detail/--no-detail"),
    only_transcribe: bool = typer.Option(False, "--only-transcribe"),
    webhook: Optional[str] = typer.Option(None, "--webhook"),
    option: list[str] = typer.Option(None, "--option", help="额外任务选项 KEY=VALUE，可重复"),
    wait: bool = typer.Option(False, "--wait", help="等待全部任务到达终态"),
    target_worker: Optional[str] = typer.Option(
        None,
        "--target-worker",
        "-W",
        help="节点 ID、显示名称（如 SilentField）或 local",
    ),
    remote: Optional[bool] = typer.Option(
        None,
        "--remote/--local",
        help="本次连接配置的协调服务器或本机 daemon",
    ),
    api_flow: bool = typer.Option(
        False, "--api-flow", help="纯 API 流程：跳过 UVR/本地分离，使用 API ASR"
    ),
):
    """纯提交，打印 task_id 后立即返回（供脚本捕获）。

    示例：

    \b
    ID=$(mpp submit video.mp4)
    mpp attach $ID
    """
    from app.cli.commands.common import api_call
    from app.cli.context import get_cli_context

    expanded = expand_sources(sources or [], from_file=from_file, recursive=recursive)
    options = task_options(
        force_asr=force_asr,
        prefer_subtitles=prefer_subtitles,
        skip_separation=no_sep,
        speakers=speakers,
        hotwords=hotword or [],
        legacy_hotwords=hotwords,
        api_flow=api_flow,
        assignments=option or [],
        workflow=dict(diarize=diarize, speaker_review=speaker_review, polish=polish,
                      summary=summary, mindmap=mindmap, detail=detail),
        only_transcribe=only_transcribe,
    )
    effective_remote = remote if remote is not None else get_cli_context().remote_preference
    submitted, submission_errors = api_call(
        lambda: submit_to_servers(
            lambda: _require_daemon(auto_start=True),
            expanded,
            options=options,
            webhook_url=webhook,
            upload_mode=upload,
            collection=collection,
            raw_target=target_worker,
            remote=effective_remote,
            import_options=ImportOptions(
                folder, move_existing, collection_folder, force_duplicate, episodes
            ),
        )
    )
    result_tasks = _submission_finals(submitted, wait=wait)
    if (
        not wait and not submission_errors and get_cli_context().output_mode == "text"
        and len(result_tasks) == 1 and not result_tasks[0].get("outcome")
    ):
        task = result_tasks[0]
        typer.echo(task["id"])
        typer.echo(f"queued  {str(task['id'])[:8]}", err=True)
        if task.get("folder_error"):
            typer.echo(task["folder_error"], err=True)
    else:
        _emit_submission(result_tasks, submission_errors)
    _check_submission_exit(result_tasks, submission_errors, waited=wait)


def _waitable_submission(task: dict) -> bool:
    return not task.get("outcome") and task.get("status") in {"pending", "queued", "processing"}


def _submission_finals(submitted, *, wait: bool) -> list[dict]:
    from app.cli.commands.common import api_call

    results = []
    for task, client in submitted:
        if wait and _waitable_submission(task):
            final = api_call(lambda: _wait_for_task(str(task["id"]), client))
            task = {**task, **final}
        results.append(task)
    return results


def _report_submission_errors(errors: list[dict]) -> None:
    if _command_context().output_mode == "text":
        for item in errors:
            typer.echo(f"{item['code']}\t{item['source']}\t{item['message']}", err=True)


def _emit_submission(tasks: list[dict], errors: list[dict]) -> None:
    from app.cli.output import emit

    emit({"tasks": tasks, "errors": errors} if errors else tasks, text=batch_text(tasks))
    _report_submission_errors(errors)


def _check_submission_exit(tasks: list[dict], errors: list[dict], *, waited: bool) -> None:
    failed = [
        task
        for task in tasks
        if waited and not task.get("outcome") and task.get("status") != "completed"
    ]
    if errors or failed:
        raise typer.Exit(5 if len(tasks) > len(failed) else 1)


def attach(
    task_ref: str = typer.Argument(..., help="Task ID、前缀或 @last / @fail / @run"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="只输出最终结果"),
):
    """挂接到任务实时进度流（任务已完成则立即显示结果）。"""
    from app.cli.commands.common import api_call

    client = _require_daemon(auto_start=True)
    task_id = api_call(lambda: _resolve_ref(task_ref, client=client))
    final = api_call(lambda: _do_attach(task_id, client=client, quiet=quiet))
    if final.get("status") != "completed":
        raise typer.Exit(1)


def _wait_for_task(task_id: str, client=None) -> dict:
    """Wait for one task without writing progress output."""
    if client is None:
        client = _require_daemon(auto_start=True)
    current = client.get_task(task_id)
    if current.get("status") in ("completed", "failed", "cancelled"):
        return current
    for event in client.stream_task_events(task_id):
        if event.get("type") in ("completed", "failed", "cancelled", "deleted"):
            break
    return client.get_task(task_id)


def _do_attach(task_id: str, client=None, quiet: bool = False) -> dict:
    """Core attach logic: stream SSE events for a task to the terminal."""
    from app.cli.display import console

    if client is None:
        client = _require_daemon(auto_start=True)

    # Snapshot current state first — task may already be done
    current = client.get_task(task_id)
    status = current.get("status", "")

    if status in ("completed", "failed", "cancelled"):
        _print_final(current, quiet=quiet)
        return current

    if quiet or (_command_context().output_mode in {"json", "jsonl"}):
        # Minimal mode: just wait for completion event
        try:
            for event in client.stream_task_events(task_id):
                etype = event.get("type", "")
                if etype in ("completed", "failed", "cancelled"):
                    final = client.get_task(task_id)
                    _print_final(final, quiet=quiet)
                    return final
        except KeyboardInterrupt:
            if _command_context().output_mode in {"json", "jsonl"}:
                from app.cli.output import emit

                emit({"detached": True, "task_id": task_id})
            else:
                _print_detach_hint(task_id)
            raise typer.Exit(130)
        final = client.get_task(task_id)
        _print_final(final, quiet=quiet)
        return final

    # Rich progress display — uv-style: each completed step prints a line,
    # current step shows a live spinner+bar.
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    from app.cli.display import STEP_LABELS

    src = current.get("source", task_id)
    label = src if len(src) <= 40 else "..." + src[-37:]

    ok_char = "+" if _command_context().plain else "✓"
    err_char = "x" if _command_context().plain else "✗"

    # Track which steps have already been printed as completed lines
    printed_steps: set[str] = set()
    step_start_time: dict[str, float] = {}
    import time as _time

    # Pre-fill already-completed steps from snapshot (resume / already-running task)
    for s in current.get("completed_steps") or []:
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
        transient=True,  # clears the bar line when a step finishes
    ) as progress:
        step_label = (
            STEP_LABELS.get(current_step_name, current_step_name) if current_step_name else label
        )
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
                data = event.get("data", {})

                if etype == "step":
                    step = data.get("step", "")
                    completed = data.get("completed", False)
                    msg = data.get("message", "")
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
            raise typer.Exit(130)

    final = client.get_task(task_id)
    _print_final(final, quiet=quiet)
    return final


def _print_final(task: dict, quiet: bool = False) -> None:
    from app.cli.display import console

    status = task.get("status", "")
    ok = "+" if _command_context().plain else "✓"
    err = "x" if _command_context().plain else "✗"

    if _command_context().output_mode in {"json", "jsonl"}:
        from app.cli.output import emit

        emit(task)
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

    console.print("\n[yellow]已脱离，任务仍在后台运行[/yellow]")
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
        import os
        import signal as _sig

        console.print("[dim]正在关闭后台服务…[/dim]")
        os.kill(os.getpid(), _sig.SIGINT)  # triggers uvicorn graceful shutdown in the bg thread


def retry(
    task_ref: Optional[str] = typer.Argument(None, help="Task ID、前缀或 @last / @fail"),
    all_failed: bool = typer.Option(False, "--all-failed"),
    force_new: bool = typer.Option(False, "--new", help="按原参数创建新任务"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
):
    """恢复暂停任务，或从失败和取消任务的 checkpoint 重跑。"""
    from app.cli.commands.common import api_call
    from app.cli.commands.task import apply_task_actions, select_task_ids
    from app.cli.output import emit, emit_error
    from app.core.remote_execution import KEY

    if bool(task_ref) == all_failed:
        emit_error("invalid_options", "Provide a task reference or --all-failed.", exit_code=2)
    client = _require_daemon(auto_start=True)
    if all_failed:
        task_ids = api_call(lambda: select_task_ids(client, [], ["failed"]))
    else:
        task_ids = [api_call(lambda: _resolve_ref(task_ref, client=client))]
    def enqueue_retry(task_id: str) -> dict:
        from app.cli.client import MppClientError

        original = client.get_task(task_id)
        status = original.get("status")
        if force_new:
            options = original.get("options") or {}
            target = (options.get(KEY) or {}).get("target_worker_id")
            routing = {"target_worker_id": target} if target else {}
            webhook = (
                {"webhook_url": original["webhook_url"]} if original.get("webhook_url") else {}
            )
            new_task = client.create_task(original["source"], options=options, **routing, **webhook)
            task_id = new_task["id"]
        elif status == "paused":
            client.resume_task(task_id)
        elif status in {"failed", "cancelled"}:
            client.checkpoint_rerun(task_id)
        else:
            raise MppClientError(
                "invalid_task_status", f"Cannot retry a task in status {status}.", exit_code=2,
            )
        return {"task_id": str(task_id)}

    retried = [row["task_id"] for row in apply_task_actions(task_ids, enqueue_retry, "queued")]

    if _command_context().output_mode in {"json", "jsonl"}:
        finals = [api_call(lambda: _wait_for_task(task_id, client)) for task_id in retried]
        emit(finals if all_failed else finals[0])
    else:
        if not retried:
            typer.echo("No failed tasks.")
        finals = [
            api_call(lambda: _do_attach(task_id, client=client, quiet=quiet)) for task_id in retried
        ]
    if any(task.get("status") != "completed" for task in finals):
        raise typer.Exit(1)
