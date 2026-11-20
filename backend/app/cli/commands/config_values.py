"""Config values for the mpp CLI."""

from __future__ import annotations

from difflib import get_close_matches
from typing import Optional

import typer

from app.cli.commands.root_support import _emit_json_compat, _get_client
from app.cli.context import get_cli_context as _command_context
from app.cli.output import is_secret_key, mask_secret

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
        "local_llm_engine",
        "local_llm_name",
        "local_llm_model_path",
        "local_llm_mmproj_path",
        "local_llm_device",
        "local_llm_dtype",
        "local_llm_max_new_tokens",
        "local_llm_thinking",
        "local_llm_n_gpu_layers",
        "local_llm_n_ctx",
        "local_llm_n_batch",
        "local_llm_timeout_sec",
        "local_llm_keepalive_sec",
        "local_llm_concurrency",
        "polish_provider",
        "llm_polish_concurrency",
        "codex_max_concurrency",
    ],
    "providers": [
        "service_connections",
        "service_models",
        "providers",
        "deleted_provider_ids",
        "runtime_model_bindings",
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
    "local_asr": [
        "llama_cpp_binary_path",
        "llama_asr_model_path",
        "llama_asr_mmproj_path",
        "llama_asr_device",
        "llama_asr_concurrency",
        "llama_asr_max_new_tokens",
        "llama_asr_timeout_sec",
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
        "aligner_device",
        "moss_cpp_binary_path",
        "moss_cpp_model_path",
        "moss_cpp_device",
        "moss_cpp_threads",
        "moss_cpp_max_new_tokens",
        "moss_cpp_chunk_duration_sec",
        "moss_cpp_chunk_overlap_sec",
        "moss_cpp_timeout_sec",
    ],
    "diarization": [
        "enable_diarization",
        "hf_token",
        "hf_proxy",
        "pyannote_model_path",
        "pyannote_segmentation_path",
        "pyannote_embedding_path",
        "pyannote_device",
        "diarization_batch_size",
        "diarization_clustering_strategy",
        "diarization_clustering_threshold",
    ],
    "voiceprint": [
        "enable_voiceprint",
        "voiceprint_match_threshold",
        "voiceprint_suggest_threshold",
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
        "uvr_mdx_segment_size",
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
    "remote": [
        "remote_sync_enabled",
        "remote_server_url",
        "remote_api_token",
        "remote_worker_id",
        "remote_worker_name",
        "remote_sync_interval_sec",
        "remote_sync_upload_results",
        "remote_sync_download_results",
        "remote_sync_include_media",
        "remote_execution_enabled",
        "remote_preferred_worker_id",
        "remote_execution_offline_policy",
        "remote_execution_worker_enabled",
        "remote_execution_wait_sec",
        "cli_use_remote_server",
        "cli_local_files_stay_local",
        "cli_default_worker_id",
    ],
    "security": [
        "api_token",
        "remote_api_token",
        "anthropic_api_key",
        "openai_api_key",
        "custom_api_key",
        "deepseek_api_key",
        "hf_token",
        "hf_proxy",
        "bilibili_sessdata",
        "bilibili_bili_jct",
        "bilibili_dede_user_id",
        "allow_remote_filesystem",
        "siliconflow_api_key",
        "vlm_api_key",
        "kb_embedding_api_key",
        "jina_reader_api_key",
        "xiaohongshu_cookie",
        "network_proxy",
        "youtube_proxy",
    ],
    "bilibili": [
        "bilibili_sessdata",
        "bilibili_bili_jct",
        "bilibili_dede_user_id",
        "bilibili_preferred_quality",
        "bilibili_subtitle_engine",
        "bilibili_subtitle_strict_validation",
        "bilibili_subtitle_min_coverage",
        "bilibili_subtitle_allow_legacy_fallback",
    ],
    "concurrency": [
        "max_download_concurrency",
    ],
    "platforms": [
        "network_proxy",
        "youtube_cookies_file",
        "youtube_cookies_browser",
        "youtube_proxy",
        "youtube_preferred_quality",
        "ytdlp_auto_update",
        "xiaohongshu_cookie",
        "xiaohongshu_storage_state_path",
        "twitter_storage_state_path",
        "zhihu_browser_mode",
        "defuddle_enabled",
        "playwright_enabled",
        "jina_reader_enabled",
        "jina_reader_api_base",
        "jina_reader_api_key",
        "jina_reader_bypass_cache",
        "web_scrape_timeout_sec",
        "platform_configs",
    ],
    "vlm": [
        "vlm_api_base",
        "vlm_api_key",
        "vlm_model",
        "vlm_max_tokens",
        "vlm_concurrency",
        "vlm_timeout_sec",
    ],
    "kb": [
        "kb_enabled",
        "kb_embedding_api_base",
        "kb_embedding_api_key",
        "kb_embedding_model",
        "kb_embedding_dim",
        "kb_chunk_size_chars",
        "kb_chunk_overlap_chars",
    ],
    "storage": ["data_root", "media_retention_policy"],
    "workflow": [
        "pipeline_workflow",
        "generate_video_detail",
        "audio_processing_flow",
        "pipeline_overlap",
        "flow_profiles",
        "active_flow_defaults",
    ],
    "system": ["update_repo"],
}


def _mask(key: str, value) -> str:
    return str(mask_secret(value) if is_secret_key(key) else value)


def _use_daemon(client) -> bool:
    ctx = _command_context()
    return not ctx.is_local or ctx.remote_preference is True or client.ping()


def _read_settings() -> dict:
    from app.cli.output import redact

    client = _get_client()
    if _use_daemon(client):
        from app.cli.commands.common import api_call

        return redact(api_call(client.get_settings))
    from app.core.settings import get_runtime_settings

    return redact(get_runtime_settings().model_dump())


def _all_valid_keys() -> list[str]:
    from app.core.settings import RuntimeSettings

    return list(RuntimeSettings.model_fields.keys())


def _config_default(ctx: typer.Context):
    """查看/修改配置。子命令: list / get / set"""
    if ctx.invoked_subcommand is None:
        config_list(group=None, json_out=False)


def config_list(
    group: Optional[str] = typer.Option(
        None,
        "--group",
        "-g",
        help="分组: llm/asr/uvr/diarization/subtitle/paths/security/bilibili/concurrency/remote",
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
    from rich.table import Table
    from rich.text import Text

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
        if isinstance(v, list):
            value = f"[{len(v)} items]" if v else "[]"
        elif isinstance(v, dict):
            value = f"{{{len(v)} keys}}" if v else "{}"
        else:
            value = _mask(k, v)
        table.add_row(k, Text(value))

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
    if _use_daemon(client):
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
            "sherpa_model_id": "qwen3-asr-0.6b-int8",
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
    if _use_daemon(client):
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
