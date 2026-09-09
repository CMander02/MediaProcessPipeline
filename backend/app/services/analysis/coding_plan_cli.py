"""OAuth-backed coding-plan CLI adapters for text and image-understanding calls.

The application never reads or copies OAuth tokens.  It invokes the installed
Codex, Antigravity, Kimi Code, or Qoder CLI, leaving credential storage and
refresh to the CLI that owns the login session.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from app.core.network import runtime_proxy_url

CODEX_PROVIDER_TYPE = "codex_oauth"
AGY_PROVIDER_TYPE = "agy_oauth"
KIMI_PROVIDER_TYPE = "kimi_oauth"
QODER_PROVIDER_TYPE = "qoder_oauth"
CODING_PLAN_PROVIDER_TYPES = {
    CODEX_PROVIDER_TYPE,
    AGY_PROVIDER_TYPE,
    KIMI_PROVIDER_TYPE,
    QODER_PROVIDER_TYPE,
}

_CLI_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}
_CODEX_DISABLED_FEATURES = (
    "plugins",
    "plugin_sharing",
    "apps",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "computer_use",
    "image_generation",
    "multi_agent",
    "goals",
    "tool_suggest",
    "shell_tool",
    "workspace_dependencies",
)


class CodingPlanCLIError(RuntimeError):
    """A coding-plan CLI is unavailable, unauthenticated, or failed."""


def is_coding_plan_provider(provider_type: str) -> bool:
    return str(provider_type or "").strip().lower() in CODING_PLAN_PROVIDER_TYPES


def agy_model_id(display_name: str) -> str:
    """Convert AGY's display-only model list into its command-line model id."""

    value = str(display_name or "").strip().lower()
    return re.sub(r"[^a-z0-9.]+", "-", value).strip("-")


def _agy_catalog_model(raw_value: str) -> dict[str, str] | None:
    """Parse the tabular ``agy models`` output into one stable catalog entry."""

    value = str(raw_value or "").strip()
    if not value:
        return None
    columns = re.split(r"\t+|\s{2,}", value, maxsplit=1)
    if len(columns) == 2 and re.fullmatch(r"[a-zA-Z0-9._-]+", columns[0]):
        model_id, display_name = columns[0].strip(), columns[1].strip()
    else:
        display_name = value
        model_id = agy_model_id(display_name)
    if not model_id:
        return None
    return {
        "id": model_id,
        "display_name": display_name or model_id,
        "model_type": "llm",
        "cli_model_name": display_name or model_id,
    }


def _configured_path(value: str) -> Path | None:
    raw = str(value or "").strip().strip('"')
    if not raw:
        return None
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    return path if path.is_file() else None


def _resolve_agy_executable(cli_path: str = "") -> Path | None:
    configured = _configured_path(cli_path)
    if configured is not None:
        return configured

    discovered = shutil.which("agy.exe") or shutil.which("agy")
    if discovered and Path(discovered).suffix.lower() == ".exe":
        return Path(discovered)

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidate = Path(local_app_data) / "agy" / "bin" / "agy.exe"
    return candidate if candidate.is_file() else None


def _resolve_codex_executable(cli_path: str = "") -> Path | None:
    configured = _configured_path(cli_path)
    if configured is not None and configured.suffix.lower() == ".exe":
        return configured

    discovered = shutil.which("codex.exe") or shutil.which("codex")
    if discovered and Path(discovered).suffix.lower() not in {".cmd", ".bat", ".ps1"}:
        return Path(discovered)

    app_data = os.environ.get("APPDATA", "")
    package_root = Path(app_data) / "npm" / "node_modules" / "@openai" / "codex"
    if package_root.is_dir():
        candidates = sorted(
            package_root.glob("node_modules/@openai/codex-*/vendor/*/bin/codex.exe")
        )
        if candidates:
            return candidates[0]
    return None


