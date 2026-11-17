from __future__ import annotations

import asyncio
import hashlib
import os
import platform
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
from pydantic import BaseModel, Field

from app.core.atomic_file import atomic_write_text
from app.core.network import httpx_client_kwargs
from app.core.paths import get_workspace_paths
from app.core.settings import get_runtime_settings
from app.core.workspace_lifecycle import run_in_thread, uses_workspace
from app.version import __version__

_SEMVER = re.compile(
    r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
_ASSET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
CACHE_SECONDS = 30 * 60


class UpdateError(ValueError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _version(value: str) -> tuple[tuple[int, int, int], tuple[str, ...]]:
    match = _SEMVER.fullmatch(value)
    if not match:
        raise ValueError(f"Invalid semantic version: {value}")
    prerelease = tuple(match[4].split(".")) if match[4] else ()
    if any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in prerelease):
        raise ValueError(f"Invalid semantic version: {value}")
    return (int(match[1]), int(match[2]), int(match[3])), prerelease


def version_is_newer(latest: str, current: str) -> bool:
    latest_core, latest_pre = _version(latest)
    current_core, current_pre = _version(current)
    if latest_core != current_core:
        return latest_core > current_core
    if not latest_pre or not current_pre:
        return bool(current_pre) and not latest_pre
    for left, right in zip(latest_pre, current_pre):
        if left == right:
            continue
        if left.isdigit() and right.isdigit():
            return int(left) > int(right)
        if left.isdigit() != right.isdigit():
            return not left.isdigit()
        return left > right
    return len(latest_pre) > len(current_pre)


class ReleaseAsset(BaseModel):
    name: str
    browser_download_url: str
    size: int = Field(ge=0)


class Release(BaseModel):
    tag_name: str
    html_url: str
    published_at: str | None = None
    body: str | None = None
    assets: list[ReleaseAsset]


class CachedRelease(BaseModel):
    repo: str
    checked_at: float
    etag: str = ""
    release: Release


def _matches_release_url(url: str, repo: str, suffix: str) -> bool:
    parsed = urlsplit(url)
    parts = unquote(parsed.path).split("/", 3)
    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() == "github.com"
        and len(parts) == 4
        and f"{parts[1]}/{parts[2]}".casefold() == repo.casefold()
        and parts[3] == suffix
        and not parsed.query
        and not parsed.fragment
    )


def parse_release(value: object, repo: str) -> Release:
    release = Release.model_validate(value)
    _version(release.tag_name)
    if not _matches_release_url(release.html_url, repo, f"releases/tag/{release.tag_name}"):
        raise ValueError("Release URL does not belong to the configured repository")
    names: set[str] = set()
    for asset in release.assets:
        if not _ASSET_NAME.fullmatch(asset.name) or asset.name in names:
            raise ValueError("Release asset has an unsafe or duplicate name")
        names.add(asset.name)
        if not _matches_release_url(
            asset.browser_download_url, repo, f"releases/download/{release.tag_name}/{asset.name}"
        ):
            raise ValueError("Release asset URL does not belong to the configured repository")
    return release


def recommended_asset_names(
    release: Release,
    platform_name: str,
    machine: str,
) -> list[str]:
    names = [asset.name for asset in release.assets]
    version = release.tag_name.removeprefix("v")
    candidates: list[str] = []
    if platform_name == "win32":
        candidates = [
            f"MPP-Desktop-{version}-Setup.exe",
            f"MPP-Desktop-{version}-win-x64-Portable.exe",
        ]
    elif platform_name == "darwin":
        arch = "arm64" if machine.lower() in {"arm64", "aarch64"} else "x64"
        candidates = [f"MPP-Desktop-{version}-mac-{arch}.dmg"]
    elif platform_name.startswith("linux"):
        candidates = [f"MPP-server-bundle-{version}.tar.gz"]
    elif platform_name == "android":
        candidates = [name for name in names if name.endswith(".apk")]
    return [name for name in candidates if name in names]


def parse_checksums(content: str) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in content.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line)
        if not match or not _ASSET_NAME.fullmatch(match[2]) or match[2] in checksums:
            raise UpdateError("SHA256SUMS.txt 格式无效或包含重复文件名")
        checksums[match[2]] = match[1].lower()
    return checksums


def _file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@dataclass(frozen=True)
class UpdateContext:
    repo: str
    root: Path

    @property
    def cache_path(self) -> Path:
        key = hashlib.sha256(self.repo.encode("utf-8")).hexdigest()[:16]
        return self.root / f"release-cache-{key}.json"


