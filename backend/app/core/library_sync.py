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


class LibraryPush(BaseModel):
    folders: list[RemoteFolder] = Field(max_length=100_000)
    members: list[RemoteMembership] = Field(max_length=1_000_000)
    covers: list[RemoteCover] = Field(default_factory=list)
    collections: list[RemoteCollection] = Field(default_factory=list)

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


def remember_folder(conn: Connection, server: str, workspace: str, remote_id: str, local_id: str) -> None:
    conn.execute(
        "INSERT INTO remote_sync_folders VALUES(?,?,?,?) "
        "ON CONFLICT(server_url,workspace_id,remote_folder_id) DO UPDATE SET local_folder_id=excluded.local_folder_id "
        "WHERE local_folder_id!=excluded.local_folder_id "
        "AND NOT EXISTS (SELECT 1 FROM remote_sync_folders WHERE local_folder_id=excluded.local_folder_id) "
        "ON CONFLICT(local_folder_id) DO NOTHING",
        (server, workspace, remote_id, local_id),
    )


def merge_folder(conn: Connection, folder: RemoteFolder, preferred_id: str, fallback_id: str) -> str:
    row = conn.execute("SELECT * FROM library_folders WHERE id=?", (preferred_id,)).fetchone()
    collection_key = folder.source_collection_key or None
    collection_folder = conn.execute(
        "SELECT * FROM library_folders WHERE source_collection_key=?", (collection_key,),
    ).fetchone() if collection_key else None
    row = row or collection_folder
    if row is None and fallback_id != preferred_id:
        row = conn.execute("SELECT * FROM library_folders WHERE id=?", (fallback_id,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO library_folders VALUES(?,?,?,?,?)",
            (fallback_id, folder.name, collection_key, folder.created_at, folder.updated_at),
        )
        return fallback_id
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
    cover = conn.execute("SELECT updated_at FROM library_folder_covers WHERE folder_id=?", (folder_id,)).fetchone()
    if cover and timestamp(updated_at) <= timestamp(cover["updated_at"]):
        return
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
    mapping = {folder.id: merge_folder(conn, folder, folder.id, folder.id) for folder in payload.folders}
    keys = {}
    for member in payload.members:
        key = keys[member.content_key] = archive_keys.get(member.archive_id or "", member.content_key)
        existing = conn.execute("SELECT * FROM library_folder_members WHERE content_key=?", (key,)).fetchone()
        origin = "manual" if member.origin == "manual" else "collection"
        if (existing is None or timestamp(member.added_at) > timestamp(existing["added_at"])
                or (existing["origin"] != "manual" and origin == "manual")):
            write_member(conn, key, mapping.get(member.folder_id), origin, member.added_at or "")
    for cover in payload.covers:
        if cover.folder_id in mapping:
            key = keys.get(cover.content_key) or archive_keys.get(cover.archive_id or "", cover.content_key)
            merge_cover(conn, mapping[cover.folder_id], key, cover.updated_at)
    merge_collections(conn, payload.collections)
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
