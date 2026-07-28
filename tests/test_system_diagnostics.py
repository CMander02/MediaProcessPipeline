from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.cli import main as cli_main  # noqa: E402
from app.services import system_diagnostics  # noqa: E402
from app.services.runtime_diagnostics import (  # noqa: E402
    DIAGNOSTIC_SCHEMA_VERSION,
    DiagnosticReport,
    DiagnosticResult,
    DiagnosticStatus,
)


def _report(*results: DiagnosticResult) -> DiagnosticReport:
    return DiagnosticReport(
        schema=DIAGNOSTIC_SCHEMA_VERSION,
        requested_probes=tuple(result.probe_id for result in results),
        results=results,
    )


def _payload() -> dict:
    return {
        "appVersion": "0.4.1",
        "components": [],
        "diagnosticSchemaVersion": 1,
        "healthy": True,
        "limits": {},
        "reportDigest": "sha256:" + "a" * 64,
        "requestedProbes": [],
        "schema": "mpp.system-diagnostics",
        "schemaVersion": 1,
        "status": "available",
        "verified": True,
    }


def test_system_diagnostics_document_has_stable_schema_and_digest() -> None:
    report = _report(
        DiagnosticResult(
            "python",
            DiagnosticStatus.AVAILABLE,
            "Python runtime is available.",
            (("version", "3.12.8"),),
        ),
        DiagnosticResult(
            "pillow",
            DiagnosticStatus.AVAILABLE,
            "Pillow image codecs are available.",
            (("formats", ("jpeg", "png", "webp")),),
        ),
    )

    first = system_diagnostics.build_system_diagnostics_document(
        report,
        app_version="0.4.1",
    )
    second = system_diagnostics.build_system_diagnostics_document(
        report,
        app_version="0.4.1",
    )

    assert first == second
    assert first["schema"] == system_diagnostics.SYSTEM_DIAGNOSTICS_SCHEMA
    assert first["schemaVersion"] == 1
    assert first["diagnosticSchemaVersion"] == DIAGNOSTIC_SCHEMA_VERSION
    assert first["status"] == "available"
    assert first["healthy"] is True
    assert first["verified"] is True
    assert [component["id"] for component in first["components"]] == [
        "python",
        "pillow",
    ]

    digest = first["reportDigest"]
    unsigned = dict(first)
    del unsigned["reportDigest"]
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert digest == f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def test_public_probe_set_matches_remote_api_core_profile() -> None:
    assert system_diagnostics.SYSTEM_DIAGNOSTICS_PROBES == (
        "configuration",
        "disk",
        "fastapi",
        "ffmpeg",
        "ffprobe",
        "openai",
        "pillow",
        "python",
        "uv",
        "writable-paths",
    )


def test_document_summary_is_derived_only_from_published_components() -> None:
    report = DiagnosticReport(
        schema=DIAGNOSTIC_SCHEMA_VERSION,
        requested_probes=("python", "cuda"),
        results=(
            DiagnosticResult(
                "python",
                DiagnosticStatus.AVAILABLE,
                "Python runtime is available.",
            ),
            DiagnosticResult(
                "cuda",
                DiagnosticStatus.ERROR,
                "CUDA failed outside the public core profile.",
            ),
        ),
    )

    payload = system_diagnostics.build_system_diagnostics_document(report)

    assert [component["id"] for component in payload["components"]] == ["python"]
    assert payload["status"] == "available"
    assert payload["healthy"] is True
    assert payload["verified"] is True


@pytest.mark.parametrize(
    "diagnostic_status",
    [DiagnosticStatus.UNAVAILABLE, DiagnosticStatus.UNTRUSTED],
)
def test_public_binary_failure_is_preserved(
    diagnostic_status: DiagnosticStatus,
) -> None:
    report = _report(
        DiagnosticResult(
            "ffmpeg",
            diagnostic_status,
            "Signed runtime manifest trust is unavailable.",
        )
    )

    payload = system_diagnostics.build_system_diagnostics_document(report)

    assert payload["components"][0]["id"] == "ffmpeg"
    assert payload["components"][0]["status"] == diagnostic_status.value
    assert payload["status"] == "attention"
    assert payload["healthy"] is False
    assert payload["verified"] is False


