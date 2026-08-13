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

import os
import subprocess
import sys
from difflib import get_close_matches
from pathlib import Path
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

# Resource-oriented command groups. High-frequency top-level commands below
# remain as compatibility aliases.
from app.cli.commands.archive import archive_app, speaker_app  # noqa: E402
from app.cli.commands.configuration import (  # noqa: E402
    flow_app,
    model_app,
    provider_app,
    register_config_extensions,
    source_app,
)
from app.cli.commands.operations import (  # noqa: E402
    auth_app,
    fs_app,
    kb_app,
    logs_app,
    pipeline_app,
    register_misc_commands,
    server_app,
    stage_app,
    storage_app,
    sync_app,
    voiceprint_app,
)
from app.cli.commands.task import task_app  # noqa: E402

app.add_typer(server_app, name="server")
app.add_typer(auth_app, name="auth")
app.add_typer(task_app, name="task")
app.add_typer(archive_app, name="archive")
app.add_typer(speaker_app, name="speaker")
app.add_typer(stage_app, name="stage")
app.add_typer(provider_app, name="provider")
app.add_typer(model_app, name="model")
app.add_typer(flow_app, name="flow")
app.add_typer(source_app, name="source")
app.add_typer(kb_app, name="kb")
app.add_typer(voiceprint_app, name="voiceprint")
app.add_typer(logs_app, name="logs")
app.add_typer(storage_app, name="storage")
app.add_typer(fs_app, name="fs")
app.add_typer(sync_app, name="sync")
app.add_typer(pipeline_app, name="pipeline")
register_config_extensions(config_app)
register_misc_commands(app)


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


def _emit_json_compat(data, *, legacy_json: bool = False) -> None:
    """Emit through the shared envelope for legacy command-local --json flags."""
    from app.cli.context import configure_cli_context, get_cli_context
    from app.cli.output import emit

    previous_mode = get_cli_context().output_mode
    if legacy_json and previous_mode == "text":
        configure_cli_context(output_mode="json")
    try:
        emit(data)
    finally:
        if legacy_json and previous_mode == "text":
            configure_cli_context(output_mode=previous_mode)


@app.callback(invoke_without_command=True)
def _global_options(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="显示 MPP 版本", is_eager=True),
    server: Optional[str] = typer.Option(
        None, "--server", help="daemon URL", envvar="MPP_SERVER_URL"
    ),
    token: Optional[str] = typer.Option(
        None, "--token", help="Bearer token", envvar="MPP_API_TOKEN"
    ),
    token_env: Optional[str] = typer.Option(
        None, "--token-env", help="从指定环境变量读取 Bearer token"
    ),
    timeout: float = typer.Option(
        30.0, "--timeout", min=0.1, help="普通 HTTP 请求超时", envvar="MPP_TIMEOUT"
    ),
    plain: bool = typer.Option(
        False, "--plain", help="纯文本输出，无颜色，无 Unicode 图标（ASCII）"
    ),
    no_color: bool = typer.Option(False, "--no-color", help="去掉颜色，保留格式结构"),
    json_out: bool = typer.Option(False, "--json", help="机器可读 JSON 输出（stdout）"),
    jsonl: bool = typer.Option(False, "--jsonl", help="事件或批量结果逐行 JSON 输出"),
    quiet: bool = typer.Option(False, "--quiet", help="只输出主要结果"),
    no_input: bool = typer.Option(False, "--no-input", help="禁止交互提示", envvar="MPP_NO_INPUT"),
    assume_yes: bool = typer.Option(False, "--yes", help="确认当前破坏性操作"),
    debug: bool = typer.Option(False, "--debug", help="输出完整异常诊断"),
    skip_version_check: bool = typer.Option(
        False,
        "--skip-version-check",
        help="跳过 yt-dlp 版本检查",
        envvar="MPP_SKIP_VERSION_CHECK",
    ),
) -> None:
    global _plain_mode, _json_mode
    if version:
        from app.version import __version__

        typer.echo(f"MPP {__version__}")
        raise typer.Exit()

    if json_out and jsonl:
        from app.cli.output import emit_error

        emit_error("invalid_output_mode", "Choose one of --json or --jsonl.", exit_code=2)
    token_value = token or ""
    if token_env:
        token_value = os.environ.get(token_env, "")
        if not token_value:
            from app.cli.output import emit_error

            emit_error(
                "token_env_missing", f"Environment variable {token_env!r} is empty.", exit_code=2
            )
    from app.cli.context import configure_cli_context, normalize_server_url

    try:
        server_url = normalize_server_url(server)
    except ValueError as exc:
        from app.cli.output import emit_error

        emit_error("invalid_server_url", str(exc), exit_code=2)
    _plain_mode = plain or (os.environ.get("MPP_PLAIN_OUTPUT") == "1")
    _json_mode = json_out or jsonl
    output_mode = "jsonl" if jsonl else "json" if json_out else "text"
    configure_cli_context(
        server_url=server_url,
        api_token=token_value,
        timeout=timeout,
        output_mode=output_mode,
        plain=_plain_mode,
        quiet=quiet,
        no_input=no_input or os.environ.get("MPP_NO_INPUT", "").lower() in {"1", "true", "yes"},
        assume_yes=assume_yes,
        debug=debug,
    )

    # Apply to display module so Rich output adapts
    if plain or _plain_mode:
        from app.cli.display import set_plain

        set_plain(True)
    elif no_color:
        from app.cli.display import set_no_color

        set_no_color(True)

    # Offer to upgrade yt-dlp if behind PyPI. Only on interactive commands that
    # actually need yt-dlp; skip for json/quiet/scripted invocations and serve.
    if (
        not skip_version_check
        and not _json_mode
        and not no_input
        and sys.stdin.isatty()
        and ctx.invoked_subcommand in ("run", "submit", "retry")
    ):
        _maybe_prompt_ytdlp_upgrade()


