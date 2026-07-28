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
TREE_RE = re.compile(r"^[0-9a-f]{40,64}$")
BUILD_INPUT_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
WEB_REFERENCE_RE = re.compile(r"""(?:src|href)=["'](?P<path>/[^"'?#]+)""")
BUILD_INPUT_DIGEST_DOMAIN = b"mpp-build-input-v1\0"
FORBIDDEN_GIT_ENVIRONMENT = {
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_ATTR_NOSYSTEM",
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_EXEC_PATH",
    "GIT_GLOB_PATHSPECS",
    "GIT_ICASE_PATHSPECS",
    "GIT_INDEX_FILE",
    "GIT_LITERAL_PATHSPECS",
    "GIT_NAMESPACE",
    "GIT_NOGLOB_PATHSPECS",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_REPLACE_REF_BASE",
    "GIT_SHALLOW_FILE",
    "GIT_WORK_TREE",
}
RELEASE_INPUT_FILES = {
    ".gitattributes",
    ".gitignore",
    ".python-version",
    "VERSION",
    "pyproject.toml",
    "rust-toolchain.toml",
    "uv.lock",
    "scripts/build-desktop.ps1",
    "scripts/check-desktop-runtime.py",
}
RELEASE_INPUT_PREFIXES = (
    "backend/app/",
    "backend/resources/",
    "packaging/",
    "web/",
)
DANGEROUS_GIT_ATTRIBUTES = (
    "filter",
    "working-tree-encoding",
    "ident",
    "export-subst",
)
FORBIDDEN_PARTS = {
    ".env",
    ".envrc",
    ".git",
    ".git-credentials",
    ".hg",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".svn",
    ".ssh",
    ".aws",
    "__pycache__",
    "auth.json",
    "client-secret.json",
    "client-secret.yaml",
    "client-secret.yml",
    "config.json",
    "cookies.json",
    "cookies.txt",
    "credential.json",
    "credential.yaml",
    "credential.yml",
    "credentials.json",
    "credentials.yaml",
    "credentials.yml",
    "client_secret.json",
    "client_secret.yaml",
    "client_secret.yml",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "service-account.json",
    "service-account.yaml",
    "service-account.yml",
    "service_account.json",
    "service_account.yaml",
    "service_account.yml",
    "settings.json",
    "storage_state.json",
    "token.json",
    "token.yaml",
    "token.yml",
    "tokens.json",
    "tokens.yaml",
    "tokens.yml",
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


def _trusted_source_files(source_root: Path) -> dict[str, Path]:
    """Map staged paths to the exact build inputs in a checked-out repository."""

    source_root = source_root.resolve()
    sources: dict[str, Path] = {}

    def add(staged_path: str, source_path: Path) -> None:
        try:
            metadata = source_path.lstat()
        except OSError as exc:
            raise RuntimeError(f"trusted source is unavailable: {source_path}: {exc}") from exc
        if _is_link_or_reparse(metadata) or not stat_module.S_ISREG(metadata.st_mode):
            raise RuntimeError(f"trusted source must be a regular file: {source_path}")
        resolved = source_path.resolve()
        try:
            resolved.relative_to(source_root)
        except ValueError as exc:
            raise RuntimeError(f"trusted source escapes the repository: {source_path}") from exc
        sources[staged_path] = resolved

    for relative_path in ("VERSION", "pyproject.toml", "uv.lock"):
        add(relative_path, source_root / relative_path)

    tracked = _git_process(
        source_root,
        "ls-files",
        "-z",
        "--",
        "backend/app",
        "backend/resources",
    )
    for raw_path in tracked.stdout.decode("utf-8", errors="strict").split("\0"):
        if raw_path:
            add(raw_path, source_root.joinpath(*raw_path.split("/")))

    web_dist = source_root / "web" / "dist"
    web_metadata = web_dist.lstat()
    if _is_link_or_reparse(web_metadata) or not stat_module.S_ISDIR(
        web_metadata.st_mode
    ):
        raise RuntimeError(f"trusted Web distribution must be a real directory: {web_dist}")
    try:
        web_dist.resolve().relative_to(source_root)
    except ValueError as exc:
        raise RuntimeError(
            f"trusted Web distribution escapes the repository: {web_dist}"
        ) from exc
    web_files, web_errors = _relative_files(web_dist)
    if web_errors:
        raise RuntimeError("; ".join(web_errors))
    for relative_path, source_path in web_files.items():
        add(f"web/dist/{relative_path}", source_path)

    add(TOOL_CONTRACT_RUNTIME_PATH, source_root / TOOL_CONTRACT_RUNTIME_PATH)
    add(
        UV_LICENSE_RUNTIME_PATH,
        source_root / "packaging" / "third-party-licenses" / "uv-LICENSE-MIT.txt",
    )
    add(
        ".gitkeep",
        source_root / "web" / "src-tauri" / "resources" / "runtime" / ".gitkeep",
    )
    return sources


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
    allow_dirty: bool = False,
    expected_source_commit: str | None = None,
    expected_app_version: str | None = None,
    trusted_tool_contract: dict[str, Any] | None = None,
    trusted_source_root: Path | None = None,
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
    if expected_app_version is not None:
        if not expected_app_version.strip():
            errors.append("expected application version must be non-empty")
        elif manifest.get("appVersion") != expected_app_version:
            errors.append(
                "manifest appVersion differs from the expected application version"
            )

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
    elif manifest["sourceDirty"] and not allow_dirty:
        errors.append("manifest sourceDirty must be false for production verification")
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

    if trusted_source_root is not None:
        try:
            trusted_sources = _trusted_source_files(trusted_source_root)
        except (OSError, UnicodeError, RuntimeError) as exc:
            errors.append(f"trusted runtime source validation failed: {exc}")
        else:
            expected_from_sources = set(trusted_sources) | {"bin/uv.exe"}
            for missing in sorted(expected_from_sources - actual):
                errors.append(f"trusted runtime source is missing from staging: {missing}")
            for unexpected in sorted(actual - expected_from_sources):
                errors.append(f"staged file has no trusted runtime source: {unexpected}")
            for relative_path in sorted(actual & set(trusted_sources)):
                try:
                    source_size = trusted_sources[relative_path].stat().st_size
                    source_hash = _sha256(trusted_sources[relative_path])
                except OSError as exc:
                    errors.append(
                        f"trusted runtime source is unreadable: {relative_path}: {exc}"
                    )
                    continue
                if actual_metadata.get(relative_path) != (source_size, source_hash):
                    errors.append(
                        f"staged file differs from trusted runtime source: {relative_path}"
                    )

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


def _git_environment_override_names() -> list[str]:
    configured = {
        name for name in FORBIDDEN_GIT_ENVIRONMENT if name in os.environ
    }
    configured.update(
        name for name in os.environ if name.startswith("GIT_CONFIG_")
    )
    return sorted(configured)


def _sanitized_git_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in list(environment):
        if name in FORBIDDEN_GIT_ENVIRONMENT or name.startswith("GIT_CONFIG_"):
            environment.pop(name, None)
    return environment


def _git_process(
    source_root: Path,
    *arguments: str,
    input_bytes: bytes | None = None,
    allowed_returncodes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", *arguments],
        cwd=source_root,
        env=_sanitized_git_environment(),
        input=input_bytes,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode not in allowed_returncodes:
        detail = (result.stderr or result.stdout).decode(
            "utf-8", errors="replace"
        ).strip()
        raise RuntimeError(f"git {' '.join(arguments)} failed: {detail}")
    return result


def _git_output(source_root: Path, *arguments: str) -> str:
    return _git_process(source_root, *arguments).stdout.decode(
        "utf-8", errors="strict"
    )


def _git_bytes(source_root: Path, *arguments: str) -> bytes:
    return _git_process(source_root, *arguments).stdout


def _repo_head(source_root: Path = REPO_ROOT) -> str:
    commit = _git_output(source_root, "rev-parse", "HEAD").strip()
    if not COMMIT_RE.fullmatch(commit):
        raise RuntimeError(f"repository HEAD is not a full lowercase Git SHA: {commit!r}")
    return commit


def _repo_tree(source_root: Path) -> str:
    tree = _git_output(source_root, "rev-parse", "HEAD^{tree}").strip()
    if not TREE_RE.fullmatch(tree):
        raise RuntimeError(f"repository HEAD tree is invalid: {tree!r}")
    return tree


def _release_input_path(relative_path: str) -> bool:
    return relative_path in RELEASE_INPUT_FILES or relative_path.startswith(
        RELEASE_INPUT_PREFIXES
    )


def _git_tree_entries(
    source_root: Path,
) -> tuple[bytes, list[tuple[str, str, str, str]]]:
    listing = _git_bytes(source_root, "ls-tree", "-r", "-z", "--full-tree", "HEAD")
    entries: list[tuple[str, str, str, str]] = []
    for record in listing.split(b"\0"):
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", 1)
            mode, object_type, object_id = header.decode("ascii").split(" ", 2)
            relative_path = raw_path.decode("utf-8", errors="strict")
        except (UnicodeError, ValueError) as exc:
            raise RuntimeError(f"repository tree contains an invalid entry: {exc}") from exc
        if (
            not relative_path
            or Path(relative_path).is_absolute()
            or "\\" in relative_path
            or "\0" in relative_path
            or ".." in Path(relative_path).parts
        ):
            raise RuntimeError(
                f"repository tree contains an unsafe path: {relative_path!r}"
            )
        entries.append((mode, object_type, object_id, relative_path))
    return listing, entries


def compute_build_input_digest(source_root: Path) -> tuple[str, str]:
    commit = _repo_head(source_root)
    tree = _repo_tree(source_root)
    listing, _ = _git_tree_entries(source_root)
    digest = hashlib.sha256()
    digest.update(BUILD_INPUT_DIGEST_DOMAIN)
    digest.update(commit.encode("ascii"))
    digest.update(b"\0")
    digest.update(tree.encode("ascii"))
    digest.update(b"\0")
    digest.update(listing)
    return digest.hexdigest(), tree


def _git_index_flag_errors(source_root: Path) -> list[str]:
    errors: list[str] = []
    listing = _git_output(source_root, "ls-files", "-v", "-z")
    for record in listing.split("\0"):
        if not record:
            continue
        if len(record) < 3 or record[1] != " ":
            errors.append(f"git ls-files returned an invalid index record: {record!r}")
            continue
        tag = record[0]
        relative_path = record[2:]
        if tag == "S":
            errors.append(f"skip-worktree is forbidden for release input: {relative_path}")
        if tag.islower():
            errors.append(
                f"assume-unchanged is forbidden for release input: {relative_path}"
            )
    return errors


def _git_configuration_errors(source_root: Path) -> list[str]:
    errors: list[str] = []
    config = _git_process(
        source_root,
        "config",
        "--show-origin",
        "--get-regexp",
        r"^(core\.(attributesfile|excludesfile|worktree)|extensions\.worktreeconfig)$",
        allowed_returncodes=(0, 1),
    )
    if config.returncode == 0 and config.stdout.strip():
        details = config.stdout.decode("utf-8", errors="replace").strip()
        errors.append(f"release Git configuration redirects source interpretation:\n{details}")

    replacements = _git_output(source_root, "replace", "-l").strip()
    if replacements:
        errors.append(f"Git replacement objects are forbidden for release builds:\n{replacements}")

    info_exclude_value = _git_output(
        source_root, "rev-parse", "--git-path", "info/exclude"
    ).strip()
    if info_exclude_value:
        info_exclude = Path(info_exclude_value)
        if not info_exclude.is_absolute():
            info_exclude = source_root / info_exclude
        try:
            exclude_lines = info_exclude.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            exclude_lines = []
        except (OSError, UnicodeError) as exc:
            errors.append(f"Git info/exclude cannot be attested: {exc}")
        else:
            active = [
                line
                for line in exclude_lines
                if line.strip() and not line.lstrip().startswith("#")
            ]
            if active:
                errors.append(
                    "Git info/exclude contains release-hidden paths: "
                    + ", ".join(active)
                )
    return errors


def _release_input_attribute_errors(
    source_root: Path,
    relative_paths: list[str],
) -> list[str]:
    if not relative_paths:
        return []
    result = _git_process(
        source_root,
        "check-attr",
        "-z",
        "--stdin",
        *DANGEROUS_GIT_ATTRIBUTES,
        input_bytes=("\0".join(relative_paths) + "\0").encode("utf-8"),
    )
    fields = result.stdout.split(b"\0")
    if fields and not fields[-1]:
        fields.pop()
    if len(fields) % 3:
        return ["git check-attr returned an invalid release attribute response"]
    errors: list[str] = []
    for index in range(0, len(fields), 3):
        relative_path = fields[index].decode("utf-8", errors="replace")
        attribute = fields[index + 1].decode("ascii", errors="replace")
        value = fields[index + 2].decode("utf-8", errors="replace")
        if value not in {"unspecified", "unset"}:
            errors.append(
                f"Git attribute {attribute}={value!r} is forbidden for release input: "
                f"{relative_path}"
            )
    return errors


def _git_blob_hash(path: Path, *, object_format: str) -> str:
    try:
        before = path.stat()
    except OSError as exc:
        raise RuntimeError(f"release input is unavailable: {path}: {exc}") from exc
    digest = hashlib.new(object_format)
    digest.update(f"blob {before.st_size}\0".encode("ascii"))
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        after = path.stat()
    except OSError as exc:
        raise RuntimeError(f"release input cannot be hashed: {path}: {exc}") from exc
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise RuntimeError(f"release input changed while it was hashed: {path}")
    return digest.hexdigest()


def validate_release_input_blobs(source_root: Path) -> list[str]:
    errors: list[str] = []
    try:
        _, entries = _git_tree_entries(source_root)
        object_format = _git_output(
            source_root, "rev-parse", "--show-object-format"
        ).strip()
    except (OSError, UnicodeError, RuntimeError) as exc:
        return [f"could not enumerate release input blobs: {exc}"]
    if object_format not in {"sha1", "sha256"}:
        return [f"unsupported Git object format: {object_format!r}"]

    release_paths = [
        relative_path
        for _, _, _, relative_path in entries
        if _release_input_path(relative_path)
    ]
    try:
        errors.extend(_release_input_attribute_errors(source_root, release_paths))
    except (OSError, UnicodeError, RuntimeError) as exc:
        errors.append(f"could not attest release Git attributes: {exc}")

    for mode, object_type, object_id, relative_path in entries:
        if not _release_input_path(relative_path):
            continue
        path = source_root.joinpath(*relative_path.split("/"))
        try:
            metadata = path.lstat()
        except OSError as exc:
            errors.append(f"release input is unavailable: {relative_path}: {exc}")
            continue
        if (
            object_type != "blob"
            or mode == "120000"
            or _is_link_or_reparse(metadata)
            or not stat_module.S_ISREG(metadata.st_mode)
        ):
            errors.append(
                f"release input must be a regular HEAD blob: {relative_path}"
            )
            continue
        try:
            actual_object_id = _git_blob_hash(path, object_format=object_format)
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        if actual_object_id != object_id:
            errors.append(
                f"release input differs byte-for-byte from HEAD blob: {relative_path}"
            )
    return errors


def validate_repository_identity(
    source_root: Path,
    *,
    expected_source_commit: str,
    require_clean: bool,
) -> list[str]:
    configured_git_environment = _git_environment_override_names()
    errors = (
        [
            "Git repository override variables are forbidden for release attestation: "
            + ", ".join(configured_git_environment)
        ]
        if configured_git_environment
        else []
    )
    try:
        repository_commit = _repo_head(source_root)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return [*errors, f"could not attest repository HEAD: {exc}"]
    try:
        top_level = Path(
            _git_output(source_root, "rev-parse", "--show-toplevel").strip()
        ).resolve()
        expected_top_level = source_root.resolve()
    except (OSError, UnicodeError, RuntimeError) as exc:
        errors.append(f"could not attest repository top-level: {exc}")
    else:
        if top_level != expected_top_level:
            errors.append(
                f"repository top-level differs from source root: {top_level} != "
                f"{expected_top_level}"
            )
    if not COMMIT_RE.fullmatch(expected_source_commit):
        errors.append("expected source commit must be a full lowercase Git SHA")
    elif repository_commit != expected_source_commit:
        errors.append(
            "repository HEAD differs from the expected source commit: "
            f"{repository_commit} != {expected_source_commit}"
        )
    if require_clean:
        try:
            errors.extend(_git_index_flag_errors(source_root))
            errors.extend(_git_configuration_errors(source_root))
        except (OSError, UnicodeError, RuntimeError) as exc:
            errors.append(f"could not attest release Git metadata: {exc}")
        try:
            dirty_status = _git_output(
                source_root,
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            )
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            errors.append(f"could not attest repository cleanliness: {exc}")
        else:
            if dirty_status.strip():
                errors.append(
                    "release source worktree must be clean; Git reported:\n"
                    + dirty_status.rstrip()
                )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "runtime_root",
        nargs="?",
        type=Path,
        default=DEFAULT_RUNTIME_ROOT,
    )
    parser.add_argument("--verify-tools", action="store_true")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Accept an explicitly marked development runtime.",
    )
    parser.add_argument("--expected-source-commit")
    parser.add_argument("--expected-app-version")
    parser.add_argument("--expected-build-input-digest")
    parser.add_argument(
        "--build-input-digest-only",
        action="store_true",
        help="Attest the release source and print its stable Git-tree input digest.",
    )
    parser.add_argument(
        "--require-clean-source",
        action="store_true",
        help="Require the verifier repository to match the expected commit and be clean.",
    )
    parser.add_argument("--tool-contract", type=Path, default=DEFAULT_TOOL_CONTRACT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    build_input_digest: str | None = None
    source_tree: str | None = None
    try:
        expected_source_commit = args.expected_source_commit or _repo_head()
        require_release_source = (
            args.require_clean_source
            or args.build_input_digest_only
            or args.expected_build_input_digest is not None
        )
        repository_errors = validate_repository_identity(
            REPO_ROOT,
            expected_source_commit=expected_source_commit,
            require_clean=require_release_source,
        )
        release_input_errors = (
            validate_release_input_blobs(REPO_ROOT)
            if require_release_source
            else []
        )
        if require_release_source:
            build_input_digest, source_tree = compute_build_input_digest(REPO_ROOT)
        digest_errors: list[str] = []
        if args.expected_build_input_digest is not None:
            if not BUILD_INPUT_DIGEST_RE.fullmatch(
                args.expected_build_input_digest
            ):
                digest_errors.append(
                    "expected build input digest must be a lowercase SHA-256"
                )
            elif build_input_digest != args.expected_build_input_digest:
                digest_errors.append(
                    "release build input digest differs from the expected digest"
                )
        source_errors = [
            *repository_errors,
            *release_input_errors,
            *digest_errors,
        ]
        if args.build_input_digest_only:
            errors = source_errors
        else:
            trusted_tool_contract, contract_error = _read_json_object(
                args.tool_contract.resolve(), "trusted desktop tool contract"
            )
            if contract_error:
                errors = [*source_errors, contract_error]
            else:
                errors = [
                    *source_errors,
                    *validate_runtime(
                        args.runtime_root,
                        verify_tools=args.verify_tools,
                        allow_dirty=args.allow_dirty,
                        expected_source_commit=expected_source_commit,
                        expected_app_version=args.expected_app_version,
                        trusted_tool_contract=trusted_tool_contract,
                        trusted_source_root=REPO_ROOT,
                    ),
                ]
    except Exception as exc:
        errors = [f"desktop runtime verification failed: {exc}"]

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not errors,
                    "runtimeRoot": str(args.runtime_root.resolve()),
                    "sourceTree": source_tree,
                    "buildInputDigest": build_input_digest,
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
    elif args.build_input_digest_only:
        assert build_input_digest is not None
        print(build_input_digest)
    else:
        print(f"[PASS] desktop runtime contract: {args.runtime_root.resolve()}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