def test_system_diagnostics_document_redacts_secrets_and_bounds_output() -> None:
    details = (
        ("apiKey", "sk-sensitive-credential-value"),
        ("authorization", "Bearer another-sensitive-value"),
        ("note", "password=hunter2"),
        ("longText", "界" * 300),
        ("items", tuple(f"item-{index}" for index in range(40))),
        *((f"field{index}", f"value-{index}") for index in range(30)),
    )
    report = _report(
        DiagnosticResult(
            "python",
            DiagnosticStatus.AVAILABLE,
            "Authorization: Bearer top-secret-token",
            details,
        )
    )

    payload = system_diagnostics.build_system_diagnostics_document(report)
    encoded = json.dumps(payload, ensure_ascii=False)

    assert "sensitive-credential-value" not in encoded
    assert "another-sensitive-value" not in encoded
    assert "hunter2" not in encoded
    assert "top-secret-token" not in encoded
    component = payload["components"][0]
    assert component["summary"] == "[REDACTED]"
    assert component["details"]["apiKey"] == "[REDACTED]"
    assert component["details"]["authorization"] == "[REDACTED]"
    assert component["details"]["note"] == "[REDACTED]"
    assert len(component["details"]) <= system_diagnostics.SYSTEM_DIAGNOSTICS_MAX_DETAIL_FIELDS
    assert (
        len(component["details"]["items"])
        <= system_diagnostics.SYSTEM_DIAGNOSTICS_MAX_DETAIL_ITEMS
    )

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)

    assert all(
        len(value.encode("utf-8"))
        <= system_diagnostics.SYSTEM_DIAGNOSTICS_MAX_STRING_BYTES
        for value in strings(payload)
    )


def test_get_system_diagnostics_uses_fixed_probes_and_deadlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}
    report = _report(
        DiagnosticResult(
            "python",
            DiagnosticStatus.AVAILABLE,
            "Python runtime is available.",
        )
    )

    def run(probes, **kwargs):
        captured["probes"] = probes
        captured.update(kwargs)
        return report

    monkeypatch.setattr(system_diagnostics, "run_runtime_diagnostics", run)

    payload = system_diagnostics.get_system_diagnostics()

    assert captured == {
        "probes": system_diagnostics.SYSTEM_DIAGNOSTICS_PROBES,
        "timeout_seconds": (
            system_diagnostics.SYSTEM_DIAGNOSTICS_PER_PROBE_TIMEOUT_SECONDS
        ),
        "total_timeout_seconds": (
            system_diagnostics.SYSTEM_DIAGNOSTICS_TOTAL_TIMEOUT_SECONDS
        ),
    }
    assert payload["components"][0]["id"] == "python"


@pytest.mark.parametrize("arguments", [["doctor", "--json"], ["--json", "doctor"]])
def test_doctor_json_uses_shared_diagnostics_service(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    monkeypatch.setattr(
        system_diagnostics,
        "get_system_diagnostics",
        lambda: payload,
    )

    result = CliRunner().invoke(cli_main.app, arguments)

    assert result.exit_code == 0
    assert json.loads(result.stdout) == payload
    assert "Daemon" not in result.stdout


def test_doctor_json_failure_is_machine_readable_and_does_not_echo_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail():
        raise RuntimeError("api_key=do-not-print-this")

    monkeypatch.setattr(system_diagnostics, "get_system_diagnostics", fail)

    result = CliRunner().invoke(cli_main.app, ["doctor", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "error": "runtime-diagnostics-unavailable",
        "schema": system_diagnostics.SYSTEM_DIAGNOSTICS_SCHEMA,
        "schemaVersion": system_diagnostics.SYSTEM_DIAGNOSTICS_SCHEMA_VERSION,
    }
    assert "do-not-print-this" not in result.output


def test_doctor_interactive_output_remains_compatible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_main,
        "_get_client",
        lambda: SimpleNamespace(ping=lambda: True),
    )
    monkeypatch.setattr(
        cli_main,
        "_read_settings",
        lambda: {
            "data_root": str(tmp_path),
            "llm_provider": "deepseek",
            "qwen3_asr_model_path": "",
        },
    )
    monkeypatch.setattr(shutil, "which", lambda _name: "C:\\tools\\ffmpeg.exe")
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=lambda: True,
                get_device_name=lambda _index: "Test GPU",
            )
        ),
    )

    result = CliRunner().invoke(cli_main.app, ["--plain", "doctor"])

    assert result.exit_code == 0
    for label in ("Daemon", "ffmpeg", "CUDA", "data_root", "LLM", "ASR model"):
        assert label in result.stdout
