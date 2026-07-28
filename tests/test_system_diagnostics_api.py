from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.api.routes import system  # noqa: E402


def _payload() -> dict:
    return {
        "appVersion": "0.4.1",
        "components": [],
        "diagnosticSchemaVersion": 1,
        "healthy": True,
        "limits": {},
        "reportDigest": "sha256:" + "a" * 64,
        "requestedProbes": [],
        "schema": "mpp.system-diagnostics",
        "schemaVersion": 1,
        "status": "available",
        "verified": True,
    }


def test_system_diagnostics_route_is_read_only_and_disables_caching(
    monkeypatch,
) -> None:
    payload = _payload()
    monkeypatch.setattr(system, "get_system_diagnostics", lambda: payload)
    app = FastAPI()
    app.include_router(system.router, prefix="/api")

    with TestClient(app) as client:
        response = client.get("/api/system/diagnostics")
        mutation = client.post("/api/system/diagnostics")

    assert response.status_code == 200
    assert response.json() == payload
    assert response.headers["cache-control"] == "no-store"
    assert mutation.status_code == 405


def test_system_diagnostics_route_returns_fixed_error_without_exception_data(
    monkeypatch,
) -> None:
    def fail():
        raise RuntimeError("Authorization: Bearer do-not-return-this")

    monkeypatch.setattr(system, "get_system_diagnostics", fail)
    app = FastAPI()
    app.include_router(system.router, prefix="/api")

    with TestClient(app) as client:
        response = client.get("/api/system/diagnostics")

    assert response.status_code == 503
    assert response.json() == {"detail": "Runtime diagnostics are unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert "do-not-return-this" not in json.dumps(response.json())


def test_main_app_mounts_diagnostics_under_existing_api_authentication(
    monkeypatch,
) -> None:
    from app import main as app_main

    payload = _payload()
    monkeypatch.delenv("MPP_REQUIRE_API_TOKEN", raising=False)
    monkeypatch.setattr(app_main, "_DESKTOP_SESSION_SECRET", "")
    monkeypatch.setattr(
        app_main,
        "get_runtime_settings",
        lambda: SimpleNamespace(api_token="diagnostics-test-token"),
    )
    monkeypatch.setattr(system, "get_system_diagnostics", lambda: payload)

    client = TestClient(app_main.app)
    try:
        unauthorized = client.get("/api/system/diagnostics")
        authorized = client.get(
            "/api/system/diagnostics",
            headers={"Authorization": "Bearer diagnostics-test-token"},
        )
        mutation = client.post(
            "/api/system/diagnostics",
            headers={
                "Authorization": "Bearer diagnostics-test-token",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
    finally:
        client.close()

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json() == payload
    assert mutation.status_code == 405
