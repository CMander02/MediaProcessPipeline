"""Single source of truth for the MPP application version."""

from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_FILE = PROJECT_ROOT / "pyproject.toml"


def _load_version() -> str:
    if PYPROJECT_FILE.is_file():
        with PYPROJECT_FILE.open("rb") as file:
            project = tomllib.load(file).get("project", {})
        value = str(project.get("version", "")).strip()
        if value:
            return value

    try:
        return distribution_version("MediaProcessPipeline")
    except PackageNotFoundError:
        return "0+unknown"


__version__ = _load_version()
