import asyncio
import json
import os
import signal
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.analysis import coding_plan_cli  # noqa: E402


@pytest.mark.parametrize("name", ["codex", "codex.exe"])
def test_codex_configured_executable_takes_precedence_over_path(monkeypatch, tmp_path, name):
    executable = tmp_path / name
    executable.touch()
    monkeypatch.setattr(coding_plan_cli.shutil, "which", lambda _: "/other/codex")
    assert coding_plan_cli._resolve_codex_executable(str(executable)) == executable


@pytest.mark.parametrize("suffix", [".cmd", ".bat", ".ps1"])
def test_codex_configured_shell_wrapper_falls_back_to_native_cli(monkeypatch, tmp_path, suffix):
    wrapper = tmp_path / f"codex{suffix}"
    wrapper.touch()
    monkeypatch.setattr(coding_plan_cli.shutil, "which", lambda _: "/native/codex.exe")
    assert coding_plan_cli._resolve_codex_executable(str(wrapper)) == Path("/native/codex.exe")


@pytest.mark.skipif(sys.platform == "win32", reason="Unix desktop PATH fallback")
@pytest.mark.parametrize("installation", ["local_bin", "standalone"])
def test_codex_discovery_without_shell_path(monkeypatch, tmp_path, installation):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(coding_plan_cli.shutil, "which", lambda _: None)
    codex_home = tmp_path / "custom-codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    executable = (
        tmp_path / ".local" / "bin" / "codex" if installation == "local_bin"
        else codex_home / "packages" / "standalone" / "current" / "bin" / "codex"
    )
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    assert coding_plan_cli._resolve_codex_executable() == executable


def test_agy_model_id_matches_cli_slug_format():
    assert coding_plan_cli.agy_model_id("Gemini 3.1 Pro (High)") == "gemini-3.1-pro-high"
    assert (
        coding_plan_cli.agy_model_id("Claude Sonnet 4.6 (Thinking)") == "claude-sonnet-4.6-thinking"
    )
    assert coding_plan_cli.agy_model_id("GPT-OSS 120B (Medium)") == "gpt-oss-120b-medium"


