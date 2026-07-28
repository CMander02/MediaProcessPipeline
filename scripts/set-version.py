#!/usr/bin/env python3
"""Synchronize the application version across release manifests."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from uuid import uuid4

SEMVER_PATTERN = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-(?:(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+(?:[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


class VersionUpdateError(RuntimeError):
    """A release manifest could not be updated safely."""


def _semver(value: str) -> str:
    value = value.strip()
    if not SEMVER_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a valid SemVer")
    return value


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("android versionCode must be a positive integer")
    return parsed


def _replace_section_version(text: str, section: str, version: str, path: Path) -> str:
    header = re.search(rf"(?m)^\[{re.escape(section)}\][ \t]*$", text)
    if not header:
        raise VersionUpdateError(f"{path}: missing [{section}] section")
    next_header = re.search(r"(?m)^\[", text[header.end() :])
    section_end = header.end() + next_header.start() if next_header else len(text)
    body = text[header.end() : section_end]
    pattern = re.compile(r'(?m)^(version[ \t]*=[ \t]*")[^"\r\n]+(")[ \t]*$')
    updated_body, count = pattern.subn(rf"\g<1>{version}\g<2>", body)
    if count != 1:
        raise VersionUpdateError(
            f"{path}: expected one version field in [{section}], found {count}"
        )
    return text[: header.end()] + updated_body + text[section_end:]


def _replace_cargo_lock_version(text: str, version: str, path: Path) -> str:
    block_pattern = re.compile(
        r"(?ms)^\[\[package\]\][ \t]*\r?\n.*?(?=^\[\[package\]\][ \t]*\r?\n|\Z)"
    )
    matches = [
        match
        for match in block_pattern.finditer(text)
        if re.search(r'(?m)^name[ \t]*=[ \t]*"mpp-desktop"[ \t]*$', match.group(0))
    ]
    if len(matches) != 1:
        raise VersionUpdateError(
            f"{path}: expected one mpp-desktop package block, found {len(matches)}"
        )
    match = matches[0]
    block, count = re.subn(
        r'(?m)^(version[ \t]*=[ \t]*")[^"\r\n]+(")[ \t]*$',
        rf"\g<1>{version}\g<2>",
        match.group(0),
    )
    if count != 1:
        raise VersionUpdateError(
            f"{path}: expected one mpp-desktop version field, found {count}"
        )
    return text[: match.start()] + block + text[match.end() :]


def _replace_uv_lock_version(text: str, version: str, path: Path) -> str:
    block_pattern = re.compile(
        r"(?ms)^\[\[package\]\][ \t]*\r?\n.*?(?=^\[\[package\]\][ \t]*\r?\n|\Z)"
    )
    matches = [
        match
        for match in block_pattern.finditer(text)
        if re.search(
            r'(?m)^name[ \t]*=[ \t]*"mediaprocesspipeline"[ \t]*$',
            match.group(0),
        )
        and re.search(
            r'(?m)^source[ \t]*=[ \t]*\{[ \t]*(?:virtual|editable)'
            r'[ \t]*=[ \t]*"\."[ \t]*\}[ \t]*$',
            match.group(0),
        )
    ]
    if len(matches) != 1:
        raise VersionUpdateError(
            f"{path}: expected one local virtual/editable mediaprocesspipeline package block, "
            f"found {len(matches)}"
        )
    match = matches[0]
    block, count = re.subn(
        r'(?m)^(version[ \t]*=[ \t]*")[^"\r\n]+(")[ \t]*$',
        rf"\g<1>{version}\g<2>",
        match.group(0),
    )
    if count != 1:
        raise VersionUpdateError(
            f"{path}: expected one mediaprocesspipeline version field, found {count}"
        )
    return text[: match.start()] + block + text[match.end() :]


def _replace_json_version(text: str, version: str, path: Path) -> str:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VersionUpdateError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("version"), str):
        raise VersionUpdateError(f"{path}: missing top-level string version")
    pattern = re.compile(r'(?m)^([ \t]*"version"[ \t]*:[ \t]*")[^"\r\n]+(")')
    updated, count = pattern.subn(rf"\g<1>{version}\g<2>", text)
    if count != 1:
        raise VersionUpdateError(
            f"{path}: expected one top-level version field, found {count}"
        )
    return updated


def _replace_web_version(text: str, version: str, path: Path) -> str:
    pattern = re.compile(
        r'(?m)^(export const APP_VERSION[ \t]*=[ \t]*")[^"\r\n]+("'
        r"(?:[ \t]+as const)?[ \t]*)$"
    )
    updated, count = pattern.subn(rf"\g<1>{version}\g<2>", text)
    if count != 1:
        raise VersionUpdateError(
            f"{path}: expected one generated APP_VERSION, found {count}"
        )
    return updated


def _replace_android_value(
    text: str,
    *,
    field: str,
    value: str,
    quoted: bool,
    path: Path,
) -> str:
    if quoted:
        pattern = re.compile(
            rf'(?m)^([ \t]*{re.escape(field)}[ \t]*=[ \t]*")[^"\r\n]+(")'
        )
        replacement = rf"\g<1>{value}\g<2>"
    else:
        pattern = re.compile(
            rf"(?m)^([ \t]*{re.escape(field)}[ \t]*=[ \t]*)\d+([ \t]*)$"
        )
        replacement = rf"\g<1>{value}\g<2>"
    updated, count = pattern.subn(replacement, text)
    if count != 1:
        raise VersionUpdateError(f"{path}: expected one {field} field, found {count}")
    return updated


def _transactional_write(updates: dict[Path, str]) -> None:
    token = uuid4().hex
    pending: dict[Path, Path] = {}
    backups: dict[Path, Path | None] = {}
    try:
        for path, text in updates.items():
            temporary = path.with_name(f".{path.name}.{token}.update.tmp")
            temporary.write_text(text, encoding="utf-8", newline="")
            pending[path] = temporary
            if path.is_file():
                backup = path.with_name(f".{path.name}.{token}.rollback.tmp")
                backup.write_bytes(path.read_bytes())
                backups[path] = backup
            else:
                backups[path] = None

        replaced: list[Path] = []
        try:
            for path in updates:
                os.replace(pending[path], path)
                replaced.append(path)
        except OSError as exc:
            rollback_errors: list[str] = []
            for path in reversed(replaced):
                backup = backups[path]
                try:
                    if backup is None:
                        path.unlink(missing_ok=True)
                    else:
                        os.replace(backup, path)
                except OSError as rollback_exc:
                    rollback_errors.append(f"{path}: {rollback_exc}")
            suffix = (
                "; rollback errors: " + "; ".join(rollback_errors)
                if rollback_errors
                else "; all replaced files were restored"
            )
            raise VersionUpdateError(f"version update transaction failed: {exc}{suffix}") from exc
    finally:
        for temporary in [*pending.values(), *(path for path in backups.values() if path)]:
            temporary.unlink(missing_ok=True)


def synchronize_version(root: Path, version: str, android_version_code: int | None) -> None:
    paths = {
        "version": root / "VERSION",
        "pyproject": root / "pyproject.toml",
        "uv_lock": root / "uv.lock",
        "cargo": root / "web" / "src-tauri" / "Cargo.toml",
        "cargo_lock": root / "web" / "src-tauri" / "Cargo.lock",
        "tauri": root / "web" / "src-tauri" / "tauri.conf.json",
        "web_version": root / "web" / "src" / "generated" / "app-version.ts",
        "android": root / "android" / "app" / "build.gradle.kts",
    }
    missing = [str(path) for key, path in paths.items() if key != "version" and not path.is_file()]
    if missing:
        raise VersionUpdateError("missing release manifest(s): " + ", ".join(missing))

    original = {
        key: path.read_text(encoding="utf-8-sig")
        for key, path in paths.items()
        if key != "version"
    }
    updated = {
        paths["version"]: f"{version}\n",
        paths["pyproject"]: _replace_section_version(
            original["pyproject"], "project", version, paths["pyproject"]
        ),
        paths["uv_lock"]: _replace_uv_lock_version(
            original["uv_lock"], version, paths["uv_lock"]
        ),
        paths["cargo"]: _replace_section_version(
            original["cargo"], "package", version, paths["cargo"]
        ),
        paths["cargo_lock"]: _replace_cargo_lock_version(
            original["cargo_lock"], version, paths["cargo_lock"]
        ),
        paths["tauri"]: _replace_json_version(
            original["tauri"], version, paths["tauri"]
        ),
        paths["web_version"]: _replace_web_version(
            original["web_version"], version, paths["web_version"]
        ),
    }
    android = _replace_android_value(
        original["android"],
        field="versionName",
        value=version,
        quoted=True,
        path=paths["android"],
    )
    if android_version_code is not None:
        android = _replace_android_value(
            android,
            field="versionCode",
            value=str(android_version_code),
            quoted=False,
            path=paths["android"],
        )
    updated[paths["android"]] = android

    _transactional_write(updated)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", type=_semver, help="target SemVer, without a v prefix")
    parser.add_argument(
        "--android-version-code",
        type=_positive_int,
        help="explicitly set Android versionCode; omitted keeps the existing value",
    )
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
        synchronize_version(args.root.resolve(), args.version, args.android_version_code)
    except (OSError, VersionUpdateError) as exc:
        print(f"version update failed: {exc}", file=sys.stderr)
        return 1
    suffix = (
        f"; Android versionCode={args.android_version_code}"
        if args.android_version_code is not None
        else "; Android versionCode unchanged"
    )
    print(f"synchronized application version {args.version}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
