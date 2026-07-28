"""Read-only, bounded runtime-settings validation for the desktop bootstrap.

The command contract is intentionally tiny and credential-safe. It emits
exactly one fixed token and never includes configuration values, paths, or
exception messages.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from enum import Enum
from pathlib import Path
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.settings import (  # noqa: E402
    RuntimeSettings,
    _normalize_custom_profile_state,
    _normalize_settings_document_state,
)

SETTINGS_PREFLIGHT_SCHEMA_VERSION = 1
MAX_SETTINGS_BYTES = 1024 * 1024
SETTINGS_PREFLIGHT_OK_TOKEN = "MPP_SETTINGS_PREFLIGHT_V1_OK"
SETTINGS_PREFLIGHT_INVALID_TOKEN = "MPP_SETTINGS_PREFLIGHT_V1_INVALID"
SETTINGS_PREFLIGHT_ERROR_TOKEN = "MPP_SETTINGS_PREFLIGHT_V1_ERROR"


class SettingsPreflightStatus(Enum):
    VALID = ("valid", 0, SETTINGS_PREFLIGHT_OK_TOKEN)
    MISSING = ("missing", 0, SETTINGS_PREFLIGHT_OK_TOKEN)
    INVALID = ("invalid", 2, SETTINGS_PREFLIGHT_INVALID_TOKEN)
    TOO_LARGE = ("too-large", 2, SETTINGS_PREFLIGHT_INVALID_TOKEN)
    UNAVAILABLE = ("unavailable", 2, SETTINGS_PREFLIGHT_INVALID_TOKEN)
    INTERNAL_ERROR = ("internal-error", 70, SETTINGS_PREFLIGHT_ERROR_TOKEN)
    USAGE_ERROR = ("usage-error", 64, SETTINGS_PREFLIGHT_ERROR_TOKEN)

    def __init__(self, label: str, exit_code: int, token: str) -> None:
        self.label = label
        self.exit_code = exit_code
        self.token = token


def _is_link_or_reparse(path: Path, metadata: os.stat_result) -> bool:
    if path.is_symlink():
        return True
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(file_attributes & 0x0400)


def _read_bounded_settings(path: Path) -> tuple[SettingsPreflightStatus | None, bytes]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return SettingsPreflightStatus.MISSING, b""
    except OSError:
        return SettingsPreflightStatus.UNAVAILABLE, b""

    if (
        _is_link_or_reparse(path, metadata)
        or not stat.S_ISREG(metadata.st_mode)
    ):
        return SettingsPreflightStatus.UNAVAILABLE, b""
    if metadata.st_size > MAX_SETTINGS_BYTES:
        return SettingsPreflightStatus.TOO_LARGE, b""

    try:
        with path.open("rb") as file:
            content = file.read(MAX_SETTINGS_BYTES + 1)
    except OSError:
        return SettingsPreflightStatus.UNAVAILABLE, b""
    if len(content) > MAX_SETTINGS_BYTES:
        return SettingsPreflightStatus.TOO_LARGE, b""
    return None, content


def preflight_runtime_settings(path: Path) -> SettingsPreflightStatus:
    """Validate one settings file without changing process or filesystem state."""

    if not isinstance(path, Path) or not path.is_absolute():
        return SettingsPreflightStatus.UNAVAILABLE

    read_status, content = _read_bounded_settings(path)
    if read_status is SettingsPreflightStatus.MISSING:
        try:
            RuntimeSettings()
        except Exception:
            return SettingsPreflightStatus.INTERNAL_ERROR
        return SettingsPreflightStatus.MISSING
    if read_status is not None:
        return read_status

    try:
        document = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return SettingsPreflightStatus.INVALID
    if not isinstance(document, dict):
        return SettingsPreflightStatus.INVALID

    try:
        _normalize_custom_profile_state(document, prefer_profiles=True)
        _normalize_settings_document_state(document)
        RuntimeSettings(**document)
    except Exception:
        return SettingsPreflightStatus.INVALID
    return SettingsPreflightStatus.VALID


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        status = SettingsPreflightStatus.USAGE_ERROR
    else:
        try:
            path = Path(arguments[0])
            status = preflight_runtime_settings(path)
        except BaseException:
            status = SettingsPreflightStatus.INTERNAL_ERROR
    sys.stdout.write(f"{status.token}\n")
    return status.exit_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MAX_SETTINGS_BYTES",
    "SETTINGS_PREFLIGHT_ERROR_TOKEN",
    "SETTINGS_PREFLIGHT_INVALID_TOKEN",
    "SETTINGS_PREFLIGHT_OK_TOKEN",
    "SETTINGS_PREFLIGHT_SCHEMA_VERSION",
    "SettingsPreflightStatus",
    "main",
    "preflight_runtime_settings",
]