def _resolve_kimi_executable(cli_path: str = "") -> Path | None:
    configured = _configured_path(cli_path)
    if configured is not None:
        return configured

    discovered = shutil.which("kimi.exe") or shutil.which("kimi")
    if discovered and Path(discovered).suffix.lower() not in {".cmd", ".bat", ".ps1"}:
        return Path(discovered)

    candidate = Path.home() / ".kimi-code" / "bin" / "kimi.exe"
    return candidate if candidate.is_file() else None


def _resolve_qoder_executable(cli_path: str = "") -> Path | None:
    configured = _configured_path(cli_path)
    if configured is not None:
        return configured

    discovered = (
        shutil.which("qoderclicn.exe")
        or shutil.which("qoderclicn")
        or shutil.which("qoder.exe")
        or shutil.which("qoder")
    )
    if discovered:
        return Path(discovered)

    candidates = (
        Path.home() / ".qoder-cn" / "bin" / "qoderclicn" / "qoderclicn.exe",
        Path.home() / ".qoder" / "bin" / "qoder.exe",
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def resolve_coding_plan_executable(provider_type: str, cli_path: str = "") -> Path | None:
    normalized = str(provider_type or "").strip().lower()
    if normalized == CODEX_PROVIDER_TYPE:
        return _resolve_codex_executable(cli_path)
    if normalized == AGY_PROVIDER_TYPE:
        return _resolve_agy_executable(cli_path)
    if normalized == KIMI_PROVIDER_TYPE:
        return _resolve_kimi_executable(cli_path)
    if normalized == QODER_PROVIDER_TYPE:
        return _resolve_qoder_executable(cli_path)
    return None


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["PYTHONUTF8"] = "1"
    proxy = runtime_proxy_url(prefer_windows_proxy=True)
    proxy_keys = (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    )
    if proxy == "":
        for key in proxy_keys:
            env.pop(key, None)
    elif proxy:
        for key in proxy_keys:
            if os.name == "nt" and key.islower():
                env.pop(key, None)
                continue
            env[key] = proxy
    return env


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) | int(
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )


async def _run_cli(
    command: list[str],
    *,
    cwd: Path,
    timeout_sec: float,
    stdin_text: str | None = None,
    env_override: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            env=env_override or _subprocess_env(),
            stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=_creation_flags(),
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        raise CodingPlanCLIError(f"无法启动 OAuth CLI：{exc}") from exc
    payload = stdin_text.encode("utf-8") if stdin_text is not None else None
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(payload), timeout=max(1.0, timeout_sec)
        )
    except asyncio.CancelledError:
        await _terminate_process_tree(process)
        raise
    except asyncio.TimeoutError:
        await _terminate_process_tree(process)
        raise
    return (
        int(process.returncode or 0),
        stdout.decode("utf-8", errors="replace").strip(),
        stderr.decode("utf-8", errors="replace").strip(),
    )


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    """Terminate the exact CLI process tree so helper children cannot leak."""

    if process.returncode is None:
        if os.name == "nt":
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill.exe",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
                )
                await asyncio.wait_for(killer.wait(), timeout=10)
            except (OSError, asyncio.TimeoutError):
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                process.kill()
    try:
        await asyncio.wait_for(process.communicate(), timeout=10)
    except (ProcessLookupError, asyncio.TimeoutError):
        if process.returncode is None:
            process.kill()
            await process.communicate()


async def _remove_temp_dir(path: Path) -> None:
    for delay_sec in (0.0, 0.1, 0.25, 0.5, 1.0):
        if delay_sec:
            await asyncio.sleep(delay_sec)
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            continue
    shutil.rmtree(path, ignore_errors=True)


def _diagnostic(stderr: str, stdout: str = "") -> str:
    lines = [line.strip() for line in f"{stderr}\n{stdout}".splitlines() if line.strip()]
    preferred = [
        line
        for line in lines
        if any(token in line.lower() for token in ("error", "failed", "invalid", "login", "oauth"))
    ]
    selected = preferred[-1] if preferred else (lines[-1] if lines else "unknown CLI error")
    return selected[:600]


def _qoder_subprocess_env() -> dict[str, str]:
    env = _subprocess_env()
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        env.pop(key, None)
    return env