@pytest.mark.asyncio
async def test_agy_explicit_model_uses_cli_display_name_without_fallback(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    async def fake_run(command, *, cwd, timeout_sec, stdin_text=None):
        calls.append(command)
        assert cwd == tmp_path
        return 0, "完成结果", ""

    monkeypatch.setattr(coding_plan_cli, "_run_cli", fake_run)
    prompt_file = tmp_path / "request.txt"
    prompt_file.write_text("test", encoding="utf-8")

    monkeypatch.setattr(coding_plan_cli.tempfile, "mkdtemp", lambda **_kwargs: str(tmp_path))
    result = await coding_plan_cli._call_agy(
        Path("agy.exe"),
        "Gemini 3.1 Pro (High)",
        "request",
        60,
    )

    assert result == "完成结果"
    assert len(calls) == 1
    assert "--model" in calls[0]
    assert calls[0][calls[0].index("--model") + 1] == "Gemini 3.1 Pro (High)"
    assert calls[0][calls[0].index("--print-timeout") + 1] == "90s"


@pytest.mark.asyncio
async def test_codex_call_reads_long_request_in_ephemeral_session(monkeypatch):
    captured: dict[str, object] = {}
    prompt = '中文字幕🎬 "引号" & <正文>\n' * 10_000

    async def fake_run(command, *, cwd, timeout_sec, stdin_text=None):
        captured.update(
            {
                "command": command,
                "cwd": cwd,
                "timeout_sec": timeout_sec,
                "stdin_text": stdin_text,
                "request": (cwd / "request.txt").read_text(encoding="utf-8"),
            }
        )
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text("Codex result", encoding="utf-8")
        return 0, "", "diagnostics"

    monkeypatch.setattr(coding_plan_cli, "_run_cli", fake_run)
    monkeypatch.setattr(coding_plan_cli, "_codex_current_model", lambda: "gpt-current")

    result = await coding_plan_cli._call_codex(
        Path("codex.exe"),
        "default",
        prompt,
        120,
        reasoning_effort="max",
    )

    command = captured["command"]
    assert result == "Codex result"
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert command[command.index("--model") + 1] == "gpt-current"
    assert command[command.index("--config") + 1] == 'model_reasoning_effort="max"'
    assert command[-1] == "-"
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "shell_tool" in command
    assert captured["stdin_text"] == captured["request"]
    assert captured["request"] == coding_plan_cli._text_backend_prompt(prompt)
    assert sum(len(part) for part in command) < 2_048
    assert not captured["cwd"].exists()


@pytest.mark.asyncio
async def test_codex_call_attaches_staged_image(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    image_path = tmp_path / "原图.png"
    image_path.write_bytes(b"test-image")

    async def fake_run(command, *, cwd, timeout_sec, stdin_text=None):
        staged_path = Path(command[command.index("--image") + 1])
        captured.update(
            {
                "command": command,
                "staged_name": staged_path.name,
                "staged_bytes": staged_path.read_bytes(),
                "stdin_text": stdin_text,
                "request": (cwd / "request.txt").read_text(encoding="utf-8"),
            }
        )
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text("Image result", encoding="utf-8")
        return 0, "", ""

    monkeypatch.setattr(coding_plan_cli, "_run_cli", fake_run)

    result = await coding_plan_cli._call_codex(
        Path("codex.exe"),
        "gpt-5.6-luna",
        "描述图片",
        120,
        reasoning_effort="max",
        image_paths=[image_path],
    )

    assert result == "Image result"
    assert captured["staged_name"] == "image-01.png"
    assert captured["staged_bytes"] == b"test-image"
    assert "图片文件：image-01.png" in captured["request"]
    assert "描述图片" in captured["request"]


@pytest.mark.asyncio
async def test_codex_status_reads_login_state_without_token_files(monkeypatch, tmp_path):
    executable = tmp_path / "codex.exe"
    executable.touch()

    async def fake_run(command, *, cwd, timeout_sec, stdin_text=None):
        assert command[-2:] == ["login", "status"]
        return 0, "Logged in using ChatGPT", ""

    monkeypatch.setattr(
        coding_plan_cli, "resolve_coding_plan_executable", lambda *_args, **_kwargs: executable
    )
    monkeypatch.setattr(coding_plan_cli, "_run_cli", fake_run)
    monkeypatch.setattr(coding_plan_cli, "_codex_current_model", lambda: "gpt-5.6-sol")

    status = await coding_plan_cli.coding_plan_status("codex_oauth")

    assert status["installed"] is True
    assert status["authenticated"] is True
    assert status["current_model"] == "gpt-5.6-sol"
    assert "token" not in status


@pytest.mark.asyncio
@pytest.mark.parametrize("current_model", ["gpt-6-astra", "gpt-5.6-luna", ""])
async def test_codex_catalog_discovers_cached_models_beyond_default(
    monkeypatch, tmp_path, current_model
):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(f'model = "{current_model}"', encoding="utf-8")
    (tmp_path / "models_cache.json").write_text(json.dumps({"models": [
        {"slug": "gpt-6-luna", "visibility": "list"},
        {"slug": "gpt-5.6-luna", "visibility": "list", "supported_in_api": False},
        {"slug": " gpt-5.6-luna ", "visibility": "list"},
        {"slug": "codex-auto-review", "visibility": "hide"},
        {"slug": "hidden-model", "visibility": "hidden"},
        {"slug": "default"},
        {"slug": ""},
        {"slug": 123},
        {},
        None,
    ]}), encoding="utf-8")

    async def fake_run(command, **_kwargs):
        assert command[-2:] == ["login", "status"]
        return 0, "Logged in using ChatGPT", ""

    monkeypatch.setattr(
        coding_plan_cli, "resolve_coding_plan_executable", lambda *_a, **_kw: Path("codex")
    )
    monkeypatch.setattr(coding_plan_cli, "_run_cli", fake_run)

    status = await coding_plan_cli.coding_plan_status("codex_oauth")
    assert status["models"] == list(dict.fromkeys(
        ["gpt-6-luna", "gpt-5.6-luna"] + ([current_model] if current_model else [])
    ))
    models = await coding_plan_cli.coding_plan_models("codex_oauth")
    assert [model["id"] for model in models] == ["default"] + [
        model for model in ["gpt-6-luna", "gpt-5.6-luna"] if model != current_model
    ]
    assert models[0].get("cli_model_name", "") == current_model
    for model in models[1:]:
        assert model["cli_model_name"] == model["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cache",
    [None, b"{", b"\xff", b"[]", b"null", b"{}", b'{"models": null}', b'{"models": {}}'],
)
async def test_codex_catalog_falls_back_to_default_without_valid_cache(
    monkeypatch, tmp_path, cache
):
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text('model = "gpt-6-astra"', encoding="utf-8")
    if cache is not None:
        (codex_home / "models_cache.json").write_bytes(cache)

    async def fake_run(*_args, **_kwargs):
        return 0, "Logged in using ChatGPT", ""

    monkeypatch.setattr(
        coding_plan_cli, "resolve_coding_plan_executable", lambda *_a, **_kw: Path("codex")
    )
    monkeypatch.setattr(coding_plan_cli, "_run_cli", fake_run)
    models = await coding_plan_cli.coding_plan_models("codex_oauth")
    assert models == [{
        "id": "default",
        "display_name": "CLI 当前默认模型（gpt-6-astra）",
        "model_type": "llm",
        "cli_model_name": "gpt-6-astra",
    }]


@pytest.mark.asyncio
async def test_agy_catalog_keeps_stable_id_and_real_cli_model_name(monkeypatch):
    async def fake_status(*_args, **_kwargs):
        return {
            "installed": True,
            "authenticated": True,
            "current_model": "Gemini 3.6 Flash (High)",
            "models": ["gemini-3.1-pro-high\tGemini 3.1 Pro (High)"],
            "message": "ok",
        }

    monkeypatch.setattr(coding_plan_cli, "coding_plan_status", fake_status)

    models = await coding_plan_cli.coding_plan_models("agy_oauth")

    assert models[1] == {
        "id": "gemini-3.1-pro-high",
        "display_name": "Gemini 3.1 Pro (High)",
        "model_type": "llm",
        "cli_model_name": "Gemini 3.1 Pro (High)",
    }


@pytest.mark.asyncio
async def test_catalog_keeps_current_oauth_model_only_as_default_alias(monkeypatch):
    async def fake_status(*_args, **_kwargs):
        return {
            "installed": True,
            "authenticated": True,
            "current_model": "gpt-5.6-luna",
            "models": ["gpt-5.6-luna"],
            "message": "ok",
        }

    monkeypatch.setattr(coding_plan_cli, "coding_plan_status", fake_status)

    models = await coding_plan_cli.coding_plan_models("codex_oauth")

    assert models == [{
        "id": "default",
        "display_name": "CLI 当前默认模型（gpt-5.6-luna）",
        "model_type": "llm",
        "cli_model_name": "gpt-5.6-luna",
    }]


@pytest.mark.asyncio
async def test_kimi_status_and_catalog_include_k27_models(monkeypatch, tmp_path):
    executable = tmp_path / "kimi.exe"
    executable.touch()
    kimi_home = tmp_path / ".kimi-code"
    kimi_home.mkdir()
    (kimi_home / "config.toml").write_text(
        """
default_model = "kimi-code/k3"

[models."kimi-code/kimi-for-coding"]
provider = "managed:kimi-code"
model = "kimi-for-coding"
display_name = "K2.7 Coding"

[models."kimi-code/kimi-for-coding-highspeed"]
provider = "managed:kimi-code"
model = "kimi-for-coding-highspeed"
display_name = "K2.7 Coding Highspeed"

[models."kimi-code/k3"]
provider = "managed:kimi-code"
model = "k3"
display_name = "K3"
""".strip(),
        encoding="utf-8",
    )

    async def fake_run(command, *, cwd, timeout_sec, stdin_text=None):
        assert command[-2:] == ["provider", "list"]
        return 0, "managed:kimi-code  type=kimi  models=3  source=oauth", ""

    monkeypatch.setenv("KIMI_CODE_HOME", str(kimi_home))
    monkeypatch.setattr(
        coding_plan_cli, "resolve_coding_plan_executable", lambda *_args, **_kwargs: executable
    )
    monkeypatch.setattr(coding_plan_cli, "_run_cli", fake_run)

    status = await coding_plan_cli.coding_plan_status("kimi_oauth")
    models = await coding_plan_cli.coding_plan_models("kimi_oauth")

    assert status["authenticated"] is True
    assert status["current_model"] == "kimi-code/k3"
    assert [model["display_name"] for model in models[1:]] == [
        "K2.7 Coding",
        "K2.7 Coding Highspeed",
    ]
    assert models[1]["cli_model_name"] == "kimi-code/kimi-for-coding"


@pytest.mark.asyncio
async def test_kimi_call_stages_long_prompt_in_file(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    staging_dir = tmp_path / "kimi-staging"
    staging_dir.mkdir()
    long_prompt = "需要总结的长文本。" * 10_000

    async def fake_run(command, *, cwd, timeout_sec, stdin_text=None, env_override=None):
        captured.update(
            {
                "command": command,
                "cwd": cwd,
                "stdin_text": stdin_text,
                "env_override": env_override,
                "request": (cwd / "request.txt").read_text(encoding="utf-8"),
            }
        )
        return 0, "Kimi result", ""

    monkeypatch.setattr(coding_plan_cli, "_run_cli", fake_run)
    monkeypatch.setattr(coding_plan_cli.tempfile, "mkdtemp", lambda **_kwargs: str(staging_dir))

    result = await coding_plan_cli._call_kimi(
        Path("kimi.exe"),
        "kimi-code/kimi-for-coding",
        long_prompt,
        120,
        reasoning_effort="low",
    )

    command = captured["command"]
    assert result == "Kimi result"
    assert command[command.index("--model") + 1] == "kimi-code/kimi-for-coding"
    assert "--output-format" not in command
    assert "request.txt" in command[command.index("--prompt") + 1]
    assert long_prompt not in command
    assert long_prompt in captured["request"]
    assert sum(len(part) for part in command) < 2_048
    assert captured["stdin_text"] is None
    assert captured["env_override"]["KIMI_MODEL_THINKING_EFFORT"] == "low"
    assert not staging_dir.exists()


@pytest.mark.asyncio
async def test_qoder_status_reads_models_and_nested_current_model(monkeypatch, tmp_path):
    executable = tmp_path / "qoder.exe"
    executable.touch()
    qoder_home = tmp_path / ".qoder"
    qoder_home.mkdir()
    (qoder_home / "settings.json").write_text(
        '{"model":{"name":"performance"}}',
        encoding="utf-8",
    )

    async def fake_run(command, *, cwd, timeout_sec, stdin_text=None):
        assert command[-3:] == ["--list-models", "--output-format", "json"]
        return 0, '{"models":[{"id":"auto"},{"id":"performance"}]}', ""

    monkeypatch.setenv("QODER_CONFIG_DIR", str(qoder_home))
    monkeypatch.setattr(
        coding_plan_cli, "resolve_coding_plan_executable", lambda *_args, **_kwargs: executable
    )
    monkeypatch.setattr(coding_plan_cli, "_run_cli", fake_run)

    status = await coding_plan_cli.coding_plan_status("qoder_oauth")
    models = await coding_plan_cli.coding_plan_models("qoder_oauth")

    assert status["authenticated"] is True
    assert status["current_model"] == "performance"
    assert [model["id"] for model in models] == ["default", "auto"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt",
    ["总结这段文字", '中文字幕🎬 "引号" & <正文>\n' * 10_000],
    ids=["short", "long-unicode"],
)
async def test_qoder_call_reads_utf8_request_file(monkeypatch, prompt):
    captured: dict[str, object] = {}

    async def fake_run(
        command,
        *,
        cwd,
        timeout_sec,
        stdin_text=None,
        env_override=None,
    ):
        captured.update(
            {
                "command": command,
                "cwd": cwd,
                "stdin_text": stdin_text,
                "env_override": env_override,
                "request": (cwd / "request.txt").read_text(encoding="utf-8"),
            }
        )
        return 0, "Qoder result", ""

    monkeypatch.setattr(coding_plan_cli, "_run_cli", fake_run)

    result = await coding_plan_cli._call_qoder(
        Path("qoder.exe"),
        "performance",
        prompt,
        120,
    )

    command = captured["command"]
    assert result == "Qoder result"
    assert "--no-session-persistence" in command
    assert command[command.index("--tools") + 1] == "Read"
    assert command[command.index("--allowed-tools") + 1] == "Read"
    assert command[command.index("--permission-mode") + 1] == "dont_ask"
    assert command[command.index("--model") + 1] == "performance"
    assert command[command.index("--max-model-request-retries") + 1] == "1"
    assert command[command.index("--input-format") + 1] == "text"
    assert "HTTPS_PROXY" not in captured["env_override"]
    assert prompt not in command
    assert sum(len(part) for part in command) < 2_048
    assert "request.txt" in captured["stdin_text"]
    assert prompt not in captured["stdin_text"]
    assert captured["request"] == coding_plan_cli._text_backend_prompt(prompt)
    assert not captured["cwd"].exists()


@pytest.mark.asyncio
async def test_run_cli_preserves_large_utf8_stdin(tmp_path):
    prompt = '中文字幕🎬 "引号" & <正文>\n' * 10_000 + "全文结束"
    code, stdout, stderr = await coding_plan_cli._run_cli(
        [
            sys.executable,
            "-X",
            "utf8",
            "-c",
            "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())",
        ],
        cwd=tmp_path,
        timeout_sec=30,
        stdin_text=prompt,
    )

    assert code == 0
    assert stdout == prompt
    assert stderr == ""


@pytest.mark.asyncio
async def test_qoder_call_attaches_staged_image(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    image_path = tmp_path / "screen.jpg"
    image_path.write_bytes(b"test-image")

    async def fake_run(
        command,
        *,
        cwd,
        timeout_sec,
        stdin_text=None,
        env_override=None,
    ):
        staged_path = Path(command[command.index("--attachment") + 1])
        captured.update(
            {
                "command": command,
                "staged_name": staged_path.name,
                "staged_bytes": staged_path.read_bytes(),
                "stdin_text": stdin_text,
                "request": (cwd / "request.txt").read_text(encoding="utf-8"),
            }
        )
        return 0, "Image result", ""

    monkeypatch.setattr(coding_plan_cli, "_run_cli", fake_run)

    result = await coding_plan_cli._call_qoder(
        Path("qoder.exe"),
        "performance",
        "描述图片",
        120,
        image_paths=[image_path],
    )

    assert result == "Image result"
    assert captured["staged_name"] == "image-01.jpg"
    assert captured["staged_bytes"] == b"test-image"
    assert "图片文件：image-01.jpg" in captured["request"]
    assert "描述图片" in captured["request"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cli", ["codex", "agy", "kimi", "qoder"])
@pytest.mark.parametrize("failure", ["error", "timeout", "cancel"])
async def test_request_file_is_removed_after_cli_failure(monkeypatch, cli, failure):
    captured = {}

    async def fake_run(command, *, cwd, **kwargs):
        captured["cwd"] = cwd
        assert "测试正文🎬" in (cwd / "request.txt").read_text(encoding="utf-8")
        if failure == "timeout":
            raise asyncio.TimeoutError
        if failure == "cancel":
            raise asyncio.CancelledError
        return 1, "", "failed"

    monkeypatch.setattr(coding_plan_cli, "_run_cli", fake_run)
    expected = asyncio.CancelledError if failure == "cancel" else coding_plan_cli.CodingPlanCLIError
    with pytest.raises(expected):
        await getattr(coding_plan_cli, f"_call_{cli}")(
            Path(f"{cli}.exe"), "test-model", "测试正文🎬", 120,
        )
    assert not captured["cwd"].exists()


@pytest.mark.asyncio
async def test_qoder_provider_allows_parallel_headless_calls(monkeypatch, tmp_path):
    executable = tmp_path / "qoder.exe"
    executable.touch()
    active = 0
    peak = 0
    both_started = asyncio.Event()

    async def fake_call(*_args, **_kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=1)
        active -= 1
        return "ok"

    monkeypatch.setattr(
        coding_plan_cli, "resolve_coding_plan_executable", lambda *_args, **_kwargs: executable
    )
    monkeypatch.setattr(coding_plan_cli, "_call_qoder", fake_call)

    results = await asyncio.gather(
        coding_plan_cli.call_coding_plan_cli(
            "qoder_oauth", model="auto", prompt="one", timeout_sec=30
        ),
        coding_plan_cli.call_coding_plan_cli(
            "qoder_oauth", model="auto", prompt="two", timeout_sec=30
        ),
    )

    assert results == ["ok", "ok"]
    assert peak == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [1, 2])
async def test_codex_requests_share_runtime_limit_across_models(monkeypatch, tmp_path, limit):
    from app.core import settings

    monkeypatch.setattr(
        settings, "get_runtime_settings", lambda: SimpleNamespace(codex_max_concurrency=limit)
    )
    executable = tmp_path / "codex.exe"
    executable.touch()
    active = 0
    peak = 0
    limit_reached = asyncio.Event()
    release = asyncio.Event()

    async def fake_call(*_args, **_kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == limit:
            limit_reached.set()
        await release.wait()
        active -= 1
        return "ok"

    monkeypatch.setattr(
        coding_plan_cli, "resolve_coding_plan_executable", lambda *_args, **_kwargs: executable
    )
    monkeypatch.setattr(coding_plan_cli, "_call_codex", fake_call)

    pending = asyncio.gather(
        *(
            coding_plan_cli.call_coding_plan_cli(
                provider_type, model=model, prompt="request", timeout_sec=30
            )
            for provider_type, model in [
                ("codex_oauth", "model-for-analyze"),
                (" CODEX_OAUTH ", "model-for-polish"),
                ("codex_oauth", "model-for-mindmap"),
            ]
        )
    )
    try:
        await asyncio.wait_for(limit_reached.wait(), timeout=1)
        await asyncio.sleep(0)
        assert active == limit
    finally:
        release.set()
        results = await asyncio.wait_for(pending, timeout=2)
    assert results == ["ok", "ok", "ok"]
    assert peak == limit


@pytest.mark.asyncio
async def test_kimi_provider_allows_parallel_ephemeral_calls(monkeypatch, tmp_path):
    executable = tmp_path / "kimi.exe"
    executable.touch()
    active = 0
    peak = 0
    both_started = asyncio.Event()

    async def fake_call(*_args, **_kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=1)
        active -= 1
        return "ok"

    monkeypatch.setattr(
        coding_plan_cli, "resolve_coding_plan_executable", lambda *_args, **_kwargs: executable
    )
    monkeypatch.setattr(coding_plan_cli, "_call_kimi", fake_call)

    results = await asyncio.gather(
        coding_plan_cli.call_coding_plan_cli(
            "kimi_oauth", model="kimi-code/kimi-for-coding", prompt="one", timeout_sec=30
        ),
        coding_plan_cli.call_coding_plan_cli(
            "kimi_oauth", model="kimi-code/kimi-for-coding", prompt="two", timeout_sec=30
        ),
    )

    assert results == ["ok", "ok"]
    assert peak == 2


@pytest.mark.asyncio
async def test_status_wraps_cli_start_failure(monkeypatch, tmp_path):
    executable = tmp_path / "codex.exe"
    executable.touch()

    async def fake_run(*_args, **_kwargs):
        raise coding_plan_cli.CodingPlanCLIError("无法启动 OAuth CLI：access denied")

    monkeypatch.setattr(
        coding_plan_cli,
        "resolve_coding_plan_executable",
        lambda *_args, **_kwargs: executable,
    )
    monkeypatch.setattr(coding_plan_cli, "_run_cli", fake_run)

    status = await coding_plan_cli.coding_plan_status("codex_oauth")

    assert status["installed"] is True
    assert status["authenticated"] is False
    assert status["message"] == "无法启动 OAuth CLI：access denied"


def test_codex_concurrency_defaults_to_one_and_rejects_unbounded_values():
    from app.core.settings import RuntimeSettings
    from pydantic import ValidationError

    assert RuntimeSettings().codex_max_concurrency == 1
    for limit in (0, -1, 9):
        with pytest.raises(ValidationError):
            RuntimeSettings(codex_max_concurrency=limit)


@pytest.mark.asyncio
async def test_lowering_codex_limit_waits_for_existing_requests(monkeypatch):
    from app.core import settings

    runtime = SimpleNamespace(codex_max_concurrency=2)
    monkeypatch.setattr(settings, "get_runtime_settings", lambda: runtime)
    limiter = coding_plan_cli._CODEX_LIMITER
    release = [asyncio.Event() for _ in range(3)]
    entered = []

    async def request(index):
        async with limiter:
            entered.append(index)
            await release[index].wait()

    first = asyncio.create_task(request(0))
    second = asyncio.create_task(request(1))
    await _wait_until(lambda: len(entered) == 2)
    runtime.codex_max_concurrency = 1
    third = asyncio.create_task(request(2))
    await asyncio.sleep(0)
    assert entered == [0, 1]
    release[0].set()
    await first
    await asyncio.sleep(0)
    assert entered == [0, 1]
    release[1].set()
    await second
    await _wait_until(lambda: len(entered) == 3)
    release[2].set()
    await third


@pytest.mark.asyncio
async def test_codex_budget_is_shared_by_daemon_and_worker_loops(monkeypatch):
    from app.core import settings

    monkeypatch.setattr(
        settings, "get_runtime_settings", lambda: SimpleNamespace(codex_max_concurrency=1)
    )
    monkeypatch.setattr(
        coding_plan_cli, "resolve_coding_plan_executable", lambda *_a, **_kw: Path("codex")
    )
    daemon_started = asyncio.Event()
    release = asyncio.Event()
    worker_waiting = threading.Event()
    worker_started = threading.Event()

    async def fake_call(_executable, _model, prompt, *_args, **_kwargs):
        if prompt == "daemon":
            daemon_started.set()
            await release.wait()
        else:
            worker_started.set()
        return prompt

    monkeypatch.setattr(coding_plan_cli, "_call_codex", fake_call)

    async def call(prompt):
        if prompt == "worker":
            asyncio.get_running_loop().call_soon(worker_waiting.set)
        return await coding_plan_cli.call_coding_plan_cli(
            "codex_oauth", model="default", prompt=prompt
        )

    daemon = asyncio.create_task(call("daemon"))
    await asyncio.wait_for(daemon_started.wait(), timeout=2)
    worker = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(call("worker"))))
    try:
        assert await asyncio.to_thread(worker_waiting.wait, 2)
        assert not worker_started.is_set()
    finally:
        release.set()
        results = await asyncio.wait_for(asyncio.gather(daemon, worker), timeout=2)
    assert results == ["daemon", "worker"]


@pytest.mark.asyncio
async def test_codex_waiter_cancel_does_not_start_cli_or_lose_permit(monkeypatch):
    from app.core import settings

    monkeypatch.setattr(
        settings, "get_runtime_settings", lambda: SimpleNamespace(codex_max_concurrency=1)
    )
    monkeypatch.setattr(
        coding_plan_cli, "resolve_coding_plan_executable", lambda *_a, **_kw: Path("codex")
    )
    started = asyncio.Event()
    release = asyncio.Event()
    prompts = []

    async def fake_call(_executable, _model, prompt, *_args, **_kwargs):
        prompts.append(prompt)
        started.set()
        await release.wait()
        return prompt

    monkeypatch.setattr(coding_plan_cli, "_call_codex", fake_call)

    def call(prompt):
        return asyncio.create_task(coding_plan_cli.call_coding_plan_cli(
            "codex_oauth", model="default", prompt=prompt
        ))

    first = call("first")
    await started.wait()
    cancelled = call("cancelled")
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    following = call("following")
    await asyncio.sleep(0)
    assert prompts == ["first"]
    release.set()
    assert await asyncio.gather(first, following) == ["first", "following"]
    assert prompts == ["first", "following"]


@pytest.mark.asyncio
async def test_codex_cancel_during_spawn_holds_permit_until_cleanup(monkeypatch, tmp_path):
    from app.core import settings

    monkeypatch.setattr(
        settings, "get_runtime_settings", lambda: SimpleNamespace(codex_max_concurrency=1)
    )
    monkeypatch.setattr(
        coding_plan_cli, "resolve_coding_plan_executable", lambda *_a, **_kw: Path("codex")
    )
    spawning = asyncio.Event()
    spawned = asyncio.Event()
    cleaning = asyncio.Event()
    cleaned = asyncio.Event()
    process = object()
    prompts = []

    async def fake_spawn(*_args, **_kwargs):
        spawning.set()
        await spawned.wait()
        return process

    async def fake_cleanup(actual_process):
        assert actual_process is process
        cleaning.set()
        await cleaned.wait()

    async def fake_call(_executable, _model, prompt, *_args, **_kwargs):
        prompts.append(prompt)
        if prompt == "first":
            await coding_plan_cli._run_cli(["codex"], cwd=tmp_path, timeout_sec=30)
        return prompt

    monkeypatch.setattr(coding_plan_cli.asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(coding_plan_cli, "_terminate_process_tree", fake_cleanup)
    monkeypatch.setattr(coding_plan_cli, "_call_codex", fake_call)
    first = asyncio.create_task(coding_plan_cli.call_coding_plan_cli(
        "codex_oauth", model="default", prompt="first"
    ))
    await spawning.wait()
    first.cancel()
    following = asyncio.create_task(coding_plan_cli.call_coding_plan_cli(
        "codex_oauth", model="default", prompt="following"
    ))
    spawned.set()
    await asyncio.wait_for(cleaning.wait(), timeout=1)
    first.cancel()
    await asyncio.sleep(0)
    assert prompts == ["first"]
    assert not first.done()
    assert not following.done()
    cleaned.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert await asyncio.wait_for(following, timeout=1) == "following"


async def _wait_until(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout=5)


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group cleanup")
@pytest.mark.parametrize("outcome", ["success", "error", "timeout", "cancel"])
async def test_cli_cleanup_kills_helpers_even_after_parent_exits(tmp_path, outcome):
    pid_file = tmp_path / "helper.pid"
    script = "\n".join([
        "import pathlib, subprocess, sys, time",
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)",
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))",
        "time.sleep(60)" if outcome in {"timeout", "cancel"} else "",
        "sys.exit(1)" if outcome == "error" else "",
    ])
    pending = asyncio.create_task(coding_plan_cli._run_cli(
        [sys.executable, "-c", script], cwd=tmp_path,
        timeout_sec=1 if outcome == "timeout" else 30,
    ))
    await _wait_until(lambda: pid_file.exists() and pid_file.stat().st_size > 0)
    pid = int(pid_file.read_text())

    def helper_alive():
        try:
            return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False

    try:
        if outcome == "cancel":
            pending.cancel()
        if outcome in {"timeout", "cancel"}:
            expected = asyncio.TimeoutError if outcome == "timeout" else asyncio.CancelledError
            with pytest.raises(expected):
                await pending
        else:
            code, _, _ = await pending
            assert code == (1 if outcome == "error" else 0)
        await _wait_until(lambda: not helper_alive())
    finally:
        if helper_alive():
            os.kill(pid, signal.SIGKILL)
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_codex_limiter_grants_queued_waiters_in_fifo_order(monkeypatch):
    from app.core import settings

    monkeypatch.setattr(
        settings, "get_runtime_settings", lambda: SimpleNamespace(codex_max_concurrency=1)
    )
    limiter = coding_plan_cli._CODEX_LIMITER
    holder_entered = asyncio.Event()
    release_holder = asyncio.Event()
    order: list[int] = []

    async def holder():
        async with limiter:
            holder_entered.set()
            await release_holder.wait()

    async def queued(idx: int):
        async with limiter:
            order.append(idx)

    t0 = asyncio.create_task(holder())
    await holder_entered.wait()
    waiters = [asyncio.create_task(queued(i)) for i in (1, 2, 3)]
    for _ in range(3):
        await asyncio.sleep(0)
    release_holder.set()
    await asyncio.gather(t0, *waiters)
    assert order == [1, 2, 3]
