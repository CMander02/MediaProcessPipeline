#!/usr/bin/env python3
"""Validate the dependency contract used by production releases.

The checker intentionally uses only the Python standard library so it can run
before the project environment is created.  Static checks inspect both
``pyproject.toml`` and ``uv.lock``; the default CLI also asks uv to prove that
the lock is current and that every declared extra can be exported from it.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse

EXPECTED_PYTHON_MINORS = ("3.11", "3.12")
CORE_DEPENDENCIES = (
    "fastapi",
    "httpx",
    "numpy",
    "openai",
    "pillow",
    "pydantic",
    "transformers",
    "uvicorn",
)
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
EXTRA_MARKER_RE = re.compile(r"""extra\s*==\s*(['"])(?P<extra>[^'"]+)\1""")
REQUIREMENT_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[(?P<extras>[^\]]+)\])?"
    r"\s*(?P<specifier>[^;]*?)"
    r"(?:\s*;\s*(?P<marker>.+))?\s*$"
)
SPECIFIER_RE = re.compile(
    r"^(?P<operator>===|==|!=|~=|>=|<=|>|<)"
    r"(?P<version>\d+(?:\.\d+){0,2})(?P<wildcard>\.\*)?$"
)


def canonicalize_name(value: str) -> str:
    """Return the PEP 503 canonical form used to compare package names."""

    return re.sub(r"[-_.]+", "-", value).lower()


@dataclass(frozen=True)
class Requirement:
    name: str
    extras: tuple[str, ...] = ()
    specifier: str = ""
    marker: str = ""

    @property
    def key(self) -> tuple[str, tuple[str, ...]]:
        return self.name, self.extras


@dataclass
class GateReport:
    project_root: Path
    checks: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_check(
        self,
        check_id: str,
        status: str,
        message: str,
        **details: Any,
    ) -> None:
        item: dict[str, Any] = {
            "id": check_id,
            "status": status,
            "message": message,
        }
        if details:
            item["details"] = details
        self.checks.append(item)

    def fail(self, code: str, message: str, **details: Any) -> None:
        item: dict[str, Any] = {"code": code, "message": message}
        if details:
            item["details"] = details
        self.errors.append(item)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "project_root": str(self.project_root),
            "summary": self.summary,
            "checks": self.checks,
            "errors": self.errors,
        }


def parse_requirement(value: str) -> Requirement:
    match = REQUIREMENT_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"unsupported requirement syntax: {value!r}")
    extras = tuple(
        sorted(
            canonicalize_name(extra.strip())
            for extra in (match.group("extras") or "").split(",")
            if extra.strip()
        )
    )
    return Requirement(
        name=canonicalize_name(match.group("name")),
        extras=extras,
        specifier=re.sub(r"\s+", "", match.group("specifier") or ""),
        marker=re.sub(r"\s+", " ", match.group("marker") or "").strip(),
    )


def _parse_requirements(
    values: Iterable[Any],
    *,
    group: str,
    report: GateReport,
) -> list[Requirement]:
    requirements: list[Requirement] = []
    for value in values:
        if not isinstance(value, str):
            report.fail(
                "invalid_requirement",
                f"{group} contains a non-string dependency declaration",
                group=group,
                value=repr(value),
            )
            continue
        try:
            requirements.append(parse_requirement(value))
        except ValueError as exc:
            report.fail(
                "invalid_requirement",
                str(exc),
                group=group,
                value=value,
            )
    return requirements


def _normalized_python_specifier(value: str) -> tuple[str, ...]:
    return tuple(sorted(part.strip().replace(" ", "") for part in value.split(",") if part.strip()))


def _version_tuple(value: str) -> tuple[int, int, int]:
    parts = [int(part) for part in value.split(".")]
    return tuple((parts + [0, 0, 0])[:3])  # type: ignore[return-value]


