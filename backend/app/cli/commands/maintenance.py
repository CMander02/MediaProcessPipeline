"""Maintenance for the mpp CLI."""

from __future__ import annotations

import typer
from app.cli.commands.config_values import _read_settings
from app.cli.commands.root_support import _get_client
from app.cli.context import get_cli_context as _command_context


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


def serve(
    host: str = typer.Option("localhost", help="Bind address"),
    port: int = typer.Option(18000, help="Port"),
    reload: bool = typer.Option(False, help="Enable auto-reload"),
):
    """启动 daemon 服务（前台运行）。"""
    from app.cli.serve import run_server

    run_server(host=host, port=port, reload=reload)


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


def doctor():
    """检查运行环境（ffmpeg、CUDA、模型文件、API key 等）。"""
    import importlib.util
    import pathlib
    import shutil

    from app.cli.context import get_cli_context
    from app.cli.display import console
    from app.cli.output import emit

    ok = "[green]+" if _command_context().plain else "[green]✓"
    err = "[red]x" if _command_context().plain else "[red]✗"
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
