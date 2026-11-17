from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import shutil
import socket
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import quote, urlparse
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.archive_sync import (
    MAX_ARCHIVE_MEMBERS,
    MAX_EXTRACTED_BYTES,
    MAX_MEMBER_BYTES,
    ArchiveSyncService,
    _safe_member_path,
    archive_file_manifest,
    get_archive_sync_service,
    publish_archive,
)
from app.core.database import _get_conn, _save_task_row, get_task_store, task_write_transaction
from app.core.events import TaskEvent, get_event_bus
from app.core.network import httpx_client_kwargs
from app.core.paths import ACTIVE_STATUSES, archive_directory, get_workspace_paths
from app.core.settings import RuntimeSettings, get_runtime_settings
from app.core.workspace_lifecycle import run_in_thread, uses_workspace
from app.models import Task, TaskStatus, TaskType
from app.services.remote_archive_upload import _normalized_server_url, _retry_delay

logger = logging.getLogger(__name__)
_MAX_JSON_BYTES = 16 * 1024 * 1024


class RemoteStatus(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=4096)
    revision: int = Field(ge=0)
    instance_id: UUID | None = None


class RemoteFile(BaseModel):
    relative_path: str
    size: int = Field(ge=0, le=MAX_MEMBER_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")

    @field_validator("relative_path")
    @classmethod
    def allowed_path(cls, value: str) -> str:
        path = _safe_member_path(value)
        if not ArchiveSyncService._is_allowed(Path(path)):
            raise ValueError("Remote manifest contains a disallowed file")
        return path.as_posix()

    @field_validator("sha256")
    @classmethod
    def lowercase_hash(cls, value: str) -> str:
        return value.lower()


class RemoteManifest(BaseModel):
    workspace_id: str
    archive_id: UUID
    files: list[RemoteFile] = Field(max_length=MAX_ARCHIVE_MEMBERS)

    @model_validator(mode="after")
    def complete_manifest(self):
        paths = [entry.relative_path.casefold() for entry in self.files]
        if len(paths) != len(set(paths)) or "metadata.json" not in paths:
            raise ValueError("Remote manifest has duplicate paths or no metadata.json")
        if sum(entry.size for entry in self.files) > MAX_EXTRACTED_BYTES:
            raise ValueError("Remote manifest exceeds the archive size limit")
        return self


class RemoteChange(BaseModel):
    revision: int = Field(gt=0)
    archive_id: UUID
    operation: Literal["upsert", "delete"]


class RemoteChanges(BaseModel):
    workspace_id: str
    changes: list[RemoteChange] = Field(max_length=500)
    next_cursor: int = Field(ge=0)
    has_more: bool
    library_revision: int = Field(ge=0)
    server_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def ordered_changes(self):
        revisions = [entry.revision for entry in self.changes]
        if revisions != sorted(set(revisions)):
            raise ValueError("Remote change revisions must be strictly increasing")
        if self.changes and self.next_cursor != self.changes[-1].revision:
            raise ValueError("Remote next_cursor does not match its change page")
        if self.has_more and not self.changes:
            raise ValueError("Remote change page cannot advance")
        if self.next_cursor > self.server_revision:
            raise ValueError("Remote change cursor exceeds the server revision")
        return self


class RemoteFolder(BaseModel):
    id: str = Field(min_length=1, max_length=1024)
    name: str = Field(min_length=1, max_length=1024)
    created_at: str
    updated_at: str


class RemoteMembership(BaseModel):
    content_key: str = Field(min_length=1, max_length=4096)
    folder_id: str | None


class RemoteLibrary(BaseModel):
    library_revision: int = Field(ge=0)
    folders: list[RemoteFolder] = Field(max_length=100_000)
    members: list[RemoteMembership] = Field(max_length=1_000_000)

    @model_validator(mode="after")
    def valid_memberships(self):
        ids = [folder.id for folder in self.folders]
        if len(ids) != len(set(ids)):
            raise ValueError("Remote library contains duplicate folders")
        if any(member.folder_id and member.folder_id not in ids for member in self.members):
            raise ValueError("Remote library membership references an unknown folder")
        keys = [member.content_key for member in self.members]
        if len(keys) != len(set(keys)):
            raise ValueError("Remote library contains duplicate memberships")
        return self


def _is_self_server(server_url: str, workspace_id: str, data_root: Path) -> bool:
    if workspace_id != str(data_root):
        return False
    host = urlparse(server_url).hostname
    if host in {"localhost", socket.gethostname().lower(), socket.getfqdn().lower()}:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    import psutil

    return any(
        item.address.split("%", 1)[0] == str(address)
        for addresses in psutil.net_if_addrs().values()
        for item in addresses
    )


def _cursor(server_url: str, workspace_id: str) -> tuple[int, int]:
    row = (
        _get_conn()
        .execute(
            "SELECT cursor,library_revision FROM remote_sync_cursors "
            "WHERE server_url=? AND workspace_id=?",
            (server_url, workspace_id),
        )
        .fetchone()
    )
    return (row["cursor"], row["library_revision"]) if row else (0, -1)


def _advance(server_url: str, workspace_id: str, cursor: int) -> None:
    with task_write_transaction() as conn:
        conn.execute(
            "INSERT INTO remote_sync_cursors(server_url,workspace_id,cursor) VALUES(?,?,?) "
            "ON CONFLICT(server_url,workspace_id) DO UPDATE SET cursor=excluded.cursor",
            (server_url, workspace_id, cursor),
        )


def _file_hashes(directory: Path) -> dict[str, str]:
    return {
        entry.relative_path: entry.sha256
        for entry in get_archive_sync_service()._iter_sync_files(directory, with_hash=True)
    }


def _source_marker(server_url: str, workspace_id: str) -> dict[str, str]:
    return {"server_url": server_url, "workspace_id": workspace_id}


def _archive_record(archive_id: UUID):
    return get_archive_sync_service()._active_record(str(archive_id))


def _unchanged(manifest: RemoteManifest) -> bool:
    record = _archive_record(manifest.archive_id)
    return bool(
        record
        and _file_hashes(Path(record["archive_path"]))
        == {entry.relative_path: entry.sha256 for entry in manifest.files}
    )


def _destination(root: Path, archive_id: UUID, title: str) -> Path:
    record = _archive_record(archive_id)
    if record:
        return archive_directory(record["archive_path"], root)
    from app.api.routes.sync import _safe_archive_name

    base = get_workspace_paths(root).archives / _safe_archive_name(title)
    candidate, suffix = base, 2
    while candidate.exists():
        candidate = base.with_name(f"{base.name} ({suffix})")
        suffix += 1
    return candidate


def _commit_archive(staging: Path, manifest: RemoteManifest, server_url: str, root: Path) -> None:
    from app.core.artifacts import _write_lock, get_artifact_store
    from app.services.archiving.archive import get_archive_service

    metadata = json.loads((staging / "metadata.json").read_text(encoding="utf-8-sig"))
    if not isinstance(metadata, dict) or metadata.get("archive_id") != str(manifest.archive_id):
        raise ValueError("Remote metadata does not match the requested archive")
    marker = _source_marker(server_url, manifest.workspace_id)
    key = (server_url, manifest.workspace_id, str(manifest.archive_id))
    sync = get_archive_sync_service()
    artifacts = get_artifact_store()
    publication, task = None, None
    import_id = str(uuid4())
    with sync._reconcile_lock, _write_lock:
        try:
            with task_write_transaction() as conn:
                previous = conn.execute(
                    "SELECT * FROM remote_sync_archives "
                    "WHERE server_url=? AND workspace_id=? AND archive_id=?",
                    key,
                ).fetchone()
                destination = _destination(
                    root, manifest.archive_id, str(metadata.get("title") or manifest.archive_id)
                )
                store = get_task_store()
                owner = store.find_task_by_output_dir(destination)
                if owner and owner.status in ACTIVE_STATUSES:
                    raise ValueError(
                        "Local archive is used by an active task; synchronization will retry"
                    )
                try:
                    task_id = UUID(str(metadata.get("task_id") or manifest.archive_id))
                except ValueError as exc:
                    raise ValueError("Remote metadata contains an invalid task_id") from exc
                task = owner or store.get(task_id)
                if task and not owner:
                    task_id = uuid5(
                        NAMESPACE_URL, f"{server_url}/{manifest.workspace_id}/{manifest.archive_id}"
                    )
                    task = store.get(task_id)
                if task and task.status in ACTIVE_STATUSES:
                    raise ValueError("Local task is active; synchronization will retry")
                owned = bool(previous["owned"]) if previous else not destination.exists()
                if destination.exists():
                    previous_files = json.loads(previous["files"]) if previous else {}
                    for local in destination.rglob("*"):
                        if local.is_symlink():
                            raise ValueError("Local archive contains a symbolic link")
                        if not local.is_file():
                            continue
                        relative = local.relative_to(destination).as_posix()
                        target = staging / relative
                        if relative not in previous_files and not target.exists():
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(local, target)
                if task is None:
                    task = Task(
                        id=task_id,
                        task_type=TaskType.PIPELINE,
                        source=str(
                            metadata.get("source_url") or f"remote-archive:{manifest.archive_id}"
                        ),
                        options={"_remote_pull": marker, "read_only": True},
                    )
                task.options = {**task.options, "_remote_pull": marker}
                task.status, task.progress, task.error = TaskStatus.COMPLETED, 1.0, None
                task.message = "已从远程服务器同步归档"
                task.updated_at = datetime.now()
                task.completed_at = task.completed_at or task.updated_at
                task.result = {
                    **(task.result or {}),
                    "metadata": metadata,
                    "output_dir": str(destination),
                    "remote_sync": {**marker, "import_id": import_id},
                }
                publication = publish_archive(
                    staging, destination, root, task_id=task.id, import_id=import_id
                )
                task.result["archive"] = {
                    "output_dir": str(destination),
                    "files": archive_file_manifest(destination),
                }
                _save_task_row(conn, task)
                artifacts.import_mirrors(conn, task.id, destination)
                archive_service = get_archive_service()
                item = archive_service._archive_item(
                    destination,
                    {
                        archive_service._path_key(destination): {
                            "id": str(task.id),
                            "created_at": task.created_at.isoformat(),
                            "status": task.status,
                            "error": None,
                        }
                    },
                    lite=True,
                )
                if item is None:
                    raise ValueError("Remote archive cannot be indexed")
                sync._upsert_if_changed(
                    str(manifest.archive_id),
                    destination,
                    sync._file_state(destination),
                    item,
                    transaction=conn,
                )
                conn.execute(
                    "INSERT INTO remote_sync_archives VALUES(?,?,?,?,?,?,?) "
                    "ON CONFLICT(server_url,workspace_id,archive_id) DO UPDATE SET "
                    "task_id=excluded.task_id,archive_path=excluded.archive_path,files=excluded.files,owned=excluded.owned",
                    (
                        *key,
                        str(task.id),
                        destination.relative_to(root).as_posix(),
                        json.dumps({entry.relative_path: entry.sha256 for entry in manifest.files}),
                        int(owned),
                    ),
                )
        except BaseException:
            if publication:
                current = get_task_store().get(task.id)
                if (
                    current
                    and ((current.result or {}).get("remote_sync") or {}).get("import_id")
                    == import_id
                ):
                    publication.finish()
                else:
                    publication.rollback()
            raise
        publication.finish()


def _delete_archive(server_url: str, workspace_id: str, archive_id: UUID, root: Path) -> bool:
    from app.core.archive_lifecycle import get_archive_lifecycle
    from app.core.artifacts import _write_lock

    key = (server_url, workspace_id, str(archive_id))
    row = (
        _get_conn()
        .execute(
            "SELECT * FROM remote_sync_archives "
            "WHERE server_url=? AND workspace_id=? AND archive_id=?",
            key,
        )
        .fetchone()
    )
    if not row:
        return False
    active = _archive_record(archive_id)
    directory = archive_directory(
        active["archive_path"] if active else root / row["archive_path"], root
    )
    removed = False
    sync = get_archive_sync_service()
    with sync._reconcile_lock, _write_lock:
        if row["owned"] and directory.is_dir():
            expected = json.loads(row["files"])
            actual = {
                path.relative_to(directory).as_posix()
                for path in directory.rglob("*")
                if path.is_file()
            }
            task = get_task_store().get(UUID(row["task_id"]))
            if (
                actual == set(expected)
                and _file_hashes(directory) == expected
                and task
                and task.options.get("_remote_pull") == _source_marker(server_url, workspace_id)
            ):
                lifecycle = get_archive_lifecycle()
                lifecycle._finish(lifecycle._prepare(directory), root, sync)
                removed = True
    with task_write_transaction() as conn:
        conn.execute(
            "DELETE FROM remote_sync_archives "
            "WHERE server_url=? AND workspace_id=? AND archive_id=?",
            key,
        )
    return removed


def _merge_library(server_url: str, workspace_id: str, library: RemoteLibrary) -> bool:
    from app.core.library import changed

    origin = "remote:" + uuid5(NAMESPACE_URL, f"{server_url}/{workspace_id}").hex
    with task_write_transaction() as conn:
        before = conn.total_changes
        mapping = {}
        for folder in library.folders:
            local_id = str(uuid5(NAMESPACE_URL, f"{server_url}/{workspace_id}/folders/{folder.id}"))
            mapping[folder.id] = local_id
            conn.execute(
                "INSERT OR IGNORE INTO remote_sync_folders VALUES(?,?,?,?)",
                (server_url, workspace_id, folder.id, local_id),
            )
            conn.execute(
                "INSERT INTO library_folders(id,name,created_at,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name,updated_at=excluded.updated_at "
                "WHERE name!=excluded.name OR updated_at!=excluded.updated_at",
                (local_id, folder.name, folder.created_at, folder.updated_at),
            )
        members = {
            member.content_key: mapping[member.folder_id]
            for member in library.members
            if member.folder_id
        }
        existing = {
            row["content_key"]: row["folder_id"]
            for row in conn.execute(
                "SELECT content_key,folder_id FROM library_folder_members WHERE origin=?",
                (origin,),
            )
        }
        for content_key in existing.keys() - members.keys():
            conn.execute(
                "DELETE FROM library_folder_members WHERE content_key=? AND origin=?",
                (content_key, origin),
            )
        now = datetime.now().astimezone().isoformat()
        for content_key, folder_id in members.items():
            conn.execute(
                "INSERT INTO library_folder_members(content_key,folder_id,origin,added_at) "
                "VALUES(?,?,?,?) ON CONFLICT(content_key) DO UPDATE SET "
                "folder_id=excluded.folder_id,added_at=excluded.added_at "
                "WHERE origin=excluded.origin AND folder_id IS NOT excluded.folder_id",
                (content_key, folder_id, origin, now),
            )
        for row in conn.execute(
            "SELECT remote_folder_id,local_folder_id FROM remote_sync_folders "
            "WHERE server_url=? AND workspace_id=?",
            (server_url, workspace_id),
        ).fetchall():
            if row["remote_folder_id"] not in mapping:
                conn.execute("DELETE FROM library_folders WHERE id=?", (row["local_folder_id"],))
                conn.execute(
                    "DELETE FROM remote_sync_folders WHERE local_folder_id=?",
                    (row["local_folder_id"],),
                )
        updated = conn.total_changes != before
        if updated:
            changed(conn)
        conn.execute(
            "INSERT INTO remote_sync_cursors(server_url,workspace_id,library_revision) "
            "VALUES(?,?,?) ON CONFLICT(server_url,workspace_id) DO UPDATE SET "
            "library_revision=excluded.library_revision",
            (server_url, workspace_id, library.library_revision),
        )
    return updated


class RemoteArchivePullService:
    def __init__(
        self,
        *,
        settings_getter: Callable[[], RuntimeSettings] = get_runtime_settings,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        self._settings_getter = settings_getter
        self._client_factory = client_factory
        self._lock = asyncio.Lock()
        self._runner: asyncio.Task | None = None

    async def start(self) -> None:
        if self._runner is None or self._runner.done():
            self._runner = asyncio.create_task(self._run(), name="remote-archive-pull")

    async def stop(self) -> None:
        if self._runner:
            self._runner.cancel()
            with suppress(asyncio.CancelledError):
                await self._runner
            self._runner = None
        async with self._lock:
            pass

    async def _run(self) -> None:
        failures = 0
        while True:
            settings = self._settings_getter()
            interval = max(5.0, float(settings.remote_sync_interval_sec))
            try:
                await self.sync_once()
                failures = 0
            except Exception:
                failures += 1
                logger.warning("Remote archive pull failed", exc_info=True)
            await asyncio.sleep(_retry_delay(interval, failures))

    @uses_workspace
    async def sync_once(self) -> dict:
        async with self._lock:
            settings = self._settings_getter()
            if not (
                settings.remote_sync_enabled
                and settings.remote_sync_download_results
                and settings.remote_server_url
            ):
                return {"enabled": False, "upserted": 0, "deleted": 0, "skipped": 0}
            server = _normalized_server_url(settings.remote_server_url)
            root = Path(settings.data_root).resolve()
            headers = {"User-Agent": "MPP-Remote-Archive-Pull/1"}
            if settings.remote_api_token:
                headers["Authorization"] = f"Bearer {settings.remote_api_token}"
            async with self._client_factory(
                headers=headers,
                follow_redirects=False,
                timeout=httpx.Timeout(60, connect=15),
                **httpx_client_kwargs(server),
            ) as client:
                status = RemoteStatus.model_validate(
                    await self._json(client, f"{server}/api/sync/status")
                )
                is_self = (
                    status.instance_id == UUID(get_archive_sync_service().instance_id)
                    if status.instance_id is not None
                    else await run_in_thread(_is_self_server, server, status.workspace_id, root)
                )
                if is_self:
                    return {
                        "enabled": False,
                        "reason": "self_workspace",
                        "upserted": 0,
                        "deleted": 0,
                        "skipped": 0,
                    }
                client.headers["X-MPP-Workspace-ID"] = quote(status.workspace_id, safe="")
                cursor, library_revision = await run_in_thread(_cursor, server, status.workspace_id)
                outcome = {
                    "enabled": True,
                    "upserted": 0,
                    "deleted": 0,
                    "skipped": 0,
                    "workspace_id": status.workspace_id,
                    "cursor": cursor,
                    "library_changed": False,
                }
                while True:
                    page = await self._change_page(client, server, status.workspace_id, cursor)
                    for entry in page.changes:
                        if entry.operation == "upsert":
                            updated = await self._upsert_remote(
                                client, server, status.workspace_id, root, entry
                            )
                            outcome["upserted" if updated else "skipped"] += 1
                        elif await run_in_thread(
                            _delete_archive, server, status.workspace_id, entry.archive_id, root
                        ):
                            outcome["deleted"] += 1
                        else:
                            outcome["skipped"] += 1
                        cursor = entry.revision
                        await run_in_thread(_advance, server, status.workspace_id, cursor)
                        outcome["cursor"] = cursor
                        if entry.operation == "upsert" or outcome["deleted"]:
                            await get_event_bus().publish(
                                TaskEvent("library", "library_changed"), persist=False
                            )
                    if not page.has_more:
                        break
                if library_revision != page.library_revision:
                    library = RemoteLibrary.model_validate(
                        await self._json(client, f"{server}/api/sync/library")
                    )
                    outcome["library_changed"] = await run_in_thread(
                        _merge_library, server, status.workspace_id, library
                    )
                    if outcome["library_changed"]:
                        await get_event_bus().publish(
                            TaskEvent("library", "library_changed"), persist=False
                        )
                return outcome

    async def _upsert_remote(
        self,
        client: httpx.AsyncClient,
        server: str,
        workspace_id: str,
        root: Path,
        entry: RemoteChange,
    ) -> bool:
        try:
            payload = await self._json(
                client, f"{server}/api/sync/archives/{entry.archive_id}/manifest"
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404 or not await self._has_later_tombstone(
                client, server, workspace_id, entry
            ):
                raise
            return False
        manifest = RemoteManifest.model_validate(payload)
        if manifest.workspace_id != workspace_id or manifest.archive_id != entry.archive_id:
            raise ValueError("Remote manifest does not match the requested workspace and archive")
        if await run_in_thread(_unchanged, manifest):
            return False
        await self._download_archive(client, server, root, manifest)
        return True

    async def _has_later_tombstone(
        self, client: httpx.AsyncClient, server: str, workspace_id: str, entry: RemoteChange
    ) -> bool:
        cursor, last_operation, stop_at = entry.revision, None, None
        while True:
            page = await self._change_page(client, server, workspace_id, cursor)
            if stop_at is None:
                stop_at = page.server_revision
            for later in page.changes:
                if later.revision <= stop_at and later.archive_id == entry.archive_id:
                    last_operation = later.operation
            if not page.has_more or page.next_cursor >= stop_at:
                return last_operation == "delete"
            cursor = page.next_cursor

    async def _change_page(
        self, client: httpx.AsyncClient, server: str, workspace_id: str, cursor: int
    ) -> RemoteChanges:
        page = RemoteChanges.model_validate(
            await self._json(client, f"{server}/api/sync/changes?cursor={cursor}")
        )
        if page.workspace_id != workspace_id or any(
            entry.revision <= cursor for entry in page.changes
        ):
            raise ValueError("Remote workspace or change cursor changed during synchronization")
        return page

    @staticmethod
    async def _json(client: httpx.AsyncClient, url: str):
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > _MAX_JSON_BYTES:
                    raise ValueError("Remote synchronization response exceeds the JSON size limit")
        return json.loads(content)

    async def _download_archive(
        self, client: httpx.AsyncClient, server: str, root: Path, manifest: RemoteManifest
    ) -> None:
        staging = get_workspace_paths(root).temporary("remote_sync_client") / f"pull-{uuid4().hex}"
        await run_in_thread(staging.mkdir, parents=True, exist_ok=False)
        try:
            for entry in manifest.files:
                target = staging / entry.relative_path
                await run_in_thread(target.parent.mkdir, parents=True, exist_ok=True)
                file_path = quote(entry.relative_path, safe="/")
                url = f"{server}/api/sync/archives/{manifest.archive_id}/files/{file_path}"
                digest, size = hashlib.sha256(), 0
                with target.open("xb") as output:
                    async with client.stream("GET", url) as response:
                        response.raise_for_status()
                        async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                            size += len(chunk)
                            if size > entry.size:
                                raise ValueError(
                                    f"Remote file exceeds its declared size: {entry.relative_path}"
                                )
                            digest.update(chunk)
                            await run_in_thread(output.write, chunk)
                if size != entry.size or digest.hexdigest() != entry.sha256:
                    raise ValueError(f"Remote file SHA-256 or size mismatch: {entry.relative_path}")
            await run_in_thread(_commit_archive, staging, manifest, server, root)
        finally:
            await run_in_thread(shutil.rmtree, staging, True)


_service: RemoteArchivePullService | None = None


def get_remote_archive_pull_service() -> RemoteArchivePullService:
    global _service
    if _service is None:
        _service = RemoteArchivePullService()
    return _service