def _specifier_accepts_minor(specifier: str, minor: str) -> bool:
    candidate = _version_tuple(f"{minor}.999")
    for raw_clause in _normalized_python_specifier(specifier):
        match = SPECIFIER_RE.fullmatch(raw_clause)
        if match is None:
            raise ValueError(f"unsupported Python specifier clause: {raw_clause!r}")
        operator = match.group("operator")
        expected = _version_tuple(match.group("version"))
        wildcard = match.group("wildcard")
        if wildcard:
            equal = candidate[:2] == expected[:2]
            if operator == "==" and not equal:
                return False
            if operator == "!=" and equal:
                return False
            if operator not in {"==", "!="}:
                raise ValueError(f"wildcard is not supported with {operator}")
            continue
        if operator in {"==", "==="} and candidate != expected:
            return False
        if operator == "!=" and candidate == expected:
            return False
        if operator == ">=" and candidate < expected:
            return False
        if operator == "<=" and candidate > expected:
            return False
        if operator == ">" and candidate <= expected:
            return False
        if operator == "<" and candidate >= expected:
            return False
        if operator == "~=":
            upper = (expected[0] + 1, 0, 0)
            if len(match.group("version").split(".")) >= 2:
                upper = (expected[0], expected[1] + 1, 0)
            if candidate < expected or candidate >= upper:
                return False
    return True


def _validate_python_contract(
    project_specifier: Any,
    lock_specifier: Any,
    report: GateReport,
) -> tuple[str, ...]:
    if not isinstance(project_specifier, str) or not project_specifier.strip():
        report.fail(
            "python_range_missing",
            "project.requires-python must declare the supported Python range",
        )
        return ()
    if not isinstance(lock_specifier, str) or not lock_specifier.strip():
        report.fail(
            "lock_python_range_missing",
            "uv.lock must declare requires-python",
        )
        return ()

    if _normalized_python_specifier(project_specifier) != _normalized_python_specifier(
        lock_specifier
    ):
        report.fail(
            "python_range_lock_drift",
            "pyproject.toml and uv.lock declare different Python ranges",
            pyproject=project_specifier,
            lock=lock_specifier,
        )

    try:
        accepted = tuple(
            f"3.{minor}"
            for minor in range(8, 16)
            if _specifier_accepts_minor(project_specifier, f"3.{minor}")
        )
    except ValueError as exc:
        report.fail("python_range_invalid", str(exc), value=project_specifier)
        return ()

    if accepted != EXPECTED_PYTHON_MINORS:
        report.fail(
            "python_range_contract_mismatch",
            "Release builds must support exactly Python 3.11 and 3.12",
            expected=list(EXPECTED_PYTHON_MINORS),
            actual=list(accepted),
        )
    report.add_check(
        "python-range",
        "pass" if accepted == EXPECTED_PYTHON_MINORS else "fail",
        f"Python range resolves to {', '.join(accepted) or 'no supported minor'}",
        pyproject=project_specifier,
        lock=lock_specifier,
    )
    return accepted


def _lock_requirement(item: dict[str, Any]) -> Requirement:
    extras_value = item.get("extras", item.get("extra", ()))
    if isinstance(extras_value, str):
        extras_value = (extras_value,)
    return Requirement(
        name=canonicalize_name(str(item.get("name", ""))),
        extras=tuple(sorted(canonicalize_name(str(extra)) for extra in extras_value or ())),
        specifier=re.sub(r"\s+", "", str(item.get("specifier", ""))),
        marker=re.sub(r"\s+", " ", str(item.get("marker", ""))).strip(),
    )


def _extra_from_marker(marker: str) -> str | None:
    match = EXTRA_MARKER_RE.search(marker)
    return match.group("extra") if match else None


def _format_keys(keys: Iterable[tuple[str, tuple[str, ...]]]) -> list[str]:
    values = []
    for name, extras in sorted(keys):
        suffix = f"[{','.join(extras)}]" if extras else ""
        values.append(f"{name}{suffix}")
    return values


def _compare_direct_dependencies(
    *,
    group: str,
    expected: Sequence[Requirement],
    actual_items: Any,
    report: GateReport,
) -> None:
    actual = [
        _lock_requirement(item)
        for item in actual_items
        if isinstance(item, dict) and item.get("name")
    ] if isinstance(actual_items, list) else []
    expected_keys = {requirement.key for requirement in expected}
    actual_keys = {requirement.key for requirement in actual}
    if expected_keys != actual_keys:
        report.fail(
            "lock_direct_dependencies_drift",
            f"uv.lock direct dependencies differ for {group}",
            group=group,
            missing=_format_keys(expected_keys - actual_keys),
            unexpected=_format_keys(actual_keys - expected_keys),
        )


