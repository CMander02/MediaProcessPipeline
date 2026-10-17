from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import main
from app.core.settings import RuntimeSettings


@pytest.fixture
def static_client(tmp_path, monkeypatch):
    dist = tmp_path / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("PUBLIC_SPA", encoding="utf-8")
    (dist / "favicon.svg").write_text("PUBLIC_ICON", encoding="utf-8")
    (dist / "plain.txt").write_text("PUBLIC_ASSET", encoding="utf-8")
    (tmp_path / "canary.txt").write_text("PRIVATE_CANARY", encoding="utf-8")
    monkeypatch.setattr(main, "_web_dist", dist)
    monkeypatch.setattr(
        main, "get_runtime_settings", lambda: RuntimeSettings(api_token="test-token")
    )
    return TestClient(main.app), dist


@pytest.mark.parametrize(
    "path",
    [
        "/%2e%2e/%2e%2e/canary.txt",
        "/..%2f..%2fcanary.txt",
        "/..%5c..%5ccanary.txt",
        "/%5c%5clocalhost%5cshare%5ccanary.txt",
        "/C:%5cWindows%5cwin.ini",
        "/plain.txt%3Asecret",
        "/%00canary.txt",
    ],
)
def test_anonymous_static_traversal_never_reads_outside_dist(static_client, path):
    client, _ = static_client
    assert client.get("/api/tasks").status_code == 401
    response = client.get(path)
    assert response.status_code == 404
    assert "PRIVATE_CANARY" not in response.text


def test_normal_assets_and_spa_routes_still_work(static_client):
    client, _ = static_client
    assert client.get("/plain.txt").text == "PUBLIC_ASSET"
    assert client.get("/favicon.svg").text == "PUBLIC_ICON"
    assert client.get("/files/folder").text == "PUBLIC_SPA"
    assert "PRIVATE_CANARY" not in client.get("/%252e%252e/canary.txt").text


@pytest.mark.parametrize("name", ["linked.txt", "index.html", "favicon.svg"])
def test_static_symlink_outside_build_is_rejected(static_client, name):
    client, dist = static_client
    link = dist / name
    link.unlink(missing_ok=True)
    try:
        link.symlink_to(dist.parent.parent / "canary.txt")
    except OSError:
        pytest.skip("Host does not allow unprivileged file symlinks")
    response = client.get("/missing" if name == "index.html" else f"/{name}")
    assert response.status_code == 404
    assert "PRIVATE_CANARY" not in response.text
