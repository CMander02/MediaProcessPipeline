from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.runtime_profiles import (  # noqa: E402
    DependencyRequirement,
    RuntimeProfileConflictError,
    RuntimeProfileValidationError,
    RuntimeTarget,
    all_runtime_targets,
    declared_runtime_targets,
    dependency_requirement_applies_to_target,
    load_runtime_profile_catalog,
    resolve_runtime_profiles,
    validate_runtime_profile_catalog,
)

MANIFEST = ROOT / "backend" / "resources" / "runtime-profiles.json"
WINDOWS_NVIDIA = RuntimeTarget("windows", "x86_64", "nvidia")
WINDOWS_CPU = RuntimeTarget("windows", "x86_64", "none")


def _copy_catalog_fixture(tmp_path: Path) -> Path:
    resource_dir = tmp_path / "backend" / "resources"
    resource_dir.mkdir(parents=True)
    shutil.copy2(MANIFEST, resource_dir / MANIFEST.name)
    shutil.copy2(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copy2(ROOT / "uv.lock", tmp_path / "uv.lock")
    return tmp_path


def _mutate_manifest(tmp_path: Path, mutate) -> Path:
    root = _copy_catalog_fixture(tmp_path)
    path = root / "backend" / "resources" / "runtime-profiles.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return root


def test_checked_in_catalog_matches_complete_dependency_contract() -> None:
    catalog = load_runtime_profile_catalog()

    validate_runtime_profile_catalog(catalog)
    assert catalog.schema == 1
    assert len(catalog.digest) == 64
    assert len(catalog.dependency_bindings) == len(
        catalog.dependency_requirements
    )
    assert {profile.id for profile in catalog.profiles} == {
        "browser-extraction",
        "full-local-nvidia",
        "local-llm",
        "onnx-vad-cpu",
        "qwen-asr-local",
        "remote-api",
        "transformers-local",
        "uvr-nvidia",
    }
    used_extras = {extra for profile in catalog.profiles for extra in profile.uv_extras}
    assert used_extras == {
        "asr-api-vad",
        "hf-local-inference",
        "local-asr",
        "local-llm",
        "local-models",
        "uvr",
    }
    packaging = [
        requirement
        for requirement in catalog.dependency_requirements
        if requirement.name == "packaging" and not requirement.marker
    ]
    assert len(packaging) == 1
    assert packaging[0].specifier == ">=24.0"
    torch = [
        requirement
        for requirement in catalog.dependency_requirements
        if requirement.name == "torch"
    ]
    assert torch
    assert {requirement.source_kind for requirement in torch} == {"index"}
    assert {requirement.source_value for requirement in torch} == {
        "https://download.pytorch.org/whl/cu128"
    }
    onnx_binding = next(
        binding
        for binding in catalog.dependency_bindings
        if binding.name == "onnxruntime"
    )
    assert onnx_binding.locked_version == "1.27.0"
    assert onnx_binding.marker == 'extra == "asr-api-vad"'
    assert onnx_binding.locked_source_kind == "index"
    assert onnx_binding.locked_source_value == "https://pypi.org/simple"
    safetensors_binding = next(
        binding
        for binding in catalog.locked_probe_bindings
        if binding.name == "safetensors"
    )
    assert safetensors_binding.probe_id == "safetensors"
    assert safetensors_binding.locked_version == "0.7.0"
    assert next(
        profile.verification_pending
        for profile in catalog.profiles
        if profile.id == "onnx-vad-cpu"
    )
    assert next(
        profile.verification_pending
        for profile in catalog.profiles
        if profile.id == "browser-extraction"
    )


def test_every_profile_declares_at_least_one_concrete_target() -> None:
    catalog = load_runtime_profile_catalog()
    universe = set(all_runtime_targets())

    for profile in catalog.profiles:
        declared = set(declared_runtime_targets(profile))
        assert declared
        assert declared <= universe
        for target in declared:
            plan = resolve_runtime_profiles(catalog, [profile.id], target=target)
            assert plan.target == target


def test_profiles_use_explicit_installable_target_triples() -> None:
    catalog = load_runtime_profile_catalog()
    forbidden_pairs = {
        ("windows", "aarch64"),
        ("macos", "x86_64"),
    }

    for profile in catalog.profiles:
        assert len(profile.targets) == len(set(profile.targets))
        assert all(
            (target.operating_system, target.architecture)
            not in forbidden_pairs
            for target in profile.targets
        )
        assert all(
            not (
                target.operating_system == "macos"
                and target.gpu_mode == "nvidia"
            )
            for target in profile.targets
        )


def test_catalog_digest_is_canonical_across_format_and_order(tmp_path: Path) -> None:
    root = _copy_catalog_fixture(tmp_path)
    first = load_runtime_profile_catalog(project_root=root)
    manifest_path = root / "backend" / "resources" / "runtime-profiles.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["profiles"].reverse()
    for profile in payload["profiles"]:
        for field in (
            "components",
            "mutuallyExclusive",
            "probes",
            "requiredBinaries",
            "requiredModels",
            "targets",
            "uvExtras",
        ):
            profile[field].reverse()
        for target in profile["targets"]:
            target_items = list(target.items())
            target.clear()
            target.update(reversed(target_items))
        items = list(profile.items())
        profile.clear()
        profile.update(reversed(items))
    manifest_path.write_text(
        json.dumps({"profiles": payload["profiles"], "schema": 1}, separators=(",", ":")),
        encoding="utf-8",
    )

    second = load_runtime_profile_catalog(project_root=root)

    assert second.digest == first.digest
    assert second.canonical_dict() == first.canonical_dict()


@pytest.mark.parametrize("extra", ["dev", "shell-command", "../../uvr"])
def test_manifest_rejects_non_runtime_extras(tmp_path: Path, extra: str) -> None:
    root = _mutate_manifest(
        tmp_path,
        lambda payload: payload["profiles"][0]["uvExtras"].append(extra),
    )

    with pytest.raises(RuntimeProfileValidationError, match="uvExtras"):
        load_runtime_profile_catalog(project_root=root)


def test_manifest_rejects_unknown_fields(tmp_path: Path) -> None:
    root = _mutate_manifest(
        tmp_path,
        lambda payload: payload["profiles"][0].__setitem__(
            "installCommand", "uv sync && run-user-input"
        ),
    )

    with pytest.raises(RuntimeProfileValidationError, match="unknown fields"):
        load_runtime_profile_catalog(project_root=root)


@pytest.mark.parametrize(
    "model_id",
    ["../model", "owner/../../model", r"owner\model", "C:/model", "owner/model/extra"],
)
def test_manifest_rejects_unsafe_model_identifiers(
    tmp_path: Path,
    model_id: str,
) -> None:
    root = _mutate_manifest(
        tmp_path,
        lambda payload: payload["profiles"][0]["requiredModels"].append(model_id),
    )

    with pytest.raises(RuntimeProfileValidationError, match="model"):
        load_runtime_profile_catalog(project_root=root)


def test_manifest_requires_pending_model_verification(tmp_path: Path) -> None:
    root = _mutate_manifest(
        tmp_path,
        lambda payload: next(
            profile
            for profile in payload["profiles"]
            if profile["id"] == "qwen-asr-local"
        ).__setitem__("verificationPending", False),
    )

    with pytest.raises(RuntimeProfileValidationError, match="verificationPending"):
        load_runtime_profile_catalog(project_root=root)


def test_manifest_keeps_browser_pending_until_defuddle_sidecar_contract(
    tmp_path: Path,
) -> None:
    root = _mutate_manifest(
        tmp_path,
        lambda payload: next(
            profile
            for profile in payload["profiles"]
            if profile["id"] == "browser-extraction"
        ).__setitem__("verificationPending", False),
    )

    with pytest.raises(RuntimeProfileValidationError, match="Defuddle"):
        load_runtime_profile_catalog(project_root=root)


def test_loader_rejects_alternate_manifest_path(tmp_path: Path) -> None:
    root = _copy_catalog_fixture(tmp_path)
    alternate = root / "runtime-profiles.json"
    shutil.copy2(MANIFEST, alternate)

    with pytest.raises(RuntimeProfileValidationError, match="Unsupported manifest path"):
        load_runtime_profile_catalog(project_root=root, manifest_path=alternate)


def test_loader_rejects_direct_lock_dependency_drift(tmp_path: Path) -> None:
    root = _copy_catalog_fixture(tmp_path)
    lock_path = root / "uv.lock"
    text = lock_path.read_text(encoding="utf-8")
    original = """[package.optional-dependencies]
asr-api-vad = [
    { name = "onnxruntime" },
]"""
    assert original in text
    lock_path.write_text(
        text.replace(
            original,
            """[package.optional-dependencies]
asr-api-vad = []""",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeProfileValidationError, match="optional dependencies differ"):
        load_runtime_profile_catalog(project_root=root)


def test_loader_rejects_version_specifier_drift(tmp_path: Path) -> None:
    root = _copy_catalog_fixture(tmp_path)
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert '"onnxruntime>=1.16.1"' in text
    pyproject.write_text(
        text.replace('"onnxruntime>=1.16.1"', '"onnxruntime>=99.0"', 1),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeProfileValidationError, match="metadata.requires-dist"):
        load_runtime_profile_catalog(project_root=root)


def test_loader_rejects_locked_version_outside_direct_specifier(
    tmp_path: Path,
) -> None:
    root = _copy_catalog_fixture(tmp_path)
    pyproject = root / "pyproject.toml"
    pyproject_text = pyproject.read_text(encoding="utf-8")
    assert '"onnxruntime>=1.16.1"' in pyproject_text
    pyproject.write_text(
        pyproject_text.replace(
            '"onnxruntime>=1.16.1"',
            '"onnxruntime>=99.0"',
            1,
        ),
        encoding="utf-8",
    )
    lock_path = root / "uv.lock"
    lock_text = lock_path.read_text(encoding="utf-8")
    original = """{ name = "onnxruntime", marker = "extra == 'asr-api-vad'", specifier = ">=1.16.1" }"""
    replacement = """{ name = "onnxruntime", marker = "extra == 'asr-api-vad'", specifier = ">=99.0" }"""
    assert original in lock_text
    lock_path.write_text(
        lock_text.replace(original, replacement, 1),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeProfileValidationError, match="package/version/source"):
        load_runtime_profile_catalog(project_root=root)


def test_loader_rejects_direct_package_record_source_mismatch(
    tmp_path: Path,
) -> None:
    root = _copy_catalog_fixture(tmp_path)
    lock_path = root / "uv.lock"
    text = lock_path.read_text(encoding="utf-8")
    original = """name = "onnxruntime"
version = "1.27.0"
source = { registry = "https://pypi.org/simple" }"""
    replacement = """name = "onnxruntime"
version = "1.27.0"
source = { registry = "https://download.pytorch.org/whl/cu128" }"""
    assert original in text
    lock_path.write_text(
        text.replace(original, replacement, 1),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeProfileValidationError, match="source"):
        load_runtime_profile_catalog(project_root=root)


def test_loader_rejects_unbounded_locked_version_segment(
    tmp_path: Path,
) -> None:
    root = _copy_catalog_fixture(tmp_path)
    lock_path = root / "uv.lock"
    text = lock_path.read_text(encoding="utf-8")
    original = 'name = "onnxruntime"\nversion = "1.27.0"'
    replacement = (
        'name = "onnxruntime"\nversion = "1.9999999999999.0"'
    )
    assert original in text
    lock_path.write_text(
        text.replace(original, replacement, 1),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeProfileValidationError, match="bounded version"):
        load_runtime_profile_catalog(project_root=root)


def test_loader_rejects_marker_drift(tmp_path: Path) -> None:
    root = _copy_catalog_fixture(tmp_path)
    lock_path = root / "uv.lock"
    text = lock_path.read_text(encoding="utf-8")
    original = """{ name = "onnxruntime", marker = "extra == 'asr-api-vad'", specifier = ">=1.16.1" }"""
    replacement = """{ name = "onnxruntime", marker = "extra == 'local-asr'", specifier = ">=1.16.1" }"""
    assert original in text
    lock_path.write_text(text.replace(original, replacement, 1), encoding="utf-8")

    with pytest.raises(RuntimeProfileValidationError, match="metadata.requires-dist"):
        load_runtime_profile_catalog(project_root=root)


def test_loader_rejects_locked_resolution_marker_gap(
    tmp_path: Path,
) -> None:
    root = _copy_catalog_fixture(tmp_path)
    lock_path = root / "uv.lock"
    text = lock_path.read_text(encoding="utf-8")
    original = """name = "onnxruntime"
version = "1.27.0"
source = { registry = "https://pypi.org/simple" }"""
    replacement = """name = "onnxruntime"
version = "1.27.0"
source = { registry = "https://pypi.org/simple" }
resolution-markers = ["python_version >= '3.13'"]"""
    assert original in text
    lock_path.write_text(
        text.replace(original, replacement, 1),
        encoding="utf-8",
    )

    with pytest.raises(
        RuntimeProfileValidationError,
        match="no package record for probe",
    ):
        load_runtime_profile_catalog(project_root=root)


@pytest.mark.parametrize("source_kind", ["git", "path"])
def test_loader_rejects_git_and_path_dependency_sources(
    tmp_path: Path,
    source_kind: str,
) -> None:
    root = _copy_catalog_fixture(tmp_path)
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    original = 'torch = { index = "pytorch-cu128" }'
    replacement = (
        'torch = { git = "https://github.com/pytorch/pytorch.git" }'
        if source_kind == "git"
        else 'torch = { path = "../pytorch" }'
    )
    assert original in text
    pyproject.write_text(
        text.replace(original, replacement, 1),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeProfileValidationError, match="trust boundary"):
        load_runtime_profile_catalog(project_root=root)


def test_loader_rejects_transitive_git_source_in_lock(tmp_path: Path) -> None:
    root = _copy_catalog_fixture(tmp_path)
    lock_path = root / "uv.lock"
    text = lock_path.read_text(encoding="utf-8")
    original = 'source = { registry = "https://pypi.org/simple" }'
    assert original in text
    lock_path.write_text(
        text.replace(
            original,
            'source = { git = "https://github.com/example/package.git" }',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeProfileValidationError, match="trust boundary"):
        load_runtime_profile_catalog(project_root=root)


def test_loader_rejects_unpinned_lock_artifact(tmp_path: Path) -> None:
    root = _copy_catalog_fixture(tmp_path)
    lock_path = root / "uv.lock"
    text = lock_path.read_text(encoding="utf-8")
    original = 'hash = "sha256:'
    assert original in text
    lock_path.write_text(
        text.replace(original, 'hash = "', 1),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeProfileValidationError, match="valid SHA-256"):
        load_runtime_profile_catalog(project_root=root)


def test_static_catalog_validation_rejects_noncanonical_dependency_url() -> None:
    catalog = load_runtime_profile_catalog()
    requirement = next(
        item for item in catalog.dependency_requirements if item.source_kind == "index"
    )
    altered = replace(
        requirement,
        source_value="https://download.pytorch.org/whl/cu128/",
    )
    modified = replace(
        catalog,
        dependency_requirements=tuple(
            altered if item is requirement else item
            for item in catalog.dependency_requirements
        ),
    )

    with pytest.raises(RuntimeProfileValidationError, match="not canonical"):
        validate_runtime_profile_catalog(modified)


def test_loader_rejects_direct_url_drift(tmp_path: Path) -> None:
    root = _copy_catalog_fixture(tmp_path)
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert '"onnxruntime>=1.16.1"' in text
    pyproject.write_text(
        text.replace(
            '"onnxruntime>=1.16.1"',
            '"onnxruntime @ https://packages.example.invalid/onnxruntime.whl"',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        RuntimeProfileValidationError,
        match="allowlist|SHA-256",
    ):
        load_runtime_profile_catalog(project_root=root)


def test_loader_requires_hash_for_trusted_direct_url(tmp_path: Path) -> None:
    root = _copy_catalog_fixture(tmp_path)
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert '"onnxruntime>=1.16.1"' in text
    pyproject.write_text(
        text.replace(
            '"onnxruntime>=1.16.1"',
            (
                '"onnxruntime @ '
                'https://files.pythonhosted.org/packages/onnxruntime.whl"'
            ),
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeProfileValidationError, match="SHA-256"):
        load_runtime_profile_catalog(project_root=root)


def test_loader_rejects_index_drift(tmp_path: Path) -> None:
    root = _copy_catalog_fixture(tmp_path)
    lock_path = root / "uv.lock"
    text = lock_path.read_text(encoding="utf-8")
    original = 'index = "https://download.pytorch.org/whl/cu128"'
    assert original in text
    lock_path.write_text(
        text.replace(
            original,
            'index = "https://packages.example.invalid/cu128"',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeProfileValidationError, match="allowlist"):
        load_runtime_profile_catalog(project_root=root)


def test_loader_rejects_missing_metadata_requirement(tmp_path: Path) -> None:
    root = _copy_catalog_fixture(tmp_path)
    lock_path = root / "uv.lock"
    text = lock_path.read_text(encoding="utf-8")
    line = '    { name = "packaging", specifier = ">=24.0" },\n'
    assert line in text
    lock_path.write_text(text.replace(line, "", 1), encoding="utf-8")

    with pytest.raises(RuntimeProfileValidationError, match="metadata.requires-dist"):
        load_runtime_profile_catalog(project_root=root)


def test_manifest_requires_declared_cpu_gpu_onnx_conflict(tmp_path: Path) -> None:
    def remove_conflict(payload: dict) -> None:
        by_id = {profile["id"]: profile for profile in payload["profiles"]}
        by_id["onnx-vad-cpu"]["mutuallyExclusive"].remove("uvr-nvidia")
        by_id["uvr-nvidia"]["mutuallyExclusive"].remove("onnx-vad-cpu")

    root = _mutate_manifest(tmp_path, remove_conflict)

    with pytest.raises(RuntimeProfileValidationError, match="CPU/GPU ONNX"):
        load_runtime_profile_catalog(project_root=root)


def test_resolver_rejects_mutually_exclusive_onnx_profiles() -> None:
    catalog = load_runtime_profile_catalog()

    with pytest.raises(RuntimeProfileConflictError, match="conflicts"):
        resolve_runtime_profiles(
            catalog,
            ["onnx-vad-cpu", "uvr-nvidia"],
            target=WINDOWS_NVIDIA,
        )


def test_resolver_produces_fixed_actions_and_pending_readiness() -> None:
    catalog = load_runtime_profile_catalog()

    plan = resolve_runtime_profiles(
        catalog,
        ["browser-extraction", "qwen-asr-local"],
        target=WINDOWS_NVIDIA,
    )

    assert plan.uv_arguments == (
        "sync",
        "--frozen",
        "--no-dev",
        "--extra",
        "local-asr",
    )
    assert plan.uv_dry_run_arguments == (
        "sync",
        "--frozen",
        "--no-dev",
        "--extra",
        "local-asr",
        "--dry-run",
        "--offline",
        "--python-platform",
        "x86_64-pc-windows-msvc",
    )
    assert [action.argv for action in plan.component_actions] == [
        (
            "run",
            "--frozen",
            "--no-sync",
            "playwright",
            "install",
            "chromium",
        )
    ]
    assert plan.verification_pending is True
    assert plan.verification_pending_profiles == (
        "browser-extraction",
        "qwen-asr-local",
    )
    assert plan.ready is False
    expectations = dict(plan.version_expectations)
    assert expectations["qwen-asr"] == "==0.0.6"
    assert expectations["torchaudio"] == "==2.8.0+cu128"
    assert expectations["cuda"] == "==2.8.0+cu128"


def test_remote_api_plan_is_ready_without_model_verification() -> None:
    catalog = load_runtime_profile_catalog()

    plan = resolve_runtime_profiles(catalog, ["remote-api"], target=WINDOWS_CPU)

    assert plan.ready is True
    assert plan.verification_pending is False


def test_transitive_probe_versions_are_bound_to_exact_lock_records() -> None:
    catalog = load_runtime_profile_catalog()

    plan = resolve_runtime_profiles(
        catalog,
        ["transformers-local"],
        target=WINDOWS_NVIDIA,
    )

    expectations = dict(plan.version_expectations)
    assert expectations["transformers"] == "==4.57.6"
    assert expectations["accelerate"] == "==1.12.0"
    assert expectations["safetensors"] == "==0.7.0"
    assert expectations["cuda"] == "==2.8.0+cu128"


def test_browser_profile_stays_pending_until_defuddle_sidecar_is_declared() -> None:
    catalog = load_runtime_profile_catalog()

    plan = resolve_runtime_profiles(
        catalog,
        ["browser-extraction"],
        target=WINDOWS_CPU,
    )

    assert plan.verification_pending is True
    assert plan.ready is False
    assert plan.verification_pending_profiles == ("browser-extraction",)


def test_dependency_markers_are_evaluated_for_explicit_runtime_target() -> None:
    requirement = DependencyRequirement(
        name="example",
        extras=(),
        specifier=">=1",
        marker='sys_platform == "win32" and platform_machine == "AMD64"',
        source_kind="",
        source_value="",
    )

    assert dependency_requirement_applies_to_target(
        requirement,
        selected_extras=(),
        target=WINDOWS_CPU,
    )
    assert not dependency_requirement_applies_to_target(
        requirement,
        selected_extras=(),
        target=RuntimeTarget("linux", "x86_64", "none"),
    )


@pytest.mark.parametrize(
    "selection",
    [
        [],
        ["missing-profile"],
        ["uv sync --extra local-models"],
        ["remote-api", "remote-api"],
    ],
)
def test_resolver_rejects_empty_unknown_command_like_or_duplicate_input(
    selection: list[str],
) -> None:
    catalog = load_runtime_profile_catalog()

    with pytest.raises(RuntimeProfileValidationError):
        resolve_runtime_profiles(catalog, selection, target=WINDOWS_NVIDIA)


def test_resolver_requires_runtime_target() -> None:
    catalog = load_runtime_profile_catalog()

    with pytest.raises(TypeError):
        resolve_runtime_profiles(catalog, ["remote-api"])  # type: ignore[call-arg]
    with pytest.raises(RuntimeProfileValidationError, match="target"):
        resolve_runtime_profiles(
            catalog,
            ["remote-api"],
            target=None,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "values",
    [
        ("solaris", "x86_64", "none"),
        ("windows", "armv7", "none"),
        ("windows", "x86_64", "amd"),
    ],
)
def test_runtime_target_rejects_unknown_values(values: tuple[str, str, str]) -> None:
    with pytest.raises(RuntimeProfileValidationError):
        RuntimeTarget(*values)


def test_combined_profiles_must_share_the_explicit_target() -> None:
    catalog = load_runtime_profile_catalog()
    mac_arm = RuntimeTarget("macos", "aarch64", "nvidia")

    with pytest.raises(RuntimeProfileConflictError, match="qwen-asr-local"):
        resolve_runtime_profiles(
            catalog,
            ["remote-api", "qwen-asr-local"],
            target=mac_arm,
        )


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    root = _copy_catalog_fixture(tmp_path)
    manifest_path = root / "backend" / "resources" / "runtime-profiles.json"
    manifest_path.write_text(
        '{"schema":1,"schema":1,"profiles":[]}',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeProfileValidationError, match="Duplicate JSON field"):
        load_runtime_profile_catalog(project_root=root)


def test_boolean_schema_is_rejected(tmp_path: Path) -> None:
    root = _mutate_manifest(
        tmp_path,
        lambda payload: payload.__setitem__("schema", True),
    )

    with pytest.raises(RuntimeProfileValidationError, match="schema"):
        load_runtime_profile_catalog(project_root=root)


def test_runtime_profile_checker_exercises_targets_and_selection() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "check-runtime-profiles.py"),
            "--root",
            str(ROOT),
            "--profiles",
            "remote-api",
            "browser-extraction",
            "--target-os",
            "windows",
            "--target-arch",
            "x86_64",
            "--target-gpu",
            "none",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["declaredTargetCount"] > 0
    assert payload["rejectedIncompatibleTargetCount"] > 0
    assert payload["selection"]["uvArguments"] == [
        "sync",
        "--frozen",
        "--no-dev",
    ]
    assert payload["uvFrozenDryRunCount"] > 0
    assert payload["uvFrozenDryRunMode"] == "isolated-offline"
    assert payload["selection"]["target"] == {
        "arch": "x86_64",
        "gpu": "none",
        "os": "windows",
    }


def test_runtime_profile_checker_requires_selection_target() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "check-runtime-profiles.py"),
            "--root",
            str(ROOT),
            "--profiles",
            "remote-api",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )

    assert completed.returncode == 1
    assert "requires --target-os" in completed.stderr


def test_runtime_profile_checker_dry_runs_the_real_combined_selection(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script_path = ROOT / "scripts" / "check-runtime-profiles.py"
    spec = importlib.util.spec_from_file_location(
        "check_runtime_profiles_test_module",
        script_path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    captured_plans = []

    def capture_plans(root: Path, plans: list) -> int:
        assert root == ROOT.resolve()
        captured_plans.extend(plans)
        return len(
            {
                plan.uv_dry_run_arguments
                for plan in plans
            }
        )

    monkeypatch.setattr(module, "_validate_uv_dry_runs", capture_plans)
    result = module.main(
        [
            "--root",
            str(ROOT),
            "--profiles",
            "qwen-asr-local",
            "transformers-local",
            "--target-os",
            "windows",
            "--target-arch",
            "x86_64",
            "--target-gpu",
            "nvidia",
            "--json",
        ]
    )
    capsys.readouterr()

    assert result == 0
    combined = [
        plan
        for plan in captured_plans
        if plan.profile_ids
        == ("qwen-asr-local", "transformers-local")
    ]
    assert len(combined) == 1
    assert combined[0].uv_extras == ("hf-local-inference", "local-asr")