def _validate_metadata_requirements(
    *,
    base: Sequence[Requirement],
    extras: dict[str, list[Requirement]],
    metadata_items: Any,
    report: GateReport,
) -> None:
    locked = [
        _lock_requirement(item)
        for item in metadata_items
        if isinstance(item, dict) and item.get("name")
    ] if isinstance(metadata_items, list) else []

    for requirement in base:
        matches = [
            item
            for item in locked
            if item.key == requirement.key and _extra_from_marker(item.marker) is None
        ]
        if not any(item.specifier == requirement.specifier for item in matches):
            report.fail(
                "lock_requirement_metadata_drift",
                f"uv.lock metadata is stale for base dependency {requirement.name}",
                dependency=requirement.name,
                expected_specifier=requirement.specifier,
                locked_specifiers=sorted({item.specifier for item in matches}),
            )

    for extra_name, requirements in extras.items():
        for requirement in requirements:
            matches = [
                item
                for item in locked
                if item.key == requirement.key and _extra_from_marker(item.marker) == extra_name
            ]
            if not any(item.specifier == requirement.specifier for item in matches):
                report.fail(
                    "lock_requirement_metadata_drift",
                    f"uv.lock metadata is stale for {extra_name}:{requirement.name}",
                    extra=extra_name,
                    dependency=requirement.name,
                    expected_specifier=requirement.specifier,
                    locked_specifiers=sorted({item.specifier for item in matches}),
                )


def _find_root_package(
    packages: list[dict[str, Any]],
    *,
    project_name: str,
    report: GateReport,
) -> dict[str, Any] | None:
    def is_local_project_source(value: Any) -> bool:
        return isinstance(value, dict) and any(
            value.get(kind) == "." for kind in ("virtual", "editable")
        )

    candidates = [
        package
        for package in packages
        if canonicalize_name(str(package.get("name", ""))) == project_name
        and is_local_project_source(package.get("source"))
    ]
    if len(candidates) != 1:
        report.fail(
            "lock_project_package_missing",
            "uv.lock must contain exactly one local virtual/editable package for the project",
            project=project_name,
            count=len(candidates),
        )
        return None
    return candidates[0]


def _validate_dependency_graph(
    packages: list[dict[str, Any]],
    package_names: set[str],
    report: GateReport,
) -> None:
    missing_edges: list[dict[str, str]] = []
    for package in packages:
        parent = canonicalize_name(str(package.get("name", "")))
        dependencies = package.get("dependencies", [])
        if not isinstance(dependencies, list):
            continue
        for dependency in dependencies:
            if not isinstance(dependency, dict) or not dependency.get("name"):
                continue
            child = canonicalize_name(str(dependency["name"]))
            if child not in package_names:
                missing_edges.append({"package": parent, "dependency": child})
    if missing_edges:
        report.fail(
            "lock_graph_missing_package",
            "uv.lock contains dependency edges with no locked package",
            edges=missing_edges[:50],
            truncated=len(missing_edges) > 50,
        )
    report.add_check(
        "lock-graph",
        "fail" if missing_edges else "pass",
        (
            f"Found {len(missing_edges)} unresolved dependency edge(s)"
            if missing_edges
            else f"All dependency edges resolve across {len(package_names)} locked package name(s)"
        ),
    )


def _wheel_basename(wheel: dict[str, Any]) -> str:
    return Path(urlparse(str(wheel.get("url", ""))).path).name.lower()


def _valid_wheel_artifact(wheel: dict[str, Any]) -> bool:
    url = str(wheel.get("url", ""))
    return (
        urlparse(url).scheme == "https"
        and SHA256_RE.fullmatch(str(wheel.get("hash", "")).lower()) is not None
        and isinstance(wheel.get("size"), int)
        and wheel["size"] > 0
    )


