from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "check-desktop-runtime.py"
SPEC = importlib.util.spec_from_file_location("check_desktop_runtime", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
desktop_runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = desktop_runtime
SPEC.loader.exec_module(desktop_runtime)

SOURCE_COMMIT = "a" * 40


def test_repository_uv_contract_is_pinned_to_windows_x64_release():
    contract = json.loads(
        (ROOT / "packaging" / "desktop-tools.json").read_text(encoding="utf-8")
    )
    uv = contract["tools"]["uv"]
    license_path = ROOT / uv["license"]["sourcePath"]

    assert contract["schema"] == 1
    assert uv["version"] == "0.9.21"
    assert uv["platform"] == {
        "os": "windows",
        "arch": "x64",
        "target": "x86_64-pc-windows-msvc",
    }
    assert uv["source"] == {
        "url": (
            "https://github.com/astral-sh/uv/releases/download/0.9.21/"
            "uv-x86_64-pc-windows-msvc.zip"
        ),
        "archiveSha256": (
            "d27952e73183ef8f6ee8c2a50cf8b3f2e08e01b6a9279a00a85cb261ea8d8337"
        ),
    }
    assert uv["binary"] == {
        "runtimePath": "bin/uv.exe",
        "sha256": (
            "c526c46af4e584f0a3fc9dde57f548a50489e17a673349486530cd8c36a8e16b"
        ),
        "size": 62778880,
        "peMachine": "0x8664",
    }
    normalized_license = (
        license_path.read_text(encoding="utf-8")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .encode()
    )
    assert _sha256(normalized_license) == uv["license"]["sha256"]


def _pe_fixture(machine: int = 0x8664) -> bytes:
    content = bytearray(128)
    content[0:2] = b"MZ"
    content[0x3C:0x40] = (64).to_bytes(4, "little")
    content[64:68] = b"PE\0\0"
    content[68:70] = machine.to_bytes(2, "little")
    content[70:] = b"fixture uv"
    return bytes(content)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_runtime(
    root: Path,
    *,
    source_commit: str = SOURCE_COMMIT,
    required_uv_version: str = "0.9.21",
) -> dict:
    uv_content = _pe_fixture()
    license_content = b"fixture MIT license\n"
    contract = {
        "schema": 1,
        "tools": {
            "uv": {
                "version": "0.9.21",
                "platform": {
                    "os": "windows",
                    "arch": "x64",
                    "target": "x86_64-pc-windows-msvc",
                },
                "source": {
                    "url": (
                        "https://github.com/astral-sh/uv/releases/download/0.9.21/"
                        "uv-x86_64-pc-windows-msvc.zip"
                    ),
                    "archiveSha256": "b" * 64,
                },
                "binary": {
                    "runtimePath": "bin/uv.exe",
                    "sha256": _sha256(uv_content),
                    "size": len(uv_content),
                    "peMachine": "0x8664",
                },
                "license": {
                    "spdx": "MIT",
                    "sourcePath": (
                        "packaging/third-party-licenses/uv-LICENSE-MIT.txt"
                    ),
                    "runtimePath": "third-party-licenses/uv-LICENSE-MIT.txt",
                    "sha256": _sha256(license_content),
                },
            }
        },
    }
    contract_content = json.dumps(contract, indent=2).encode()
    files = {
        "VERSION": b"0.4.1\n",
        "pyproject.toml": (
            b"[project]\nname='MediaProcessPipeline'\n"
            + f'[tool.uv]\nrequired-version = "=={required_uv_version}"\n'.encode()
        ),
        "uv.lock": b"version = 1\n",
        "backend/app/__init__.py": b"",
        "web/dist/index.html": (
            b'<html><link href="/assets/app.css">'
            b'<script src="/assets/app.js"></script></html>'
        ),
        "web/dist/assets/app.css": b"body {}",
        "web/dist/assets/app.js": b"console.log('mpp')",
        "packaging/desktop-tools.json": contract_content,
        "third-party-licenses/uv-LICENSE-MIT.txt": license_content,
        "bin/uv.exe": uv_content,
    }
    records = []
    for relative_path, content in files.items():
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        records.append(
            {
                "path": relative_path,
                "size": len(content),
                "sha256": _sha256(content),
            }
        )
    records.sort(key=lambda item: item["path"])
    uv_binary = contract["tools"]["uv"]["binary"]
    manifest = {
        "schema": 1,
        "appVersion": "0.4.1",
        "sourceCommit": source_commit,
        "sourceDirty": False,
        "toolContract": "packaging/desktop-tools.json",
        "uv": {
            "version": "0.9.21",
            "path": "bin/uv.exe",
            "sha256": uv_binary["sha256"],
            "size": uv_binary["size"],
            "peMachine": uv_binary["peMachine"],
        },
        "files": records,
    }
    (root / "runtime-manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return contract


def _validate(root: Path, contract: dict, **kwargs) -> list[str]:
    return desktop_runtime.validate_runtime(
        root,
        expected_source_commit=SOURCE_COMMIT,
        trusted_tool_contract=contract,
        **kwargs,
    )


def test_runtime_contract_accepts_complete_manifest(tmp_path):
    contract = _write_runtime(tmp_path)

    assert _validate(tmp_path, contract) == []


def test_runtime_contract_rejects_modified_file(tmp_path):
    contract = _write_runtime(tmp_path)
    (tmp_path / "web" / "dist" / "assets" / "app.js").write_text(
        "tampered",
        encoding="utf-8",
    )

    errors = _validate(tmp_path, contract)

    assert "size mismatch: web/dist/assets/app.js" in errors
    assert "SHA-256 mismatch: web/dist/assets/app.js" in errors


def test_runtime_contract_rejects_unlisted_secret_file(tmp_path):
    contract = _write_runtime(tmp_path)
    (tmp_path / ".env").write_text("API_KEY=secret", encoding="utf-8")

    errors = _validate(tmp_path, contract)

    assert "forbidden runtime path: .env" in errors
    assert "unlisted runtime file: .env" in errors


def test_runtime_contract_checks_web_asset_references(tmp_path):
    contract = _write_runtime(tmp_path)
    missing = tmp_path / "web" / "dist" / "assets" / "app.css"
    missing.unlink()

    errors = _validate(tmp_path, contract)

    assert "declared file is missing: web/dist/assets/app.css" in errors
    assert (
        "Web index references a missing asset: web/dist/assets/app.css" in errors
    )


def test_runtime_contract_binds_expected_source_commit(tmp_path):
    contract = _write_runtime(tmp_path, source_commit="c" * 40)

    errors = _validate(tmp_path, contract)

    assert "manifest sourceCommit differs from the expected Git SHA" in errors


def test_runtime_contract_binds_uv_to_pyproject(tmp_path):
    contract = _write_runtime(tmp_path, required_uv_version="0.9.22")

    errors = _validate(tmp_path, contract)

    assert (
        "desktop tool contract uv.version differs from pyproject required-version"
        in errors
    )


def test_runtime_contract_binds_staged_tool_contract_to_trusted_copy(tmp_path):
    contract = _write_runtime(tmp_path)
    trusted_contract = copy.deepcopy(contract)
    trusted_contract["tools"]["uv"]["source"]["archiveSha256"] = "d" * 64

    errors = _validate(tmp_path, trusted_contract)

    assert "staged desktop tool contract differs from the trusted contract" in errors


def test_runtime_contract_does_not_execute_uv_before_integrity_passes(
    tmp_path, monkeypatch
):
    contract = _write_runtime(tmp_path)
    (tmp_path / "web" / "dist" / "assets" / "app.js").write_text(
        "tampered",
        encoding="utf-8",
    )

    def unexpected_run(*args, **kwargs):
        raise AssertionError("bundled uv must not execute when integrity checks fail")

    monkeypatch.setattr(desktop_runtime.subprocess, "run", unexpected_run)

    errors = _validate(tmp_path, contract, verify_tools=True)

    assert "SHA-256 mismatch: web/dist/assets/app.js" in errors


def test_runtime_contract_reports_directory_symlink_without_traversing(
    tmp_path,
):
    contract = _write_runtime(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    link = tmp_path / "linked-directory"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlinks are unavailable in this environment")

    errors = _validate(tmp_path, contract)

    assert "symlink or reparse point is not allowed: linked-directory" in errors
    assert not any("linked-directory/secret.txt" in error for error in errors)