def _maybe_prompt_ytdlp_upgrade() -> None:
    """Check yt-dlp version; if stale, ask user if they want to upgrade now."""
    try:
        from app.cli.display import console
        from app.services.ingestion.ytdlp_version import check_version, upgrade

        info = check_version()
    except Exception:
        return  # network down or import error — silent

    if not info or not info.is_stale:
        return

    console.print(
        f"[yellow]![/yellow] yt-dlp 已过期: [bold]{info.installed}[/bold] → "
        f"PyPI 最新 [bold]{info.latest}[/bold]"
    )
    console.print("  YouTube/抖音 等平台经常需要最新版本才能下载。")
    try:
        ans = input("  现在升级？[Y/n] ").strip().lower()
    except (EOFError, OSError):
        return

    if ans and ans not in ("y", "yes", "是"):
        console.print("  [dim]已跳过。可设置 MPP_SKIP_VERSION_CHECK=1 永久跳过此提示。[/dim]")
        return

    console.print("  [dim]运行 yt-dlp 更新 …[/dim]")
    result = upgrade()
    if result.get("ok"):
        console.print(
            f"  [green]✓[/green] 已升级到 [bold]{result.get('new')}[/bold]"
            f"  [dim](原: {result.get('old')})[/dim]"
        )
        if result.get("restart_recommended"):
            console.print("  [yellow]提示：daemon 已加载旧版本 yt-dlp，重启后生效。[/yellow]")
    else:
        console.print(f"  [red]✗[/red] 升级失败:\n{result.get('output', '')}")


@app.command("upgrade-ytdlp")
def upgrade_ytdlp():
    """升级 yt-dlp 到当前环境可用的最新版。"""
    from app.cli.output import emit, emit_error
    from app.services.ingestion.ytdlp_version import upgrade

    result = upgrade()
    if result.get("ok"):
        emit(result, text=f"updated\t{result.get('old')}\t{result.get('new')}")
        return
    emit_error(
        "ytdlp_upgrade_failed", str(result.get("output", "yt-dlp upgrade failed")), details=result
    )


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
        from app.cli.context import get_cli_context
        from app.cli.output import emit_error

        emit_error(
            "daemon_unavailable",
            f"Cannot reach {get_cli_context().server_url}. Run mpp server start for localhost.",
            retryable=True,
            exit_code=3,
        )

    from app.cli.context import get_cli_context
    from app.cli.daemon import start_daemon
    from app.cli.output import emit_error

    cli_context = get_cli_context()
    if not cli_context.is_local:
        emit_error(
            "daemon_unavailable",
            f"Cannot reach remote daemon {cli_context.server_url}.",
            retryable=True,
            exit_code=3,
        )
    if cli_context.output_mode == "text":
        typer.echo("starting daemon", err=True)
    try:
        result = start_daemon(
            cli_context.server_url,
            api_token=cli_context.api_token,
            timeout=max(cli_context.timeout, 20.0),
        )
    except RuntimeError as exc:
        emit_error("daemon_start_failed", str(exc), retryable=True, exit_code=3)
    if cli_context.output_mode == "text":
        typer.echo(f"daemon started\t{result['server']}", err=True)
    return client


