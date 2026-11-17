import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "pyproject.toml",
    "desktop/package.json",
    "desktop/package-lock.json",
    "web/scripts/check-release-version.mjs",
    "web/android/app/build.gradle",
    "web/release.config.json",
    "web/capacitor.config.ts",
    "web/vite.config.ts",
    "scripts/bump_version.py",
)


@pytest.fixture
def release_copy(tmp_path):
    for name in FILES:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "mediaprocesspipeline"\nversion = "0.6.6"\nsource = { virtual = "." }\n',
        encoding="utf-8",
    )
    return tmp_path


def run_version(root, *args, tag=None):
    env = os.environ.copy()
    env.pop("RELEASE_TAG", None)
    if tag is not None:
        env["RELEASE_TAG"] = tag
    return subprocess.run(args, cwd=root, capture_output=True, text=True, encoding="utf-8", env=env)


def test_checker_detects_desktop_mismatch(release_copy):
    checker = ("node", "web/scripts/check-release-version.mjs")
    assert run_version(release_copy, *checker).returncode == 0
    package = release_copy / "desktop/package.json"
    metadata = json.loads(package.read_bytes())
    metadata["version"] = "99.0.0"
    package.write_text(json.dumps(metadata), encoding="utf-8")
    result = run_version(release_copy, *checker)
    assert result.returncode != 0
    assert "desktop/package.json" in result.stderr


def test_checker_requires_exact_release_tag(release_copy):
    checker = ("node", "web/scripts/check-release-version.mjs")
    project = tomllib.loads((release_copy / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert run_version(release_copy, *checker, tag="v" + project["version"]).returncode == 0
    result = run_version(release_copy, *checker, tag=project["version"])
    assert result.returncode != 0
    assert "RELEASE_TAG" in result.stderr


def test_bump_updates_version_and_lockfiles_and_checks_without_writing(release_copy):
    bump = (sys.executable, "scripts/bump_version.py")
    target = "7.8.9"
    result = run_version(release_copy, *bump, target, tag="v" + target)
    assert result.returncode == 0, result.stderr
    lock = json.loads((release_copy / "desktop/package-lock.json").read_bytes())
    assert lock["version"] == lock["packages"][""]["version"] == target
    uv_lock = tomllib.loads((release_copy / "uv.lock").read_text(encoding="utf-8"))
    assert next(pkg["version"] for pkg in uv_lock["package"] if pkg["name"] == "mediaprocesspipeline") == target
    before = {name: (release_copy / name).read_bytes() for name in (*FILES, "uv.lock")}
    assert run_version(release_copy, *bump, "--check", tag="v" + target).returncode == 0
    assert {name: (release_copy / name).read_bytes() for name in before} == before


def test_bump_restores_files_after_failed_validation(release_copy):
    bump = (sys.executable, "scripts/bump_version.py")
    before = {name: (release_copy / name).read_bytes() for name in (*FILES, "uv.lock")}
    failure = run_version(release_copy, *bump, "7.8.9", tag="v0.0.0")
    assert failure.returncode != 0
    assert "RELEASE_TAG" in failure.stderr
    assert run_version(release_copy, *bump, "01.2.3").returncode != 0
    assert {name: (release_copy / name).read_bytes() for name in before} == before
