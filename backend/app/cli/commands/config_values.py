"""Config values for the mpp CLI."""

from __future__ import annotations

from difflib import get_close_matches
from typing import Optional

import typer
from app.cli.commands.root_support import _emit_json_compat, _get_client
from app.cli.context import get_cli_context as _command_context

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


def _config_default(ctx: typer.Context):
    """查看/修改配置。子命令: list / get / set"""
    if ctx.invoked_subcommand is None:
        # Bare `mpp config` → show all (same as `mpp config list`)
        _config_list_impl(group=None)


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
    if json_out or (_command_context().output_mode in {"json", "jsonl"}):
        settings = _read_settings()
        if group:
            keys = _CONFIG_GROUPS.get(group, [])
            settings = {k: settings[k] for k in keys if k in settings}
        _emit_json_compat(settings, legacy_json=json_out)
    else:
        _config_list_impl(group=group)


def _config_list_impl(group: str | None) -> None:
    from app.cli.display import console
    from rich.table import Table

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

    if json_out or (_command_context().output_mode in {"json", "jsonl"}):
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
