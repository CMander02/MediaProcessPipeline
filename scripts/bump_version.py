#!/usr/bin/env python3
import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2}")
PROJECT_SECTION = re.compile(r"^\[project\][ \t]*\r?\n(.*?)(?=^\[|\Z)", re.M | re.S)


def version_changes(version: str) -> dict[Path, tuple[bytes, bytes]]:
    pyproject = ROOT / "pyproject.toml"
    desktop = ROOT / "desktop/package.json"
    sources = {path: path.read_bytes() for path in (pyproject, desktop)}
    project_text = sources[pyproject].decode("utf-8")
    project_name = tomllib.loads(project_text)["project"]["name"]
    section = PROJECT_SECTION.search(project_text)
    if section is None:
        raise ValueError("无法读取 pyproject.toml 的 [project] 段")
    body = replace_version(section[1], r'(?m)^(version[ \t]*=[ \t]*")[^"\r\n]+(")', version)
    updated = {
        pyproject: project_text[:section.start(1)] + body + project_text[section.end(1):],
        desktop: replace_version(
            sources[desktop].decode("utf-8"),
            r'(?m)^([ \t]*"version"[ \t]*:[ \t]*")[^"\r\n]+(")',
            version,
        ),
    }

    desktop_lock = desktop.with_name("package-lock.json")
    if desktop_lock.is_file():
        sources[desktop_lock] = desktop_lock.read_bytes()
        metadata = json.loads(sources[desktop_lock])
        metadata["version"] = version
        metadata["packages"][""]["version"] = version
        updated[desktop_lock] = json.dumps(metadata, ensure_ascii=False, indent=2) + "\n"

    uv_lock = ROOT / "uv.lock"
    if uv_lock.is_file():
        sources[uv_lock] = uv_lock.read_bytes()
        name = re.escape(re.sub(r"[-_.]+", "-", project_name).lower())
        updated[uv_lock] = replace_version(
            sources[uv_lock].decode("utf-8"),
            rf'(?m)^(name = "{name}"\r?\nversion = ")[^"\r\n]+(")(?=\r?\nsource = \{{ (?:virtual|editable) = "\." \}})',
            version,
        )
    return {path: (original, updated[path].encode("utf-8")) for path, original in sources.items()}


def replace_version(text: str, pattern: str, version: str) -> str:
    updated, count = re.subn(pattern, lambda match: match[1] + version + match[2], text, count=1)
    if count != 1:
        raise ValueError("无法定位需要更新的版本字段")
    return updated


def replace_file(path: Path, content: bytes) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    stream = tempfile.NamedTemporaryFile(dir=path.parent, delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            stream.write(content)
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def check_version() -> None:
    subprocess.run(["node", "web/scripts/check-release-version.mjs"], cwd=ROOT, check=True)


def bump_version(version: str) -> None:
    changes = version_changes(version)
    changed = []
    try:
        for path, (original, updated) in changes.items():
            if original != updated:
                replace_file(path, updated)
                changed.append(path)
        check_version()
    except BaseException:
        for path in reversed(changed):
            replace_file(path, changes[path][0])
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="更新或校验 MPP 发布版本")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("version", nargs="?", help="目标版本，格式为 MAJOR.MINOR.PATCH")
    mode.add_argument("--check", action="store_true", help="只校验版本")
    args = parser.parse_args()
    if args.version is not None and not VERSION.fullmatch(args.version):
        parser.error("版本必须为 MAJOR.MINOR.PATCH，各段不能有前导零")
    try:
        if args.check:
            check_version()
        else:
            bump_version(args.version)
    except subprocess.CalledProcessError as error:
        return error.returncode
    except (OSError, ValueError, KeyError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