def _resolve_ref(ref: str, client=None) -> str:
    """Resolve @last / @fail / @run to a real task ID.

    Falls back to SQLite offline read when daemon is not reachable.
    Prefix-match for plain hex IDs.
    """
    if client is not None and client.ping():
        from app.cli.commands.common import resolve_task_ref

        return resolve_task_ref(ref, client)

    if not ref.startswith("@"):
        # Plain ID or prefix — resolve via list
        return _resolve_prefix(ref, client)

    keyword = ref.lstrip("@").lower()
    status_map = {
        "last": None,
        "fail": ["failed"],
        "run": ["processing"],
        "queued": ["queued"],
        "paused": ["paused"],
        "completed": ["completed"],
        "active": ["pending", "queued", "processing", "paused"],
    }
    if keyword not in status_map:
        from app.cli.output import emit_error

        emit_error(
            "invalid_task_ref",
            f"Unknown task reference: {ref}",
            detail={"supported": [f"@{name}" for name in status_map]},
            exit_code=2,
        )

    # Try daemon first, fall back to SQLite
    tasks = _list_tasks_any(limit=10000, client=client)
    statuses = status_map[keyword]
    if statuses:
        tasks = [task for task in tasks if task.get("status") in statuses]
    if not tasks:
        from app.cli.output import emit_error

        emit_error("task_not_found", f"No task matches {ref}.", exit_code=4)
    return tasks[0]["id"]


def _resolve_prefix(prefix: str, client=None) -> str:
    """Resolve a task ID prefix to a full ID."""
    tasks = _list_tasks_any(limit=10000, client=client)
    matches = [t for t in tasks if t["id"].startswith(prefix)]
    if not matches:
        from app.cli.output import emit_error

        emit_error("task_not_found", f"No task ID starts with {prefix!r}.", exit_code=4)
    if len(matches) > 1:
        from app.cli.output import emit_error

        emit_error(
            "ambiguous_task_ref",
            f"Task prefix {prefix!r} matches {len(matches)} tasks.",
            detail=[
                {"id": item["id"], "status": item.get("status"), "source": item.get("source")}
                for item in matches[:20]
            ],
            exit_code=4,
        )
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
    from app.cli.context import get_cli_context

    if not get_cli_context().is_local:
        return []
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
    host: str = typer.Option("localhost", help="Bind address"),
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
    from app.cli.context import get_cli_context
    from app.cli.output import emit, emit_error

    client = _get_client()
    if client.ping():
        server_url = get_cli_context().server_url
        emit({"online": True, "server": server_url}, text=f"online\t{server_url}")
        return
    emit_error("daemon_unavailable", f"Cannot reach {get_cli_context().server_url}.", exit_code=3)


# ---------------------------------------------------------------------------
# mpp run
# ---------------------------------------------------------------------------