def _validate_pillow_wheels(
    pillow_packages: list[dict[str, Any]],
    python_minors: Sequence[str],
    report: GateReport,
) -> dict[str, Any]:
    wheels = [
        wheel
        for package in pillow_packages
        for wheel in package.get("wheels", [])
        if isinstance(wheel, dict)
    ]
    matrix: dict[str, Any] = {}
    for minor in python_minors:
        cp_tag = f"cp{minor.replace('.', '')}"
        targets = {
            "windows_x64": lambda name, tag=cp_tag: (
                f"-{tag}-{tag}-" in name and name.endswith("-win_amd64.whl")
            ),
            "linux_x64": lambda name, tag=cp_tag: (
                f"-{tag}-{tag}-" in name
                and "manylinux" in name
                and name.endswith("_x86_64.whl")
            ),
        }
        matrix[minor] = {}
        for target, predicate in targets.items():
            candidates = [wheel for wheel in wheels if predicate(_wheel_basename(wheel))]
            valid = [wheel for wheel in candidates if _valid_wheel_artifact(wheel)]
            if not candidates:
                report.fail(
                    "pillow_wheel_missing",
                    f"Pillow has no {target} wheel for CPython {minor}",
                    python=minor,
                    target=target,
                )
                matrix[minor][target] = None
                continue
            if len(valid) != len(candidates):
                report.fail(
                    "pillow_wheel_hash_invalid",
                    f"Pillow {target} wheel metadata lacks an HTTPS URL, size, or SHA-256 hash",
                    python=minor,
                    target=target,
                    invalid=[_wheel_basename(wheel) for wheel in candidates if wheel not in valid],
                )
            selected = valid[0] if valid else candidates[0]
            matrix[minor][target] = {
                "file": _wheel_basename(selected),
                "hash": selected.get("hash"),
                "size": selected.get("size"),
            }
    wheel_errors = [
        error
        for error in report.errors
        if error["code"] in {"pillow_wheel_missing", "pillow_wheel_hash_invalid"}
    ]
    report.add_check(
        "pillow-wheel-contract",
        "fail" if wheel_errors else "pass",
        (
            f"Pillow wheel contract has {len(wheel_errors)} error(s)"
            if wheel_errors
            else "Pillow has hashed Windows x64 and Linux x64 wheels for every supported Python"
        ),
        matrix=matrix,
    )
    return matrix


def _run_command(command: list[str], *, cwd: Path) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 125, str(exc)
    return completed.returncode, completed.stderr.strip()


