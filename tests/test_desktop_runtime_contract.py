from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
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
        ".gitkeep": b"",
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


def _write_trusted_sources(source_root: Path, runtime_root: Path) -> None:
    source_paths = {
        ".gitkeep": "web/src-tauri/resources/runtime/.gitkeep",
        "VERSION": "VERSION",
        "pyproject.toml": "pyproject.toml",
        "uv.lock": "uv.lock",
        "backend/app/__init__.py": "backend/app/__init__.py",
        "web/dist/index.html": "web/dist/index.html",
        "web/dist/assets/app.css": "web/dist/assets/app.css",
        "web/dist/assets/app.js": "web/dist/assets/app.js",
        "packaging/desktop-tools.json": "packaging/desktop-tools.json",
        "third-party-licenses/uv-LICENSE-MIT.txt": (
            "packaging/third-party-licenses/uv-LICENSE-MIT.txt"
        ),
    }
    for staged_path, source_path in source_paths.items():
        destination = source_root / source_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(runtime_root / staged_path, destination)
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=source_root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "add", "--", "backend/app"],
        cwd=source_root,
        check=True,
        capture_output=True,
    )


def _initialize_git_repository(
    root: Path,
    relative_path: str = "source.txt",
) -> tuple[Path, str]:
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    source = root / relative_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("trusted\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "--", relative_path],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=MPP Tests",
            "-c",
            "user.email=mpp-tests@example.invalid",
            "-c",
            "commit.gpgSign=false",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return source, desktop_runtime._repo_head(root)


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


def test_runtime_contract_rejects_dirty_manifest_by_default(tmp_path):
    contract = _write_runtime(tmp_path)
    manifest_path = tmp_path / "runtime-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sourceDirty"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert (
        "manifest sourceDirty must be false for production verification"
        in _validate(tmp_path, contract)
    )
    assert _validate(tmp_path, contract, allow_dirty=True) == []


def test_runtime_contract_rejects_modified_file(tmp_path):
    contract = _write_runtime(tmp_path)
    (tmp_path / "web" / "dist" / "assets" / "app.js").write_text(
        "tampered",
        encoding="utf-8",
    )

    errors = _validate(tmp_path, contract)

    assert "size mismatch: web/dist/assets/app.js" in errors
    assert "SHA-256 mismatch: web/dist/assets/app.js" in errors


def test_runtime_contract_binds_manifest_records_to_build_sources(tmp_path):
    runtime_root = tmp_path / "runtime"
    source_root = tmp_path / "source"
    contract = _write_runtime(runtime_root)
    _write_trusted_sources(source_root, runtime_root)
    changed = b"# attacker replaced the backend and regenerated the manifest\n"
    (runtime_root / "backend" / "app" / "__init__.py").write_bytes(changed)
    manifest_path = runtime_root / "runtime-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = next(
        item
        for item in manifest["files"]
        if item["path"] == "backend/app/__init__.py"
    )
    record["size"] = len(changed)
    record["sha256"] = _sha256(changed)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert _validate(runtime_root, contract) == []
    errors = desktop_runtime.validate_runtime(
        runtime_root,
        expected_source_commit=SOURCE_COMMIT,
        trusted_tool_contract=contract,
        trusted_source_root=source_root,
    )

    assert (
        "staged file differs from trusted runtime source: backend/app/__init__.py"
        in errors
    )


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


def test_runtime_contract_binds_self_consistent_version_to_desktop_build(tmp_path):
    contract = _write_runtime(tmp_path)
    version_content = b"9.9.9\n"
    (tmp_path / "VERSION").write_bytes(version_content)
    manifest_path = tmp_path / "runtime-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["appVersion"] = "9.9.9"
    version_record = next(
        item for item in manifest["files"] if item["path"] == "VERSION"
    )
    version_record["size"] = len(version_content)
    version_record["sha256"] = _sha256(version_content)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    errors = desktop_runtime.validate_runtime(
        tmp_path,
        expected_source_commit=SOURCE_COMMIT,
        expected_app_version="0.4.1",
        trusted_tool_contract=contract,
    )

    assert (
        "manifest appVersion differs from the expected application version" in errors
    )


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


