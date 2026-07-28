from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.runtime_diagnostics import (  # noqa: E402
    DiagnosticPaths,
    DiagnosticSelectionError,
    DiagnosticStatus,
    TrustedBinary,
    TrustedBinaryRegistry,
    TrustedComponentRegistry,
    list_diagnostic_capabilities,
    run_runtime_diagnostics,
)
from app.services.runtime_profiles import PROBE_ALLOWLIST  # noqa: E402


def _paths(root: Path) -> DiagnosticPaths:
    return DiagnosticPaths(
        config_file=root / "config" / "settings.json",
        data_dir=root / "data",
        cache_dir=root / "cache",
        log_dir=root / "logs",
    )


def _helper_result(arguments: list[str], payload: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(arguments, 0, json.dumps(payload), "")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _signed_registry(
    root: Path,
    *binaries: TrustedBinary,
) -> TrustedBinaryRegistry:
    return TrustedBinaryRegistry(
        manifest_root=root.resolve(),
        manifest_sha256="a" * 64,
        manifest_signature_verified=True,
        binaries=tuple(binaries),
    )


def _signed_component_registry(paths: DiagnosticPaths) -> TrustedComponentRegistry:
    manifest_root = (paths.cache_dir / "ms-playwright").resolve()
    component_root = manifest_root / "chromium-test"
    component_root.mkdir(parents=True, exist_ok=True)
    executable = component_root / "chrome.exe"
    executable.write_bytes(b"signed chromium fixture")
    files = [
        {
            "path": executable.name,
            "sha256": _sha256(executable),
            "size": executable.stat().st_size,
        }
    ]
    tree_bytes = json.dumps(
        files,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    manifest = {
        "schema": 1,
        "components": [
            {
                "id": "playwright-chromium",
                "root": component_root.name,
                "executable": executable.name,
                "treeSha256": hashlib.sha256(tree_bytes).hexdigest(),
                "files": files,
            }
        ],
    }
    manifest_path = manifest_root / "component-tree.json"
    manifest_path.write_text(
        json.dumps(
            manifest,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return TrustedComponentRegistry(
        manifest_root=manifest_root,
        manifest_path=manifest_path,
        manifest_sha256=_sha256(manifest_path),
        manifest_signature_verified=True,
    )


def test_capability_list_matches_expanded_profile_probe_allowlist() -> None:
    capabilities = list_diagnostic_capabilities()

    assert tuple(capability.id for capability in capabilities) == tuple(
        sorted(PROBE_ALLOWLIST)
    )
    assert {
        "accelerate",
        "chromium",
        "fastapi",
        "onnx-cuda-provider",
        "openai",
        "pyannote",
        "safetensors",
        "torchaudio",
    } <= {capability.id for capability in capabilities}
    assert all(capability.description for capability in capabilities)


def test_runtime_paths_use_current_effective_settings_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import settings as settings_module

    effective_data = tmp_path / "configured" / "task-data"
    monkeypatch.setattr(
        settings_module,
        "get_runtime_settings",
        lambda: SimpleNamespace(data_root=str(effective_data)),
    )

    paths = DiagnosticPaths.from_runtime()

    assert paths.data_dir == effective_data.resolve()


def test_corrupt_runtime_settings_use_safe_paths_and_keep_basic_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import settings as settings_module

    def corrupt_settings():
        raise ValueError("private corrupt configuration contents")

    monkeypatch.setattr(settings_module, "get_runtime_settings", corrupt_settings)

    paths = DiagnosticPaths.from_runtime()
    report = run_runtime_diagnostics(
        ["configuration", "python"],
        paths=paths,
    )

    assert paths.configuration_state == "fallback"
    statuses = {result.probe_id: result.status for result in report.results}
    assert statuses == {
        "configuration": DiagnosticStatus.DEGRADED,
        "python": DiagnosticStatus.AVAILABLE,
    }
    assert "private corrupt configuration contents" not in json.dumps(report.to_dict())


def test_python_filesystem_report_is_stable_and_path_free(tmp_path: Path) -> None:
    paths = _paths(tmp_path / "private-user-name")

    def runner(arguments, **kwargs):
        del kwargs
        if arguments[4] == "disk":
            return _helper_result(
                arguments,
                {
                    "status": "available",
                    "cacheFreeBytes": 750,
                    "cacheTotalBytes": 1000,
                    "configFreeBytes": 750,
                    "configTotalBytes": 1000,
                    "dataFreeBytes": 750,
                    "dataTotalBytes": 1000,
                    "logsFreeBytes": 750,
                    "logsTotalBytes": 1000,
                },
            )
        return _helper_result(
            arguments,
            {
                "status": "unknown",
                "cache": "permission-hint",
                "config": "permission-hint",
                "data": "permission-hint",
                "logs": "permission-hint",
            },
        )

    kwargs = {
        "paths": paths,
        "command_runner": runner,
    }
    first = run_runtime_diagnostics(
        ["writable-paths", "python", "disk"],
        **kwargs,
    )
    second = run_runtime_diagnostics(
        ["disk", "python", "writable-paths"],
        **kwargs,
    )

    assert first == second
    assert first.requested_probes == ("disk", "python", "writable-paths")
    assert first.healthy is True
    assert first.verified is False
    assert first.overall_status == "unknown"
    encoded = json.dumps(first.to_dict(), sort_keys=True)
    assert str(tmp_path) not in encoded
    statuses = {result.probe_id: result.status for result in first.results}
    assert statuses["disk"] is DiagnosticStatus.AVAILABLE
    assert statuses["python"] is DiagnosticStatus.AVAILABLE
    assert statuses["writable-paths"] is DiagnosticStatus.UNKNOWN


def test_writable_probe_is_read_only_and_never_claims_available(tmp_path: Path) -> None:
    paths = _paths(tmp_path / "runtime-user")
    before = tuple(tmp_path.rglob("*"))

    report = run_runtime_diagnostics(
        ["writable-paths"],
        paths=paths,
    )

    assert report.results[0].status is DiagnosticStatus.UNKNOWN
    assert tuple(tmp_path.rglob("*")) == before


def test_writable_probe_validates_file_and_directory_types(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.config_file.mkdir(parents=True)
    paths.data_dir.write_text("file where a directory is required", encoding="utf-8")

    report = run_runtime_diagnostics(
        ["writable-paths"],
        paths=paths,
    )

    result = report.results[0]
    assert result.status is DiagnosticStatus.DEGRADED
    assert result.to_dict()["details"]["config"] == "type-mismatch"
    assert result.to_dict()["details"]["data"] == "type-mismatch"


def test_pillow_helper_checks_jpeg_png_webp_and_version(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict]] = []

    def runner(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return _helper_result(
            arguments,
            {
                "status": "available",
                "version": "12.1.0",
                "jpeg": True,
                "png": True,
                "webp": True,
            },
        )

    report = run_runtime_diagnostics(
        ["pillow"],
        paths=_paths(tmp_path),
        command_runner=runner,
        version_expectations={"pillow": ">=12.1.0"},
    )

    result = report.results[0]
    assert result.status is DiagnosticStatus.AVAILABLE
    assert result.to_dict()["details"] == {
        "installedVersion": "12.1.0",
        "jpeg": True,
        "png": True,
        "versionState": "compatible",
        "webp": True,
    }
    assert calls[0][0][1:3] == ["-I", "-c"]
    assert calls[0][0][-1] == "pillow"


def test_helper_preserves_virtual_environment_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    launcher = tmp_path / "venv" / "bin" / "python"
    base_interpreter = tmp_path / "base" / "bin" / "python"
    original_resolve = Path.resolve
    calls: list[tuple[list[str], dict]] = []

    def resolve(path: Path, *args, **kwargs) -> Path:
        if path == launcher:
            return base_interpreter
        return original_resolve(path, *args, **kwargs)

    def runner(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return _helper_result(
            arguments,
            {"status": "available", "version": "1.0.0"},
        )

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(sys, "executable", str(launcher))

    report = run_runtime_diagnostics(
        ["fastapi"],
        paths=paths,
        command_runner=runner,
    )

    assert report.results[0].status is DiagnosticStatus.AVAILABLE
    assert calls[0][0][0] == str(launcher)
    assert calls[0][1]["cwd"] == launcher.parent


@pytest.mark.parametrize(
    "probe_id",
    [
        "accelerate",
        "audio-separator",
        "chromium",
        "cuda",
        "fastapi",
        "onnx-cuda-provider",
        "onnx-provider",
        "openai",
        "pillow",
        "playwright",
        "pyannote",
        "qwen-asr",
        "safetensors",
        "torchaudio",
        "transformers",
    ],
)
def test_isolated_capability_absence_is_unavailable(
    tmp_path: Path,
    probe_id: str,
) -> None:
    report = run_runtime_diagnostics(
        [probe_id],
        paths=_paths(tmp_path),
        command_runner=lambda arguments, **kwargs: _helper_result(
            arguments,
            {"status": "unavailable", "errorCode": "import-failed"},
        ),
    )

    assert report.results[0].status is DiagnosticStatus.UNAVAILABLE
    assert report.results[0].probe_id == probe_id


def test_isolated_helper_timeout_is_bounded(tmp_path: Path) -> None:
    calls: list[tuple[list[str], float]] = []

    def runner(arguments, **kwargs):
        calls.append((arguments, kwargs["timeout"]))
        raise subprocess.TimeoutExpired(arguments, kwargs["timeout"])

    report = run_runtime_diagnostics(
        ["transformers"],
        paths=_paths(tmp_path),
        timeout_seconds=0.25,
        command_runner=runner,
    )

    assert report.results[0].status is DiagnosticStatus.TIMEOUT
    assert calls[0][0][-1] == "transformers"
    assert calls[0][1] == 0.25


def test_helper_uses_minimal_environment_and_no_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-private-api-token")
    monkeypatch.setenv("HF_TOKEN", "hf_private-token")
    monkeypatch.setenv("GITHUB_PAT", "ghp_private-token")
    monkeypatch.setenv("MODELSCOPE_API_TOKEN", "modelscope-private")
    monkeypatch.setenv("HTTPS_PROXY", "https://user:password@proxy.invalid")
    captured: dict = {}

    def runner(arguments, **kwargs):
        captured["arguments"] = arguments
        captured.update(kwargs)
        return _helper_result(
            arguments,
            {"status": "available", "version": "1.0.0"},
        )

    report = run_runtime_diagnostics(
        ["fastapi"],
        paths=_paths(tmp_path),
        command_runner=runner,
    )

    assert report.results[0].status is DiagnosticStatus.AVAILABLE
    environment = captured["env"]
    for key in (
        "OPENAI_API_KEY",
        "HF_TOKEN",
        "GITHUB_PAT",
        "MODELSCOPE_API_TOKEN",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
    ):
        assert key not in environment
    assert captured["stdin"] == subprocess.DEVNULL
    assert captured["shell"] is False
    assert captured["creationflags"] == int(
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )
    assert "PLAYWRIGHT_BROWSERS_PATH" not in environment


def test_chromium_probe_uses_controlled_runtime_browser_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setenv(
        "PLAYWRIGHT_BROWSERS_PATH",
        str((tmp_path.parent / "user-controlled-browsers").resolve()),
    )
    captured: dict = {}

    def runner(arguments, **kwargs):
        captured["arguments"] = arguments
        captured.update(kwargs)
        return _helper_result(
            arguments,
            {"status": "available", "version": "1.57.0"},
        )

    report = run_runtime_diagnostics(
        ["chromium"],
        paths=paths,
        trusted_components=_signed_component_registry(paths),
        command_runner=runner,
    )

    assert report.results[0].status is DiagnosticStatus.AVAILABLE
    assert captured["env"]["PLAYWRIGHT_BROWSERS_PATH"] == str(
        (paths.cache_dir / "ms-playwright").resolve()
    )
    assert captured["arguments"][4] == "chromium"
    assert len(captured["arguments"]) == 8


def test_chromium_probe_requires_signed_component_manifest(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def forbidden_runner(arguments, **kwargs):
        del kwargs
        calls.append(arguments)
        raise AssertionError("unsigned Chromium was executed")

    report = run_runtime_diagnostics(
        ["chromium"],
        paths=_paths(tmp_path),
        command_runner=forbidden_runner,
    )

    assert calls == []
    assert report.results[0].status is DiagnosticStatus.UNAVAILABLE
    assert result_detail(report, "errorCode") == "component-trust-root-unavailable"


def test_chromium_probe_rejects_mutated_signed_component_tree(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    registry = _signed_component_registry(paths)
    assert registry.manifest_root is not None
    executable = registry.manifest_root / "chromium-test" / "chrome.exe"
    executable.write_bytes(b"mutated chromium payload")

    report = run_runtime_diagnostics(
        ["chromium"],
        paths=paths,
        trusted_components=registry,
        timeout_seconds=5,
    )

    assert report.results[0].status is DiagnosticStatus.UNTRUSTED
    assert result_detail(report, "errorCode") == "component-integrity-failed"


def test_chromium_probe_rejects_manifest_hash_mismatch(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    registry = _signed_component_registry(paths)
    mismatched_registry = TrustedComponentRegistry(
        manifest_root=registry.manifest_root,
        manifest_path=registry.manifest_path,
        manifest_sha256="0" * 64,
        manifest_signature_verified=True,
    )

    report = run_runtime_diagnostics(
        ["chromium"],
        paths=paths,
        trusted_components=mismatched_registry,
        timeout_seconds=5,
    )

    assert report.results[0].status is DiagnosticStatus.UNTRUSTED
    assert result_detail(report, "errorCode") == "component-integrity-failed"


def test_chromium_registry_root_must_be_runtime_browser_cache(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    registry = _signed_component_registry(paths)
    assert registry.manifest_path is not None
    outside = (tmp_path / "other-component-root").resolve()
    outside.mkdir()
    outside_manifest = outside / "component-tree.json"
    outside_manifest.write_bytes(registry.manifest_path.read_bytes())
    outside_registry = TrustedComponentRegistry(
        manifest_root=outside,
        manifest_path=outside_manifest,
        manifest_sha256=_sha256(outside_manifest),
        manifest_signature_verified=True,
    )

    with pytest.raises(DiagnosticSelectionError, match="cache/ms-playwright"):
        run_runtime_diagnostics(
            ["chromium"],
            paths=paths,
            trusted_components=outside_registry,
        )


def test_chromium_probe_rejects_supplied_browser_path_outside_runtime_cache(
    tmp_path: Path,
) -> None:
    with pytest.raises(DiagnosticSelectionError, match="runtime cache"):
        run_runtime_diagnostics(
            ["chromium"],
            paths=_paths(tmp_path),
            playwright_browsers_path=(tmp_path.parent / "outside-cache").resolve(),
        )


def test_chromium_probe_rejects_sibling_directory_inside_runtime_cache(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)

    with pytest.raises(DiagnosticSelectionError, match="ms-playwright"):
        run_runtime_diagnostics(
            ["chromium"],
            paths=paths,
            playwright_browsers_path=(paths.cache_dir / "other-browser").resolve(),
        )


@pytest.mark.parametrize("probe_id", ["disk", "writable-paths"])
def test_filesystem_helper_timeout_terminates_blocking_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_id: str,
) -> None:
    from app.services import runtime_diagnostics as diagnostics_module

    monkeypatch.setattr(
        diagnostics_module,
        "_HELPER_PROGRAM",
        "import time; time.sleep(60)",
    )
    started = time.monotonic()

    report = run_runtime_diagnostics(
        [probe_id],
        paths=_paths(tmp_path),
        timeout_seconds=0.1,
        total_timeout_seconds=1.0,
    )

    assert time.monotonic() - started < 5
    assert report.results[0].status is DiagnosticStatus.TIMEOUT


def test_filesystem_probe_does_not_resolve_configured_paths_in_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    original_resolve = Path.resolve

    def guarded_resolve(path: Path, *args, **kwargs) -> Path:
        if path == paths.cache_dir or paths.cache_dir in path.parents:
            raise AssertionError("configured cache path was resolved in parent")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", guarded_resolve)

    def runner(arguments, **kwargs):
        del kwargs
        return _helper_result(
            arguments,
            {
                "status": "available",
                "cacheFreeBytes": 1,
                "cacheTotalBytes": 2,
                "configFreeBytes": 1,
                "configTotalBytes": 2,
                "dataFreeBytes": 1,
                "dataTotalBytes": 2,
                "logsFreeBytes": 1,
                "logsTotalBytes": 2,
            },
        )

    report = run_runtime_diagnostics(
        ["disk"],
        paths=paths,
        command_runner=runner,
    )

    assert report.results[0].status is DiagnosticStatus.AVAILABLE


def test_prebootstrap_binary_probe_is_unavailable_and_never_executes(
    tmp_path: Path,
) -> None:
    def forbidden_runner(arguments, **kwargs):
        raise AssertionError(f"untrusted binary executed: {arguments!r} {kwargs!r}")

    report = run_runtime_diagnostics(
        ["uv"],
        paths=_paths(tmp_path),
        which=lambda name: str(tmp_path / "user-writable" / "uv.exe"),
        command_runner=forbidden_runner,
    )

    assert report.results[0].status is DiagnosticStatus.UNAVAILABLE
    assert result_detail(report, "errorCode") == "trust-root-unavailable"


def test_missing_unregistered_binary_is_unavailable(tmp_path: Path) -> None:
    report = run_runtime_diagnostics(
        ["ffmpeg"],
        paths=_paths(tmp_path),
        which=lambda name: None,
    )

    assert report.results[0].status is DiagnosticStatus.UNAVAILABLE


def test_unverified_manifest_registry_never_executes_binary(tmp_path: Path) -> None:
    executable = (tmp_path / "runtime" / "uv.exe").resolve()
    executable.parent.mkdir()
    executable.write_bytes(b"fixture")

    def forbidden_runner(arguments, **kwargs):
        raise AssertionError(f"unverified binary executed: {arguments} {kwargs}")

    registry = TrustedBinaryRegistry(
        manifest_root=executable.parent,
        manifest_sha256="b" * 64,
        manifest_signature_verified=False,
        binaries=(TrustedBinary("uv", executable, _sha256(executable)),),
    )
    report = run_runtime_diagnostics(
        ["uv"],
        paths=_paths(tmp_path),
        trusted_binaries=registry,
        command_runner=forbidden_runner,
    )

    assert report.results[0].status is DiagnosticStatus.UNAVAILABLE
    assert result_detail(report, "errorCode") == "trust-root-unavailable"


def test_trusted_registry_rejects_binary_outside_signed_root(tmp_path: Path) -> None:
    root = (tmp_path / "runtime").resolve()
    root.mkdir()
    executable = (tmp_path / "outside" / "uv.exe").resolve()
    executable.parent.mkdir()
    executable.write_bytes(b"fixture")

    with pytest.raises(DiagnosticSelectionError, match="escapes"):
        _signed_registry(
            root,
            TrustedBinary("uv", executable, _sha256(executable)),
        )


def test_trusted_binary_requires_sha256(tmp_path: Path) -> None:
    executable = (tmp_path / "uv.exe").resolve()

    with pytest.raises(DiagnosticSelectionError, match="SHA-256"):
        TrustedBinary("uv", executable, "")


def test_trusted_binary_uses_fixed_args_minimal_env_and_strict_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = (tmp_path / "runtime" / "uv.exe").resolve()
    executable.parent.mkdir()
    executable.write_bytes(b"trusted uv fixture")
    monkeypatch.setenv("OPENAI_API_KEY", "private")
    calls: list[tuple[list[str], dict]] = []

    def runner(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return _helper_result(
            arguments,
            {"status": "available", "version": "0.9.21"},
        )

    report = run_runtime_diagnostics(
        ["uv"],
        paths=_paths(tmp_path),
        trusted_binaries=_signed_registry(
            executable.parent,
            TrustedBinary(
                "uv",
                executable,
                _sha256(executable),
                version_specifier="==0.9.21",
            ),
        ),
        which=lambda name: "/must/not/be/used",
        command_runner=runner,
    )

    result = report.results[0]
    assert result.status is DiagnosticStatus.AVAILABLE
    assert result.to_dict()["details"]["installedVersion"] == "0.9.21"
    assert calls[0][0][:2] == [sys.executable, "-I"]
    assert calls[0][0][4:7] == ["__trusted-binary", "uv", str(executable)]
    assert calls[0][0][-2:] == [str(executable.parent), _sha256(executable)]
    assert "OPENAI_API_KEY" not in calls[0][1]["env"]
    assert calls[0][1]["cwd"] == Path(sys.executable).parent


def test_trusted_binary_paths_are_resolved_only_inside_bounded_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = (tmp_path / "runtime" / "uv.exe").resolve()
    executable.parent.mkdir()
    executable.write_bytes(b"trusted uv fixture")
    registry = _signed_registry(
        executable.parent,
        TrustedBinary("uv", executable, _sha256(executable)),
    )
    original_resolve = Path.resolve

    def guarded_resolve(path: Path, *args, **kwargs) -> Path:
        if path in {executable, executable.parent}:
            raise AssertionError("trusted binary path resolved in parent process")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", guarded_resolve)
    report = run_runtime_diagnostics(
        ["uv"],
        paths=_paths(tmp_path),
        trusted_binaries=registry,
        command_runner=lambda arguments, **kwargs: _helper_result(
            arguments,
            {"status": "available", "version": "0.9.21"},
        ),
    )

    assert report.results[0].status is DiagnosticStatus.AVAILABLE


def test_binary_report_never_contains_raw_credential_output(tmp_path: Path) -> None:
    executable = (tmp_path / "uv.exe").resolve()
    executable.write_bytes(b"fixture")
    output = (
        'uv 0.9.21 {"token":"json-supersecret"} '
        'password: "yaml-supersecret" '
        "GITHUB_PAT=ghp_supersecret HF_TOKEN=hf_supersecret "
        "MODELSCOPE_API_TOKEN=modelscope-supersecret "
        "HTTPS_PROXY=https://user:proxy-password@proxy.invalid"
    )
    report = run_runtime_diagnostics(
        ["uv"],
        paths=_paths(tmp_path),
        trusted_binaries=_signed_registry(
            executable.parent,
            TrustedBinary("uv", executable, _sha256(executable)),
        ),
        command_runner=lambda arguments, **kwargs: subprocess.CompletedProcess(
            arguments,
            0,
            json.dumps({"status": "available", "version": "0.9.21"}),
            output + ' Authorization: "Bearer another-secret"',
        ),
    )

    encoded = json.dumps(report.to_dict())
    assert report.results[0].status is DiagnosticStatus.AVAILABLE
    for secret in (
        "json-supersecret",
        "yaml-supersecret",
        "ghp_supersecret",
        "hf_supersecret",
        "modelscope-supersecret",
        "proxy-password",
        "another-secret",
    ):
        assert secret not in encoded


def test_binary_output_limit_is_fixed_and_does_not_disclose_content(
    tmp_path: Path,
) -> None:
    executable = (tmp_path / "runtime" / "uv.exe").resolve()
    executable.parent.mkdir()
    executable.write_bytes(b"fixture")
    secret = "binary-private-output"
    report = run_runtime_diagnostics(
        ["uv"],
        paths=_paths(tmp_path),
        trusted_binaries=_signed_registry(
            executable.parent,
            TrustedBinary("uv", executable, _sha256(executable)),
        ),
        command_runner=lambda arguments, **kwargs: subprocess.CompletedProcess(
            arguments,
            0,
            "uv 0.9.21\n" + secret + ("x" * 9000),
            "",
        ),
    )

    encoded = json.dumps(report.to_dict())
    assert report.results[0].status is DiagnosticStatus.ERROR
    assert result_detail(report, "errorCode") == "output-limit-exceeded"
    assert secret not in encoded


def test_invalid_binary_output_is_fixed_error_without_echo(tmp_path: Path) -> None:
    executable = (tmp_path / "uv.exe").resolve()
    executable.write_bytes(b"fixture")
    report = run_runtime_diagnostics(
        ["uv"],
        paths=_paths(tmp_path),
        trusted_binaries=_signed_registry(
            executable.parent,
            TrustedBinary("uv", executable, _sha256(executable)),
        ),
        command_runner=lambda arguments, **kwargs: _helper_result(
            arguments,
            {
                "status": "unavailable",
                "errorCode": "invalid-version-output",
            },
        ),
    )

    encoded = json.dumps(report.to_dict())
    assert report.results[0].status is DiagnosticStatus.ERROR
    assert "private-output" not in encoded
    assert result_detail(report, "errorCode") == "invalid-version-output"


def result_detail(report, key: str):
    return report.results[0].to_dict()["details"][key]


def test_trusted_binary_hash_mismatch_is_rejected_inside_bounded_helper(
    tmp_path: Path,
) -> None:
    executable = (tmp_path / "uv.exe").resolve()
    executable.write_bytes(b"fixture")

    report = run_runtime_diagnostics(
        ["uv"],
        paths=_paths(tmp_path),
        trusted_binaries=_signed_registry(
            executable.parent,
            TrustedBinary("uv", executable, sha256="0" * 64),
        ),
    )

    assert report.results[0].status is DiagnosticStatus.UNTRUSTED
    assert result_detail(report, "errorCode") == "hash-mismatch"


def test_trusted_binary_version_mismatch_is_degraded(tmp_path: Path) -> None:
    executable = (tmp_path / "uv.exe").resolve()
    executable.write_bytes(b"fixture")
    report = run_runtime_diagnostics(
        ["uv"],
        paths=_paths(tmp_path),
        trusted_binaries=_signed_registry(
            executable.parent,
            TrustedBinary(
                "uv",
                executable,
                _sha256(executable),
                version_specifier="==0.9.21",
            ),
        ),
        command_runner=lambda arguments, **kwargs: subprocess.CompletedProcess(
            arguments,
            0,
            json.dumps({"status": "available", "version": "0.10.0"}),
            "",
        ),
    )

    assert report.results[0].status is DiagnosticStatus.DEGRADED
    assert result_detail(report, "versionState") == "incompatible"


def test_trusted_binary_timeout_is_bounded(tmp_path: Path) -> None:
    executable = (tmp_path / "ffmpeg.exe").resolve()
    executable.write_bytes(b"fixture")
    observed: list[tuple[list[str], float]] = []

    def runner(arguments, **kwargs):
        observed.append((arguments, kwargs["timeout"]))
        raise subprocess.TimeoutExpired(arguments, kwargs["timeout"])

    report = run_runtime_diagnostics(
        ["ffmpeg"],
        paths=_paths(tmp_path),
        trusted_binaries=_signed_registry(
            executable.parent,
            TrustedBinary("ffmpeg", executable, _sha256(executable)),
        ),
        command_runner=runner,
        timeout_seconds=10,
        total_timeout_seconds=0.25,
    )

    assert report.results[0].status is DiagnosticStatus.TIMEOUT
    assert observed[0][0][4] == "__trusted-binary"
    assert 0 < observed[0][1] <= 0.25
    assert result_detail(report, "timeoutScope") == "total"


def test_onnx_cuda_probe_requires_cuda_provider_flavor(tmp_path: Path) -> None:
    report = run_runtime_diagnostics(
        ["onnx-cuda-provider"],
        paths=_paths(tmp_path),
        command_runner=lambda arguments, **kwargs: _helper_result(
            arguments,
            {
                "status": "available",
                "version": "1.23.2",
                "providerFlavor": "cpu",
                "distribution": "onnxruntime",
            },
        ),
    )

    assert report.results[0].status is DiagnosticStatus.UNAVAILABLE
    assert result_detail(report, "errorCode") == "onnx-smoke-failed"


@pytest.mark.parametrize(
    ("probe_id", "provider_flavor", "distribution"),
    [
        ("onnx-cpu-provider", "cpu", "onnxruntime"),
        ("onnx-cuda-provider", "cuda", "onnxruntime-gpu"),
    ],
)
def test_onnx_probe_enforces_distribution_and_provider_contract(
    tmp_path: Path,
    probe_id: str,
    provider_flavor: str,
    distribution: str,
) -> None:
    report = run_runtime_diagnostics(
        [probe_id],
        paths=_paths(tmp_path),
        command_runner=lambda arguments, **kwargs: _helper_result(
            arguments,
            {
                "status": "available",
                "version": "1.23.2",
                "providerFlavor": provider_flavor,
                "distribution": distribution,
            },
        ),
    )

    assert report.results[0].status is DiagnosticStatus.AVAILABLE
    assert result_detail(report, "providerFlavor") == provider_flavor
    assert result_detail(report, "distribution") == distribution


def test_onnx_cpu_probe_rejects_gpu_distribution(tmp_path: Path) -> None:
    report = run_runtime_diagnostics(
        ["onnx-cpu-provider"],
        paths=_paths(tmp_path),
        command_runner=lambda arguments, **kwargs: _helper_result(
            arguments,
            {
                "status": "available",
                "version": "1.23.2",
                "providerFlavor": "cpu",
                "distribution": "onnxruntime-gpu",
            },
        ),
    )

    assert report.results[0].status is DiagnosticStatus.UNAVAILABLE
    assert result_detail(report, "errorCode") == "onnx-smoke-failed"


def test_generic_onnx_probe_rejects_mismatched_provider_distribution(
    tmp_path: Path,
) -> None:
    report = run_runtime_diagnostics(
        ["onnx-provider"],
        paths=_paths(tmp_path),
        command_runner=lambda arguments, **kwargs: _helper_result(
            arguments,
            {
                "status": "available",
                "version": "1.23.2",
                "providerFlavor": "cuda",
                "distribution": "onnxruntime",
            },
        ),
    )

    assert report.results[0].status is DiagnosticStatus.UNAVAILABLE
    assert result_detail(report, "errorCode") == "onnx-smoke-failed"


def test_module_version_expectation_prevents_old_package_green(tmp_path: Path) -> None:
    report = run_runtime_diagnostics(
        ["transformers"],
        paths=_paths(tmp_path),
        version_expectations={"transformers": ">=4.45"},
        command_runner=lambda arguments, **kwargs: _helper_result(
            arguments,
            {"status": "available", "version": "4.20.0"},
        ),
    )

    assert report.results[0].status is DiagnosticStatus.DEGRADED
    assert result_detail(report, "versionState") == "incompatible"


def test_exact_transitive_version_expectation_prevents_lock_drift_green(
    tmp_path: Path,
) -> None:
    report = run_runtime_diagnostics(
        ["safetensors"],
        paths=_paths(tmp_path),
        version_expectations={"safetensors": "==0.7.0"},
        command_runner=lambda arguments, **kwargs: _helper_result(
            arguments,
            {"status": "available", "version": "0.6.2"},
        ),
    )

    assert report.results[0].status is DiagnosticStatus.DEGRADED
    assert result_detail(report, "installedVersion") == "0.6.2"
    assert result_detail(report, "versionState") == "incompatible"


def test_malformed_helper_output_cannot_leak_secrets(tmp_path: Path) -> None:
    raw = (
        '{"status":"available","version":"hf_private-token",'
        '"token":"ghp_private-token","proxy":"user:password"}'
    )
    report = run_runtime_diagnostics(
        ["openai"],
        paths=_paths(tmp_path),
        command_runner=lambda arguments, **kwargs: subprocess.CompletedProcess(
            arguments, 0, raw, ""
        ),
    )

    encoded = json.dumps(report.to_dict())
    assert report.results[0].status is DiagnosticStatus.ERROR
    assert "hf_private-token" not in encoded
    assert "ghp_private-token" not in encoded
    assert "user:password" not in encoded


def test_helper_version_field_cannot_smuggle_high_entropy_token(
    tmp_path: Path,
) -> None:
    secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    report = run_runtime_diagnostics(
        ["openai"],
        paths=_paths(tmp_path),
        command_runner=lambda arguments, **kwargs: _helper_result(
            arguments,
            {"status": "available", "version": f"1.0.0+{secret}"},
        ),
    )

    encoded = json.dumps(report.to_dict())
    assert report.results[0].status is DiagnosticStatus.ERROR
    assert secret not in encoded


@pytest.mark.parametrize(
    "version",
    [
        "1." + ("9" * 13) + ".0",
        "1.0." + ("9" * 65),
    ],
)
def test_helper_version_rejects_unbounded_digit_segments(
    tmp_path: Path,
    version: str,
) -> None:
    report = run_runtime_diagnostics(
        ["openai"],
        paths=_paths(tmp_path),
        command_runner=lambda arguments, **kwargs: _helper_result(
            arguments,
            {"status": "available", "version": version},
        ),
    )

    assert report.results[0].status is DiagnosticStatus.ERROR
    assert result_detail(report, "errorCode") == "invalid-helper-output"


def test_version_expectation_rejects_unbounded_digit_segment(
    tmp_path: Path,
) -> None:
    with pytest.raises(DiagnosticSelectionError, match="exceeds limits"):
        run_runtime_diagnostics(
            ["python"],
            paths=_paths(tmp_path),
            version_expectations={"python": ">=" + ("9" * 13)},
        )


def test_runner_exception_message_is_not_disclosed(tmp_path: Path) -> None:
    def failing_runner(arguments, **kwargs):
        raise RuntimeError('{"token":"private-diagnostic-token"}')

    report = run_runtime_diagnostics(
        ["transformers"],
        paths=_paths(tmp_path),
        command_runner=failing_runner,
    )

    encoded = json.dumps(report.to_dict())
    assert report.results[0].status is DiagnosticStatus.ERROR
    assert "private-diagnostic-token" not in encoded


def test_helper_output_limit_is_fixed_and_does_not_disclose_content(
    tmp_path: Path,
) -> None:
    secret = "private-output-token"
    oversized = secret + ("x" * 9000)
    report = run_runtime_diagnostics(
        ["openai"],
        paths=_paths(tmp_path),
        command_runner=lambda arguments, **kwargs: subprocess.CompletedProcess(
            arguments,
            0,
            oversized,
            "",
        ),
    )

    encoded = json.dumps(report.to_dict())
    assert report.results[0].status is DiagnosticStatus.ERROR
    assert result_detail(report, "errorCode") == "output-limit-exceeded"
    assert secret not in encoded


def test_total_deadline_marks_remaining_probes_timeout(tmp_path: Path) -> None:
    moments = iter((0.0, 0.0, 2.0))
    report = run_runtime_diagnostics(
        ["configuration", "python"],
        paths=_paths(tmp_path),
        total_timeout_seconds=1.0,
        clock=lambda: next(moments),
    )

    statuses = {result.probe_id: result.status for result in report.results}
    assert statuses["configuration"] is DiagnosticStatus.AVAILABLE
    assert statuses["python"] is DiagnosticStatus.TIMEOUT
    python_result = next(
        result for result in report.results if result.probe_id == "python"
    )
    assert python_result.to_dict()["details"]["timeoutScope"] == "total"


def test_remaining_total_deadline_caps_helper_timeout(tmp_path: Path) -> None:
    moments = iter((0.0, 0.75))
    observed_timeout: list[float] = []

    def runner(arguments, **kwargs):
        observed_timeout.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(arguments, kwargs["timeout"])

    report = run_runtime_diagnostics(
        ["fastapi"],
        paths=_paths(tmp_path),
        timeout_seconds=10.0,
        total_timeout_seconds=1.0,
        clock=lambda: next(moments),
        command_runner=runner,
    )

    assert observed_timeout == [0.25]
    assert report.results[0].status is DiagnosticStatus.TIMEOUT
    assert result_detail(report, "timeoutScope") == "total"


@pytest.mark.parametrize(
    ("probe_ids", "timeout"),
    [
        ([], 1),
        (["unknown"], 1),
        (["python", "python"], 1),
        ("python", 1),
        (["python"], 0),
        (["python"], 31),
        (["python"], float("nan")),
        (["python"], float("inf")),
        (["python"], float("-inf")),
    ],
)
def test_diagnostics_rejects_empty_unknown_or_unbounded_selection(
    tmp_path: Path,
    probe_ids,
    timeout,
) -> None:
    with pytest.raises(DiagnosticSelectionError):
        run_runtime_diagnostics(
            probe_ids,
            paths=_paths(tmp_path),
            timeout_seconds=timeout,
        )


@pytest.mark.parametrize(
    "total_timeout",
    [0, 301, float("nan"), float("inf"), float("-inf"), True],
)
def test_diagnostics_rejects_invalid_total_timeout(
    tmp_path: Path,
    total_timeout,
) -> None:
    with pytest.raises(DiagnosticSelectionError, match="total_timeout_seconds"):
        run_runtime_diagnostics(
            ["python"],
            paths=_paths(tmp_path),
            total_timeout_seconds=total_timeout,
        )


def test_version_expectations_reject_unknown_or_invalid_input(tmp_path: Path) -> None:
    with pytest.raises(DiagnosticSelectionError):
        run_runtime_diagnostics(
            ["python"],
            paths=_paths(tmp_path),
            version_expectations={"unknown": ">=1"},
        )
    with pytest.raises(DiagnosticSelectionError):
        run_runtime_diagnostics(
            ["python"],
            paths=_paths(tmp_path),
            version_expectations={"python": "not-a-specifier"},
        )


def test_disk_probe_failure_does_not_disclose_path_or_exception(
    tmp_path: Path,
) -> None:
    report = run_runtime_diagnostics(
        ["disk"],
        paths=_paths(tmp_path),
        command_runner=lambda arguments, **kwargs: _helper_result(
            arguments,
            {
                "status": "degraded",
                "unavailableScopes": ["cache", "config", "data", "logs"],
            },
        ),
    )

    encoded = json.dumps(report.to_dict())
    assert report.results[0].status is DiagnosticStatus.DEGRADED
    assert str(tmp_path) not in encoded
    assert "private path failure" not in encoded


def test_real_fastapi_helper_process_isolated_smoke(tmp_path: Path) -> None:
    report = run_runtime_diagnostics(
        ["fastapi"],
        paths=_paths(tmp_path),
        timeout_seconds=10,
        version_expectations={"fastapi": ">=0.115"},
    )

    assert report.results[0].status is DiagnosticStatus.AVAILABLE


def test_real_pillow_helper_decodes_jpeg_png_and_webp(tmp_path: Path) -> None:
    report = run_runtime_diagnostics(
        ["pillow"],
        paths=_paths(tmp_path),
        timeout_seconds=10,
        version_expectations={"pillow": ">=12.1.0"},
    )

    result = report.results[0]
    assert result.status is DiagnosticStatus.AVAILABLE
    assert result.to_dict()["details"]["jpeg"] is True
    assert result.to_dict()["details"]["png"] is True
    assert result.to_dict()["details"]["webp"] is True
