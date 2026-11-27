"""Folder metadata shared by library synchronization and archive transfers."""

from __future__ import annotations

import asyncio
from sqlite3 import Connection
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field, model_validator

from app.core.archive_index import timestamp

library_sync_lock = asyncio.Lock()


class RemoteFolder(BaseModel):
    id: str = Field(min_length=1, max_length=1024)
    name: str = Field(min_length=1, max_length=1024)
    source_collection_key: str | None = None
    created_at: str
    updated_at: str


class RemoteMembership(BaseModel):
    content_key: str = Field(min_length=1, max_length=4096)
    folder_id: str | None
    origin: str = "collection"
    added_at: str | None = None
    archive_id: str | None = None


class RemoteCover(BaseModel):
    folder_id: str
    content_key: str
    updated_at: str
    archive_id: str | None = None


class RemoteCollection(BaseModel):
    collection_key: str
    title: str
    owner_name: str
    owner_avatar_url: str
    checked_at: str


class RemoteArchiveFolder(BaseModel):
    archive_id: str
    content_key: str | None = None
    folder_id: str | None


class RemoteFolderDeletion(BaseModel):
    folder_id: str = Field(min_length=1, max_length=1024)
    deleted_at: str


class RemoteCoverDeletion(BaseModel):
    folder_id: str = Field(min_length=1, max_length=1024)
    deleted_at: str


class RemoteCollectionMember(BaseModel):
    collection_key: str = Field(min_length=1, max_length=1024)
    content_key: str = Field(min_length=1, max_length=4096)


class LibraryPush(BaseModel):
    folders: list[RemoteFolder] = Field(max_length=100_000)
    members: list[RemoteMembership] = Field(max_length=1_000_000)
    covers: list[RemoteCover] = Field(default_factory=list)
    collections: list[RemoteCollection] = Field(default_factory=list)
    deleted_folders: list[RemoteFolderDeletion] = Field(default_factory=list)
    deleted_covers: list[RemoteCoverDeletion] = Field(default_factory=list)
    collection_members: list[RemoteCollectionMember] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_memberships(self):
        ids = {folder.id for folder in self.folders}
        if len(ids) != len(self.folders):
            raise ValueError("Library contains duplicate folders")
        if any(member.folder_id is not None and member.folder_id not in ids for member in self.members):
            raise ValueError("Library membership references an unknown folder")
        if len({member.content_key for member in self.members}) != len(self.members):
            raise ValueError("Library contains duplicate memberships")
        return self


class ArchiveClassification(BaseModel):
    folder: RemoteFolder
    origin: str
    added_at: str
    cover_updated_at: str | None = None


def remote_origin(server_url: str, workspace_id: str) -> str:
    return "remote:" + uuid5(NAMESPACE_URL, f"{server_url}/{workspace_id}").hex


