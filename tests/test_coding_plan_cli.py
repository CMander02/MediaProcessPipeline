import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.analysis import coding_plan_cli  # noqa: E402


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

    class FixedTemporaryDirectory:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return str(tmp_path)

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(coding_plan_cli.tempfile, "TemporaryDirectory", FixedTemporaryDirectory)
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
async def test_codex_call_uses_ephemeral_text_only_session(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_run(command, *, cwd, timeout_sec, stdin_text=None):
        captured.update(
            {
                "command": command,
                "cwd": cwd,
                "timeout_sec": timeout_sec,
                "stdin_text": stdin_text,
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
        "总结这段文字",
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
    assert "总结这段文字" in captured["stdin_text"]


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
    assert "图片文件：image-01.png" in captured["stdin_text"]


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
async def test_qoder_call_is_text_only_ephemeral_and_uses_stdin(monkeypatch):
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
            }
        )
        return 0, "Qoder result", ""

    monkeypatch.setattr(coding_plan_cli, "_run_cli", fake_run)

    result = await coding_plan_cli._call_qoder(
        Path("qoder.exe"),
        "performance",
        "总结这段文字",
        120,
    )

    command = captured["command"]
    assert result == "Qoder result"
    assert "--no-session-persistence" in command
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--model") + 1] == "performance"
    assert command[command.index("--max-model-request-retries") + 1] == "1"
    assert "HTTPS_PROXY" not in captured["env_override"]
    assert command[-2] == "--"
    assert "总结这段文字" in command[-1]
    assert captured["stdin_text"] is None


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
    assert "图片文件：image-01.jpg" in captured["command"][-1]


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
async def test_codex_provider_allows_parallel_ephemeral_calls(monkeypatch, tmp_path):
    executable = tmp_path / "codex.exe"
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
    monkeypatch.setattr(coding_plan_cli, "_call_codex", fake_call)

    results = await asyncio.gather(
        coding_plan_cli.call_coding_plan_cli(
            "codex_oauth", model="gpt-5.6-luna", prompt="one", timeout_sec=30
        ),
        coding_plan_cli.call_coding_plan_cli(
            "codex_oauth", model="gpt-5.6-luna", prompt="two", timeout_sec=30
        ),
    )

    assert results == ["ok", "ok"]
    assert peak == 2


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
