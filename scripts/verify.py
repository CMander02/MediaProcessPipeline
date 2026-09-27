#!/usr/bin/env python3
"""Run the checks for the parts of the repo a change touches.

    python scripts/verify.py             areas changed in the working tree (vs HEAD)
    python scripts/verify.py --staged    exactly what is staged, in a clean temporary worktree
    python scripts/verify.py --all       every suite
    python scripts/verify.py --area web --area desktop

--staged is the one to use before committing when the working tree also holds other,
unrelated uncommitted changes: they are left out, so they can neither break nor hide
a failure. Exits 0 only when every selected check passed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = os.name == "nt"

# (label, directory, command) per area; commands run through the shell so npm/npx resolve on Windows.
CHECKS: dict[str, list[tuple[str, str, str]]] = {
    "web": [
        ("types", "web", "npx tsc -b"),
        ("lint", "web", "npx eslint ."),
        ("unit tests", "web", "npx vitest run"),
        ("build", "web", "npm run build"),
    ],
    "backend": [
        ("pytest", "backend", "{python} -m pytest ../tests -q -p no:cacheprovider"),
    ],
    "desktop": [
        ("node tests", "desktop", "npm test"),
    ],
}


def area_of(path: str) -> str | None:
    if path.startswith("web/"):
        return None if path.startswith("web/dist/") else "web"
    if path.startswith(("backend/", "tests/")) or path in {"pyproject.toml", "uv.lock"}:
        return "backend"
    if path.startswith("desktop/"):
        return "desktop"
    return None


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def changed_paths(staged: bool) -> list[str]:
    if staged:
        return [p for p in git("diff", "--cached", "--name-only").splitlines() if p]
    tracked = git("diff", "HEAD", "--name-only").splitlines()
    untracked = git("ls-files", "--others", "--exclude-standard").splitlines()
    return [p for p in tracked + untracked if p]


def venv_python() -> str:
    candidate = ROOT / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")
    return str(candidate) if candidate.exists() else sys.executable


def link_dir(link: Path, target: Path) -> None:
    """Directory junction on Windows (no admin rights needed), symlink elsewhere."""
    if not target.exists() or link.exists():
        return
    if WINDOWS:
        import _winapi  # noqa: PLC0415 - Windows only

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def unlink_dir(link: Path) -> None:
    """Remove the link itself, never what it points to."""
    if link.is_symlink():
        link.unlink()
        return
    try:
        attributes = getattr(os.lstat(link), "st_file_attributes", 0)
    except OSError:
        return
    if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        os.rmdir(link)  # a junction: rmdir drops the link and leaves the target alone


def run_checks(areas: list[str], base: Path) -> bool:
    results: list[tuple[str, str, bool, float]] = []
    python = venv_python()
    for area in areas:
        for label, directory, command in CHECKS[area]:
            command = command.format(python=f'"{python}"')
            print(f"\n==> {area}: {label}  ({directory}$ {command})", flush=True)
            started = time.monotonic()
            code = subprocess.run(command, cwd=base / directory, shell=True).returncode
            results.append((area, label, code == 0, time.monotonic() - started))
            if code != 0:
                break  # later checks in the same area add little once one fails
    print("\nSummary")
    for area, label, ok, seconds in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {area:<8} {label:<11} {seconds:6.1f}s")
    return all(ok for *_, ok, _ in results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--staged", action="store_true", help="check only the staged changes in a clean worktree")
    parser.add_argument("--all", action="store_true", help="run every suite")
    parser.add_argument("--area", action="append", choices=sorted(CHECKS), help="run these suites")
    args = parser.parse_args()

    paths = changed_paths(args.staged)
    if args.all:
        areas = list(CHECKS)
    elif args.area:
        areas = [area for area in CHECKS if area in args.area]
    else:
        areas = [area for area in CHECKS if any(area_of(path) == area for path in paths)]
    if args.staged and not paths:
        print("Nothing is staged.")
        return 1
    if not areas:
        print("No code areas changed (docs or config only): no checks to run.")
        return 0
    print(f"Areas: {', '.join(areas)}  ({len(paths)} changed files)")

    if not args.staged:
        return 0 if run_checks(areas, ROOT) else 1

    # Clean worktree at HEAD plus exactly the staged diff; dependencies are linked in.
    work = Path(tempfile.mkdtemp(prefix="mpp-verify-"))
    links = [work / "web" / "node_modules", work / "desktop" / "node_modules"]
    try:
        git("worktree", "add", "--detach", str(work), "HEAD")
        patch = subprocess.run(["git", "diff", "--cached", "--binary"], cwd=ROOT, check=True,
                               capture_output=True).stdout
        if patch:
            subprocess.run(["git", "apply", "--index", "--whitespace=nowarn"], cwd=work, input=patch, check=True)
        for link in links:
            link_dir(link, ROOT / link.relative_to(work))
        return 0 if run_checks(areas, work) else 1
    finally:
        for link in links:
            unlink_dir(link)
        subprocess.run(["git", "worktree", "remove", "--force", str(work)], cwd=ROOT, capture_output=True)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
