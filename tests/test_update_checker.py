from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api.routes import system as system_route
from app.core import settings as settings_module
from app.core.paths import get_workspace_paths
from app.core.settings import RuntimeSettings
from app.services import update_checker as updates

REPO = "CMander02/MediaProcessPipeline"
VERSION = "0.8.0"
CONTENT = b"verified update package"
SERVER = f"MPP-server-bundle-{VERSION}.tar.gz"
ASSET_NAMES = [
    SERVER,
    f"MPP-Desktop-{VERSION}-Setup.exe",
    f"MPP-Desktop-{VERSION}-win-x64-Portable.exe",
    f"MPP-Desktop-{VERSION}-mac-arm64.dmg",
    f"MPP-Android-{VERSION}.apk",
    "SHA256SUMS.txt",
]


def release(repo=REPO):
    return {
        "tag_name": f"v{VERSION}",
        "html_url": f"https://github.com/{repo}/releases/tag/v{VERSION}",
        "published_at": "2026-11-17T00:00:00Z",
        "body": "Release notes",
        "assets": [
            {
                "name": name,
                "size": len(CONTENT),
                "browser_download_url": f"https://github.com/{repo}/releases/download/v{VERSION}/{name}",
            }
            for name in ASSET_NAMES
        ],
    }


@pytest.mark.parametrize("configured_repo", [REPO.lower(), REPO.upper()])
def test_repository_configuration_accepts_github_canonical_casing(configured_repo):
    parsed = updates.parse_release(release(), configured_repo)
    assert parsed.tag_name == f"v{VERSION}"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    config = RuntimeSettings(data_root=str(tmp_path))
    monkeypatch.setattr(settings_module, "_runtime_settings", config)
    monkeypatch.setattr(updates, "httpx_client_kwargs", lambda url: {"trust_env": False})
    monkeypatch.setattr(updates.sys, "platform", "linux")
    monkeypatch.setattr(updates, "__version__", "0.7.0")
    return config


class GitHub:
    def __init__(self):
        self.requests = []
        self.tamper = False
        self.unreachable = False
        self.api_status = 200
        self.payload = release()
        self.checksums = "".join(
            f"{hashlib.sha256(CONTENT).hexdigest()}  {name}\n"
            for name in ASSET_NAMES
            if name != "SHA256SUMS.txt"
        )

    def handle(self, request):
        self.requests.append(request)
        if self.unreachable:
            raise httpx.ConnectError("offline", request=request)
        if request.url.host == "api.github.com":
            if request.headers.get("if-none-match") == '"release-etag"' and self.api_status == 200:
                return httpx.Response(304)
            return httpx.Response(
                self.api_status, json=self.payload, headers={"etag": '"release-etag"'}
            )
        if request.url.path.endswith("SHA256SUMS.txt"):
            return httpx.Response(200, text=self.checksums)
        return httpx.Response(200, content=b"tampered" if self.tamper else CONTENT)

    def checker(self):
        return updates.UpdateChecker(transport=httpx.MockTransport(self.handle))


@pytest.mark.asyncio
async def test_check_cache_etag_expiry_and_process_restart(runtime, monkeypatch):
    github = GitHub()
    monkeypatch.setattr(updates.time, "time", lambda: 10000)
    checker = github.checker()
    first = await checker.check()
    assert first["current_version"] == "0.7.0"
    assert first["latest_version"] == VERSION and first["update_available"]
    assert first["notes"] == "Release notes"
    assert [asset["name"] for asset in first["assets"] if asset["recommended"]] == [SERVER]
    assert await checker.check() == first
    assert len(github.requests) == 1
    restarted = github.checker()
    assert await restarted.check() == first
    assert len(github.requests) == 1
    assert await restarted.check(force=True) == first
    assert github.requests[-1].headers["if-none-match"] == '"release-etag"'
    assert len(github.requests) == 2
    monkeypatch.setattr(updates.time, "time", lambda: 11801)
    await checker.check()
    assert len(github.requests) == 3
    monkeypatch.setattr(updates.time, "time", lambda: 11802)
    assert await github.checker().check() == first
    assert len(github.requests) == 3


@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        ("v1.0.0", "0.9.9", True),
        ("1.0.0", "1.0.0", False),
        ("1.0.0+build.2", "1.0.0+build.1", False),
        ("1.0.0", "1.0.0-rc.1", True),
        ("1.0.0-rc.1", "1.0.0", False),
        ("1.0.0-rc.10", "1.0.0-rc.2", True),
        ("1.0.0-beta", "1.0.0-2", True),
        ("1.0.0-rc.1", "1.0.0-rc", True),
        ("0.9.0", "1.0.0", False),
    ],
)
def test_semantic_version_comparison(latest, current, expected):
    assert updates.version_is_newer(latest, current) is expected