@app.command()
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
    webhook: Optional[str] = typer.Option(None, "--webhook"),
    option: list[str] = typer.Option(None, "--option", help="额外任务选项 KEY=VALUE，可重复"),
    detach: bool = typer.Option(False, "--detach", help="提交后立即返回"),
    direct: bool = typer.Option(
        False, "--direct", help="不启动 daemon，在当前 CLI 进程直接跑完整流程"
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
    from app.cli.submission import batch_text, expand_sources, submit_sources, task_options

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
    )

    if direct:
        if upload != "auto" or collection or webhook:
            from app.cli.output import emit_error

            emit_error(
                "direct_option_conflict",
                "--direct does not use --upload, --collection, or --webhook.",
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

    client = _require_daemon(auto_start=True)
    tasks_created, submission_errors = api_call(
        lambda: submit_sources(
            client,
            expanded,
            options=options,
            webhook_url=webhook,
            upload_mode=upload,
            collection=collection,
        )
    )
    if detach:
        payload = (
            {"tasks": tasks_created, "errors": submission_errors}
            if submission_errors
            else tasks_created
        )
        emit(payload, text=batch_text(tasks_created))
        if submission_errors:
            for item in submission_errors:
                sys.stderr.write(f"upload failed\t{item['source']}\t{item['message']}\n")
            raise typer.Exit(5)
        return
    if not quiet and not _json_mode and len(tasks_created) == 1:
        from app.cli.display import console

        console.print(f"已提交  [bold]{str(tasks_created[0]['id'])[:8]}[/bold]")
    if get_cli_context().output_mode in {"json", "jsonl"}:
        finals = [
            api_call(lambda task=task: _wait_for_task(str(task["id"]), client))
            for task in tasks_created
        ]
        payload = {"tasks": finals, "errors": submission_errors} if submission_errors else finals
        emit(payload)
        failed = [task for task in finals if task.get("status") != "completed"]
        if submission_errors or failed:
            raise typer.Exit(5 if any(task.get("status") == "completed" for task in finals) else 1)
        return
    finals = []
    for task in tasks_created:
        finals.append(
            api_call(lambda task=task: _do_attach(str(task["id"]), client=client, quiet=quiet))
        )
    if submission_errors:
        for item in submission_errors:
            sys.stderr.write(f"upload failed\t{item['source']}\t{item['message']}\n")
    failed = [task for task in finals if task.get("status") != "completed"]
    if submission_errors or failed:
        raise typer.Exit(5 if any(task.get("status") == "completed" for task in finals) else 1)


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

    if not quiet and not _json_mode:
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
    if quiet or _json_mode:
        await runner
        return

    import asyncio
    import time as _time

    from app.cli.display import STEP_LABELS, console

    ok_char = "+" if _plain_mode else "✓"
    err_char = "x" if _plain_mode else "✗"
    run_char = ">" if _plain_mode else "▶"
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


# ---------------------------------------------------------------------------
# mpp submit
# ---------------------------------------------------------------------------


@app.command()
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
    webhook: Optional[str] = typer.Option(None, "--webhook"),
    option: list[str] = typer.Option(None, "--option", help="额外任务选项 KEY=VALUE，可重复"),
    wait: bool = typer.Option(False, "--wait", help="等待全部任务到达终态"),
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
    from app.cli.output import emit
    from app.cli.submission import batch_text, expand_sources, submit_sources, task_options

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
    )
    client = _require_daemon(auto_start=True)
    tasks_created, submission_errors = api_call(
        lambda: submit_sources(
            client,
            expanded,
            options=options,
            webhook_url=webhook,
            upload_mode=upload,
            collection=collection,
        )
    )
    result_tasks = (
        [
            api_call(lambda task=task: _wait_for_task(str(task["id"]), client))
            for task in tasks_created
        ]
        if wait
        else tasks_created
    )
    payload = (
        {"tasks": result_tasks, "errors": submission_errors} if submission_errors else result_tasks
    )
    if (
        wait
        or submission_errors
        or get_cli_context().output_mode in {"json", "jsonl"}
        or len(result_tasks) > 1
    ):
        emit(payload, text=batch_text(result_tasks))
    else:
        task_id = str(result_tasks[0]["id"])
        print(task_id)
        sys.stderr.write(f"queued  {task_id[:8]}\n")
    if submission_errors:
        for item in submission_errors:
            sys.stderr.write(f"upload failed\t{item['source']}\t{item['message']}\n")
    failed = [task for task in result_tasks if wait and task.get("status") != "completed"]
    if submission_errors or failed:
        raise typer.Exit(
            5 if any(task.get("status") == "completed" for task in result_tasks) else 1
        )


# ---------------------------------------------------------------------------
# mpp attach
# ---------------------------------------------------------------------------


@app.command()
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

    if quiet or _json_mode:
        # Minimal mode: just wait for completion event
        try:
            for event in client.stream_task_events(task_id):
                etype = event.get("type", "")
                if etype in ("completed", "failed", "cancelled"):
                    final = client.get_task(task_id)
                    _print_final(final, quiet=quiet)
                    return final
        except KeyboardInterrupt:
            if _json_mode:
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

    ok_char = "+" if _plain_mode else "✓"
    err_char = "x" if _plain_mode else "✗"

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
    ok = "+" if _plain_mode else "✓"
    err = "x" if _plain_mode else "✗"

    if _json_mode:
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


# ---------------------------------------------------------------------------
# mpp retry
# ---------------------------------------------------------------------------


