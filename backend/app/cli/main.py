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
import sys
from typing import Optional

import typer
from app.cli.commands.config_values import _all_valid_keys as _all_valid_keys
from app.cli.commands.config_values import _config_default as _config_default
from app.cli.commands.config_values import _config_list_impl as _config_list_impl
from app.cli.commands.config_values import _mask as _mask
from app.cli.commands.config_values import _read_settings as _read_settings
from app.cli.commands.config_values import config_get as config_get
from app.cli.commands.config_values import config_list as config_list
from app.cli.commands.config_values import config_preset as config_preset
from app.cli.commands.config_values import config_set as config_set
from app.cli.commands.direct_execution import (
    _apply_direct_runtime_overrides as _apply_direct_runtime_overrides,
)
from app.cli.commands.direct_execution import _build_options as _build_options
from app.cli.commands.direct_execution import _create_direct_task as _create_direct_task
from app.cli.commands.direct_execution import _run_direct as _run_direct
from app.cli.commands.direct_execution import _run_direct_async as _run_direct_async
from app.cli.commands.direct_execution import _stream_direct_events as _stream_direct_events
from app.cli.commands.execution import _do_attach as _do_attach
from app.cli.commands.execution import _maybe_stop_daemon as _maybe_stop_daemon
from app.cli.commands.execution import _print_detach_hint as _print_detach_hint
from app.cli.commands.execution import _print_final as _print_final
from app.cli.commands.execution import _wait_for_task as _wait_for_task
from app.cli.commands.execution import attach as attach
from app.cli.commands.execution import retry as retry
from app.cli.commands.execution import run as run
from app.cli.commands.execution import submit as submit
from app.cli.commands.maintenance import _maybe_prompt_ytdlp_upgrade as _maybe_prompt_ytdlp_upgrade
from app.cli.commands.maintenance import doctor as doctor
from app.cli.commands.maintenance import ping as ping
from app.cli.commands.maintenance import serve as serve
from app.cli.commands.maintenance import upgrade_ytdlp as upgrade_ytdlp
from app.cli.commands.root_support import _emit_json_compat as _emit_json_compat
from app.cli.commands.root_support import _get_client as _get_client
from app.cli.commands.root_support import _list_tasks_any as _list_tasks_any
from app.cli.commands.root_support import _require_daemon as _require_daemon
from app.cli.commands.root_support import _resolve_prefix as _resolve_prefix
from app.cli.commands.root_support import _resolve_ref as _resolve_ref
from app.cli.commands.root_support import _task_to_dict as _task_to_dict
from app.cli.commands.task_views import _cat_task as _cat_task
from app.cli.commands.task_views import _tasks_watch as _tasks_watch
from app.cli.commands.task_views import cancel as cancel
from app.cli.commands.task_views import list_alias as list_alias
from app.cli.commands.task_views import open_output as open_output
from app.cli.commands.task_views import show as show
from app.cli.commands.task_views import status_alias as status_alias
from app.cli.commands.task_views import tasks as tasks

# App

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


# Global state (set via callback before any command runs)

_plain_mode: bool = False
_json_mode: bool = False


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


app.command("upgrade-ytdlp")(upgrade_ytdlp)
app.command()(serve)
app.command()(ping)
app.command()(run)
app.command()(submit)
app.command()(attach)
app.command()(retry)
app.command()(tasks)
app.command(name="list")(list_alias)
app.command(name="status")(status_alias)
app.command()(show)
app.command(name="open")(open_output)
app.command()(cancel)
config_app.callback(invoke_without_command=True)(_config_default)
config_app.command(name="list")(config_list)
config_app.command(name="get")(config_get)
config_app.command(name="set")(config_set)
config_app.command(name="preset")(config_preset)
app.command()(doctor)
