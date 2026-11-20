"""Runtime settings, providers, models, flows, and source commands."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from enum import Enum
from functools import partial
from http.cookies import CookieError, SimpleCookie
from io import StringIO
from pathlib import Path
from typing import Any, Optional

import typer

from app.cli.client import MppClientError
from app.cli.commands.common import api_call, client
from app.cli.context import get_cli_context
from app.cli.output import (
    confirm_action,
    emit,
    emit_error,
    parse_assignments,
    parse_value,
    read_json_file,
    redact,
)
from app.cli.renderers import render_fields, render_source_collection, render_source_probe

provider_app = typer.Typer(help="Provider 连接与状态管理", no_args_is_help=True)
model_app = typer.Typer(help="模型目录、模型记录与运行绑定", no_args_is_help=True)
binding_app = typer.Typer(help="运行能力到 Provider 模型的绑定", no_args_is_help=True)
flow_app = typer.Typer(help="处理 flow profile 与默认选择", no_args_is_help=True)
source_app = typer.Typer(help="来源平台、预检、合集与认证", no_args_is_help=True)
source_config_app = typer.Typer(help="来源平台配置", no_args_is_help=True)
source_auth_app = typer.Typer(help="来源平台认证", no_args_is_help=True)
device_app = typer.Typer(help="计算设备查询与阶段设置", no_args_is_help=True)
model_app.add_typer(binding_app, name="binding")
source_app.add_typer(source_config_app, name="config")
source_app.add_typer(source_auth_app, name="auth")


def _settings(api) -> dict[str, Any]:
    return api_call(api.get_settings)


def _providers(settings: dict[str, Any]) -> list[dict[str, Any]]:
    value = settings.get("providers")
    return (
        [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    )


def _provider(settings: dict[str, Any], provider_id: str) -> dict[str, Any]:
    for item in _providers(settings):
        if str(item.get("id")) == provider_id:
            return item
    emit_error("provider_not_found", f"Provider {provider_id!r} was not found.", exit_code=4)


def _set_dotted(target: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    current = target
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def register_config_extensions(config_app: typer.Typer) -> None:
    @config_app.command("patch")
    def config_patch(
        source_file: Path = typer.Option(..., "--from", exists=True, dir_okay=False, readable=True),
    ):
        """从 JSON 对象批量更新 runtime settings。"""
        updates = read_json_file(source_file)
        api = client()
        result = api_call(lambda: api.patch_settings(updates))
        emit(
            result,
            text=json.dumps(redact(result), ensure_ascii=False, indent=2),
            redact_secrets=True,
        )

    @config_app.command("replace")
    def config_replace(
        source_file: Path = typer.Option(..., "--from", exists=True, dir_okay=False, readable=True),
        dry_run: bool = typer.Option(False, "--dry-run"),
        yes: bool = typer.Option(False, "--yes"),
    ):
        """使用完整 JSON 文档替换 runtime settings。"""
        from pydantic import ValidationError

        from app.core.settings import RuntimeSettings

        data = read_json_file(source_file)
        try:
            validated = RuntimeSettings.model_validate(data)
        except ValidationError as exc:
            emit_error(
                "settings_validation_failed",
                "Runtime settings validation failed.",
                detail=exc.errors(),
                exit_code=2,
            )
        preview = {
            "valid": True,
            "source": str(source_file),
            "fields": len(RuntimeSettings.model_fields),
        }
        if dry_run:
            emit(preview, text=json.dumps(preview, ensure_ascii=False, indent=2))
            return
        confirm_action("Replace the complete runtime settings document?", explicit_yes=yes)
        api = client()
        result = api_call(lambda: api.put_settings(validated.model_dump(mode="json")))
        emit(
            result,
            text=json.dumps(redact(result), ensure_ascii=False, indent=2),
            redact_secrets=True,
        )

    @config_app.command("export")
    def config_export(
        output: Optional[Path] = typer.Option(None, "--output", "-o"),
        redacted: bool = typer.Option(True, "--redacted/--no-redacted", help="输出中掩码 secret"),
    ):
        """导出 runtime settings；默认掩码 secret。"""
        api = client()
        result = _settings(api)
        safe = globals()["redact"](result) if redacted else result
        rendered = json.dumps(safe, ensure_ascii=False, indent=2, default=str)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered + "\n", encoding="utf-8")
            emit({"output": str(output), "redacted": redacted}, text=str(output))
        else:
            emit(safe, text=rendered, redact_secrets=redacted)

    @config_app.command("validate")
    def config_validate(
        source_file: Optional[Path] = typer.Option(
            None, "--from", exists=True, dir_okay=False, readable=True
        ),
    ):
        """使用 RuntimeSettings 校验当前或指定 JSON 设置。"""
        from pydantic import ValidationError

        from app.core.settings import RuntimeSettings

        api = client()
        data = read_json_file(source_file) if source_file else _settings(api)
        try:
            RuntimeSettings.model_validate(data)
        except ValidationError as exc:
            emit_error(
                "settings_validation_failed",
                "Runtime settings validation failed.",
                detail=exc.errors(),
                exit_code=2,
            )
        emit({"valid": True, "fields": len(RuntimeSettings.model_fields)}, text="valid")


@provider_app.command("list")
def provider_list():
    """列出 Provider。"""
    api = client()
    items = _providers(_settings(api))
    text = "ID\tTYPE\tENABLED\tMODELS\tNAME\n" + "\n".join(
        "\t".join(
            [
                str(item.get("id")),
                str(item.get("provider_type")),
                str(item.get("enabled")),
                str(len(item.get("models") or [])),
                str(item.get("name")),
            ]
        )
        for item in items
    )
    emit(items, text=text, redact_secrets=True)


@provider_app.command("show")
def provider_show(provider_id: str = typer.Argument(...)):
    """显示一个 Provider。"""
    api = client()
    item = _provider(_settings(api), provider_id)
    emit(item, text=json.dumps(redact(item), ensure_ascii=False, indent=2), redact_secrets=True)


@provider_app.command("add")
def provider_add(
    provider_id: str = typer.Argument(...),
    provider_type: str = typer.Option("openai_compatible", "--type"),
    api_base: str = typer.Option("", "--api-base"),
    name: str = typer.Option("", "--name"),
    api_key: str = typer.Option("", "--api-key"),
    cli_path: str = typer.Option("", "--cli-path"),
    api_mode: str = typer.Option("chat_completions", "--api-mode"),
):
    """添加 Provider。"""
    api = client()
    settings = _settings(api)
    providers = _providers(settings)
    if any(str(item.get("id")) == provider_id for item in providers):
        emit_error("provider_exists", f"Provider {provider_id!r} already exists.", exit_code=4)
    record = {
        "id": provider_id,
        "name": name or provider_id,
        "provider_type": provider_type,
        "enabled": True,
        "api_base": api_base,
        "api_key": api_key,
        "api_mode": api_mode,
        "cli_path": cli_path,
        "timeout_sec": 600,
        "headers": {},
        "extra_body": {},
        "balance": {"enabled": False, "endpoint_path": "", "method": "GET"},
        "models": [],
    }
    deleted = [item for item in settings.get("deleted_provider_ids", []) if item != provider_id]
    result = api_call(
        lambda: api.patch_settings(
            {"providers": [*providers, record], "deleted_provider_ids": deleted}
        )
    )
    emit(_provider(result, provider_id), text=provider_id, redact_secrets=True)


@provider_app.command("update")
def provider_update(
    provider_id: str = typer.Argument(...), values: list[str] = typer.Argument(...)
):
    """使用 KEY=VALUE 更新 Provider，支持点路径。"""
    patch = parse_assignments(values)
    if "id" in patch:
        emit_error("immutable_provider_id", "Provider id cannot be changed.", exit_code=2)
    api = client()
    settings = _settings(api)
    providers = _providers(settings)
    found = False
    for item in providers:
        if str(item.get("id")) == provider_id:
            found = True
            for key, value in patch.items():
                _set_dotted(item, key, value)
    if not found:
        emit_error("provider_not_found", f"Provider {provider_id!r} was not found.", exit_code=4)
    result = api_call(lambda: api.patch_settings({"providers": providers}))
    emit(_provider(result, provider_id), text=provider_id, redact_secrets=True)


def _provider_enabled(provider_id: str, enabled: bool) -> None:
    api = client()
    settings = _settings(api)
    providers = _providers(settings)
    found = False
    for item in providers:
        if str(item.get("id")) == provider_id:
            item["enabled"] = enabled
            found = True
    if not found:
        emit_error("provider_not_found", f"Provider {provider_id!r} was not found.", exit_code=4)
    result = api_call(lambda: api.patch_settings({"providers": providers}))
    emit(_provider(result, provider_id), text=f"{provider_id}\t{enabled}", redact_secrets=True)


@provider_app.command("enable")
def provider_enable(provider_id: str = typer.Argument(...)):
    """启用 Provider。"""
    _provider_enabled(provider_id, True)


@provider_app.command("disable")
def provider_disable(provider_id: str = typer.Argument(...)):
    """停用 Provider。"""
    _provider_enabled(provider_id, False)


@provider_app.command("delete")
def provider_delete(
    provider_id: str = typer.Argument(...), yes: bool = typer.Option(False, "--yes")
):
    """删除 Provider 并记录 deleted_provider_ids。"""
    confirm_action(f"Delete provider {provider_id}?", explicit_yes=yes)
    api = client()
    settings = _settings(api)
    providers = _providers(settings)
    if not any(str(item.get("id")) == provider_id for item in providers):
        emit_error("provider_not_found", f"Provider {provider_id!r} was not found.", exit_code=4)
    providers = [item for item in providers if str(item.get("id")) != provider_id]
    deleted = sorted(
        {*(str(item) for item in settings.get("deleted_provider_ids", [])), provider_id}
    )
    bindings = {
        key: value
        for key, value in (settings.get("runtime_model_bindings") or {}).items()
        if not isinstance(value, dict) or str(value.get("provider_id")) != provider_id
    }
    result = api_call(
        lambda: api.patch_settings(
            {
                "providers": providers,
                "deleted_provider_ids": deleted,
                "runtime_model_bindings": bindings,
            }
        )
    )
    emit(
        {"id": provider_id, "deleted": True, "providers": len(_providers(result))}, text=provider_id
    )


@provider_app.command("oauth-status")
def provider_oauth_status(provider_id: str = typer.Argument(...)):
    """检查 CLI OAuth Provider 状态。"""
    api = client()
    result = api_call(lambda: api.provider_oauth_status(provider_id))
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))


@provider_app.command("login")
def provider_login(provider_id: str = typer.Argument(...)):
    """交互登录 CLI OAuth Provider 并同步模型。"""
    from app.services.analysis.coding_plan_cli import resolve_coding_plan_executable

    api = client()
    provider = _provider(_settings(api), provider_id)
    provider_type = provider.get("provider_type")
    commands = {
        "codex_oauth": ["login"],
        "agy_oauth": [],
        "kimi_oauth": ["login"],
        "qoder_oauth": ["login"],
    }
    if provider_type not in commands:
        emit_error("unsupported_provider", "This provider does not use CLI OAuth.", exit_code=2)
    ctx = get_cli_context()
    if ctx.is_local and ctx.interactive and sys.stdout.isatty():
        executable = resolve_coding_plan_executable(provider_type, provider.get("cli_path") or "")
        if executable is None:
            emit_error(
                "provider_cli_not_found",
                "The provider executable was not found on this machine.",
                exit_code=4,
            )
        if provider_type == "agy_oauth":
            typer.echo("Sign in within Antigravity, then exit to sync models.", err=True)
        command = [str(executable), *commands[provider_type]]
        try:
            result = subprocess.run(command, check=False)
        except OSError:
            emit_error("provider_login_failed", "Could not start the provider CLI.", exit_code=1)
        except KeyboardInterrupt:
            raise typer.Exit(130) from None
        if result.returncode:
            emit_error(
                "provider_login_failed",
                f"Provider CLI exited with code {result.returncode}.",
                exit_code=1,
            )
    status = api_call(lambda: api.provider_oauth_status(provider_id))
    if not status.get("authenticated"):
        emit_error(
            "provider_not_authenticated",
            "Run provider login in a terminal on the daemon host under its account.",
            exit_code=1,
        )
    synced = api_call(lambda: api.sync_provider_models(provider_id))
    data = {"provider_id": provider_id, "authenticated": True, "models": synced.get("models") or []}
    emit(
        data,
        text=f"{provider_id}\tauthenticated\t{len(data['models'])} model(s)",
        redact_secrets=True,
    )


@provider_app.command("balance")
def provider_balance(provider_id: str = typer.Argument(...)):
    """查询 Provider 余额。"""
    api = client()
    result = api_call(lambda: api.provider_balance(provider_id))
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))


@model_app.command("list")
def model_list(
    provider_id: Optional[str] = typer.Option(None, "--provider"),
    capability: Optional[str] = typer.Option(None, "--capability"),
):
    """列出已配置模型。"""
    api = client()
    providers = _providers(_settings(api))
    records: list[dict[str, Any]] = []
    for provider in providers:
        if provider_id and provider.get("id") != provider_id:
            continue
        for model in provider.get("models") or []:
            if not isinstance(model, dict):
                continue
            caps = model.get("capabilities") or []
            if capability and capability not in caps and model.get("model_type") != capability:
                continue
            records.append({"provider_id": provider.get("id"), **model})
    text = "PROVIDER\tMODEL\tTYPE\tENABLED\n" + "\n".join(
        f"{item.get('provider_id')}\t{item.get('model_id')}\t{item.get('model_type')}\t{item.get('enabled')}"
        for item in records
    )
    emit(records, text=text)


@model_app.command("catalog")
def model_catalog(
    provider_id: str = typer.Argument(...), capability: str = typer.Option("", "--capability")
):
    """读取 Provider 模型 catalog。"""
    api = client()
    result = api_call(lambda: api.provider_catalog(provider_id, capability))
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))


@model_app.command("siliconflow-catalog")
def model_siliconflow_catalog():
    """列出 SiliconFlow ASR 可用模型。"""
    api = client()
    result = api_call(api.siliconflow_models)
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))


@model_app.command("sync")
def model_sync(provider_id: str = typer.Argument(...)):
    """从 Provider 同步模型记录。"""
    api = client()
    result = api_call(lambda: api.sync_provider_models(provider_id))
    emit(result, text=f"{provider_id}\t{len(result.get('models') or [])}")


@model_app.command("infer")
def model_infer(
    model_id: str = typer.Argument(...),
    provider_id: Optional[str] = typer.Option(None, "--provider"),
    model_type: Optional[str] = typer.Option(None, "--type"),
):
    """推断模型类型、能力和默认参数。"""
    payload = {"model_id": model_id}
    if provider_id:
        payload["provider_id"] = provider_id
    if model_type:
        payload["model_type"] = model_type
    api = client()
    result = api_call(lambda: api.infer_model_metadata(payload))
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))


@model_app.command("add")
def model_add(
    provider_id: str = typer.Argument(...),
    model_id: str = typer.Argument(...),
    model_type: str = typer.Option("llm", "--type"),
):
    """向 Provider 添加或替换一个模型记录。"""
    api = client()
    metadata = api_call(
        lambda: api.infer_model_metadata(
            {"provider_id": provider_id, "model_id": model_id, "model_type": model_type}
        )
    )
    settings = _settings(api)
    providers = _providers(settings)
    found = False
    for provider in providers:
        if provider.get("id") != provider_id:
            continue
        found = True
        models = [
            item
            for item in provider.get("models") or []
            if isinstance(item, dict) and item.get("model_id") != model_id
        ]
        models.append(metadata)
        provider["models"] = models
    if not found:
        emit_error("provider_not_found", f"Provider {provider_id!r} was not found.", exit_code=4)
    result = api_call(lambda: api.patch_settings({"providers": providers}))
    emit(_provider(result, provider_id), text=f"{provider_id}\t{model_id}", redact_secrets=True)


@model_app.command("remove")
def model_remove(
    provider_id: str = typer.Argument(...),
    model_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes"),
):
    """从 Provider 删除一个模型记录。"""
    confirm_action(f"Remove model {model_id} from {provider_id}?", explicit_yes=yes)
    api = client()
    settings = _settings(api)
    providers = _providers(settings)
    found = False
    found_model = False
    for provider in providers:
        if provider.get("id") == provider_id:
            found = True
            found_model = any(
                isinstance(item, dict) and item.get("model_id") == model_id
                for item in provider.get("models") or []
            )
            provider["models"] = [
                item
                for item in provider.get("models") or []
                if not isinstance(item, dict) or item.get("model_id") != model_id
            ]
    if not found:
        emit_error("provider_not_found", f"Provider {provider_id!r} was not found.", exit_code=4)
    if not found_model:
        emit_error(
            "model_not_found",
            f"Model {model_id!r} was not found in provider {provider_id!r}.",
            exit_code=4,
        )
    bindings = {
        key: value
        for key, value in (settings.get("runtime_model_bindings") or {}).items()
        if not (
            isinstance(value, dict)
            and str(value.get("provider_id")) == provider_id
            and str(value.get("model_id")) == model_id
        )
    }
    api_call(
        lambda: api.patch_settings({"providers": providers, "runtime_model_bindings": bindings})
    )
    emit(
        {"provider_id": provider_id, "model_id": model_id, "deleted": True},
        text=f"{provider_id}\t{model_id}",
    )


@model_app.command("local-asr")
def model_local_asr():
    """显示本地 sherpa ASR 模型与运行时状态。"""
    api = client()
    result = api_call(api.local_asr_models)
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))


@model_app.command("detect-uvr")
def model_detect_uvr():
    """探测本机 UVR 模型目录。"""
    api = client()
    result = api_call(api.detect_local_uvr)
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))


@binding_app.command("list")
def binding_list():
    """列出运行能力绑定。"""
    api = client()
    bindings = _settings(api).get("runtime_model_bindings") or {}
    emit(bindings, text=json.dumps(bindings, ensure_ascii=False, indent=2))


@binding_app.command("set")
def binding_set(
    capability: str = typer.Argument(...),
    provider_id: str = typer.Argument(...),
    model_id: str = typer.Argument(...),
):
    """设置一个运行能力绑定。"""
    api = client()
    settings = _settings(api)
    provider = _provider(settings, provider_id)
    models = [item for item in provider.get("models") or [] if isinstance(item, dict)]
    if not any(str(item.get("model_id")) == model_id for item in models):
        emit_error(
            "model_not_found",
            f"Model {model_id!r} was not found in provider {provider_id!r}.",
            exit_code=4,
        )
    bindings = dict(settings.get("runtime_model_bindings") or {})
    bindings[capability] = {
        "provider_id": provider_id,
        "model_id": model_id,
        "capability": capability,
    }
    result = api_call(lambda: api.patch_settings({"runtime_model_bindings": bindings}))
    value = (result.get("runtime_model_bindings") or {}).get(capability)
    emit(value, text=f"{capability}\t{provider_id}\t{model_id}")


@binding_app.command("unset")
def binding_unset(capability: str = typer.Argument(...)):
    """清除一个运行能力绑定。"""
    api = client()
    settings = _settings(api)
    bindings = dict(settings.get("runtime_model_bindings") or {})
    bindings.pop(capability, None)
    result = api_call(lambda: api.patch_settings({"runtime_model_bindings": bindings}))
    emit(result.get("runtime_model_bindings") or {}, text=capability)


@flow_app.command("list")
def flow_list():
    """列出 flow profiles 与当前默认选择。"""
    api = client()
    settings = _settings(api)
    data = {
        "profiles": settings.get("flow_profiles") or [],
        "active_defaults": settings.get("active_flow_defaults") or {},
    }
    emit(data, text=json.dumps(data, ensure_ascii=False, indent=2))


@flow_app.command("show")
def flow_show(flow_id: str = typer.Argument(...)):
    """显示一个 flow profile。"""
    api = client()
    profiles = _settings(api).get("flow_profiles") or []
    for profile in profiles:
        if isinstance(profile, dict) and str(profile.get("id")) == flow_id:
            emit(profile, text=json.dumps(profile, ensure_ascii=False, indent=2))
            return
    emit_error("flow_not_found", f"Flow profile {flow_id!r} was not found.", exit_code=4)


@flow_app.command("use")
def flow_use(flow_id: str = typer.Argument(...), source: str = typer.Option("default", "--source")):
    """设置默认 flow profile。"""
    api = client()
    settings = _settings(api)
    profiles = settings.get("flow_profiles") or []
    if not any(
        isinstance(profile, dict) and str(profile.get("id")) == flow_id for profile in profiles
    ):
        emit_error("flow_not_found", f"Flow profile {flow_id!r} was not found.", exit_code=4)
    defaults = dict(settings.get("active_flow_defaults") or {})
    defaults[source] = flow_id
    result = api_call(lambda: api.patch_settings({"active_flow_defaults": defaults}))
    emit(result.get("active_flow_defaults") or {}, text=f"{source}\t{flow_id}")


@source_app.command("list")
def source_list():
    """列出来源平台配置与认证状态。"""
    api = client()
    items = api_call(api.platforms)
    text = "ID\tSTATUS\tAUTH\tNAME\n" + "\n".join(
        f"{item.get('id')}\t{item.get('status')}\t{item.get('auth_status')}\t{item.get('name')}"
        for item in items
    )
    emit(items, text=text)


@source_app.command("show")
def source_show(platform: str = typer.Argument(...)):
    """显示一个来源平台配置。"""
    api = client()
    matches = [item for item in api_call(api.platforms) if item.get("id") == platform]
    if not matches:
        emit_error("platform_not_found", f"Platform {platform!r} was not found.", exit_code=4)
    emit(matches[0], text=json.dumps(matches[0], ensure_ascii=False, indent=2))


@source_app.command("probe")
def source_probe(url: str = typer.Argument(...)):
    """提取 URL 元数据。"""
    api = client()
    result = api_call(lambda: api.probe_source(url))
    emit(result, text=lambda: render_source_probe(result))


@source_app.command("collection")
def source_collection(url: str = typer.Argument(...)):
    """检查 Bilibili 分 P 或合集条目。"""
    api = client()
    result = api_call(lambda: api.bilibili_collection(url))
    emit(result, text=lambda: render_source_collection(result))


@source_app.command("ytdlp-status")
def source_ytdlp_status():
    """显示 daemon 使用的 yt-dlp 版本状态。"""
    api = client()
    result = api_call(api.ytdlp_status)
    emit(result, text=lambda: render_fields(result))


@source_app.command("ytdlp-upgrade")
def source_ytdlp_upgrade():
    """更新 daemon 环境中的 yt-dlp。"""
    api = client()
    result = api_call(api.upgrade_ytdlp)
    emit(result, text=json.dumps(result, ensure_ascii=False, indent=2))


@source_config_app.command("set")
def source_config_set(
    platform: str = typer.Argument(...),
    key: str = typer.Argument(...),
    value: str = typer.Argument(...),
):
    """更新一个来源平台配置键。"""
    api = client()
    parsed = parse_value(value)
    result = api_call(lambda: api.update_platform(platform, {key: parsed}))
    emit(
        {"platform": platform, "key": key, "value": parsed, "result": result},
        text=f"{platform}\t{key}={parsed}",
    )


@source_auth_app.command("status")
def source_auth_status(platform: str = typer.Argument(...)):
    """检查来源平台认证状态。"""
    api = client()
    result = api_call(lambda: api.source_auth_status(platform.lower()))
    emit(result, text=lambda: render_fields(redact(result)), redact_secrets=True)


@source_auth_app.command("login")
def source_auth_login(
    platform: str = typer.Argument(...),
    timeout: int = typer.Option(180, "--timeout", min=1, max=900),
):
    """显示 B 站登录二维码，或启动小红书与 X 的浏览器登录。"""
    api = client()
    if platform.lower() == "bilibili":
        _bilibili_login(api, timeout)
        return
    result = api_call(lambda: api.source_auth_login(platform.lower(), timeout))
    emit(result, text=json.dumps(redact(result), ensure_ascii=False, indent=2), redact_secrets=True)


def _bilibili_login(api, timeout: int) -> None:
    import qrcode

    data = api_call(api.bilibili_qr_generate)
    url, session_id = data.get("url"), data.get("session_id")
    if not url or not session_id:
        emit_error(
            "invalid_qr_response", "The daemon did not return a Bilibili QR login URL.", exit_code=1
        )
    deadline = time.monotonic() + min(timeout, data.get("expires_in") or timeout)
    if get_cli_context().output_mode == "text":
        qr = qrcode.QRCode()
        qr.add_data(url)
        qr.make()
        buffer = StringIO()
        qr.print_ascii(out=buffer, invert=True)
        typer.echo(buffer.getvalue(), err=True, nl=False)
    typer.echo(url, err=True)
    previous_state = None
    while time.monotonic() < deadline:
        result = api_call(lambda: api.bilibili_qr_poll(session_id))
        state = result.get("state")
        if state == "success":
            emit(
                result,
                text=result.get("message") or "Bilibili login succeeded.",
                redact_secrets=True,
            )
            return
        if state == "expired":
            emit_error(
                "qr_expired", "The Bilibili QR code expired. Request a new code.", exit_code=1
            )
        if state not in {"waiting", "scanned"}:
            emit_error(
                "qr_poll_failed", "The daemon returned an unexpected QR login state.", exit_code=1
            )
        if (
            state != previous_state
            and result.get("message")
            and get_cli_context().output_mode == "text"
        ):
            typer.echo(result["message"], err=True)
        previous_state = state
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(2.0, remaining))
    emit_error("qr_timeout", "Bilibili login timed out.", exit_code=1)


class CookiePlatform(str, Enum):
    bilibili = "bilibili"
    xiaohongshu = "xiaohongshu"
    twitter = "twitter"
    youtube = "youtube"


class CookieBrowser(str, Enum):
    chrome = "chrome"
    edge = "edge"
    firefox = "firefox"
    safari = "safari"


@source_auth_app.command("set-cookie")
def source_auth_set_cookie(
    platform: CookiePlatform = typer.Argument(...),
    source_file: Optional[Path] = typer.Option(None, "--from", exists=True, dir_okay=False),
    browser: Optional[CookieBrowser] = typer.Option(None, "--browser"),
):
    """从文件或标准输入配置 Cookie。YouTube 也支持服务端浏览器。"""
    api = client()
    if browser:
        if platform != CookiePlatform.youtube or source_file:
            emit_error(
                "invalid_options",
                "--browser requires YouTube and cannot be combined with --from.",
                exit_code=2,
            )
        operation = partial(api.patch_settings, {
            "youtube_cookies_browser": browser.value, "youtube_cookies_file": "",
        })
    else:
        if source_file is None and sys.stdin.isatty():
            emit_error(
                "cookie_input_required", "Pipe cookie data into stdin or use --from.", exit_code=2
            )
        try:
            text = (
                source_file.read_text(encoding="utf-8-sig") if source_file else sys.stdin.read()
            ).strip()
        except (OSError, UnicodeError):
            emit_error("cookie_read_failed", "Could not read UTF-8 cookie data.", exit_code=2)
        if not text:
            emit_error("cookie_input_required", "Cookie input is empty.", exit_code=2)
        if text[:7].casefold() == "cookie:":
            text = text[7:].strip()
        if platform == CookiePlatform.bilibili:
            cookies = SimpleCookie()
            try:
                cookies.load(text)
            except CookieError:
                emit_error("invalid_cookie", "Expected a Bilibili Cookie header.", exit_code=2)
            if not cookies.get("SESSDATA") or not cookies["SESSDATA"].value:
                emit_error(
                    "invalid_cookie",
                    "The Bilibili Cookie header must contain SESSDATA.",
                    exit_code=2,
                )
            names = {
                "SESSDATA": "bilibili_sessdata",
                "bili_jct": "bilibili_bili_jct",
                "DedeUserID": "bilibili_dede_user_id",
            }
            updates = {
                key: cookies[name].value if name in cookies else "" for name, key in names.items()
            }
            operation = partial(api.patch_settings, updates)
        elif platform == CookiePlatform.xiaohongshu:
            operation = partial(api.patch_settings, {"xiaohongshu_cookie": text})
        else:
            operation = partial(api.update_platform, platform.value, {"cookie": text})
    try:
        operation()
    except MppClientError as exc:
        emit_error(
            exc.code, f"Could not configure {platform.value} cookies.", exit_code=exc.exit_code
        )
    emit({"platform": platform.value, "configured": True}, text=f"{platform.value}\tconfigured")


@source_app.command("lookup")
def source_lookup(sources: list[str] = typer.Argument(...)):
    """按来源 URL 查找已有任务和归档。"""
    api = client()
    result = api_call(lambda: api.lookup_archives(sources))
    matches = result.get("matches") or {}
    errors = result.get("errors") or {}
    lines = []
    for source in sources:
        if source in errors:
            lines.append(f"{source}\terror\t{errors[source]}")
        elif not matches.get(source):
            lines.append(f"{source}\tno match")
        else:
            for item in matches[source]:
                identifier = item.get("task_id") or item.get("archive_id") or ""
                lines.append(
                    f"{source}\t{item.get('status', '')}\t{identifier}\t"
                    f"{item.get('title', '')}\t{item.get('path', '')}"
                )
    emit(result, text="\n".join(lines))
    if errors:
        raise typer.Exit(5 if len(errors) < len(sources) else 1)


_DEVICE_FIELDS = {
    "uvr": ("uvr_device",),
    "asr": ("sherpa_device", "llama_asr_device"),
    "diarization": ("pyannote_device",),
    "aligner": ("aligner_device",),
    "moss": ("moss_cpp_device",),
    "llm": ("local_llm_device",),
}


class DeviceStage(str, Enum):
    all = "all"
    uvr = "uvr"
    asr = "asr"
    diarization = "diarization"
    aligner = "aligner"
    moss = "moss"
    llm = "llm"


class ComputeDevice(str, Enum):
    auto = "auto"
    gpu = "gpu"
    cpu = "cpu"
    cuda = "cuda"
    mps = "mps"
    metal = "metal"


@device_app.command("list")
def device_list():
    """显示服务端可用硬件及各阶段配置。"""
    api = client()
    hardware = api_call(api.capabilities).get("compute_capabilities") or {}
    settings = _settings(api)
    configured = {
        field: settings.get(field, "auto") for fields in _DEVICE_FIELDS.values() for field in fields
    }
    data = {"compute_capabilities": hardware, "devices": configured}
    emit(data, text=lambda: render_fields(data))


@device_app.command("set")
def device_set(
    stage: DeviceStage = typer.Argument(...), device: ComputeDevice = typer.Argument(...)
):
    """设置一个或全部本地推理阶段的计算设备。"""
    from app.core.settings import patch_runtime_settings

    api = client()
    fields = _DEVICE_FIELDS.get(stage.value) or tuple(
        field for values in _DEVICE_FIELDS.values() for field in values
    )
    updates = dict.fromkeys(fields, device.value)
    if get_cli_context().is_local and not api.ping():
        result = api_call(lambda: patch_runtime_settings(updates).model_dump())
    else:
        result = api_call(lambda: api.patch_settings(updates))
    configured = {field: result.get(field, updates[field]) for field in fields}
    emit(configured, text=lambda: render_fields(configured))