def _lock(provider_type: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    key = (id(loop), provider_type)
    lock = _CLI_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _CLI_LOCKS[key] = lock
    return lock


def _codex_current_model() -> str:
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    config_file = codex_home / "config.toml"
    if not config_file.is_file():
        return ""
    try:
        data = tomllib.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return ""
    return str(data.get("model") or "").strip()


def _agy_current_model() -> str:
    settings_file = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
    if not settings_file.is_file():
        return ""
    try:
        data = json.loads(settings_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("model") or "").strip() if isinstance(data, dict) else ""


def _kimi_config() -> dict[str, Any]:
    kimi_home = Path(os.environ.get("KIMI_CODE_HOME") or (Path.home() / ".kimi-code"))
    config_file = kimi_home / "config.toml"
    if not config_file.is_file():
        return {}
    try:
        data = tomllib.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _kimi_current_model() -> str:
    return str(_kimi_config().get("default_model") or "").strip()


def _kimi_model_catalog() -> list[dict[str, str]]:
    raw_models = _kimi_config().get("models")
    if not isinstance(raw_models, dict):
        return []

    models: list[dict[str, str]] = []
    for alias, raw_model in raw_models.items():
        if not isinstance(raw_model, dict):
            continue
        provider = str(raw_model.get("provider") or "").strip().lower()
        model_id = str(alias or "").strip()
        if provider != "managed:kimi-code" or not model_id:
            continue
        models.append(
            {
                "id": model_id,
                "display_name": str(raw_model.get("display_name") or model_id).strip(),
                "model_type": "llm",
                "cli_model_name": model_id,
            }
        )
    return models


def _qoder_config() -> dict[str, Any]:
    configured_dir = str(os.environ.get("QODER_CONFIG_DIR") or "").strip()
    if configured_dir:
        config_dir = Path(configured_dir)
    else:
        cn_dir = Path.home() / ".qoder-cn"
        config_dir = cn_dir if cn_dir.is_dir() else Path.home() / ".qoder"
    settings_file = config_dir / "settings.json"
    if not settings_file.is_file():
        return {}
    try:
        data = json.loads(settings_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _qoder_current_model() -> str:
    environment_model = str(os.environ.get("QODER_MODEL") or "").strip()
    if environment_model:
        return environment_model
    model = _qoder_config().get("model")
    if isinstance(model, dict):
        return str(model.get("name") or "").strip()
    return str(model or "").strip()


def _qoder_model_ids(stdout: str) -> list[str]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        payload = None

    raw_models: Any = payload
    if isinstance(payload, dict):
        raw_models = payload.get("models") or payload.get("data") or []

    candidates: list[str] = []
    if isinstance(raw_models, list):
        for item in raw_models:
            if isinstance(item, dict):
                value = item.get("id") or item.get("model_id") or item.get("name")
            else:
                value = item
            if str(value or "").strip():
                candidates.append(str(value).strip())
    else:
        ansi_pattern = re.compile(r"\x1b\[[0-9;]*m")
        for raw_line in stdout.splitlines():
            line = ansi_pattern.sub("", raw_line).strip().lstrip("-*>• ").strip()
            if not line or set(line) <= {"-", "=", " ", "\t"}:
                continue
            first_column = re.split(r"\s{2,}|\t", line, maxsplit=1)[0].strip()
            if first_column.lower() in {"model", "model id", "id"}:
                continue
            candidates.append(first_column)

    return list(dict.fromkeys(candidates))


def _provider_name(provider_type: str) -> str:
    return {
        CODEX_PROVIDER_TYPE: "Codex",
        AGY_PROVIDER_TYPE: "Antigravity",
        KIMI_PROVIDER_TYPE: "Kimi Code",
        QODER_PROVIDER_TYPE: "QoderCN",
    }.get(provider_type, "OAuth")


async def coding_plan_status(
    provider_type: str,
    *,
    cli_path: str = "",
    timeout_sec: float = 30.0,
) -> dict[str, Any]:
    normalized = str(provider_type or "").strip().lower()
    executable = resolve_coding_plan_executable(normalized, cli_path)
    provider_name = _provider_name(normalized)
    if executable is None:
        return {
            "provider_type": normalized,
            "installed": False,
            "authenticated": False,
            "executable": "",
            "current_model": "",
            "models": [],
            "message": f"未找到 {provider_name} CLI。",
        }

    try:
        if normalized == CODEX_PROVIDER_TYPE:
            code, stdout, stderr = await _run_cli(
                [str(executable), "login", "status"],
                cwd=Path(tempfile.gettempdir()),
                timeout_sec=timeout_sec,
            )
            current_model = _codex_current_model()
            authenticated = code == 0 and "logged in" in f"{stdout}\n{stderr}".lower()
            models = [current_model] if current_model else []
        elif normalized == AGY_PROVIDER_TYPE:
            code, stdout, stderr = await _run_cli(
                [str(executable), "models"],
                cwd=Path(tempfile.gettempdir()),
                timeout_sec=timeout_sec,
            )
            models = [line.strip() for line in stdout.splitlines() if line.strip()]
            current_model = _agy_current_model()
            authenticated = code == 0 and bool(models)
        elif normalized == KIMI_PROVIDER_TYPE:
            code, stdout, stderr = await _run_cli(
                [str(executable), "provider", "list"],
                cwd=Path(tempfile.gettempdir()),
                timeout_sec=timeout_sec,
            )
            provider_output = f"{stdout}\n{stderr}".lower()
            authenticated = (
                code == 0
                and "managed:kimi-code" in provider_output
                and "source=oauth" in provider_output
            )
            current_model = _kimi_current_model()
            models = [model["id"] for model in _kimi_model_catalog()]
        elif normalized == QODER_PROVIDER_TYPE:
            code, stdout, stderr = await _run_cli(
                [str(executable), "--list-models", "--output-format", "json"],
                cwd=Path(tempfile.gettempdir()),
                timeout_sec=timeout_sec,
            )
            models = _qoder_model_ids(stdout)
            current_model = _qoder_current_model()
            authenticated = code == 0 and bool(models)
        else:
            raise CodingPlanCLIError(f"不支持的 OAuth CLI Provider：{provider_type}")
    except asyncio.TimeoutError:
        return {
            "provider_type": normalized,
            "installed": True,
            "authenticated": False,
            "executable": str(executable),
            "current_model": "",
            "models": [],
            "message": f"{provider_name} CLI 登录检测超时。",
        }
    except CodingPlanCLIError as exc:
        return {
            "provider_type": normalized,
            "installed": True,
            "authenticated": False,
            "executable": str(executable),
            "current_model": "",
            "models": [],
            "message": str(exc),
        }

    message = (
        f"已连接 {provider_name} OAuth 会话。"
        if authenticated
        else f"{provider_name} CLI 可用；请先在终端完成 OAuth 登录。{_diagnostic(stderr, stdout)}"
    )
    return {
        "provider_type": normalized,
        "installed": True,
        "authenticated": authenticated,
        "executable": str(executable),
        "current_model": current_model,
        "models": models,
        "message": message,
    }


async def coding_plan_models(provider_type: str, *, cli_path: str = "") -> list[dict[str, str]]:
    status = await coding_plan_status(provider_type, cli_path=cli_path)
    if not status["installed"] or not status["authenticated"]:
        raise CodingPlanCLIError(str(status["message"]))

    current_model = str(status.get("current_model") or "").strip()
    default_name = "CLI 当前默认模型"
    if current_model:
        default_name = f"CLI 当前默认模型（{current_model}）"
    default_model = {"id": "default", "display_name": default_name, "model_type": "llm"}
    if current_model:
        default_model["cli_model_name"] = current_model
    models: list[dict[str, str]] = [default_model]
    catalog: list[dict[str, str]] = []
    if str(provider_type).strip().lower() == AGY_PROVIDER_TYPE:
        catalog.extend(
            model
            for raw_value in status.get("models", [])
            if (model := _agy_catalog_model(str(raw_value))) is not None
        )
    elif str(provider_type).strip().lower() == KIMI_PROVIDER_TYPE:
        catalog.extend(_kimi_model_catalog())
    elif str(provider_type).strip().lower() == QODER_PROVIDER_TYPE:
        for model_id in status.get("models", []):
            normalized_id = str(model_id).strip()
            if normalized_id and normalized_id != "default":
                catalog.append(
                    {
                        "id": normalized_id,
                        "display_name": normalized_id,
                        "model_type": "llm",
                        "cli_model_name": normalized_id,
                    }
                )
    elif current_model:
        catalog.append({"id": current_model, "display_name": current_model, "model_type": "llm"})

    current_aliases = {
        current_model.casefold(),
        agy_model_id(current_model).casefold(),
    } if current_model else set()
    for model in catalog:
        aliases = {
            str(model.get(key) or "").strip().casefold()
            for key in ("id", "display_name", "cli_model_name")
            if str(model.get(key) or "").strip()
        }
        if aliases.intersection(current_aliases):
            continue
        models.append(model)
    return models


def _text_backend_prompt(prompt: str) -> str:
    return (
        "你是 MediaProcessPipeline 的纯文本模型后端。只处理下面的请求并返回最终文本；"
        "不要读取工作区文件，不要运行命令，不要调用工具。\n\n"
        f"<request>\n{prompt}\n</request>"
    )


def _stage_image_paths(image_paths: list[Path] | None, cwd: Path) -> list[Path]:
    staged: list[Path] = []
    for index, raw_path in enumerate(image_paths or [], 1):
        source = Path(raw_path)
        if not source.is_file():
            raise CodingPlanCLIError(f"图文理解图片不存在：{source}")
        suffix = source.suffix.lower() or ".img"
        target = cwd / f"image-{index:02d}{suffix}"
        shutil.copy2(source, target)
        staged.append(target)
    return staged


def _multimodal_backend_prompt(prompt: str, image_paths: list[Path]) -> str:
    image_names = "、".join(path.name for path in image_paths)
    return (
        "你是 MediaProcessPipeline 的图文理解模型后端。只分析当前请求和列出的图片文件，"
        "不要读取其他文件，不要运行命令，只返回最终文本。\n"
        f"图片文件：{image_names}\n\n"
        f"<request>\n{prompt}\n</request>"
    )


async def _call_codex(
    executable: Path,
    model: str,
    prompt: str,
    timeout_sec: float,
    reasoning_effort: str = "",
    image_paths: list[Path] | None = None,
) -> str:
    with tempfile.TemporaryDirectory(
        prefix="mpp-codex-", ignore_cleanup_errors=True
    ) as temp_dir:
        cwd = Path(temp_dir)
        staged_images = _stage_image_paths(image_paths, cwd)
        output_file = cwd / "response.txt"
        command = [str(executable)]
        for feature in _CODEX_DISABLED_FEATURES:
            command.extend(["--disable", feature])
        command.extend(
            [
                "exec",
                "--sandbox",
                "read-only",
                "--cd",
                str(cwd),
                "--skip-git-repo-check",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--color",
                "never",
                "--output-last-message",
                str(output_file),
            ]
        )
        selected_model = model
        if not selected_model or selected_model.lower() == "default":
            selected_model = _codex_current_model()
        if selected_model:
            command.extend(["--model", selected_model])
        selected_effort = str(reasoning_effort or "").strip().lower()
        if selected_effort:
            command.extend(["--config", f'model_reasoning_effort="{selected_effort}"'])
        for image_path in staged_images:
            command.extend(["--image", str(image_path)])
        command.append("-")
        try:
            code, stdout, stderr = await _run_cli(
                command,
                cwd=cwd,
                timeout_sec=timeout_sec,
                stdin_text=(
                    _multimodal_backend_prompt(prompt, staged_images)
                    if staged_images
                    else _text_backend_prompt(prompt)
                ),
            )
        except asyncio.TimeoutError as exc:
            raise CodingPlanCLIError(f"Codex OAuth 推理超过 {int(timeout_sec)} 秒。") from exc
        content = output_file.read_text(encoding="utf-8").strip() if output_file.is_file() else ""
        content = content or stdout.strip()
        if code != 0:
            raise CodingPlanCLIError(f"Codex OAuth 推理失败：{_diagnostic(stderr, stdout)}")
        if not content:
            raise CodingPlanCLIError("Codex OAuth 推理完成，但没有返回文本。")
        return content


async def _call_agy_once(
    executable: Path,
    model: str,
    prompt_file: Path,
    timeout_sec: float,
) -> tuple[int, str, str]:
    command = [str(executable), "--sandbox"]
    if model and model.lower() != "default":
        command.extend(["--model", model])
    command.extend(
        [
            "--print",
            "读取当前目录中的 request.txt，严格执行其中的请求，只返回最终文本。",
            "--print-timeout",
            f"{max(1, int(timeout_sec + 30))}s",
        ]
    )
    return await _run_cli(command, cwd=prompt_file.parent, timeout_sec=timeout_sec)


async def _call_agy(
    executable: Path,
    model: str,
    prompt: str,
    timeout_sec: float,
    image_paths: list[Path] | None = None,
) -> str:
    with tempfile.TemporaryDirectory(prefix="mpp-agy-") as temp_dir:
        cwd = Path(temp_dir)
        staged_images = _stage_image_paths(image_paths, cwd)
        prompt_file = cwd / "request.txt"
        prompt_file.write_text(
            _multimodal_backend_prompt(prompt, staged_images)
            if staged_images
            else _text_backend_prompt(prompt),
            encoding="utf-8",
        )
        try:
            code, stdout, stderr = await _call_agy_once(
                executable,
                model,
                prompt_file,
                timeout_sec,
            )
        except asyncio.TimeoutError as exc:
            raise CodingPlanCLIError(f"Antigravity OAuth 推理超过 {int(timeout_sec)} 秒。") from exc
        if code != 0:
            raise CodingPlanCLIError(f"Antigravity OAuth 推理失败：{_diagnostic(stderr, stdout)}")
        content = stdout.strip()
        if not content:
            raise CodingPlanCLIError("Antigravity OAuth 推理完成，但没有返回文本。")
        return content


def _kimi_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            text = block.get("text") or block.get("content")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts).strip()


def _parse_kimi_stream_json(stdout: str) -> str:
    responses: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("role") != "assistant":
            continue
        content = _kimi_content_text(event.get("content"))
        if content:
            responses.append(content)
    return responses[-1] if responses else ""


async def _call_kimi(
    executable: Path,
    model: str,
    prompt: str,
    timeout_sec: float,
    reasoning_effort: str = "",
    image_paths: list[Path] | None = None,
) -> str:
    cwd = Path(tempfile.mkdtemp(prefix="mpp-kimi-"))
    try:
        staged_images = _stage_image_paths(image_paths, cwd)
        prompt_file = cwd / "request.txt"
        prompt_file.write_text(
            _multimodal_backend_prompt(prompt, staged_images)
            if staged_images
            else _text_backend_prompt(prompt),
            encoding="utf-8",
        )
        command = [str(executable)]
        selected_model = model
        if not selected_model or selected_model.lower() == "default":
            selected_model = _kimi_current_model()
        if selected_model:
            command.extend(["--model", selected_model])
        command.extend(
            [
                "--prompt",
                (
                    "读取当前工作目录中的 request.txt，将文件内容作为唯一请求执行。"
                    "仅按 request.txt 的说明读取其中列出的图片文件，只返回最终文本。"
                ),
            ]
        )
        try:
            env = _subprocess_env()
            selected_effort = str(reasoning_effort or "").strip().lower()
            if selected_effort:
                env["KIMI_MODEL_THINKING_EFFORT"] = selected_effort
            code, stdout, stderr = await _run_cli(
                command,
                cwd=cwd,
                timeout_sec=timeout_sec,
                env_override=env,
            )
        except asyncio.TimeoutError as exc:
            raise CodingPlanCLIError(f"Kimi Code OAuth 推理超过 {int(timeout_sec)} 秒。") from exc
        if code != 0:
            raise CodingPlanCLIError(f"Kimi Code OAuth 推理失败：{_diagnostic(stderr, stdout)}")
        content = stdout.strip()
        if not content:
            raise CodingPlanCLIError("Kimi Code OAuth 推理完成，但没有返回文本。")
        return content
    finally:
        await _remove_temp_dir(cwd)


async def _call_qoder(
    executable: Path,
    model: str,
    prompt: str,
    timeout_sec: float,
    image_paths: list[Path] | None = None,
) -> str:
    cwd = Path(tempfile.mkdtemp(prefix="mpp-qoder-"))
    try:
        staged_images = _stage_image_paths(image_paths, cwd)
        command = [
            str(executable),
            "--print",
            "--output-format",
            "text",
            "--no-session-persistence",
            "--permission-mode",
            "dont_ask",
            "--tools",
            "",
            "--max-model-request-retries",
            "1",
            "--cwd",
            str(cwd),
        ]
        selected_model = model
        if not selected_model or selected_model.lower() == "default":
            selected_model = _qoder_current_model()
        if selected_model:
            command.extend(["--model", selected_model])
        for image_path in staged_images:
            command.extend(["--attachment", str(image_path)])
        command.extend(
            [
                "--",
                _multimodal_backend_prompt(prompt, staged_images)
                if staged_images
                else _text_backend_prompt(prompt),
            ]
        )
        try:
            code, stdout, stderr = await _run_cli(
                command,
                cwd=cwd,
                timeout_sec=timeout_sec,
                env_override=_qoder_subprocess_env(),
            )
        except asyncio.TimeoutError as exc:
            raise CodingPlanCLIError(f"QoderCN OAuth 推理超过 {int(timeout_sec)} 秒。") from exc
        if code != 0:
            raise CodingPlanCLIError(f"QoderCN OAuth 推理失败：{_diagnostic(stderr, stdout)}")
        content = stdout.strip()
        if not content:
            raise CodingPlanCLIError("QoderCN OAuth 推理完成，但没有返回文本。")
        return content
    finally:
        await _remove_temp_dir(cwd)


async def call_coding_plan_cli(
    provider_type: str,
    *,
    model: str,
    prompt: str,
    cli_path: str = "",
    timeout_sec: float = 600.0,
    reasoning_effort: str = "",
    image_paths: list[Path] | None = None,
) -> str:
    normalized = str(provider_type or "").strip().lower()
    executable = resolve_coding_plan_executable(normalized, cli_path)
    if executable is None:
        cli_name = _provider_name(normalized)
        raise CodingPlanCLIError(f"未找到 {cli_name} CLI；请安装 CLI 或填写可执行文件路径。")

    async def invoke() -> str:
        if normalized == CODEX_PROVIDER_TYPE:
            return await _call_codex(
                executable,
                model,
                prompt,
                timeout_sec,
                reasoning_effort=reasoning_effort,
                image_paths=image_paths,
            )
        if normalized == AGY_PROVIDER_TYPE:
            return await _call_agy(
                executable, model, prompt, timeout_sec, image_paths=image_paths
            )
        if normalized == KIMI_PROVIDER_TYPE:
            return await _call_kimi(
                executable,
                model,
                prompt,
                timeout_sec,
                reasoning_effort=reasoning_effort,
                image_paths=image_paths,
            )
        if normalized == QODER_PROVIDER_TYPE:
            return await _call_qoder(
                executable, model, prompt, timeout_sec, image_paths=image_paths
            )
        raise CodingPlanCLIError(f"不支持的 OAuth CLI Provider：{provider_type}")

    # These headless calls are ephemeral and use isolated working directories,
    # so subtitle chunks can consume the configured concurrency.
    if normalized in {CODEX_PROVIDER_TYPE, KIMI_PROVIDER_TYPE, QODER_PROVIDER_TYPE}:
        return await invoke()
    async with _lock(normalized):
        return await invoke()
