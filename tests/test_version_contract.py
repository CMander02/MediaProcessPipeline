from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.cli.main import app as cli_app  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.version import __version__  # noqa: E402


def test_python_api_and_project_share_version():
    with (ROOT / "pyproject.toml").open("rb") as file:
        project_version = tomllib.load(file)["project"]["version"]

    assert __version__ == "0.5.0"
    assert get_settings().api_version == project_version == __version__


def test_cli_reports_project_version():
    result = CliRunner().invoke(cli_app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == f"MPP {__version__}"