@pytest.mark.parametrize("version", ["1.2", "../1.0.0", "01.0.0", "1.0.0-01", "1.0.0/path"])
def test_invalid_release_versions_rejected(version):
    with pytest.raises(ValueError):
        updates.version_is_newer(version, "0.7.0")


@pytest.mark.asyncio
async def test_unknown_current_version_returns_explicit_error(runtime, monkeypatch):
    monkeypatch.setattr(updates, "__version__", "0+unknown")
    with pytest.raises(updates.UpdateError, match="当前程序版本号无法") as error:
        await GitHub().checker().check()
    assert error.value.status_code == 503


@pytest.mark.parametrize(
    ("platform_name", "machine", "suffixes"),
    [
        ("win32", "AMD64", ["Setup.exe", "Portable.exe"]),
        ("darwin", "arm64", ["mac-arm64.dmg"]),
        ("darwin", "x86_64", []),
        ("linux", "x86_64", ["tar.gz"]),
        ("android", "aarch64", ["apk"]),
        ("unknown", "", []),
    ],
)
def test_platform_download_recommendations(platform_name, machine, suffixes):
    names = updates.recommended_asset_names(
        updates.parse_release(release(), REPO), platform_name, machine
    )
    assert len(names) == len(suffixes)
    assert all(name.endswith(suffix) for name, suffix in zip(names, suffixes))


@pytest.mark.asyncio
async def test_download_verifies_and_reuses_file(runtime):
    github = GitHub()
    checker = github.checker()
    result = await checker.download()
    path = Path(result["path"])
    assert path == get_workspace_paths().temporary("updates") / VERSION / SERVER
    assert path.read_bytes() == CONTENT
    assert result["sha256"] == hashlib.sha256(CONTENT).hexdigest()
    assert result["version"] == VERSION
    assert "停止 daemon" in result["instructions"]
    assert (path.parent / "SHA256SUMS.txt").read_text() == github.checksums
    before = sum(request.url.path.endswith(SERVER) for request in github.requests)
    (path.parent / "SHA256SUMS.txt").unlink()
    assert await checker.download(SERVER) == result
    assert (path.parent / "SHA256SUMS.txt").read_text() == github.checksums
    assert sum(request.url.path.endswith(SERVER) for request in github.requests) == before
    assert not list(path.parent.glob("*.part"))


@pytest.mark.asyncio
async def test_tampered_download_and_polluted_existing_package_are_removed(runtime):
    github = GitHub()
    checker = github.checker()
    result = await checker.download()
    path = Path(result["path"])
    path.write_bytes(b"polluted")
    github.tamper = True
    with pytest.raises(updates.UpdateError, match="SHA-256"):
        await checker.download()
    assert not path.exists()
    assert not list(path.parent.glob("*.part"))


@pytest.mark.asyncio
async def test_missing_checksums_and_unknown_asset_rejected(runtime):
    github = GitHub()
    with pytest.raises(updates.UpdateError, match="没有匹配"):
        await github.checker().download("../../outside")
    github.payload["assets"] = [
        asset for asset in github.payload["assets"] if asset["name"] != "SHA256SUMS.txt"
    ]
    runtime.data_root = str(Path(runtime.data_root) / "missing-checksums")
    with pytest.raises(updates.UpdateError, match="缺少 SHA256SUMS"):
        await github.checker().download()


@pytest.mark.parametrize(
    "checksum", ["invalid", "a" * 64 + "  ../outside", ("a" * 64 + "  file\n") * 2]
)
def test_checksum_manifest_rejects_unsafe_or_duplicate_entries(checksum):
    with pytest.raises(updates.UpdateError):
        updates.parse_checksums(checksum)


@pytest.mark.parametrize(
    "repo", ["../repo", "owner/repo?query", "owner/repo/path", "https://github.com/o/r"]
)
def test_repository_config_validation(repo):
    with pytest.raises(ValueError):
        RuntimeSettings(update_repo=repo)


