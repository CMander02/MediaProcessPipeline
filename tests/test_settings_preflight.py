from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.settings_preflight import (  # noqa: E402
    MAX_SETTINGS_BYTES,
    SETTINGS_PREFLIGHT_ERROR_TOKEN,
    SETTINGS_PREFLIGHT_INVALID_TOKEN,
    SETTINGS_PREFLIGHT_OK_TOKEN,
    SettingsPreflightStatus,
    main,
    preflight_runtime_settings,
)


def test_valid_settings_reuse_runtime_normalization_without_mutating_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "custom_active_profile_id": "legacy",
                "custom_name": "Legacy provider",
                "custom_api_base": "https://example.invalid/v1",
                "custom_model": "model",
                "max_download_concurrency": 2,
            }
        ),
        encoding="utf-8",
    )
    before = path.read_bytes()

    status = preflight_runtime_settings(path)

    assert status is SettingsPreflightStatus.VALID
    assert path.read_bytes() == before


def test_semantically_invalid_settings_are_rejected_without_exposing_secrets(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    secret = "sk-secret-value-must-not-appear"
    path.write_text(
        json.dumps(
            {
                "openai_api_key": secret,
                "max_download_concurrency": "definitely-invalid",
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            str(ROOT / "backend" / "app" / "services" / "settings_preflight.py"),
            str(path),
        ],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 2
    assert completed.stdout == f"{SETTINGS_PREFLIGHT_INVALID_TOKEN}\n"
    assert completed.stderr == ""
    assert secret not in completed.stdout + completed.stderr


@pytest.mark.parametrize("document", [[], "settings", 1, None])
def test_non_object_json_is_invalid(tmp_path: Path, document: object) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    assert preflight_runtime_settings(path) is SettingsPreflightStatus.INVALID


def test_oversized_settings_are_rejected_before_parsing(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_bytes(b"{" + b" " * MAX_SETTINGS_BYTES + b"}")

    assert preflight_runtime_settings(path) is SettingsPreflightStatus.TOO_LARGE


def test_missing_settings_use_runtime_defaults(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"

    status = preflight_runtime_settings(path)

    assert status is SettingsPreflightStatus.MISSING
    assert status.exit_code == 0
    assert status.token == SETTINGS_PREFLIGHT_OK_TOKEN
    assert not path.exists()


def test_invalid_utf8_and_invalid_json_use_the_fixed_invalid_contract(
    tmp_path: Path,
) -> None:
    invalid_utf8 = tmp_path / "invalid-utf8.json"
    invalid_utf8.write_bytes(b"{\xff}")
    invalid_json = tmp_path / "invalid-json.json"
    invalid_json.write_text("{ invalid", encoding="utf-8")

    assert (
        preflight_runtime_settings(invalid_utf8)
        is SettingsPreflightStatus.INVALID
    )
    assert (
        preflight_runtime_settings(invalid_json)
        is SettingsPreflightStatus.INVALID
    )
    assert SettingsPreflightStatus.INVALID.exit_code == 2
    assert SettingsPreflightStatus.INVALID.token == SETTINGS_PREFLIGHT_INVALID_TOKEN


def test_cli_usage_and_internal_failures_emit_only_the_fixed_error_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([]) == SettingsPreflightStatus.USAGE_ERROR.exit_code
    usage = capsys.readouterr()
    assert usage.out == f"{SETTINGS_PREFLIGHT_ERROR_TOKEN}\n"
    assert usage.err == ""

    def fail(_path: Path) -> SettingsPreflightStatus:
        raise RuntimeError("api_key=must-not-be-emitted")

    monkeypatch.setattr(
        "app.services.settings_preflight.preflight_runtime_settings",
        fail,
    )
    assert main([str(tmp_path / "config.json")]) == 70
    internal = capsys.readouterr()
    assert internal.out == f"{SETTINGS_PREFLIGHT_ERROR_TOKEN}\n"
    assert internal.err == ""


def test_desktop_runtime_accepts_the_same_fixed_tokens() -> None:
    rust_source = (
        ROOT / "web" / "src-tauri" / "src" / "main.rs"
    ).read_text(encoding="utf-8")

    assert (
        f'const SETTINGS_PREFLIGHT_OK_TOKEN: &str = '
        f'"{SETTINGS_PREFLIGHT_OK_TOKEN}";'
    ) in rust_source
    assert (
        f'const SETTINGS_PREFLIGHT_INVALID_TOKEN: &str = '
        f'"{SETTINGS_PREFLIGHT_INVALID_TOKEN}";'
    ) in rust_source
