from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "check-release-dependencies.py"
SPEC = importlib.util.spec_from_file_location("check_release_dependencies", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
release_dependencies = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release_dependencies
SPEC.loader.exec_module(release_dependencies)


def _write_dependency_fixture(
    root: Path,
    *,
    omit_declaration: str | None = None,
    omit_locked_package: str | None = None,
    stale_metadata: str | None = None,
    invalid_pillow_hash: bool = False,
    lock_version: str = "0.4.1",
    root_source: str = "virtual",
) -> None:
    core_specifiers = {
        "fastapi": ">=1.0",
        "httpx": ">=1.0",
        "numpy": ">=1.0",
        "openai": ">=1.0",
        "pillow": ">=12.1.0",
        "pydantic": ">=1.0",
        "transformers": ">=1.0",
        "uvicorn": ">=1.0",
    }
    dependencies = [
        f'"{name}{specifier}"'
        for name, specifier in core_specifiers.items()
        if name != omit_declaration
    ]
    pyproject = f"""\
[project]
name = "MediaProcessPipeline"
version = "0.4.1"
requires-python = ">=3.11,<3.13"
dependencies = [{", ".join(dependencies)}]

[project.optional-dependencies]
images = ["onnxruntime>=1.0"]
"""
    (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")

    root_dependencies = [
        f'    {{ name = "{name}" }},'
        for name in core_specifiers
        if name != omit_declaration
    ]
    metadata_requirements = []
    for name, specifier in core_specifiers.items():
        if name == omit_declaration:
            continue
        locked_specifier = ">=0.9" if stale_metadata == name else specifier
        metadata_requirements.append(
            f'    {{ name = "{name}", specifier = "{locked_specifier}" }},'
        )
    metadata_requirements.append(
        '    { name = "onnxruntime", marker = "extra == \'images\'", specifier = ">=1.0" },'
    )

    package_blocks = []
    for name in [*core_specifiers, "onnxruntime"]:
        if name == omit_locked_package:
            continue
        if name == "pillow":
            hash_value = (
                "sha256:not-a-real-hash"
                if invalid_pillow_hash
                else f"sha256:{'a' * 64}"
            )
            wheel_names = [
                "pillow-12.1.0-cp311-cp311-win_amd64.whl",
                "pillow-12.1.0-cp311-cp311-manylinux2014_x86_64.whl",
                "pillow-12.1.0-cp312-cp312-win_amd64.whl",
                "pillow-12.1.0-cp312-cp312-manylinux2014_x86_64.whl",
            ]
            wheels = "\n".join(
                (
                    f'    {{ url = "https://example.test/{wheel_name}", '
                    f'hash = "{hash_value}", size = 10 }},'
                )
                for wheel_name in wheel_names
            )
            package_blocks.append(
                f"""\
[[package]]
name = "pillow"
version = "12.1.0"
source = {{ registry = "https://pypi.org/simple" }}
wheels = [
{wheels}
]
"""
            )
        else:
            package_blocks.append(
                f"""\
[[package]]
name = "{name}"
version = "1.0.0"
source = {{ registry = "https://pypi.org/simple" }}
"""
            )

    lock = f"""\
version = 1
revision = 3
requires-python = ">=3.11, <3.13"

[[package]]
name = "mediaprocesspipeline"
version = "{lock_version}"
source = {{ {root_source} = "." }}
dependencies = [
{chr(10).join(root_dependencies)}
]

[package.optional-dependencies]
images = [
    {{ name = "onnxruntime" }},
]

[package.metadata]
requires-dist = [
{chr(10).join(metadata_requirements)}
]
provides-extras = ["images"]

{chr(10).join(package_blocks)}
"""
    (root / "uv.lock").write_text(lock, encoding="utf-8")


def _error_codes(report) -> set[str]:
    return {error["code"] for error in report.errors}


def test_dependency_gate_accepts_consistent_lock_and_pillow_wheels(tmp_path):
    _write_dependency_fixture(tmp_path)

    report = release_dependencies.check_release_dependencies(
        tmp_path,
        verify_with_uv=False,
    )

    assert report.ok is True
    assert report.summary["project"]["version"] == "0.4.1"
    assert report.summary["extras"] == {"images": ["onnxruntime"]}
    assert (
        report.summary["pillow_wheels"]["3.12"]["windows_x64"]["hash"]
        == f"sha256:{'a' * 64}"
    )


def test_dependency_gate_accepts_editable_project_root(tmp_path):
    _write_dependency_fixture(tmp_path, root_source="editable")

    report = release_dependencies.check_release_dependencies(
        tmp_path,
        verify_with_uv=False,
    )

    assert report.ok is True


def test_dependency_gate_reports_missing_core_declaration(tmp_path):
    _write_dependency_fixture(tmp_path, omit_declaration="pillow")

    report = release_dependencies.check_release_dependencies(
        tmp_path,
        verify_with_uv=False,
    )

    assert report.ok is False
    assert "core_dependency_missing" in _error_codes(report)
    assert any(
        error.get("details", {}).get("dependency") == "pillow"
        for error in report.errors
    )


@pytest.mark.parametrize("dependency", ["openai", "onnxruntime"])
def test_dependency_gate_reports_declared_package_missing_from_lock(
    tmp_path,
    dependency,
):
    _write_dependency_fixture(tmp_path, omit_locked_package=dependency)

    report = release_dependencies.check_release_dependencies(
        tmp_path,
        verify_with_uv=False,
    )

    assert report.ok is False
    assert "locked_package_missing" in _error_codes(report)
    assert any(
        error.get("details", {}).get("dependency") == dependency
        for error in report.errors
    )


def test_dependency_gate_reports_lock_metadata_drift(tmp_path):
    _write_dependency_fixture(tmp_path, stale_metadata="fastapi")

    report = release_dependencies.check_release_dependencies(
        tmp_path,
        verify_with_uv=False,
    )

    assert report.ok is False
    assert "lock_requirement_metadata_drift" in _error_codes(report)


def test_dependency_gate_reports_project_version_drift(tmp_path):
    _write_dependency_fixture(tmp_path, lock_version="0.4.0")

    report = release_dependencies.check_release_dependencies(
        tmp_path,
        verify_with_uv=False,
    )

    assert report.ok is False
    assert "lock_project_version_drift" in _error_codes(report)


def test_dependency_gate_rejects_pillow_wheel_without_sha256(tmp_path):
    _write_dependency_fixture(tmp_path, invalid_pillow_hash=True)

    report = release_dependencies.check_release_dependencies(
        tmp_path,
        verify_with_uv=False,
    )

    assert report.ok is False
    assert "pillow_wheel_hash_invalid" in _error_codes(report)


def test_json_cli_emits_machine_readable_report(tmp_path, monkeypatch, capsys):
    _write_dependency_fixture(tmp_path)
    monkeypatch.setattr(
        release_dependencies,
        "_run_uv_checks",
        lambda *_args, **_kwargs: None,
    )

    exit_code = release_dependencies.main(
        ["--project-root", str(tmp_path), "--json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["summary"]["project"]["version"] == "0.4.1"
    assert payload["errors"] == []