class UpdateChecker:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport
        self._lock = asyncio.Lock()
        self._cache: dict[UpdateContext, CachedRelease] = {}

    def _context(self) -> UpdateContext:
        return UpdateContext(
            get_runtime_settings().update_repo, get_workspace_paths().temporary("updates")
        )

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            **httpx_client_kwargs("https://api.github.com"),
            transport=self._transport,
            timeout=httpx.Timeout(60, connect=15),
            follow_redirects=True,
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        )

    async def _release(self, context: UpdateContext, *, force: bool = False) -> Release:
        cached = self._cache.get(context)
        if cached is None:
            cached = await run_in_thread(self._read_cache, context)
        if cached and not force and 0 <= time.time() - cached.checked_at < CACHE_SECONDS:
            self._cache[context] = cached
            return cached.release
        headers = {"If-None-Match": cached.etag} if cached and cached.etag else {}
        try:
            async with self._client() as client:
                response = await client.get(
                    f"https://api.github.com/repos/{context.repo}/releases/latest",
                    headers=headers,
                )
                if response.status_code == 304 and cached:
                    updated = cached.model_copy(update={"checked_at": time.time()})
                else:
                    response.raise_for_status()
                    release = parse_release(response.json(), context.repo)
                    updated = CachedRelease(
                        repo=context.repo,
                        checked_at=time.time(),
                        etag=response.headers.get("etag", ""),
                        release=release,
                    )
        except httpx.RequestError as exc:
            raise UpdateError("无法连接 GitHub，请检查网络连接与代理设置", 503) from exc
        except httpx.HTTPStatusError as exc:
            raise UpdateError(f"GitHub 更新接口返回 HTTP {exc.response.status_code}") from exc
        except ValueError as exc:
            raise UpdateError("GitHub Release 返回的版本或下载信息无效") from exc
        await run_in_thread(atomic_write_text, context.cache_path, updated.model_dump_json())
        self._cache[context] = updated
        return updated.release

    @staticmethod
    def _read_cache(context: UpdateContext) -> CachedRelease | None:
        try:
            cached = CachedRelease.model_validate_json(
                context.cache_path.read_text(encoding="utf-8")
            )
            if cached.repo == context.repo:
                parse_release(cached.release.model_dump(), context.repo)
                return cached
        except (OSError, ValueError):
            pass
        return None

    @uses_workspace
    async def check(self, *, force: bool = False) -> dict:
        try:
            _version(__version__)
        except ValueError as exc:
            raise UpdateError(
                "当前程序版本号无法进行语义化版本比较，请前往 GitHub Release 查看更新", 503
            ) from exc
        async with self._lock:
            release = await self._release(self._context(), force=force)
        recommended = recommended_asset_names(release, sys.platform, platform.machine())
        return {
            "current_version": __version__,
            "latest_version": release.tag_name.removeprefix("v"),
            "update_available": version_is_newer(release.tag_name, __version__),
            "release_url": release.html_url,
            "published_at": release.published_at,
            "notes": release.body or "",
            "assets": [
                {
                    "name": asset.name,
                    "url": asset.browser_download_url,
                    "size": asset.size,
                    "recommended": asset.name in recommended,
                }
                for asset in release.assets
            ],
        }

    @uses_workspace
    async def download(self, asset_name: str | None = None) -> dict:
        async with self._lock:
            context = self._context()
            release = await self._release(context)
            recommended = recommended_asset_names(release, sys.platform, platform.machine())
            selected_name = asset_name or next(iter(recommended), "")
            assets = {asset.name: asset for asset in release.assets}
            if selected_name not in assets or selected_name == "SHA256SUMS.txt":
                raise UpdateError("Release 中没有匹配的更新包，请前往 GitHub Release 下载", 400)
            if "SHA256SUMS.txt" not in assets:
                raise UpdateError("Release 缺少 SHA256SUMS.txt，无法校验更新包")
            folder = context.root / release.tag_name.removeprefix("v")
            await run_in_thread(folder.mkdir, parents=True, exist_ok=True)
            if folder.is_symlink() or folder.resolve().parent != context.root.resolve():
                raise UpdateError("更新下载目录超出资料库临时目录", 400)
            try:
                async with self._client() as client:
                    response = await client.get(assets["SHA256SUMS.txt"].browser_download_url)
                    response.raise_for_status()
                    expected = parse_checksums(response.text).get(selected_name)
                    if not expected:
                        raise UpdateError("SHA256SUMS.txt 未列出所选更新包")
                    await run_in_thread(atomic_write_text, folder / "SHA256SUMS.txt", response.text)
                    path = folder / selected_name
                    if path.is_symlink():
                        path.unlink()
                    if path.is_file() and await run_in_thread(_file_sha256, path) == expected:
                        return self._download_result(path, expected, release)
                    path.unlink(missing_ok=True)
                    await self._download_asset(client, assets[selected_name], path, expected)
            except httpx.RequestError as exc:
                raise UpdateError("无法下载 GitHub 更新包，请检查网络连接与代理设置", 503) from exc
            except httpx.HTTPStatusError as exc:
                raise UpdateError(f"GitHub 更新包下载返回 HTTP {exc.response.status_code}") from exc
            return self._download_result(path, expected, release)

    @staticmethod
    async def _download_asset(
        client: httpx.AsyncClient, asset: ReleaseAsset, path: Path, expected: str
    ) -> None:
        descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".part", dir=path.parent)
        temporary = Path(name)
        try:
            digest = hashlib.sha256()
            with os.fdopen(descriptor, "wb") as output:
                async with client.stream("GET", asset.browser_download_url) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                        await run_in_thread(output.write, chunk)
                        digest.update(chunk)
            if digest.hexdigest() != expected:
                raise UpdateError("更新包 SHA-256 校验失败，已删除下载文件")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _download_result(path: Path, sha256: str, release: Release) -> dict:
        if path.name.endswith(".dmg"):
            instructions = "打开 DMG，将应用拖入 Applications。安装前退出当前应用。"
        elif path.name.endswith("-Setup.exe"):
            instructions = "退出当前应用后运行安装包，按安装向导完成更新。"
        elif path.name.endswith(".tar.gz"):
            instructions = (
                "停止 daemon，备份 config.json 和资料库。"
                "解压并替换程序文件，保留 config.json 和资料库。"
                "在解压目录运行 uv sync，再进入 backend 目录运行 uv run python -m app.cli serve。"
            )
        else:
            instructions = "退出当前应用后，按 GitHub Release 中的安装说明更新。"
        return {
            "path": str(path),
            "sha256": sha256,
            "asset_name": path.name,
            "version": release.tag_name.removeprefix("v"),
            "instructions": instructions,
        }


_update_checker: UpdateChecker | None = None


def get_update_checker() -> UpdateChecker:
    global _update_checker
    if _update_checker is None:
        _update_checker = UpdateChecker()
    return _update_checker