@app.command()
def retry(
    task_ref: str = typer.Argument(..., help="Task ID、前缀或 @last / @fail"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
):
    """按原参数重新提交失败任务，然后 attach。"""
    from app.cli.commands.common import api_call
    from app.cli.display import console

    client = _require_daemon(auto_start=True)
    task_id = api_call(lambda: _resolve_ref(task_ref, client=client))
    original = api_call(lambda: client.get_task(task_id))

    source = original.get("source", "")
    options = original.get("options") or {}

    new_task = api_call(lambda: client.create_task(source, options=options))
    new_id = new_task["id"]

    if not quiet and not _json_mode:
        console.print(f"重新提交  [bold]{new_id[:8]}[/bold]  (原: {task_id[:8]})")

    final = api_call(lambda: _do_attach(new_id, client=client, quiet=quiet))
    if final.get("status") != "completed":
        raise typer.Exit(1)


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


@app.command(name="list")
def list_alias(
    status_filter: Optional[list[str]] = typer.Option(None, "--status", "-s", help="状态，可重复"),
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=10000),
    offset: int = typer.Option(0, "--offset", min=0),
):
    """列出任务，兼容旧版 mpp list。"""
    from app.cli.commands.task import list_tasks as command_task_list

    command_task_list(status=status_filter, active=False, limit=limit, offset=offset, watch=False)


@app.command(name="status")
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
        from app.cli.commands.common import api_call

        task_id = api_call(lambda: _resolve_ref(task_ref, client=client))
        task = api_call(lambda: client.get_task(task_id))
    else:
        # Offline: read from SQLite
        from app.cli.context import get_cli_context
        from app.cli.output import emit_error

        if not get_cli_context().is_local:
            emit_error(
                "daemon_unavailable",
                f"Cannot reach {get_cli_context().server_url}.",
                exit_code=3,
            )
        try:
            from app.core.database import init_db

            init_db()
            task_id = _resolve_ref(task_ref)
            all_tasks = _list_tasks_any(limit=10000)
            task = next(item for item in all_tasks if item["id"] == task_id)
            if not use_json:
                console.print("[dim](offline)[/dim]")
        except typer.Exit:
            raise
        except Exception as e:
            console.print(f"[red]无法读取任务: {e}[/red]")
            raise typer.Exit(1)

    if use_json:
        _emit_json_compat(task, legacy_json=json_out)
        return

    if summary or transcript:
        _cat_task(task, summary=summary, transcript=transcript, client=client if online else None)
        return

    print_task_detail(task)


def _cat_task(
    task: dict,
    summary: bool = False,
    transcript: bool = False,
    client=None,
) -> None:
    """Print summary or transcript file content to stdout."""
    import pathlib

    result = task.get("result") or {}
    output_dir = result.get("output_dir") or task.get("output_dir")
    if not output_dir:
        from app.cli.output import emit_error

        emit_error("task_output_missing", "The task has no output directory.", exit_code=4)

    if client is not None:
        from app.cli.commands.common import api_call
        from app.cli.output import emit_error

        listing = api_call(lambda: client.fs_list(str(output_dir), "file"))
        names = [str(item.get("name") or "") for item in listing.get("items") or []]
        wanted: list[str] = []
        if summary:
            wanted.extend(
                name for name in names if "summary" in name.lower() and name.endswith(".md")
            )
        if transcript:
            wanted.extend(
                name
                for name in names
                if name.lower().endswith(".srt") or "transcript" in name.lower()
            )
        if not wanted:
            emit_error("task_artifact_missing", "No matching task artifact was found.", exit_code=4)
        separator = "\\" if "\\" in str(output_dir) else "/"
        root = str(output_dir).rstrip("/\\")
        contents: list[str] = []
        for name in wanted:
            path = f"{root}{separator}{name}"
            result = api_call(lambda path=path: client.fs_read(path))
            if result.get("success"):
                contents.append(str(result.get("content") or ""))
        if contents:
            print("\n".join(contents))
            return
        emit_error("task_artifact_missing", "No readable task artifact was found.", exit_code=4)

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


# ---------------------------------------------------------------------------
# mpp cancel
# ---------------------------------------------------------------------------


@app.command()
def cancel(
    task_ref: str = typer.Argument(..., help="Task ID、前缀或 @last / @run"),
):
    """取消任务。"""
    from app.cli.commands.task import cancel as command_cancel

    command_cancel([task_ref])


# ---------------------------------------------------------------------------
# mpp config  (subcommands: list / get / set)
# ---------------------------------------------------------------------------

# --- config group metadata (defined on RuntimeSettings fields) ---