def _run_uv_checks(
    project_root: Path,
    extras: Sequence[str],
    report: GateReport,
    *,
    uv_executable: str | None = None,
) -> None:
    uv = uv_executable or shutil.which("uv")
    if not uv:
        report.fail(
            "uv_not_found",
            "uv is required for the authoritative lock and extra resolution checks",
        )
        report.add_check("uv-lock", "fail", "uv executable was not found")
        return

    try:
        version_result = subprocess.run(
            [uv, "--version"],
            cwd=project_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        uv_version = version_result.stdout.strip() or version_result.stderr.strip()
    except (OSError, subprocess.TimeoutExpired):
        uv_version = "unknown"
    report.summary["uv"] = {"executable": uv, "version": uv_version}

    returncode, stderr = _run_command(
        [uv, "lock", "--check", "--offline"],
        cwd=project_root,
    )
    if returncode:
        report.fail(
            "uv_lock_check_failed",
            "uv reports that uv.lock is stale or invalid",
            returncode=returncode,
            stderr=stderr,
        )
    report.add_check(
        "uv-lock",
        "fail" if returncode else "pass",
        "uv lock --check --offline passed" if not returncode else "uv lock check failed",
    )

    failed_extras: list[str] = []
    for extra in sorted(extras):
        returncode, stderr = _run_command(
            [
                uv,
                "export",
                "--locked",
                "--offline",
                "--no-emit-project",
                "--extra",
                extra,
            ],
            cwd=project_root,
        )
        if returncode:
            failed_extras.append(extra)
            report.fail(
                "extra_resolution_failed",
                f"uv could not export locked extra {extra}",
                extra=extra,
                returncode=returncode,
                stderr=stderr,
            )
    report.add_check(
        "uv-extras",
        "fail" if failed_extras else "pass",
        (
            f"uv export failed for {', '.join(failed_extras)}"
            if failed_extras
            else f"uv exported all {len(extras)} declared extra(s) from the lock"
        ),
        extras=sorted(extras),
    )


def check_release_dependencies(
    project_root: Path,
    *,
    verify_with_uv: bool = True,
    uv_executable: str | None = None,
) -> GateReport:
    """Run the release dependency gate for ``project_root``."""

    project_root = project_root.resolve()
    report = GateReport(project_root=project_root)
    pyproject_path = project_root / "pyproject.toml"
    lock_path = project_root / "uv.lock"
    missing_files = [str(path.name) for path in (pyproject_path, lock_path) if not path.is_file()]
    if missing_files:
        report.fail(
            "dependency_file_missing",
            "Release dependency files are missing",
            files=missing_files,
        )
        report.add_check("dependency-files", "fail", "Required dependency files are missing")
        return report

    try:
        with pyproject_path.open("rb") as handle:
            pyproject = tomllib.load(handle)
        with lock_path.open("rb") as handle:
            lock = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        report.fail("dependency_file_invalid", f"Could not parse dependency files: {exc}")
        report.add_check("dependency-files", "fail", "Dependency TOML parsing failed")
        return report
    report.add_check(
        "dependency-files",
        "pass",
        "pyproject.toml and uv.lock are present and valid TOML",
    )

    project = pyproject.get("project")
    if not isinstance(project, dict):
        report.fail("project_table_missing", "pyproject.toml has no [project] table")
        return report
    project_name = canonicalize_name(str(project.get("name", "")))
    project_version = str(project.get("version", ""))
    report.summary["project"] = {
        "name": project_name,
        "version": project_version,
        "requires_python": project.get("requires-python"),
    }

    base = _parse_requirements(
        project.get("dependencies", []),
        group="project.dependencies",
        report=report,
    )
    raw_extras = project.get("optional-dependencies", {})
    if not isinstance(raw_extras, dict):
        report.fail(
            "extras_table_invalid",
            "project.optional-dependencies must be a table",
        )
        raw_extras = {}
    extras = {
        str(extra): _parse_requirements(
            requirements,
            group=f"project.optional-dependencies.{extra}",
            report=report,
        )
        for extra, requirements in raw_extras.items()
        if isinstance(requirements, list)
    }

    base_names = {requirement.name for requirement in base}
    missing_core = sorted(set(CORE_DEPENDENCIES) - base_names)
    for dependency in missing_core:
        report.fail(
            "core_dependency_missing",
            f"Required core dependency {dependency} is absent from project.dependencies",
            dependency=dependency,
        )
    report.add_check(
        "core-declarations",
        "fail" if missing_core else "pass",
        (
            f"Missing {len(missing_core)} core dependency declaration(s)"
            if missing_core
            else f"All {len(CORE_DEPENDENCIES)} required core dependencies are declared"
        ),
        required=list(CORE_DEPENDENCIES),
    )

    packages = [
        package
        for package in lock.get("package", [])
        if isinstance(package, dict) and package.get("name")
    ]
    package_names = {canonicalize_name(str(package["name"])) for package in packages}
    root_package = _find_root_package(
        packages,
        project_name=project_name,
        report=report,
    )
    if root_package is not None:
        if str(root_package.get("version", "")) != project_version:
            report.fail(
                "lock_project_version_drift",
                "uv.lock project version differs from pyproject.toml",
                pyproject=project_version,
                lock=root_package.get("version"),
            )
        _compare_direct_dependencies(
            group="base",
            expected=base,
            actual_items=root_package.get("dependencies", []),
            report=report,
        )
        locked_optional = root_package.get("optional-dependencies", {})
        if not isinstance(locked_optional, dict):
            locked_optional = {}
        if set(locked_optional) != set(extras):
            report.fail(
                "lock_extras_drift",
                "uv.lock optional dependency groups differ from pyproject.toml",
                missing=sorted(set(extras) - set(locked_optional)),
                unexpected=sorted(set(locked_optional) - set(extras)),
            )
        for extra_name, requirements in extras.items():
            _compare_direct_dependencies(
                group=f"extra:{extra_name}",
                expected=requirements,
                actual_items=locked_optional.get(extra_name, []),
                report=report,
            )
        metadata = root_package.get("metadata", {})
        provides_extras = set(
            metadata.get("provides-extras", [])
            if isinstance(metadata, dict)
            else []
        )
        if provides_extras != set(extras):
            report.fail(
                "lock_provides_extras_drift",
                "uv.lock provides-extras differs from pyproject.toml",
                missing=sorted(set(extras) - provides_extras),
                unexpected=sorted(provides_extras - set(extras)),
            )
        _validate_metadata_requirements(
            base=base,
            extras=extras,
            metadata_items=metadata.get("requires-dist", [])
            if isinstance(metadata, dict)
            else [],
            report=report,
        )

    declared_requirements = [
        ("base", requirement) for requirement in base
    ] + [
        (f"extra:{extra_name}", requirement)
        for extra_name, requirements in extras.items()
        for requirement in requirements
    ]
    missing_locked = [
        {"group": group, "dependency": requirement.name}
        for group, requirement in declared_requirements
        if requirement.name not in package_names
    ]
    for item in missing_locked:
        report.fail(
            "locked_package_missing",
            f"{item['dependency']} is declared by {item['group']} but absent from uv.lock",
            **item,
        )
    report.add_check(
        "locked-declarations",
        "fail" if missing_locked else "pass",
        (
            f"{len(missing_locked)} declared dependency reference(s) are absent from uv.lock"
            if missing_locked
            else f"All {len(declared_requirements)} direct dependency declaration(s) are locked"
        ),
    )

    python_minors = _validate_python_contract(
        project.get("requires-python"),
        lock.get("requires-python"),
        report,
    )
    _validate_dependency_graph(packages, package_names, report)

    pillow_packages = [
        package
        for package in packages
        if canonicalize_name(str(package.get("name", ""))) == "pillow"
    ]
    if not pillow_packages:
        report.fail("pillow_lock_missing", "Pillow is absent from uv.lock")
        pillow_matrix: dict[str, Any] = {}
        report.add_check("pillow-wheel-contract", "fail", "Pillow is absent from uv.lock")
    else:
        pillow_matrix = _validate_pillow_wheels(
            pillow_packages,
            python_minors or EXPECTED_PYTHON_MINORS,
            report,
        )

    versions: dict[str, list[str]] = {}
    for dependency in CORE_DEPENDENCIES:
        package_versions = sorted(
            {
                str(package.get("version", ""))
                for package in packages
                if canonicalize_name(str(package.get("name", ""))) == dependency
            }
        )
        versions[dependency] = package_versions
    report.summary["core_dependencies"] = versions
    report.summary["extras"] = {
        extra: [requirement.name for requirement in requirements]
        for extra, requirements in sorted(extras.items())
    }
    report.summary["pillow_wheels"] = pillow_matrix

    static_error_codes = {
        error["code"]
        for error in report.errors
        if not error["code"].startswith("uv_") and error["code"] != "extra_resolution_failed"
    }
    report.add_check(
        "static-lock-contract",
        "fail" if static_error_codes else "pass",
        (
            f"Static dependency contract has {len(static_error_codes)} error type(s)"
            if static_error_codes
            else "Static dependency contract is internally consistent"
        ),
    )

    if verify_with_uv:
        _run_uv_checks(
            project_root,
            tuple(extras),
            report,
            uv_executable=uv_executable,
        )
    return report


def _print_human(report: GateReport) -> None:
    status = "PASS" if report.ok else "FAIL"
    project = report.summary.get("project", {})
    print(
        f"[{status}] release dependency gate: "
        f"{project.get('name', 'unknown')} {project.get('version', 'unknown')}"
    )
    for check in report.checks:
        prefix = "OK" if check["status"] == "pass" else "ERROR"
        print(f"  [{prefix}] {check['id']}: {check['message']}")
    core = report.summary.get("core_dependencies", {})
    if core:
        versions = ", ".join(
            f"{name}={('/'.join(value) if value else 'missing')}"
            for name, value in sorted(core.items())
        )
        print(f"  core: {versions}")
    for error in report.errors:
        print(f"  - {error['code']}: {error['message']}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root containing pyproject.toml and uv.lock",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON report",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = check_release_dependencies(args.project_root)
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_human(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
