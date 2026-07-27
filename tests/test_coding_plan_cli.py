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
    )

    command = captured["command"]
    assert result == "Codex result"
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert command[command.index("--model") + 1] == "gpt-current"
    assert command[-1] == "-"
    assert "总结这段文字" in captured["stdin_text"]


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
            "current_model": "Gemini 3.1 Pro (High)",
            "models": ["Gemini 3.1 Pro (High)"],
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
