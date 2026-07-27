"""OAuth-backed coding-plan CLI adapters for text-only LLM calls.

The application never reads or copies OAuth tokens.  It invokes the installed
Codex or Antigravity CLI, leaving credential storage and refresh to the CLI
that owns the login session.
"""

from __future__ import annotations

import asyncio
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
CODING_PLAN_PROVIDER_TYPES = {CODEX_PROVIDER_TYPE, AGY_PROVIDER_TYPE}

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


def resolve_coding_plan_executable(provider_type: str, cli_path: str = "") -> Path | None:
    normalized = str(provider_type or "").strip().lower()
    if normalized == CODEX_PROVIDER_TYPE:
        return _resolve_codex_executable(cli_path)
    if normalized == AGY_PROVIDER_TYPE:
        return _resolve_agy_executable(cli_path)
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
) -> tuple[int, str, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            env=_subprocess_env(),
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
        await asyncio.shield(_terminate_process_tree(process))
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


def _diagnostic(stderr: str, stdout: str = "") -> str:
    lines = [line.strip() for line in f"{stderr}\n{stdout}".splitlines() if line.strip()]
    preferred = [
        line
        for line in lines
        if any(token in line.lower() for token in ("error", "failed", "invalid", "login", "oauth"))
    ]
    selected = preferred[-1] if preferred else (lines[-1] if lines else "unknown CLI error")
    return selected[:600]


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
        import json

        data = json.loads(settings_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("model") or "").strip() if isinstance(data, dict) else ""


async def coding_plan_status(
    provider_type: str,
    *,
    cli_path: str = "",
    timeout_sec: float = 30.0,
) -> dict[str, Any]:
    normalized = str(provider_type or "").strip().lower()
    executable = resolve_coding_plan_executable(normalized, cli_path)
    provider_name = "Codex" if normalized == CODEX_PROVIDER_TYPE else "Antigravity"
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
    models: list[dict[str, str]] = [
        {"id": "default", "display_name": default_name, "model_type": "llm"}
    ]
    if str(provider_type).strip().lower() == AGY_PROVIDER_TYPE:
        for display_name in status.get("models", []):
            model_id = agy_model_id(str(display_name))
            if model_id:
                models.append(
                    {
                        "id": model_id,
                        "display_name": str(display_name),
                        "model_type": "llm",
                        "cli_model_name": str(display_name),
                    }
                )
    elif current_model:
        models.append({"id": current_model, "display_name": current_model, "model_type": "llm"})
    return models


def _text_backend_prompt(prompt: str) -> str:
    return (
        "你是 MediaProcessPipeline 的纯文本模型后端。只处理下面的请求并返回最终文本；"
        "不要读取工作区文件，不要运行命令，不要调用工具。\n\n"
        f"<request>\n{prompt}\n</request>"
    )


async def _call_codex(
    executable: Path,
    model: str,
    prompt: str,
    timeout_sec: float,
) -> str:
    with tempfile.TemporaryDirectory(prefix="mpp-codex-") as temp_dir:
        cwd = Path(temp_dir)
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
        command.append("-")
        try:
            code, stdout, stderr = await _run_cli(
                command,
                cwd=cwd,
                timeout_sec=timeout_sec,
                stdin_text=_text_backend_prompt(prompt),
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
) -> str:
    with tempfile.TemporaryDirectory(prefix="mpp-agy-") as temp_dir:
        prompt_file = Path(temp_dir) / "request.txt"
        prompt_file.write_text(_text_backend_prompt(prompt), encoding="utf-8")
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


async def call_coding_plan_cli(
    provider_type: str,
    *,
    model: str,
    prompt: str,
    cli_path: str = "",
    timeout_sec: float = 600.0,
) -> str:
    normalized = str(provider_type or "").strip().lower()
    executable = resolve_coding_plan_executable(normalized, cli_path)
    if executable is None:
        cli_name = "Codex" if normalized == CODEX_PROVIDER_TYPE else "Antigravity"
        raise CodingPlanCLIError(f"未找到 {cli_name} CLI；请安装 CLI 或填写可执行文件路径。")

    async with _lock(normalized):
        if normalized == CODEX_PROVIDER_TYPE:
            return await _call_codex(executable, model, prompt, timeout_sec)
        if normalized == AGY_PROVIDER_TYPE:
            return await _call_agy(executable, model, prompt, timeout_sec)
    raise CodingPlanCLIError(f"不支持的 OAuth CLI Provider：{provider_type}")
