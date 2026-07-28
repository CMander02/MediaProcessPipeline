#!/usr/bin/env python3
"""Validate the application version identity across release manifests."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

SEMVER_PATTERN = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-(?:(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+(?:[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


class VersionCheckError(RuntimeError):
    """The release version identity is missing or malformed."""


def _read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise VersionCheckError(f"{path}: cannot read TOML: {exc}") from exc


def _required_string(document: dict, keys: tuple[str, ...], path: Path) -> str:
    value: object = document
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            raise VersionCheckError(f"{path}: missing {'.'.join(keys)}")
        value = value[key]
    if not isinstance(value, str) or not value.strip():
        raise VersionCheckError(f"{path}: {'.'.join(keys)} must be a non-empty string")
    return value.strip()


def _lock_package_version(
    path: Path,
    *,
    package_name: str,
    require_local_project_source: bool = False,
) -> str:
    document = _read_toml(path)
    packages = document.get("package")
    if not isinstance(packages, list):
        raise VersionCheckError(f"{path}: missing package array")
    matches = [
        package
        for package in packages
        if isinstance(package, dict)
        and package.get("name") == package_name
        and (
            not require_local_project_source
            or (
                isinstance(package.get("source"), dict)
                and any(
                    package["source"].get(kind) == "."
                    for kind in ("virtual", "editable")
                )
            )
        )
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("version"), str):
        raise VersionCheckError(
            f"{path}: expected one {package_name} package with a string version"
        )
    return matches[0]["version"].strip()


def _android_identity(path: Path) -> tuple[str, int]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise VersionCheckError(f"{path}: cannot read Android manifest: {exc}") from exc
    names = re.findall(r'(?m)^[ \t]*versionName[ \t]*=[ \t]*"([^"\r\n]+)"', text)
    codes = re.findall(r"(?m)^[ \t]*versionCode[ \t]*=[ \t]*(\d+)[ \t]*$", text)
    if len(names) != 1:
        raise VersionCheckError(f"{path}: expected one versionName, found {len(names)}")
    if len(codes) != 1:
        raise VersionCheckError(f"{path}: expected one versionCode, found {len(codes)}")
    version_code = int(codes[0])
    if version_code < 1:
        raise VersionCheckError(f"{path}: versionCode must be a positive integer")
    return names[0].strip(), version_code


def _web_identity(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise VersionCheckError(f"{path}: cannot read generated Web version: {exc}") from exc
    matches = re.findall(
        r'(?m)^export const APP_VERSION[ \t]*=[ \t]*"([^"\r\n]+)"'
        r"(?:[ \t]+as const)?[ \t]*$",
        text,
    )
    if len(matches) != 1:
        raise VersionCheckError(
            f"{path}: expected one generated APP_VERSION, found {len(matches)}"
        )
    return matches[0].strip()


def collect_versions(root: Path) -> tuple[dict[str, str], int]:
    version_path = root / "VERSION"
    pyproject_path = root / "pyproject.toml"
    uv_lock_path = root / "uv.lock"
    cargo_path = root / "web" / "src-tauri" / "Cargo.toml"
    cargo_lock_path = root / "web" / "src-tauri" / "Cargo.lock"
    tauri_path = root / "web" / "src-tauri" / "tauri.conf.json"
    web_version_path = root / "web" / "src" / "generated" / "app-version.ts"
    android_path = root / "android" / "app" / "build.gradle.kts"

    try:
        canonical = version_path.read_text(encoding="utf-8-sig").strip()
        tauri = json.loads(tauri_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VersionCheckError(f"cannot read release identity: {exc}") from exc
    if not isinstance(tauri, dict) or not isinstance(tauri.get("version"), str):
        raise VersionCheckError(f"{tauri_path}: missing top-level string version")

    android_version, android_version_code = _android_identity(android_path)
    versions = {
        "VERSION": canonical,
        "pyproject.toml": _required_string(
            _read_toml(pyproject_path), ("project", "version"), pyproject_path
        ),
        "uv.lock": _lock_package_version(
            uv_lock_path,
            package_name="mediaprocesspipeline",
            require_local_project_source=True,
        ),
        "web/src-tauri/Cargo.toml": _required_string(
            _read_toml(cargo_path), ("package", "version"), cargo_path
        ),
        "web/src-tauri/Cargo.lock": _lock_package_version(
            cargo_lock_path,
            package_name="mpp-desktop",
        ),
        "web/src-tauri/tauri.conf.json": tauri["version"].strip(),
        "web/src/generated/app-version.ts": _web_identity(web_version_path),
        "android/app/build.gradle.kts": android_version,
    }
    return versions, android_version_code


def check_version(root: Path, tag: str | None = None) -> tuple[str, int]:
    versions, android_version_code = collect_versions(root)
    errors = [
        f"{source}: invalid SemVer {version!r}"
        for source, version in versions.items()
        if not SEMVER_PATTERN.fullmatch(version)
    ]
    canonical = versions["VERSION"]
    errors.extend(
        f"{source}: {version!r} does not match VERSION {canonical!r}"
        for source, version in versions.items()
        if version != canonical
    )
    if tag is not None:
        expected = f"v{canonical}"
        if tag != expected:
            errors.append(f"tag: {tag!r} does not match {expected!r}")
    if errors:
        raise VersionCheckError("\n".join(errors))
    return canonical, android_version_code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="optionally require an exact vVERSION release tag")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        version, android_version_code = check_version(args.root.resolve(), args.tag)
    except VersionCheckError as exc:
        print(f"version check failed:\n{exc}", file=sys.stderr)
        return 1
    tag_message = f", tag={args.tag}" if args.tag else ""
    print(
        f"version identity OK: {version}, Android versionCode={android_version_code}"
        f"{tag_message}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