_CONFIG_GROUPS: dict[str, list[str]] = {
    "llm": [
        "llm_provider",
        "anthropic_api_key",
        "anthropic_api_base",
        "anthropic_model",
        "openai_api_key",
        "openai_api_base",
        "openai_model",
        "custom_api_key",
        "custom_api_base",
        "custom_model",
        "custom_name",
        "custom_active_profile_id",
        "custom_llm_profiles",
        "deepseek_api_key",
        "deepseek_api_base",
        "deepseek_analyze_model",
        "deepseek_analyze_thinking",
        "deepseek_analyze_effort",
        "deepseek_polish_model",
        "deepseek_polish_thinking",
        "deepseek_polish_effort",
        "deepseek_summary_model",
        "deepseek_summary_thinking",
        "deepseek_summary_effort",
        "deepseek_mindmap_model",
        "deepseek_mindmap_thinking",
        "deepseek_mindmap_effort",
        "local_llm_model_path",
        "local_llm_n_gpu_layers",
        "local_llm_n_ctx",
        "local_llm_n_batch",
        "polish_provider",
        "llm_polish_concurrency",
    ],
    "asr": [
        "asr_provider",
        "sherpa_model_id",
        "sherpa_model_root",
        "sherpa_device",
        "sherpa_num_threads",
        "sherpa_chunk_strategy",
        "sherpa_max_chunk_sec",
        "sherpa_vad_model_path",
        "sherpa_debug",
        "asr_timestamp_mode",
        "qwen3_aligner_model_path",
        "siliconflow_api_base",
        "siliconflow_api_key",
        "siliconflow_asr_model",
        "siliconflow_asr_language",
        "siliconflow_asr_max_chunk_sec",
        "siliconflow_asr_timeout_sec",
        "siliconflow_asr_chunk_strategy",
    ],
    "diarization": [
        "enable_diarization",
        "hf_token",
        "hf_proxy",
        "pyannote_model_path",
        "pyannote_segmentation_path",
        "pyannote_embedding_path",
        "diarization_batch_size",
    ],
    "subtitle": [
        "prefer_platform_subtitles",
        "subtitle_languages",
        "force_asr",
    ],
    "uvr": [
        "uvr_model",
        "uvr_device",
        "uvr_model_dir",
        "uvr_mdx_inst_hq3_path",
        "uvr_hp_uvr_path",
        "uvr_denoise_lite_path",
        "uvr_kim_vocal_2_path",
        "uvr_deecho_dereverb_path",
        "uvr_htdemucs_path",
        "uvr_chunk_duration_sec",
    ],
    "paths": [
        "data_root",
        "sherpa_model_root",
        "sherpa_vad_model_path",
        "qwen3_aligner_model_path",
        "llama_cpp_binary_path",
        "uvr_model_dir",
        "pyannote_model_path",
        "pyannote_segmentation_path",
        "pyannote_embedding_path",
        "local_llm_model_path",
    ],
    "security": [
        "api_token",
        "anthropic_api_key",
        "openai_api_key",
        "custom_api_key",
        "deepseek_api_key",
        "hf_token",
        "hf_proxy",
        "bilibili_sessdata",
        "bilibili_bili_jct",
        "bilibili_dede_user_id",
    ],
    "bilibili": [
        "bilibili_sessdata",
        "bilibili_bili_jct",
        "bilibili_dede_user_id",
    ],
    "concurrency": [
        "max_download_concurrency",
    ],
}

_SECRET_KEYS = {
    "anthropic_api_key",
    "openai_api_key",
    "custom_api_key",
    "deepseek_api_key",
    "siliconflow_api_key",
    "hf_token",
    "hf_proxy",
    "api_token",
    "jina_reader_api_key",
    "bilibili_sessdata",
    "bilibili_bili_jct",
}


def _mask(key: str, value) -> str:
    if key in _SECRET_KEYS and value:
        s = str(value)
        return s[:4] + "..." if len(s) > 4 else "***"
    return str(value)


