"""Daemon status and verified release downloads."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import typer

from app.cli.commands.common import api_call, client
from app.cli.output import emit
from app.cli.renderers import render_fields

system_app = typer.Typer(help="系统运行状态", no_args_is_help=True)
update_app = typer.Typer(help="检查和下载软件更新", no_args_is_help=True)


def _status_text(data: dict) -> str:
    gpu = data.get("gpu") or {}
    workers = data.get("workers") or {}
    compute = workers.get("gpu") or {}
    downloads = workers.get("downloads") or {}
    disk = data.get("disk") or {}
    errors = data.get("errors") or {}
    lines = [
        f"Version\t{data.get('version', '')}",
        f"PID\t{data.get('pid', '')}",
        f"Started\t{data.get('started_at') or 'unknown'}",
    ]
    if (uptime := data.get("uptime_seconds")) is not None:
        lines.append(f"Uptime\t{uptime}s")
    elif data.get("started_at"):
        try:
            started = datetime.fromisoformat(data["started_at"])
            uptime = max(0, int((datetime.now(started.tzinfo) - started).total_seconds()))
            lines.append(f"Uptime\t{uptime}s")
        except ValueError:
            pass
    devices = gpu.get("devices") or []
    for device in devices:
        lines.append(
            f"GPU {device.get('index', '')}\t{device.get('name', '')}\t"
            f"VRAM {device.get('memory_used_mb')}/{device.get('memory_total_mb')} MiB"
        )
    if not devices:
        lines.append(f"GPU telemetry\t{gpu.get('error') or 'unavailable'}")
    if gpu.get("mpp_reserved_mb") is not None:
        lines.append(f"MPP reserved VRAM\t{gpu['mpp_reserved_mb']} MiB")
    models = data.get("models") or []
    lines.append(f"Loaded models\t{len(models)}")
    lines.extend(f"  {model.get('kind', '')}\t{model.get('name', '')}" for model in models)
    lines.extend(
        [
            f"Active compute workers\t{int(bool(compute.get('busy')))}",
            f"Active downloads\t{downloads.get('active', 0)}/{downloads.get('slots', 0)}",
            f"Queued tasks\t{workers.get('waiting', 0)}",
            f"Paused tasks\t{workers.get('paused', 0)}",
            f"Disk\t{disk.get('path', '')}\t{_disk_space(disk)}",
            f"Failed tasks (24h)\t{data.get('failed_tasks_24h', 0)}",
            f"Logged errors (24h)\t{errors.get('count', 0)}",
        ]
    )
    for entry in errors.get("recent") or []:
        lines.append(
            f"  {entry.get('time', '')}\t{entry.get('event') or entry.get('module', '')}\t"
            f"{entry.get('message', '')} ({entry.get('count', 1)})"
        )
    return "\n".join(lines)


def _disk_space(disk: dict) -> str:
    if disk.get("total") is None:
        return disk.get("error") or "unavailable"
    return (
        f"{disk['used'] / 1024**3:.2f} GiB used / {disk['total'] / 1024**3:.2f} GiB total, "
        f"{disk['free'] / 1024**3:.2f} GiB free"
    )


@system_app.command("status")
def status():
    """显示 GPU、模型、队列、磁盘、运行时间和最近错误。"""
    api = client()
    data = api_call(api.get_system_status)
    emit(data, text=lambda: _status_text(data))


@update_app.command("check")
def check(force: bool = typer.Option(False, "--force")):
    """查询最新版本和可下载的发布文件。"""
    api = client()
    data = api_call(lambda: api.check_update(force=force))
    fields = {
        key: data.get(key)
        for key in (
            "current_version",
            "latest_version",
            "update_available",
            "release_url",
        )
    }
    assets = [
        f"{asset.get('name', '')}\t{asset.get('size', 0)} bytes"
        + ("\tRecommended on this daemon" if asset.get("recommended") else "")
        for asset in data.get("assets") or []
    ]
    emit(data, text="\n".join([render_fields(fields), *assets]))


@update_app.command("download")
def download(asset: Optional[str] = typer.Option(None, "--asset")):
    """下载并校验发布包，显示服务端路径和安装说明。"""
    api = client()
    data = api_call(lambda: api.download_update(asset))
    emit(
        data,
        text=lambda: render_fields(
            {
                key: data.get(key)
                for key in (
                    "path",
                    "sha256",
                    "instructions",
                )
            }
        ),
    )
