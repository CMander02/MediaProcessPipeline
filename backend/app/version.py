"""Application version identity shared by the backend and release tooling.

The checked-in ``VERSION`` file is the canonical source.  Installed desktop
builds place that file beside the bundled ``backend`` directory, so the same
lookup works from a source checkout and from Tauri's ``resources/runtime``
directory.  ``MPP_APP_VERSION`` lets a signed launcher provide the version
directly when the backend is started from another layout.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path

_SEMVER_PATTERN = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-(?:(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+(?:[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_PEP440_PRERELEASE_PATTERN = re.compile(
    r"^(?P<base>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))"
    r"(?P<kind>a|b|rc)(?P<number>\d+)"
    r"(?P<local>\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_VERSION_ENV = "MPP_APP_VERSION"
_DISTRIBUTION_NAME = "MediaProcessPipeline"


def _validated_version(raw_version: str, source: str) -> str:
    value = raw_version.strip()
    if not _SEMVER_PATTERN.fullmatch(value):
        raise RuntimeError(f"Invalid application version in {source}: {value!r}")
    return value


def _metadata_version_to_semver(raw_version: str) -> str:
    """Restore common PEP 440 prerelease normalization to the release SemVer."""

    value = raw_version.strip()
    if _SEMVER_PATTERN.fullmatch(value):
        return value
    match = _PEP440_PRERELEASE_PATTERN.fullmatch(value)
    if match is None:
        raise RuntimeError(
            f"Invalid application version in package metadata {_DISTRIBUTION_NAME}: "
            f"{value!r}"
        )
    labels = {"a": "alpha", "b": "beta", "rc": "rc"}
    candidate = (
        f"{match.group('base')}-{labels[match.group('kind')]}.{match.group('number')}"
        f"{match.group('local') or ''}"
    )
    return _validated_version(candidate, f"package metadata {_DISTRIBUTION_NAME}")


def _version_file_for(module_path: Path) -> Path:
    """Return the canonical VERSION path for source and bundled layouts."""

    resolved = module_path.resolve()
    try:
        # source:  <root>/backend/app/version.py -> <root>/VERSION
        # bundled: <resource>/runtime/backend/app/version.py -> <resource>/runtime/VERSION
        return resolved.parents[2] / "VERSION"
    except IndexError as exc:
        raise RuntimeError(f"Cannot resolve VERSION relative to {resolved}") from exc


def get_app_version(
    *,
    environ: Mapping[str, str] | None = None,
    module_path: Path | None = None,
) -> str:
    """Resolve the application SemVer from launcher override or installed resources."""

    environment = os.environ if environ is None else environ
    overridden = environment.get(_VERSION_ENV, "").strip()
    if overridden:
        return _validated_version(overridden, _VERSION_ENV)

    version_file = _version_file_for(module_path or Path(__file__))
    if version_file.is_file():
        return _validated_version(
            version_file.read_text(encoding="utf-8-sig"),
            str(version_file),
        )

    try:
        installed_version = distribution_version(_DISTRIBUTION_NAME)
    except PackageNotFoundError as exc:
        raise RuntimeError(
            f"Application version unavailable: expected {version_file} or {_VERSION_ENV}"
        ) from exc
    return _metadata_version_to_semver(installed_version)


APP_VERSION = get_app_version()

__all__ = ["APP_VERSION", "get_app_version"]
