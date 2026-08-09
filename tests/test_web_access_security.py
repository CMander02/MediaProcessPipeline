from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.api.routes import auth as auth_route, filesystem, tasks  # noqa: E402
from app.cli import serve as serve_module  # noqa: E402
from app.core import settings as settings_module  # noqa: E402
from app.core.security import (  # noqa: E402
    SESSION_COOKIE_NAME,
    constant_time_token_matches,
    is_loopback_host,
)
from app.core.settings import RuntimeSettings  # noqa: E402


def _settings(tmp_path: Path, **updates) -> RuntimeSettings:
    return RuntimeSettings(data_root=str(tmp_path), **updates)


def test_loopback_and_constant_time_token_helpers():
    assert is_loopback_host("localhost")
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("::1")
    assert not is_loopback_host("0.0.0.0")
    assert not is_loopback_host("192.168.1.20")
    assert constant_time_token_matches("secret", "secret")
    assert not constant_time_token_matches("wrong", "secret")
    assert not constant_time_token_matches("", "secret")


def test_browser_unlock_uses_http_only_cookie(tmp_path, monkeypatch):
    runtime = _settings(tmp_path, api_token="secret-token")
    monkeypatch.setattr(auth_route, "get_runtime_settings", lambda: runtime)

    app = FastAPI()
    app.include_router(auth_route.router, prefix="/api")
    client = TestClient(app)

    initial = client.get("/api/auth/status")
    assert initial.status_code == 200
    assert initial.json() == {
        "required": True,
        "authenticated": False,
        "mode": "remote",
    }

    invalid = client.post("/api/auth/unlock", json={"token": "wrong"})
    assert invalid.status_code == 401

    unlocked = client.post("/api/auth/unlock", json={"token": "secret-token"})
    assert unlocked.status_code == 200
    assert unlocked.json()["authenticated"] is True
    assert client.cookies.get(SESSION_COOKIE_NAME) == "secret-token"
    set_cookie = unlocked.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie

    status = client.get("/api/auth/status")
    assert status.json()["authenticated"] is True

    logged_out = client.post("/api/auth/logout")
    assert logged_out.status_code == 200
    assert logged_out.json()["authenticated"] is False
    assert client.cookies.get(SESSION_COOKIE_NAME) is None


def test_android_unlock_creates_secure_cross_site_media_session(tmp_path, monkeypatch):
    runtime = _settings(tmp_path, api_token="secret-token")
    monkeypatch.setattr(auth_route, "get_runtime_settings", lambda: runtime)

    app = FastAPI()
    app.include_router(auth_route.router, prefix="/api")
    client = TestClient(app, base_url="https://mpp.example.com")

    unlocked = client.post(
        "/api/auth/unlock",
        json={"token": "secret-token", "client": "android"},
    )

    assert unlocked.status_code == 200
    set_cookie = unlocked.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "secure" in set_cookie
    assert "samesite=none" in set_cookie


def test_main_cors_allows_capacitor_https_origin(tmp_path, monkeypatch):
    from app import main as app_main

    runtime = _settings(tmp_path, api_token="")
    monkeypatch.setattr(settings_module, "_runtime_settings", runtime)
    monkeypatch.setattr(app_main, "get_runtime_settings", lambda: runtime)

    response = TestClient(app_main.app).options(
        "/api/capabilities",
        headers={
            "Origin": "https://localhost",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,x-requested-with",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://localhost"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_remote_capabilities_and_filesystem_boundary(tmp_path, monkeypatch):
    runtime = _settings(tmp_path, allow_remote_filesystem=False)
    monkeypatch.setattr(auth_route, "get_runtime_settings", lambda: runtime)
    monkeypatch.setattr(filesystem, "get_runtime_settings", lambda: runtime)
    monkeypatch.setattr(tasks, "get_runtime_settings", lambda: runtime)

    app = FastAPI()
    app.include_router(auth_route.router, prefix="/api")
    app.include_router(filesystem.router, prefix="/api")
    app.include_router(tasks.router, prefix="/api")
    client = TestClient(app)

    capabilities = client.get("/api/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["mode"] == "remote"
    assert capabilities.json()["filesystem_browse"] is False
    assert capabilities.json()["local_path_submission"] is False
    assert capabilities.json()["browser_file_upload"] is True

    assert client.get("/api/filesystem/drives").status_code == 403
    assert client.get("/api/filesystem/browse", params={"path": str(tmp_path)}).status_code == 403
    assert client.get("/api/filesystem/scan-folder", params={"path": str(tmp_path)}).status_code == 403

    local_task = client.post(
        "/api/tasks",
        json={"task_type": "pipeline", "source": str(tmp_path / "demo.mp4")},
    )
    assert local_task.status_code == 403


def test_remote_tasks_accept_only_managed_staged_uploads(tmp_path):
    staging_dir = tmp_path / "_staging" / ("a" * 32)
    staging_dir.mkdir(parents=True)
    uploaded = staging_dir / "demo.mp4"
    uploaded.write_bytes(b"media")

    assert tasks._is_managed_staging_source(str(uploaded), str(tmp_path))
    assert not tasks._is_managed_staging_source(str(tmp_path / "demo.mp4"), str(tmp_path))
    assert not tasks._is_managed_staging_source(
        str(tmp_path / "_staging" / "guessable" / "demo.mp4"),
        str(tmp_path),
    )


def test_main_middleware_accepts_unlocked_cookie(tmp_path, monkeypatch):
    from app import main as app_main

    runtime = _settings(tmp_path, api_token="secret-token")
    monkeypatch.setattr(settings_module, "_runtime_settings", runtime)
    monkeypatch.setattr(app_main, "get_runtime_settings", lambda: runtime)
    monkeypatch.setattr(auth_route, "get_runtime_settings", lambda: runtime)

    client = TestClient(app_main.app)
    assert client.get("/api/not-real").status_code == 401
    assert client.get("/api/auth/status").json()["authenticated"] is False
    assert client.post("/api/auth/unlock", json={"token": "secret-token"}).status_code == 403

    unlocked = client.post(
        "/api/auth/unlock",
        json={"token": "secret-token"},
        headers={"X-Requested-With": "fetch"},
    )
    assert unlocked.status_code == 200
    assert client.get("/api/not-real").status_code == 404


def test_remote_bind_requires_api_token(tmp_path, monkeypatch):
    monkeypatch.setattr(serve_module, "_setup_win32_job_object", lambda: None)
    monkeypatch.setattr(serve_module, "_port_in_use", lambda _host, _port: False)
    monkeypatch.setattr(serve_module.uvicorn, "run", lambda *args, **kwargs: None)

    monkeypatch.setattr(settings_module, "_runtime_settings", _settings(tmp_path, api_token=""))
    with pytest.raises(SystemExit) as exc:
        serve_module.run_server(host="0.0.0.0")
    assert exc.value.code == 2

    monkeypatch.setattr(settings_module, "_runtime_settings", _settings(tmp_path, api_token="configured"))
    serve_module.run_server(host="0.0.0.0")