@pytest.mark.asyncio
async def test_release_url_and_asset_paths_are_validated(runtime):
    github = GitHub()
    github.payload["assets"][0]["browser_download_url"] = "https://localhost/private"
    with pytest.raises(updates.UpdateError, match="下载信息无效"):
        await github.checker().check()
    github.payload = release()
    github.payload["assets"][0]["name"] = "../outside"
    with pytest.raises(updates.UpdateError):
        await github.checker().check()
    github.payload = release()
    github.payload["assets"][0]["browser_download_url"] = github.payload["assets"][0][
        "browser_download_url"
    ].replace("download/v0.8.0", "download/v0.9.0")
    with pytest.raises(updates.UpdateError):
        await github.checker().check()


@pytest.mark.asyncio
async def test_download_does_not_follow_existing_file_or_part_symlinks(runtime, tmp_path):
    github = GitHub()
    folder = get_workspace_paths().temporary("updates") / VERSION
    folder.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_bytes(b"local data")
    (folder / SERVER).symlink_to(outside)
    legacy_part = folder / (SERVER + ".part")
    legacy_part.symlink_to(outside)
    result = await github.checker().download()
    assert outside.read_bytes() == b"local data"
    assert Path(result["path"]).read_bytes() == CONTENT
    assert not Path(result["path"]).is_symlink()
    assert legacy_part.is_symlink()


@pytest.mark.asyncio
async def test_download_rejects_symlinked_version_directory(runtime, tmp_path):
    github = GitHub()
    root = get_workspace_paths().temporary("updates")
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / VERSION).symlink_to(outside, target_is_directory=True)
    with pytest.raises(updates.UpdateError, match="超出资料库"):
        await github.checker().download()
    assert not list(outside.iterdir())


@pytest.mark.asyncio
async def test_cancelled_download_removes_partial_file_and_can_retry(runtime):
    started = asyncio.Event()
    release_stream = asyncio.Event()
    github = GitHub()

    class InterruptedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"partial"
            started.set()
            await release_stream.wait()

    def handle(request):
        if request.url.path.endswith(SERVER):
            return httpx.Response(200, stream=InterruptedStream())
        return github.handle(request)

    checker = updates.UpdateChecker(transport=httpx.MockTransport(handle))
    task = asyncio.create_task(checker.download())
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    root = get_workspace_paths().temporary("updates")
    assert not list(root.rglob("*.part"))
    assert not (root / VERSION / SERVER).exists()
    assert Path((await github.checker().download())["path"]).read_bytes() == CONTENT


@pytest.mark.asyncio
async def test_cache_isolation_by_repository_and_workspace(runtime, tmp_path):
    github = GitHub()
    checker = github.checker()
    await checker.check()
    runtime.data_root = str(tmp_path / "second")
    await checker.check()
    assert len(github.requests) == 2
    runtime.update_repo = "another/repository"
    github.payload = release(runtime.update_repo)
    await checker.check()
    assert len(github.requests) == 3
    assert "/repos/another/repository/" in str(github.requests[-1].url)
    assert "if-none-match" not in github.requests[-1].headers


@pytest.mark.asyncio
async def test_concurrent_requests_share_check_and_download(runtime):
    github = GitHub()
    checker = github.checker()
    results = await asyncio.gather(
        checker.check(), checker.check(), checker.download(), checker.download()
    )
    assert results[0] == results[1]
    assert results[2] == results[3]
    assert sum(request.url.host == "api.github.com" for request in github.requests) == 1
    assert sum(request.url.path.endswith(SERVER) for request in github.requests) == 1


@pytest.mark.asyncio
async def test_system_routes_download_authorization_and_network_errors(runtime, monkeypatch):
    github = GitHub()
    checker = github.checker()
    monkeypatch.setattr(system_route, "get_update_checker", lambda: checker)
    app = FastAPI()
    app.include_router(system_route.router, prefix="/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app, client=("10.0.0.2", 5000)), base_url="http://server"
    ) as client:
        assert (await client.post("/api/system/update/download", json={})).status_code == 403
        runtime.api_token = "admin"
        assert (
            await client.post(
                "/api/system/update/download", json={}, headers={"Authorization": "Bearer wrong"}
            )
        ).status_code == 403
        assert (
            await client.post(
                "/api/system/update/download", json={}, headers={"Authorization": "Bearer admin"}
            )
        ).status_code == 200
        github.unreachable = True
        failed = await client.get("/api/system/update/check?force=true")
        assert failed.status_code == 503 and "无法连接 GitHub" in failed.json()["detail"]
        github.unreachable = False
        github.api_status = 429
        assert (await client.get("/api/system/update/check?force=true")).status_code == 502
    runtime.api_token = ""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app, client=("127.0.0.1", 5000)), base_url="http://localhost"
    ) as client:
        assert (await client.post("/api/system/update/download", json={})).status_code == 200