def replica_folder_id(server: str, workspace: str, remote_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{server}/{workspace}/folders/{remote_id}"))


def folder_mappings(conn: Connection, server_url: str, workspace_id: str) -> dict[str, str]:
    return dict(conn.execute(
        "SELECT remote_folder_id,local_folder_id FROM remote_sync_folders "
        "JOIN library_folders ON id=local_folder_id WHERE server_url=? AND workspace_id=?",
        (server_url, workspace_id),
    ))


def folder_sync_records(conn: Connection, server_url: str, workspace_id: str) -> dict[str, tuple[str, str]]:
    """Return local_folder_id -> (remote_folder_id, remote_updated_at) for active folders."""
    return {
        row["local_folder_id"]: (row["remote_folder_id"], row["remote_updated_at"] or "")
        for row in conn.execute(
            "SELECT r.local_folder_id,r.remote_folder_id,r.remote_updated_at FROM remote_sync_folders r "
            "JOIN library_folders f ON f.id=r.local_folder_id WHERE r.server_url=? AND r.workspace_id=?",
            (server_url, workspace_id),
        )
    }


def remember_folder(
    conn: Connection,
    server: str,
    workspace: str,
    remote_id: str,
    local_id: str,
    remote_updated_at: str = "",
) -> None:
    by_remote = conn.execute(
        "SELECT local_folder_id,remote_updated_at FROM remote_sync_folders "
        "WHERE server_url=? AND workspace_id=? AND remote_folder_id=?",
        (server, workspace, remote_id),
    ).fetchone()
    by_local = conn.execute(
        "SELECT server_url,workspace_id,remote_folder_id,remote_updated_at FROM remote_sync_folders "
        "WHERE local_folder_id=?",
        (local_id,),
    ).fetchone()
    if by_remote is not None:
        new_local = local_id if (by_remote["local_folder_id"] != local_id and by_local is None) else by_remote["local_folder_id"]
        next_updated_at = remote_updated_at or by_remote["remote_updated_at"] or ""
        if new_local != by_remote["local_folder_id"] or next_updated_at != (by_remote["remote_updated_at"] or ""):
            conn.execute(
                "UPDATE remote_sync_folders SET local_folder_id=?,remote_updated_at=? "
                "WHERE server_url=? AND workspace_id=? AND remote_folder_id=?",
                (new_local, next_updated_at, server, workspace, remote_id),
            )
        return
    if by_local is not None:
        if (by_local["server_url"], by_local["workspace_id"], by_local["remote_folder_id"]) == (server, workspace, remote_id):
            if remote_updated_at and remote_updated_at != (by_local["remote_updated_at"] or ""):
                conn.execute(
                    "UPDATE remote_sync_folders SET remote_updated_at=? WHERE local_folder_id=?",
                    (remote_updated_at, local_id),
                )
        return
    conn.execute(
        "INSERT INTO remote_sync_folders(server_url,workspace_id,remote_folder_id,local_folder_id,remote_updated_at) "
        "VALUES(?,?,?,?,?)",
        (server, workspace, remote_id, local_id, remote_updated_at),
    )


def merge_folder_deletions(conn: Connection, deletions: list[RemoteFolderDeletion]) -> None:
    for deletion in deletions:
        tombstone = conn.execute(
            "SELECT deleted_at FROM library_folder_deletions WHERE folder_id=?",
            (deletion.folder_id,),
        ).fetchone()
        if tombstone is None or timestamp(deletion.deleted_at) > timestamp(tombstone["deleted_at"]):
            conn.execute(
                "INSERT INTO library_folder_deletions(folder_id,deleted_at) VALUES(?,?) "
                "ON CONFLICT(folder_id) DO UPDATE SET deleted_at=excluded.deleted_at",
                (deletion.folder_id, deletion.deleted_at),
            )
        folder = conn.execute("SELECT id,updated_at FROM library_folders WHERE id=?", (deletion.folder_id,)).fetchone()
        if folder and timestamp(deletion.deleted_at) > timestamp(folder["updated_at"]):
            conn.execute(
                "UPDATE library_folder_members SET origin='manual',added_at=? WHERE folder_id=?",
                (deletion.deleted_at, deletion.folder_id),
            )
            conn.execute("DELETE FROM library_folder_covers WHERE folder_id=?", (deletion.folder_id,))
            conn.execute("DELETE FROM library_folder_cover_deletions WHERE folder_id=?", (deletion.folder_id,))
            conn.execute("DELETE FROM remote_sync_folders WHERE local_folder_id=?", (deletion.folder_id,))
            conn.execute("DELETE FROM library_folders WHERE id=?", (deletion.folder_id,))


def merge_cover_deletions(conn: Connection, deletions: list[RemoteCoverDeletion]) -> None:
    for deletion in deletions:
        if not conn.execute("SELECT 1 FROM library_folders WHERE id=?", (deletion.folder_id,)).fetchone():
            continue
        tombstone = conn.execute(
            "SELECT deleted_at FROM library_folder_cover_deletions WHERE folder_id=?",
            (deletion.folder_id,),
        ).fetchone()
        if tombstone is None or timestamp(deletion.deleted_at) > timestamp(tombstone["deleted_at"]):
            conn.execute(
                "INSERT INTO library_folder_cover_deletions(folder_id,deleted_at) VALUES(?,?) "
                "ON CONFLICT(folder_id) DO UPDATE SET deleted_at=excluded.deleted_at",
                (deletion.folder_id, deletion.deleted_at),
            )
        cover = conn.execute(
            "SELECT updated_at FROM library_folder_covers WHERE folder_id=?",
            (deletion.folder_id,),
        ).fetchone()
        if cover and timestamp(deletion.deleted_at) > timestamp(cover["updated_at"]):
            conn.execute("DELETE FROM library_folder_covers WHERE folder_id=?", (deletion.folder_id,))


def merge_collection_members(conn: Connection, members: list[RemoteCollectionMember]) -> None:
    for item in members:
        conn.execute(
            "INSERT OR IGNORE INTO library_collection_members(collection_key,content_key) VALUES(?,?)",
            (item.collection_key, item.content_key),
        )


def merge_folder(conn: Connection, folder: RemoteFolder, preferred_id: str, fallback_id: str) -> str | None:
    row = conn.execute("SELECT * FROM library_folders WHERE id=?", (preferred_id,)).fetchone()
    collection_key = folder.source_collection_key or None
    collection_folder = conn.execute(
        "SELECT * FROM library_folders WHERE source_collection_key=?", (collection_key,),
    ).fetchone() if collection_key else None
    row = row or collection_folder
    if row is None and fallback_id != preferred_id:
        row = conn.execute("SELECT * FROM library_folders WHERE id=?", (fallback_id,)).fetchone()
    tombstone_candidates = {preferred_id, fallback_id, folder.id}
    if row is not None:
        tombstone_candidates.add(row["id"])
    tombstones = [
        conn.execute("SELECT deleted_at FROM library_folder_deletions WHERE folder_id=?", (fid,)).fetchone()
        for fid in tombstone_candidates
    ]
    latest_deleted = max((t["deleted_at"] for t in tombstones if t is not None), key=timestamp, default="")
    if row is None:
        if latest_deleted and timestamp(latest_deleted) >= timestamp(folder.updated_at):
            return None
        for fid in tombstone_candidates:
            conn.execute("DELETE FROM library_folder_deletions WHERE folder_id=?", (fid,))
        conn.execute(
            "INSERT INTO library_folders VALUES(?,?,?,?,?)",
            (fallback_id, folder.name, collection_key, folder.created_at, folder.updated_at),
        )
        return fallback_id
    if latest_deleted and timestamp(latest_deleted) > timestamp(folder.updated_at):
        return None
    if latest_deleted:
        for fid in tombstone_candidates:
            conn.execute("DELETE FROM library_folder_deletions WHERE folder_id=?", (fid,))
    if timestamp(folder.updated_at) > timestamp(row["updated_at"]):
        conn.execute(
            "UPDATE library_folders SET name=?,updated_at=? WHERE id=?",
            (folder.name, folder.updated_at, row["id"]),
        )
    if collection_key and row["source_collection_key"] is None and collection_folder is None:
        conn.execute(
            "UPDATE library_folders SET source_collection_key=? WHERE id=?",
            (collection_key, row["id"]),
        )
    return row["id"]


def merge_collections(conn: Connection, collections: list[RemoteCollection]) -> None:
    for collection in collections:
        row = conn.execute("SELECT checked_at FROM library_collections WHERE collection_key=?",
                           (collection.collection_key,)).fetchone()
        if row and timestamp(collection.checked_at) <= timestamp(row["checked_at"]):
            continue
        conn.execute(
            "INSERT INTO library_collections(collection_key,title,owner_name,owner_avatar_url,checked_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(collection_key) DO UPDATE SET title=excluded.title,"
            "owner_name=excluded.owner_name,owner_avatar_url=excluded.owner_avatar_url,checked_at=excluded.checked_at",
            (collection.collection_key, collection.title, collection.owner_name,
             collection.owner_avatar_url, collection.checked_at),
        )


def merge_cover(conn: Connection, folder_id: str, key: str, updated_at: str) -> None:
    member = conn.execute("SELECT folder_id FROM library_folder_members WHERE content_key=?", (key,)).fetchone()
    if not member or member["folder_id"] != folder_id:
        return
    deleted = conn.execute(
        "SELECT deleted_at FROM library_folder_cover_deletions WHERE folder_id=?", (folder_id,),
    ).fetchone()
    if deleted and timestamp(deleted["deleted_at"]) >= timestamp(updated_at):
        return
    cover = conn.execute("SELECT updated_at FROM library_folder_covers WHERE folder_id=?", (folder_id,)).fetchone()
    if cover and timestamp(updated_at) <= timestamp(cover["updated_at"]):
        return
    if deleted:
        conn.execute("DELETE FROM library_folder_cover_deletions WHERE folder_id=?", (folder_id,))
    conn.execute(
        "INSERT INTO library_folder_covers VALUES(?,?,?) ON CONFLICT(folder_id) DO UPDATE "
        "SET content_key=excluded.content_key,updated_at=excluded.updated_at",
        (folder_id, key, updated_at),
    )


def write_member(conn: Connection, key: str, folder_id: str | None, origin: str, added_at: str) -> None:
    if conn.execute("SELECT 1 FROM library_deletions WHERE content_key=?", (key,)).fetchone():
        return
    from app.core.library import save_membership

    save_membership(conn, key, folder_id, origin, added_at)


def merge_push(conn: Connection, payload: LibraryPush, archive_keys: dict[str, str]) -> dict[str, str]:
    from app.core.library import changed

    before = conn.total_changes
    merge_folder_deletions(conn, payload.deleted_folders)
    mapping: dict[str, str] = {}
    alive: dict[str, str] = {}
    for folder in payload.folders:
        merged_id = merge_folder(conn, folder, folder.id, folder.id)
        if merged_id is not None:
            mapping[folder.id] = merged_id
            alive[folder.id] = merged_id
        else:
            mapping[folder.id] = folder.id
    keys = {}
    for member in payload.members:
        key = keys[member.content_key] = archive_keys.get(member.archive_id or "", member.content_key)
        existing = conn.execute("SELECT * FROM library_folder_members WHERE content_key=?", (key,)).fetchone()
        origin = "manual" if member.origin == "manual" else "collection"
        target_folder_id = alive.get(member.folder_id) if member.folder_id is not None else None
        if member.folder_id is not None and target_folder_id is None:
            continue
        if (existing is None or timestamp(member.added_at) > timestamp(existing["added_at"])
                or (existing["origin"] != "manual" and origin == "manual")):
            write_member(conn, key, target_folder_id, origin, member.added_at or "")
    mapped_cover_deletions = [
        RemoteCoverDeletion(folder_id=mapping.get(d.folder_id, d.folder_id), deleted_at=d.deleted_at)
        for d in payload.deleted_covers
    ]
    merge_cover_deletions(conn, mapped_cover_deletions)
    for cover in payload.covers:
        if cover.folder_id in alive:
            key = keys.get(cover.content_key) or archive_keys.get(cover.archive_id or "", cover.content_key)
            merge_cover(conn, alive[cover.folder_id], key, cover.updated_at)
    merge_collections(conn, payload.collections)
    merge_collection_members(conn, payload.collection_members)
    if conn.total_changes != before:
        changed(conn)
    return mapping


def merge_archive_classification(conn: Connection, classification: ArchiveClassification | None, key: str) -> bool:
    if classification is None:
        return False
    folder = classification.folder
    covers = []
    if classification.cover_updated_at is not None:
        covers.append(RemoteCover(folder_id=folder.id, content_key=key, updated_at=classification.cover_updated_at))
    payload = LibraryPush(
        folders=[folder],
        members=[RemoteMembership(content_key=key, folder_id=folder.id, origin=classification.origin,
                                  added_at=classification.added_at)],
        covers=covers,
    )
    before = conn.total_changes
    merge_push(conn, payload, {})
    return conn.total_changes != before