def test_release_repository_attestation_rejects_dirty_worktree(tmp_path):
    source, source_commit = _initialize_git_repository(tmp_path)

    assert (
        desktop_runtime.validate_repository_identity(
            tmp_path,
            expected_source_commit=source_commit,
            require_clean=True,
        )
        == []
    )

    source.write_text("changed\n", encoding="utf-8")
    errors = desktop_runtime.validate_repository_identity(
        tmp_path,
        expected_source_commit=source_commit,
        require_clean=True,
    )

    assert any("release source worktree must be clean" in error for error in errors)


def test_release_repository_attestation_rejects_mismatched_commit(tmp_path):
    _, source_commit = _initialize_git_repository(tmp_path)
    wrong_commit = "f" * 40
    assert source_commit != wrong_commit

    errors = desktop_runtime.validate_repository_identity(
        tmp_path,
        expected_source_commit=wrong_commit,
        require_clean=True,
    )

    assert any(
        "repository HEAD differs from the expected source commit" in error
        for error in errors
    )


@pytest.mark.parametrize(
    ("flag", "expected_error"),
    [
        ("--assume-unchanged", "assume-unchanged is forbidden"),
        ("--skip-worktree", "skip-worktree is forbidden"),
    ],
)
def test_release_repository_attestation_rejects_hidden_index_flags(
    tmp_path,
    flag,
    expected_error,
):
    _, source_commit = _initialize_git_repository(tmp_path)
    subprocess.run(
        ["git", "update-index", flag, "--", "source.txt"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    errors = desktop_runtime.validate_repository_identity(
        tmp_path,
        expected_source_commit=source_commit,
        require_clean=True,
    )

    assert any(expected_error in error for error in errors)


@pytest.mark.parametrize("environment_name", ["GIT_DIR", "GIT_CONFIG_COUNT"])
def test_release_repository_attestation_rejects_git_environment_override(
    tmp_path,
    monkeypatch,
    environment_name,
):
    _, source_commit = _initialize_git_repository(tmp_path)
    monkeypatch.setenv(environment_name, str(tmp_path / "redirected.git"))

    errors = desktop_runtime.validate_repository_identity(
        tmp_path,
        expected_source_commit=source_commit,
        require_clean=True,
    )

    assert any(
        "Git repository override variables are forbidden" in error
        and environment_name in error
        for error in errors
    )
    assert not any("could not attest repository HEAD" in error for error in errors)


def test_release_blob_attestation_detects_mid_build_mutation(tmp_path):
    source, source_commit = _initialize_git_repository(
        tmp_path,
        "web/src/main.tsx",
    )
    initial_digest, initial_tree = desktop_runtime.compute_build_input_digest(
        tmp_path
    )

    assert (
        desktop_runtime.validate_repository_identity(
            tmp_path,
            expected_source_commit=source_commit,
            require_clean=True,
        )
        == []
    )
    assert desktop_runtime.validate_release_input_blobs(tmp_path) == []

    source.write_text("mutated during build\n", encoding="utf-8")
    final_digest, final_tree = desktop_runtime.compute_build_input_digest(tmp_path)
    errors = desktop_runtime.validate_release_input_blobs(tmp_path)

    assert (final_digest, final_tree) == (initial_digest, initial_tree)
    assert any(
        "release input differs byte-for-byte from HEAD blob: web/src/main.tsx"
        in error
        for error in errors
    )


def test_release_blob_attestation_rejects_git_filter_attribute(tmp_path):
    _initialize_git_repository(tmp_path, "web/src/main.tsx")
    attributes = tmp_path / ".gitattributes"
    attributes.write_text("*.tsx filter=release-rewrite\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "--", ".gitattributes"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=MPP Tests",
            "-c",
            "user.email=mpp-tests@example.invalid",
            "-c",
            "commit.gpgSign=false",
            "commit",
            "--quiet",
            "-m",
            "attribute fixture",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    errors = desktop_runtime.validate_release_input_blobs(tmp_path)

    assert any(
        "Git attribute filter='release-rewrite' is forbidden for release input: "
        "web/src/main.tsx" in error
        for error in errors
    )


def test_runtime_contract_rejects_crlf_rewritten_uv_license(tmp_path):
    contract = _write_runtime(tmp_path)
    license_path = tmp_path / "third-party-licenses" / "uv-LICENSE-MIT.txt"
    license_path.write_bytes(license_path.read_bytes().replace(b"\n", b"\r\n"))

    errors = _validate(tmp_path, contract)

    assert "SHA-256 mismatch: third-party-licenses/uv-LICENSE-MIT.txt" in errors
    assert "bundled uv license differs from the desktop tool contract" in errors


def test_release_text_inputs_are_fixed_to_lf():
    required_lf_paths = [
        "VERSION",
        "uv.lock",
        "pyproject.toml",
        "web/src-tauri/resources/runtime/.gitkeep",
        "packaging/desktop-tools.json",
        "packaging/desktop-build-tools.json",
        "packaging/third-party-licenses/uv-LICENSE-MIT.txt",
        "scripts/build-desktop.ps1",
        "scripts/check-desktop-runtime.py",
        "web/package-lock.json",
        "web/src-tauri/Cargo.lock",
        "web/src-tauri/build.rs",
    ]
    result = subprocess.run(
        ["git", "check-attr", "-z", "eol", "--", *required_lf_paths],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    fields = result.stdout.split(b"\0")
    assert fields[-1] == b""
    attributes = {
        fields[index].decode(): fields[index + 2].decode()
        for index in range(0, len(fields) - 1, 3)
    }

    assert attributes == {path: "lf" for path in required_lf_paths}


def test_release_entry_enforces_formal_offline_build_contract():
    script = (ROOT / "scripts" / "build-desktop.ps1").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert '"--no-install"' in script
    assert '@("--", "--locked", "--offline")' in script
    assert '$env:CARGO_NET_OFFLINE = "true"' in script
    assert '$env:npm_config_offline = "true"' in script
    assert '[ValidateSet("All", "Prepare", "Build")]' in script
    assert "ConvertFrom-Json -AsHashtable" in script
    assert "Get-ReleaseSourceIdentity" in script
    assert "[switch]$SafeOutputOnFailure" in script
    assert ") -SafeOutputOnFailure" in script
    assert "$MaximumDiagnosticLength = 4096" in script
    assert "Runtime manifest changed during desktop compilation" in script
    assert "Test-FileContainsAscii" in script
    assert "scripts/build-desktop.ps1 -Phase Prepare -NoBundle -Ci" in workflow
    assert "scripts/build-desktop.ps1 -Phase Build -NoBundle -Ci" in workflow


def test_packaged_web_payload_is_bound_to_built_web_source(tmp_path):
    runtime_root = tmp_path / "runtime"
    source_root = tmp_path / "source"
    contract = _write_runtime(runtime_root)
    _write_trusted_sources(source_root, runtime_root)
    changed = b"console.log('self-consistent replacement')"
    staged_web = runtime_root / "web" / "dist" / "assets" / "app.js"
    staged_web.write_bytes(changed)
    manifest_path = runtime_root / "runtime-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = next(
        item
        for item in manifest["files"]
        if item["path"] == "web/dist/assets/app.js"
    )
    record["size"] = len(changed)
    record["sha256"] = _sha256(changed)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    errors = desktop_runtime.validate_runtime(
        runtime_root,
        expected_source_commit=SOURCE_COMMIT,
        trusted_tool_contract=contract,
        trusted_source_root=source_root,
    )

    assert (
        "staged file differs from trusted runtime source: web/dist/assets/app.js"
        in errors
    )