def _read_settings() -> dict:
    client = _get_client()
    if client.ping():
        from app.cli.commands.common import api_call

        return api_call(client.get_settings)
    from app.cli.output import redact
    from app.core.settings import get_runtime_settings

    return redact(get_runtime_settings().model_dump())


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
    group: Optional[str] = typer.Option(
        None,
        "--group",
        "-g",
        help="分组: llm/asr/uvr/diarization/subtitle/paths/security/bilibili/concurrency",
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """列出所有配置（可按组筛选）。"""
    if group and group not in _CONFIG_GROUPS:
        from app.cli.output import emit_error

        close = get_close_matches(group, list(_CONFIG_GROUPS.keys()), n=3, cutoff=0.4)
        emit_error(
            "config_group_not_found",
            f"Unknown config group: {group}",
            detail={"suggestions": close},
            exit_code=2,
        )
    if json_out or _json_mode:
        settings = _read_settings()
        if group:
            keys = _CONFIG_GROUPS.get(group, [])
            settings = {k: settings[k] for k in keys if k in settings}
        _emit_json_compat(settings, legacy_json=json_out)
    else:
        _config_list_impl(group=group)


def _config_list_impl(group: str | None) -> None:
    from rich.table import Table

    from app.cli.display import console

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
        from app.cli.output import emit_error

        close = get_close_matches(key, valid_keys, n=3, cutoff=0.4)
        emit_error(
            "config_key_not_found",
            f"Unknown config key: {key}",
            detail={"suggestions": close},
            exit_code=2,
        )

    settings = _read_settings()
    value = settings.get(key, "")

    defaults = RuntimeSettings().model_dump()
    default_val = defaults.get(key)

    if json_out or _json_mode:
        _emit_json_compat(
            {"key": key, "value": value, "default": _mask(key, default_val)},
            legacy_json=json_out,
        )
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
    valid_keys = _all_valid_keys()
    if key not in valid_keys:
        from app.cli.output import emit_error

        close = get_close_matches(key, valid_keys, n=3, cutoff=0.4)
        emit_error(
            "config_key_not_found",
            f"Unknown config key: {key}",
            detail={"suggestions": close},
            exit_code=2,
        )

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
        from app.cli.commands.common import api_call

        api_call(lambda: client.patch_settings({key: typed_value}))
    else:
        from app.cli.output import emit_error
        from app.core.settings import patch_runtime_settings

        try:
            patch_runtime_settings({key: typed_value})
        except (OSError, ValueError) as exc:
            emit_error("settings_update_failed", str(exc), exit_code=2)

    from app.cli.output import emit

    display_value = _mask(key, typed_value)
    emit({"updated": {key: display_value}}, text=f"updated\t{key}={display_value}")


@config_app.command(name="preset")
def config_preset(
    name: str = typer.Argument(..., help="预设名: api-flow / local-models"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只显示将要修改的配置，不写入"),
):
    """应用常用运行预设。"""
    presets: dict[str, dict[str, str | bool]] = {
        "api-flow": {
            "asr_provider": "siliconflow",
            "siliconflow_asr_chunk_strategy": "ffmpeg",
            "enable_diarization": False,
            "enable_voiceprint": False,
            # Empty means polish follows llm_provider, avoiding local HF loads.
            "polish_provider": "",
        },
        "local-models": {
            "asr_provider": "sherpa_onnx",
            "sherpa_model_id": "sensevoice-small-int8",
            "enable_diarization": True,
            "enable_voiceprint": True,
            "polish_provider": "local",
        },
    }

    key = name.strip().lower()
    if key not in presets:
        from app.cli.output import emit_error

        close = get_close_matches(key, list(presets.keys()), n=2, cutoff=0.4)
        emit_error(
            "config_preset_not_found",
            f"Unknown config preset: {name}",
            detail={"suggestions": close},
            exit_code=2,
        )

    updates = presets[key]
    if dry_run:
        from app.cli.output import emit

        emit(
            {"preset": key, "dry_run": True, "updates": updates},
            text="\n".join(
                [f"preset\t{key}\tdry-run", *[f"{k}={_mask(k, v)}" for k, v in updates.items()]]
            ),
        )
        return

    client = _get_client()
    if client.ping():
        from app.cli.commands.common import api_call

        api_call(lambda: client.patch_settings(updates))
    else:
        from app.cli.output import emit_error
        from app.core.settings import patch_runtime_settings

        try:
            patch_runtime_settings(updates)
        except (OSError, ValueError) as exc:
            emit_error("settings_update_failed", str(exc), exit_code=2)

    from app.cli.output import emit

    note = "请继续配置 siliconflow_api_key 和可用的 API LLM provider。" if key == "api-flow" else ""
    emit(
        {"preset": key, "updates": updates, "note": note},
        text=f"applied\t{key}" + (f"\n{note}" if note else ""),
    )


# ---------------------------------------------------------------------------
# mpp doctor
# ---------------------------------------------------------------------------


@app.command()
def doctor():
    """检查运行环境（ffmpeg、CUDA、模型文件、API key 等）。"""
    import importlib.util
    import pathlib
    import shutil

    from app.cli.context import get_cli_context
    from app.cli.display import console
    from app.cli.output import emit

    ok = "[green]+" if _plain_mode else "[green]✓"
    err = "[red]x" if _plain_mode else "[red]✗"
    checks: list[dict] = []

    def check(label: str, passed: bool, detail: str = "") -> None:
        checks.append({"name": label, "ok": passed, "detail": detail})
        if get_cli_context().output_mode in {"json", "jsonl"}:
            return
        icon = ok if passed else err
        style_end = "[/green]" if passed else "[/red]"
        line = f"  {icon}{style_end}  {label:<20}"
        if detail:
            line += f"  [dim]{detail}[/dim]"
        console.print(line)

    # Daemon
    client = _get_client()
    daemon_ok = client.ping()
    check(
        "Daemon",
        daemon_ok,
        get_cli_context().server_url if daemon_ok else "未运行，请执行 mpp server start",
    )

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
    if not daemon_ok and not get_cli_context().is_local:
        check("Remote settings", False, "daemon 连接成功后可读取")
        if get_cli_context().output_mode in {"json", "jsonl"}:
            emit({"healthy": False, "checks": checks})
        return
    settings = _read_settings()
    data_root = settings.get("data_root", "")
    dr_ok = (
        pathlib.Path(data_root).exists()
        if data_root and get_cli_context().is_local
        else bool(data_root)
    )
    check("data_root", dr_ok, data_root)

    # API key
    provider = settings.get("llm_provider", "")
    key_field = (
        f"{provider}_api_key" if provider in ("anthropic", "openai", "custom", "deepseek") else ""
    )
    if key_field:
        has_key = bool(settings.get(key_field, ""))
        check(
            f"LLM key ({provider})",
            has_key,
            "已配置" if has_key else f"未设置 — mpp config set {key_field} <key>",
        )
    else:
        check("LLM", True, f"provider={provider}")

    # ASR and speech segmentation
    asr_provider = settings.get("asr_provider", "sherpa_onnx")
    check("ASR provider", bool(asr_provider), asr_provider)

    sherpa_root = settings.get("sherpa_model_root", "")
    sherpa_model = settings.get("sherpa_model_id", "")
    sherpa_runtime_ok = importlib.util.find_spec("sherpa_onnx") is not None
    try:
        from app.services.recognition.sherpa_catalog import resolve_model

        model_path = resolve_model(sherpa_model, sherpa_root).directory
        sherpa_model_ok = True
    except Exception as exc:
        model_path = str(exc)
        sherpa_model_ok = False
    check(
        "sherpa-onnx",
        sherpa_runtime_ok and sherpa_model_ok,
        f"{sherpa_model} | {model_path}",
    )

    vad_path = settings.get("sherpa_vad_model_path", "")
    check("Sherpa VAD", bool(vad_path and pathlib.Path(vad_path).is_file()), vad_path or "未配置")

    diarization_enabled = bool(settings.get("enable_diarization", True))
    if diarization_enabled:
        pipeline_path = pathlib.Path(settings.get("pyannote_model_path", ""))
        segmentation_path = pathlib.Path(settings.get("pyannote_segmentation_path", ""))
        embedding_path = pathlib.Path(settings.get("pyannote_embedding_path", ""))
        pipeline_ok = (pipeline_path / "config.yaml").is_file()
        segmentation_ok = (segmentation_path / "config.yaml").is_file() and (
            segmentation_path / "pytorch_model.bin"
        ).is_file()
        embedding_ok = (embedding_path / "config.yaml").is_file() and (
            embedding_path / "pytorch_model.bin"
        ).is_file()
        packages_ok = all(
            importlib.util.find_spec(name) is not None
            for name in ("pyannote.audio", "soundfile", "torch", "torchaudio")
        )
        check(
            "Pyannote 3.1",
            pipeline_ok and segmentation_ok and embedding_ok and packages_ok,
            (f"{pipeline_path} | packages={'ready' if packages_ok else 'missing'}"),
        )

    if get_cli_context().output_mode in {"json", "jsonl"}:
        emit({"healthy": all(item["ok"] for item in checks), "checks": checks})
