#!/usr/bin/env python3
"""Validate the production runtime profile catalog and dependency lock."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.runtime_profiles import (  # noqa: E402
    RuntimeProfileConflictError,
    RuntimeProfileError,
    RuntimeTarget,
    all_runtime_targets,
    declared_runtime_targets,
    load_runtime_profile_catalog,
    resolve_runtime_profiles,
    validate_runtime_profile_catalog,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root containing pyproject.toml and uv.lock.",
    )
    parser.add_argument(
        "--profiles",
        nargs="+",
        help="Optionally resolve this exact profile selection after validation.",
    )
    parser.add_argument(
        "--target-os",
        choices=("windows", "linux", "macos"),
        help="Required with --profiles.",
    )
    parser.add_argument(
        "--target-arch",
        choices=("x86_64", "aarch64"),
        help="Required with --profiles.",
    )
    parser.add_argument(
        "--target-gpu",
        choices=("none", "nvidia"),
        help="Required with --profiles.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def _validate_uv_dry_runs(
    root: Path,
    plans: list,
) -> int:
    executable = shutil.which("uv")
    if executable is None:
        raise RuntimeProfileError("uv executable is unavailable for frozen dry-run")
    unique_commands = sorted(
        {
            (
                executable,
                *plan.uv_dry_run_arguments,
                "--python",
                sys.executable,
                "--no-python-downloads",
            )
            for plan in plans
        }
    )
    environment = os.environ.copy()
    environment.update(
        {
            "UV_NO_CACHE": "1",
            "UV_NO_PROGRESS": "1",
            "UV_OFFLINE": "1",
            "UV_PYTHON_DOWNLOADS": "never",
        }
    )
    for command in unique_commands:
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                cwd=root,
                env=environment,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeProfileError(
                "uv frozen dry-run could not complete"
            ) from exc
        if completed.returncode != 0:
            raise RuntimeProfileError(
                "uv frozen dry-run rejected an explicit release target"
            )
    return len(unique_commands)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        catalog = load_runtime_profile_catalog(project_root=args.root)
        validate_runtime_profile_catalog(catalog)
        if args.profiles is not None and not (
            args.target_os and args.target_arch and args.target_gpu
        ):
            raise RuntimeProfileError(
                "--profiles requires --target-os, --target-arch, and --target-gpu"
            )
        supported_count = 0
        rejected_count = 0
        universe = all_runtime_targets()
        plans = []
        for profile in catalog.profiles:
            supported = set(declared_runtime_targets(profile))
            for target in supported:
                plans.append(
                    resolve_runtime_profiles(catalog, [profile.id], target=target)
                )
                supported_count += 1
            for target in set(universe) - supported:
                try:
                    resolve_runtime_profiles(catalog, [profile.id], target=target)
                except RuntimeProfileConflictError:
                    rejected_count += 1
                else:
                    raise RuntimeProfileError(
                        f"Profile {profile.id!r} accepted undeclared target"
                    )
        if args.profiles is not None:
            selected_plan = resolve_runtime_profiles(
                catalog,
                args.profiles,
                target=RuntimeTarget(
                    args.target_os,
                    args.target_arch,
                    args.target_gpu,
                ),
            )
            plans.append(selected_plan)
        else:
            selected_plan = None
        dry_run_count = _validate_uv_dry_runs(args.root.resolve(), plans)
    except RuntimeProfileError as exc:
        if args.json:
            print(
                json.dumps(
                    {"ok": False, "error": str(exc)},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        else:
            print(f"runtime profile check failed: {exc}", file=sys.stderr)
        return 1

    result = {
        "ok": True,
        "schema": catalog.schema,
        "digest": catalog.digest,
        "profileCount": len(catalog.profiles),
        "profileIds": [profile.id for profile in catalog.profiles],
        "declaredTargetCount": supported_count,
        "rejectedIncompatibleTargetCount": rejected_count,
        "runtimeExtras": sorted(
            {extra for plan in plans for extra in plan.uv_extras}
        ),
        "uvFrozenDryRunMode": "isolated-offline",
        "uvFrozenDryRunCount": dry_run_count,
    }
    if selected_plan is not None:
        result["selection"] = {
            "components": list(selected_plan.components),
            "profileIds": list(selected_plan.profile_ids),
            "ready": selected_plan.ready,
            "target": selected_plan.target.canonical_dict(),
            "uvArguments": list(selected_plan.uv_arguments),
            "verificationPending": selected_plan.verification_pending,
            "versionExpectations": dict(selected_plan.version_expectations),
        }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(
            "runtime profile check passed: "
            f"{result['profileCount']} profiles, digest {catalog.digest}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
