from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import Response
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app import version as version_module  # noqa: E402
from app.version import APP_VERSION, get_app_version  # noqa: E402

SET_VERSION_PATH = ROOT / "scripts" / "set-version.py"
SET_VERSION_SPEC = importlib.util.spec_from_file_location(
    "set_version_script",
    SET_VERSION_PATH,
)
assert SET_VERSION_SPEC is not None and SET_VERSION_SPEC.loader is not None
set_version_script = importlib.util.module_from_spec(SET_VERSION_SPEC)
sys.modules[SET_VERSION_SPEC.name] = set_version_script
SET_VERSION_SPEC.loader.exec_module(set_version_script)


def _run_script(script: str, *args: str, root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *args, "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _write_release_fixture(root: Path) -> None:
    (root / "web" / "src-tauri").mkdir(parents=True)
    (root / "web" / "src" / "generated").mkdir(parents=True)
    (root / "android" / "app").mkdir(parents=True)
    (root / "VERSION").write_text("0.4.1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "MediaProcessPipeline"\nversion = "0.4.1"\n\n'
        '[tool.example]\nversion = "leave-me"\n',
        encoding="utf-8",
    )
    (root / "uv.lock").write_text(
        'version = 1\nrevision = 3\nrequires-python = ">=3.11, <3.13"\n\n'
        '[[package]]\nname = "dependency"\nversion = "0.4.1"\n'
        'source = { registry = "https://pypi.org/simple" }\n\n'
        '[[package]]\nname = "mediaprocesspipeline"\nversion = "0.4.1"\n'
        'source = { virtual = "." }\ndependencies = [{ name = "dependency" }]\n',
        encoding="utf-8",
    )
    (root / "web" / "src-tauri" / "Cargo.toml").write_text(
        '[package]\nname = "mpp-desktop"\nversion = "0.4.1"\n\n'
        '[dependencies]\nexample = "0.4.1"\n',
        encoding="utf-8",
    )
    (root / "web" / "src-tauri" / "Cargo.lock").write_text(
        'version = 4\n\n[[package]]\nname = "dependency"\nversion = "0.4.1"\n\n'
        '[[package]]\nname = "mpp-desktop"\nversion = "0.4.1"\n'
        'dependencies = ["dependency"]\n',
        encoding="utf-8",
    )
    (root / "web" / "src-tauri" / "tauri.conf.json").write_text(
        json.dumps({"productName": "MediaProcessPipeline", "version": "0.4.1"}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    (root / "web" / "src" / "generated" / "app-version.ts").write_text(
        '// Generated fixture\nexport const APP_VERSION = "0.4.1" as const\n',
        encoding="utf-8",
    )
    (root / "android" / "app" / "build.gradle.kts").write_text(
        'android {\n    defaultConfig {\n        versionCode = 2\n'
        '        versionName = "0.4.1"\n    }\n}\n',
        encoding="utf-8",
    )


def test_checked_in_version_is_backend_identity() -> None:
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == APP_VERSION


def test_app_version_prefers_launcher_override() -> None:
    assert (
        get_app_version(environ={"MPP_APP_VERSION": "1.2.3-rc.4+desktop"})
        == "1.2.3-rc.4+desktop"
    )


def test_app_version_reads_installed_runtime_resource(tmp_path: Path) -> None:
    runtime_root = tmp_path / "resources" / "runtime"
    module_path = runtime_root / "backend" / "app" / "version.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text("# installed module placeholder\n", encoding="utf-8")
    (runtime_root / "VERSION").write_text("2.3.4\n", encoding="utf-8")

    assert get_app_version(environ={}, module_path=module_path) == "2.3.4"


def test_app_version_rejects_invalid_launcher_override() -> None:
    with pytest.raises(RuntimeError, match="Invalid application version"):
        get_app_version(environ={"MPP_APP_VERSION": "v0.4.1"})


def test_app_version_restores_pep440_normalized_prerelease_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module_path = tmp_path / "site-packages" / "app" / "version.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text("# installed module placeholder\n", encoding="utf-8")
    monkeypatch.setattr(
        version_module,
        "distribution_version",
        lambda _name: "1.2.3rc4+desktop",
    )

    assert (
        get_app_version(environ={}, module_path=module_path)
        == "1.2.3-rc.4+desktop"
    )


def test_health_endpoint_reports_canonical_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main as app_main

    monkeypatch.setattr(app_main, "_DESKTOP_SESSION_SECRET", "")
    client = TestClient(app_main.app)
    try:
        response = client.get("/health")
    finally:
        client.close()
    payload = response.json()

    assert response.status_code == 200
    assert payload["version"] == APP_VERSION
    assert payload["product"] == "com.mpp.backend"
    assert payload["protocol"] == 1
    assert app_main.app.version == APP_VERSION


def test_health_endpoint_offers_desktop_hmac_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main as app_main

    secret = "ab" * 32
    nonce = "cd" * 32
    monkeypatch.setattr(app_main, "_DESKTOP_SESSION_SECRET", secret)
    client = TestClient(app_main.app)
    try:
        missing = client.get("/health")
        leaked_secret_header = client.get(
            "/health",
            headers={"X-MPP-Desktop-Session": secret},
        )
        malformed_nonce = client.get(
            "/health",
            headers={"X-MPP-Desktop-Nonce": "not-a-valid-nonce"},
        )
        accepted = client.get(
            "/health",
            headers={"X-MPP-Desktop-Nonce": nonce},
        )
    finally:
        client.close()

    assert missing.status_code == 200
    assert missing.json().get("desktopProof") is None
    assert leaked_secret_header.status_code == 200
    assert leaked_secret_header.json().get("desktopProof") is None
    assert malformed_nonce.status_code == 401
    assert accepted.status_code == 200
    assert accepted.headers["cache-control"] == "no-store"
    assert accepted.json() == {
        "status": "healthy",
        "service": app_main.DESKTOP_HEALTH_SERVICE,
        "version": APP_VERSION,
        "product": "com.mpp.backend",
        "protocol": 1,
        "desktopProof": app_main._desktop_health_proof(secret, nonce),
    }


def test_desktop_health_hmac_golden_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main as app_main

    monkeypatch.setattr(app_main, "APP_VERSION", "0.4.1")

    assert app_main._desktop_health_proof("ab" * 32, "cd" * 32) == (
        "290970beb86e5dc6846601100b0fd0a5419b0e612582912686e9570605b24e25"
    )


def test_desktop_session_authenticates_private_api_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main as app_main

    class _Settings:
        api_token = "configured-api-token"

    desktop_secret = "ef" * 32
    monkeypatch.setattr(app_main, "_DESKTOP_SESSION_SECRET", desktop_secret)
    monkeypatch.setattr(app_main, "get_runtime_settings", lambda: _Settings())
    monkeypatch.delenv("MPP_REQUIRE_API_TOKEN", raising=False)
    shutdown_calls: list[bool] = []
    app_main.app.state.request_desktop_shutdown = lambda: shutdown_calls.append(True)

    async def desktop_auth_contract() -> Response:
        return Response(status_code=204)

    contract_route = APIRoute(
        "/api/desktop-auth-contract",
        desktop_auth_contract,
        methods=["GET", "POST"],
    )
    app_main.app.router.routes.insert(0, contract_route)
    client = TestClient(app_main.app)
    try:
        missing = client.get("/api/desktop-auth-contract")
        wrong = client.get(
            "/api/desktop-auth-contract",
            headers={"X-MPP-Desktop-Session": "00" * 32},
        )
        accepted = client.get(
            "/api/desktop-auth-contract",
            headers={"X-MPP-Desktop-Session": desktop_secret},
        )
        csrf_blocked = client.post(
            "/api/desktop-auth-contract",
            headers={"X-MPP-Desktop-Session": desktop_secret},
        )
        mutating_accepted = client.post(
            "/api/desktop-auth-contract",
            headers={
                "X-MPP-Desktop-Session": desktop_secret,
                "X-Requested-With": "fetch",
            },
        )
        shutdown_missing_session = client.post(
            app_main._DESKTOP_SHUTDOWN_PATH,
            headers={"X-Requested-With": "fetch"},
        )
        shutdown_accepted = client.post(
            app_main._DESKTOP_SHUTDOWN_PATH,
            headers={
                "X-MPP-Desktop-Session": desktop_secret,
                "X-Requested-With": "fetch",
            },
        )
    finally:
        client.close()
        app_main.app.router.routes.remove(contract_route)
        del app_main.app.state.request_desktop_shutdown

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert accepted.status_code == 204
    assert csrf_blocked.status_code == 403
    assert mutating_accepted.status_code == 204
    assert shutdown_missing_session.status_code == 401
    assert shutdown_accepted.status_code == 200
    assert shutdown_calls == [True]


def test_set_and_check_version_scripts_keep_android_code_explicit(tmp_path: Path) -> None:
    _write_release_fixture(tmp_path)

    first = _run_script(
        "set-version.py",
        "1.2.3-rc.4+desktop",
        root=tmp_path,
    )
    assert first.returncode == 0, first.stderr
    android = (tmp_path / "android" / "app" / "build.gradle.kts").read_text(
        encoding="utf-8"
    )
    assert 'versionName = "1.2.3-rc.4+desktop"' in android
    assert "versionCode = 2" in android
    assert 'example = "0.4.1"' in (
        tmp_path / "web" / "src-tauri" / "Cargo.toml"
    ).read_text(encoding="utf-8")
    uv_lock = (tmp_path / "uv.lock").read_text(encoding="utf-8")
    assert re.search(
        r'name = "dependency"\nversion = "0\.4\.1"',
        uv_lock,
    )
    assert re.search(
        r'name = "mediaprocesspipeline"\nversion = "1\.2\.3-rc\.4\+desktop"',
        uv_lock,
    )
    cargo_lock = (tmp_path / "web" / "src-tauri" / "Cargo.lock").read_text(
        encoding="utf-8"
    )
    assert re.search(
        r'name = "dependency"\nversion = "0\.4\.1"',
        cargo_lock,
    )
    assert re.search(
        r'name = "mpp-desktop"\nversion = "1\.2\.3-rc\.4\+desktop"',
        cargo_lock,
    )
    assert (
        'APP_VERSION = "1.2.3-rc.4+desktop"'
        in (tmp_path / "web" / "src" / "generated" / "app-version.ts").read_text(
            encoding="utf-8"
        )
    )

    checked = _run_script(
        "check-version.py",
        "--tag",
        "v1.2.3-rc.4+desktop",
        root=tmp_path,
    )
    assert checked.returncode == 0, checked.stderr

    second = _run_script(
        "set-version.py",
        "1.2.4",
        "--android-version-code",
        "9",
        root=tmp_path,
    )
    assert second.returncode == 0, second.stderr
    android = (tmp_path / "android" / "app" / "build.gradle.kts").read_text(
        encoding="utf-8"
    )
    assert 'versionName = "1.2.4"' in android
    assert "versionCode = 9" in android


def test_check_version_script_rejects_mismatch_and_wrong_tag(tmp_path: Path) -> None:
    _write_release_fixture(tmp_path)
    cargo = tmp_path / "web" / "src-tauri" / "Cargo.toml"
    cargo.write_text(
        cargo.read_text(encoding="utf-8").replace(
            'version = "0.4.1"',
            'version = "0.4.2"',
            1,
        ),
        encoding="utf-8",
    )

    mismatch = _run_script("check-version.py", root=tmp_path)
    assert mismatch.returncode == 1
    assert "does not match VERSION" in mismatch.stderr

    cargo.write_text(
        cargo.read_text(encoding="utf-8").replace(
            'version = "0.4.2"',
            'version = "0.4.1"',
            1,
        ),
        encoding="utf-8",
    )
    wrong_tag = _run_script(
        "check-version.py",
        "--tag",
        "v0.4.2",
        root=tmp_path,
    )
    assert wrong_tag.returncode == 1
    assert "does not match 'v0.4.1'" in wrong_tag.stderr


def test_version_scripts_accept_editable_uv_project_root(tmp_path: Path) -> None:
    _write_release_fixture(tmp_path)
    uv_lock = tmp_path / "uv.lock"
    uv_lock.write_text(
        uv_lock.read_text(encoding="utf-8").replace(
            'source = { virtual = "." }',
            'source = { editable = "." }',
        ),
        encoding="utf-8",
    )

    updated = _run_script("set-version.py", "1.4.0", root=tmp_path)
    checked = _run_script("check-version.py", "--tag", "v1.4.0", root=tmp_path)

    assert updated.returncode == 0, updated.stderr
    assert checked.returncode == 0, checked.stderr


def test_set_version_rolls_back_every_manifest_on_replace_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_release_fixture(tmp_path)
    manifests = [
        tmp_path / "VERSION",
        tmp_path / "pyproject.toml",
        tmp_path / "uv.lock",
        tmp_path / "web" / "src-tauri" / "Cargo.toml",
        tmp_path / "web" / "src-tauri" / "Cargo.lock",
        tmp_path / "web" / "src-tauri" / "tauri.conf.json",
        tmp_path / "web" / "src" / "generated" / "app-version.ts",
        tmp_path / "android" / "app" / "build.gradle.kts",
    ]
    before = {path: path.read_bytes() for path in manifests}
    real_replace = set_version_script.os.replace
    calls = 0

    def fail_third_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise PermissionError("simulated locked manifest")
        return real_replace(source, destination)

    monkeypatch.setattr(set_version_script.os, "replace", fail_third_replace)

    with pytest.raises(
        set_version_script.VersionUpdateError,
        match="all replaced files were restored",
    ):
        set_version_script.synchronize_version(tmp_path, "1.5.0", None)

    assert {path: path.read_bytes() for path in manifests} == before


def test_set_version_rejects_non_semver_without_writing(tmp_path: Path) -> None:
    _write_release_fixture(tmp_path)
    before = (tmp_path / "VERSION").read_bytes()

    invalid = _run_script("set-version.py", "1.2.3-01", root=tmp_path)

    assert invalid.returncode == 2
    assert "is not a valid SemVer" in invalid.stderr
    assert (tmp_path / "VERSION").read_bytes() == before


def test_check_version_rejects_non_semver_identity(tmp_path: Path) -> None:
    _write_release_fixture(tmp_path)
    (tmp_path / "VERSION").write_text("1.2.3-01\n", encoding="utf-8")

    invalid = _run_script("check-version.py", root=tmp_path)

    assert invalid.returncode == 1
    assert "invalid SemVer '1.2.3-01'" in invalid.stderr
