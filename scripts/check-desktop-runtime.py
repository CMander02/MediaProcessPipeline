#!/usr/bin/env python3
"""Validate the immutable runtime directory embedded in the desktop bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat as stat_module
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNTIME_ROOT = REPO_ROOT / "web" / "src-tauri" / "resources" / "runtime"
DEFAULT_TOOL_CONTRACT = REPO_ROOT / "packaging" / "desktop-tools.json"
TOOL_CONTRACT_RUNTIME_PATH = "packaging/desktop-tools.json"
UV_LICENSE_RUNTIME_PATH = "third-party-licenses/uv-LICENSE-MIT.txt"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
WEB_REFERENCE_RE = re.compile(r"""(?:src|href)=["'](?P<path>/[^"'?#]+)""")
FORBIDDEN_PARTS = {
    ".env",
    ".envrc",
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".ssh",
    ".aws",
    "__pycache__",
    "auth.json",
    "config.json",
    "cookies.json",
    "cookies.txt",
    "credentials.json",
    "client_secret.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "secrets.json",
    "service-account.json",
    "settings.json",
    "storage_state.json",
    "token.json",
    "tokens.json",
}
FORBIDDEN_SUFFIXES = (
    ".crt",
    ".der",
    ".egg-info",
    ".jks",
    ".key",
    ".keystore",
    ".p12",
    ".pem",
    ".pfx",
    ".pkcs12",
    ".ppk",
    ".pyc",
    ".pyo",
    ".secret",
    ".secrets",
)
REQUIRED_PATHS = {
    "VERSION",
    "pyproject.toml",
    "uv.lock",
    "backend/app/__init__.py",
    "web/dist/index.html",
    TOOL_CONTRACT_RUNTIME_PATH,
    UV_LICENSE_RUNTIME_PATH,
}
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


def _is_link_or_reparse(stat_result: os.stat_result) -> bool:
    attributes = getattr(stat_result, "st_file_attributes", 0)
    return stat_module.S_ISLNK(stat_result.st_mode) or bool(
        attributes & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _relative_files(runtime_root: Path) -> tuple[dict[str, Path], list[str]]:
    files: dict[str, Path] = {}
    errors: list[str] = []
    pending = [runtime_root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            relative = (
                directory.relative_to(runtime_root).as_posix()
                if directory != runtime_root
                else "."
            )
            errors.append(f"runtime directory is unreadable: {relative}: {exc}")
            continue
        for entry in entries:
            path = Path(entry.path)
            relative_path = path.relative_to(runtime_root).as_posix()
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                errors.append(f"runtime entry is unreadable: {relative_path}: {exc}")
                continue
            if entry.is_symlink() or _is_link_or_reparse(entry_stat):
                errors.append(f"symlink or reparse point is not allowed: {relative_path}")
                continue
            if stat_module.S_ISDIR(entry_stat.st_mode):
                pending.append(path)
            elif stat_module.S_ISREG(entry_stat.st_mode):
                files[relative_path] = path
            else:
                errors.append(f"unsupported runtime entry: {relative_path}")
    return files, errors


def _forbidden_path(relative_path: str) -> bool:
    parts = relative_path.lower().split("/")
    return any(
        part in FORBIDDEN_PARTS
        or part.startswith(".env.")
        or part.endswith(FORBIDDEN_SUFFIXES)
        for part in parts
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json_object(path: Path, label: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"{label} is invalid: {exc}"
    if not isinstance(value, dict):
        return None, f"{label} must contain an object"
    return value, None


def _required_uv_version(pyproject_path: Path) -> tuple[str | None, str | None]:
    try:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        return None, f"staged pyproject.toml is invalid: {exc}"
    required = data.get("tool", {}).get("uv", {}).get("required-version")
    if not isinstance(required, str) or not required.startswith("==") or len(required) <= 2:
        return None, "staged pyproject.toml must pin [tool.uv].required-version with =="
    return required[2:], None


def _pe_machine(path: Path) -> tuple[str | None, str | None]:
    try:
        with path.open("rb") as handle:
            dos_header = handle.read(64)
            if len(dos_header) != 64 or dos_header[:2] != b"MZ":
                return None, "missing DOS MZ header"
            pe_offset = int.from_bytes(dos_header[0x3C:0x40], "little")
            if pe_offset < 64 or pe_offset > 16 * 1024 * 1024:
                return None, f"invalid PE header offset {pe_offset}"
            handle.seek(pe_offset)
            pe_header = handle.read(6)
            if len(pe_header) != 6 or pe_header[:4] != b"PE\0\0":
                return None, "missing PE signature"
            machine = int.from_bytes(pe_header[4:6], "little")
    except OSError as exc:
        return None, str(exc)
    return f"0x{machine:04x}", None


def _validate_tool_contract(
    contract: dict[str, Any],
    *,
    trusted_contract: dict[str, Any] | None,
    required_uv_version: str | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    if trusted_contract is not None and contract != trusted_contract:
        errors.append("staged desktop tool contract differs from the trusted contract")
    if contract.get("schema") != 1:
        errors.append("desktop tool contract schema must be 1")
    uv = contract.get("tools", {}).get("uv")
    if not isinstance(uv, dict):
        return None, [*errors, "desktop tool contract must define tools.uv"]

    version = uv.get("version")
    platform = uv.get("platform")
    source = uv.get("source")
    binary = uv.get("binary")
    license_info = uv.get("license")
    if not isinstance(version, str) or not version:
        errors.append("desktop tool contract uv.version must be non-empty")
    elif required_uv_version is not None and version != required_uv_version:
        errors.append(
            "desktop tool contract uv.version differs from pyproject required-version"
        )
    if not isinstance(platform, dict) or platform != {
        "os": "windows",
        "arch": "x64",
        "target": "x86_64-pc-windows-msvc",
    }:
        errors.append("desktop tool contract uv platform must be Windows x64 MSVC")
    if not isinstance(source, dict):
        errors.append("desktop tool contract uv.source must be an object")
    else:
        expected_url = (
            f"https://github.com/astral-sh/uv/releases/download/{version}/"
            "uv-x86_64-pc-windows-msvc.zip"
        )
        if source.get("url") != expected_url:
            errors.append("desktop tool contract uv source URL is invalid")
        if not SHA256_RE.fullmatch(str(source.get("archiveSha256", ""))):
            errors.append("desktop tool contract uv archive SHA-256 is invalid")
    if not isinstance(binary, dict):
        errors.append("desktop tool contract uv.binary must be an object")
    else:
        if binary.get("runtimePath") != "bin/uv.exe":
            errors.append("desktop tool contract uv binary path must be bin/uv.exe")
        if not SHA256_RE.fullmatch(str(binary.get("sha256", ""))):
            errors.append("desktop tool contract uv binary SHA-256 is invalid")
        size = binary.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            errors.append("desktop tool contract uv binary size must be a positive integer")
        if binary.get("peMachine") != "0x8664":
            errors.append("desktop tool contract uv PE machine must be 0x8664")
    if not isinstance(license_info, dict):
        errors.append("desktop tool contract uv.license must be an object")
    else:
        if license_info.get("spdx") != "MIT":
            errors.append("desktop tool contract uv license must be MIT")
        if (
            license_info.get("sourcePath")
            != "packaging/third-party-licenses/uv-LICENSE-MIT.txt"
            or license_info.get("runtimePath") != UV_LICENSE_RUNTIME_PATH
        ):
            errors.append("desktop tool contract uv license paths are invalid")
        if not SHA256_RE.fullmatch(str(license_info.get("sha256", ""))):
            errors.append("desktop tool contract uv license SHA-256 is invalid")
    return uv, errors


def validate_runtime(
    runtime_root: Path,
    *,
    verify_tools: bool = False,
    expected_source_commit: str | None = None,
    trusted_tool_contract: dict[str, Any] | None = None,
) -> list[str]:
    runtime_root = runtime_root.absolute()
    try:
        root_stat = runtime_root.lstat()
    except OSError as exc:
        return [f"runtime root is unavailable: {exc}"]
    if _is_link_or_reparse(root_stat):
        return ["runtime root must not be a symlink or reparse point"]
    if not stat_module.S_ISDIR(root_stat.st_mode):
        return ["runtime root must be a directory"]
    runtime_root = runtime_root.resolve()
    errors: list[str] = []
    manifest_path = runtime_root / "runtime-manifest.json"
    manifest, manifest_error = _read_json_object(
        manifest_path, "runtime-manifest.json"
    )
    if manifest_error:
        return [manifest_error]
    assert manifest is not None
    if manifest.get("schema") != 1:
        errors.append("runtime manifest schema must be 1")

    version_path = runtime_root / "VERSION"
    try:
        expected_version = (
            version_path.read_text(encoding="utf-8").strip()
            if version_path.is_file()
            else ""
        )
    except (OSError, UnicodeError) as exc:
        expected_version = ""
        errors.append(f"VERSION is unreadable: {exc}")
    if not expected_version:
        errors.append("VERSION is missing or empty")
    elif manifest.get("appVersion") != expected_version:
        errors.append("manifest appVersion differs from VERSION")

    source_commit = str(manifest.get("sourceCommit", ""))
    if not COMMIT_RE.fullmatch(source_commit):
        errors.append("manifest sourceCommit must be a full lowercase Git SHA")
    if expected_source_commit is not None:
        if not COMMIT_RE.fullmatch(expected_source_commit):
            errors.append("expected source commit must be a full lowercase Git SHA")
        elif source_commit != expected_source_commit:
            errors.append("manifest sourceCommit differs from the expected Git SHA")
    if not isinstance(manifest.get("sourceDirty"), bool):
        errors.append("manifest sourceDirty must be a boolean")
    if manifest.get("toolContract") != TOOL_CONTRACT_RUNTIME_PATH:
        errors.append(
            f"manifest toolContract must be {TOOL_CONTRACT_RUNTIME_PATH}"
        )

    all_files, scan_errors = _relative_files(runtime_root)
    errors.extend(scan_errors)
    for relative_path in all_files:
        if _forbidden_path(relative_path):
            errors.append(f"forbidden runtime path: {relative_path}")

    records = manifest.get("files")
    if not isinstance(records, list):
        return [*errors, "manifest files must be an array"]

    declared: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append(f"manifest files[{index}] must be an object")
            continue
        relative_path = str(record.get("path", ""))
        candidate = Path(relative_path)
        if (
            not relative_path
            or candidate.is_absolute()
            or "\\" in relative_path
            or ".." in candidate.parts
            or "\0" in relative_path
        ):
            errors.append(f"invalid manifest path: {relative_path!r}")
            continue
        if relative_path in declared:
            errors.append(f"duplicate manifest path: {relative_path}")
            continue
        declared[relative_path] = record

    actual = set(all_files) - {"runtime-manifest.json"}
    declared_paths = set(declared)
    for missing in sorted(declared_paths - actual):
        errors.append(f"declared file is missing: {missing}")
    for unexpected in sorted(actual - declared_paths):
        errors.append(f"unlisted runtime file: {unexpected}")
    for required in sorted(REQUIRED_PATHS - actual):
        errors.append(f"required runtime file is missing: {required}")

    actual_metadata: dict[str, tuple[int, str]] = {}
    for relative_path in sorted(actual & declared_paths):
        path = all_files[relative_path]
        record = declared[relative_path]
        try:
            size = path.stat().st_size
            actual_hash = _sha256(path)
        except OSError as exc:
            errors.append(f"runtime file is unreadable: {relative_path}: {exc}")
            continue
        actual_metadata[relative_path] = (size, actual_hash)
        if record.get("size") != size:
            errors.append(f"size mismatch: {relative_path}")
        expected_hash = str(record.get("sha256", ""))
        if not SHA256_RE.fullmatch(expected_hash):
            errors.append(f"invalid SHA-256 declaration: {relative_path}")
        elif actual_hash != expected_hash:
            errors.append(f"SHA-256 mismatch: {relative_path}")

    required_uv_version, pyproject_error = _required_uv_version(
        runtime_root / "pyproject.toml"
    )
    if pyproject_error:
        errors.append(pyproject_error)

    staged_contract, contract_read_error = _read_json_object(
        runtime_root / TOOL_CONTRACT_RUNTIME_PATH,
        "staged desktop tool contract",
    )
    uv_contract: dict[str, Any] | None = None
    if contract_read_error:
        errors.append(contract_read_error)
    else:
        assert staged_contract is not None
        uv_contract, contract_errors = _validate_tool_contract(
            staged_contract,
            trusted_contract=trusted_tool_contract,
            required_uv_version=required_uv_version,
        )
        errors.extend(contract_errors)

    index_path = runtime_root / "web" / "dist" / "index.html"
    if index_path.is_file():
        try:
            index_text = index_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"Web index is unreadable: {exc}")
        else:
            for match in WEB_REFERENCE_RE.finditer(index_text):
                referenced = f"web/dist/{match.group('path').lstrip('/')}"
                if referenced not in actual:
                    errors.append(f"Web index references a missing asset: {referenced}")

    manifest_uv = manifest.get("uv")
    if not isinstance(manifest_uv, dict):
        errors.append("manifest uv section must be an object")
    elif uv_contract is not None:
        binary = uv_contract.get("binary", {})
        uv_path = str(binary.get("runtimePath", ""))
        expected_uv = {
            "version": uv_contract.get("version"),
            "path": uv_path,
            "sha256": binary.get("sha256"),
            "size": binary.get("size"),
            "peMachine": binary.get("peMachine"),
        }
        if manifest_uv != expected_uv:
            errors.append("manifest uv section differs from the desktop tool contract")
        uv_metadata = actual_metadata.get(uv_path)
        if uv_metadata != (binary.get("size"), binary.get("sha256")):
            errors.append("bundled uv file differs from the desktop tool contract")
        uv_file = all_files.get(uv_path)
        if uv_file is not None:
            actual_machine, machine_error = _pe_machine(uv_file)
            if machine_error:
                errors.append(f"bundled uv PE header is invalid: {machine_error}")
            elif actual_machine != binary.get("peMachine"):
                errors.append("bundled uv PE machine differs from the tool contract")

        license_info = uv_contract.get("license", {})
        license_path = str(license_info.get("runtimePath", ""))
        license_metadata = actual_metadata.get(license_path)
        if license_metadata is None or license_metadata[1] != license_info.get("sha256"):
            errors.append("bundled uv license differs from the desktop tool contract")

    if verify_tools and not errors and uv_contract is not None:
        binary = uv_contract["binary"]
        uv_path = str(binary["runtimePath"])
        try:
            result = subprocess.run(
                [str(all_files[uv_path]), "--version"],
                cwd=runtime_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"bundled uv could not run: {exc}")
        else:
            output = (result.stdout or result.stderr).strip()
            uv_version = str(uv_contract["version"])
            if result.returncode or not re.search(
                rf"\buv\s+{re.escape(uv_version)}(?:\s|$)",
                output,
            ):
                errors.append(
                    f"bundled uv version mismatch: expected {uv_version}, received {output}"
                )

    return errors


def _repo_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    commit = result.stdout.strip()
    if result.returncode or not COMMIT_RE.fullmatch(commit):
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"could not resolve repository HEAD: {detail}")
    return commit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "runtime_root",
        nargs="?",
        type=Path,
        default=DEFAULT_RUNTIME_ROOT,
    )
    parser.add_argument("--verify-tools", action="store_true")
    parser.add_argument("--expected-source-commit")
    parser.add_argument("--tool-contract", type=Path, default=DEFAULT_TOOL_CONTRACT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        expected_source_commit = args.expected_source_commit or _repo_head()
        trusted_tool_contract, contract_error = _read_json_object(
            args.tool_contract.resolve(), "trusted desktop tool contract"
        )
        if contract_error:
            errors = [contract_error]
        else:
            errors = validate_runtime(
                args.runtime_root,
                verify_tools=args.verify_tools,
                expected_source_commit=expected_source_commit,
                trusted_tool_contract=trusted_tool_contract,
            )
    except Exception as exc:
        errors = [f"desktop runtime verification failed: {exc}"]

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not errors,
                    "runtimeRoot": str(args.runtime_root.resolve()),
                    "errors": errors,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    elif errors:
        print("[FAIL] desktop runtime contract", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
    else:
        print(f"[PASS] desktop runtime contract: {args.runtime_root.resolve()}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
