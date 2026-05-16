"""sqlite-vec knowledge base store."""

from __future__ import annotations

import json
import logging
import sqlite3
import struct
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_db_lock = threading.Lock()


def _serialize_vec(v: list[float]) -> bytes:
    return struct.pack(f"{len(v)}f", *v)


def _deserialize_vec(b: bytes) -> list[float]:
    n = len(b) // 4
    return list(struct.unpack(f"{n}f", b))


class KBStore:
    """sqlite-vec-backed chunk store.

    Uses two tables:
      kb_chunks — text + metadata per chunk
      vec_chunks — virtual table with float embeddings (tied by rowid)
    """

    def __init__(self, db_path: Path, dim: int) -> None:
        self._db_path = db_path
        self._dim = dim
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            try:
                conn.enable_load_extension(True)
                import sqlite_vec
                sqlite_vec.load(conn)
                conn.enable_load_extension(False)
            except Exception as e:
                logger.warning(f"sqlite-vec load failed: {e}")
            self._conn = conn
            self._init_schema(conn)
        return self._conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS kb_chunks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id      TEXT NOT NULL,
            archive_path TEXT NOT NULL,
            source_type  TEXT NOT NULL,
            chunk_index  INTEGER NOT NULL,
            start_ts     REAL,
            end_ts       REAL,
            text         TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kb_task_id ON kb_chunks(task_id);
        """)
        try:
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(embedding float[{self._dim}])"
            )
        except Exception as e:
            logger.warning(f"Failed to create vec_chunks virtual table: {e}")
        conn.commit()

    def upsert_task(self, task_id: str, chunks: list[dict]) -> None:
        """Replace all chunks for a task_id. chunks must include 'embedding' key."""
        conn = self._get_conn()
        with _db_lock:
            rowids = [r[0] for r in conn.execute(
                "SELECT id FROM kb_chunks WHERE task_id = ?", (task_id,)
            ).fetchall()]
            if rowids:
                placeholders = ",".join("?" for _ in rowids)
                try:
                    conn.execute(f"DELETE FROM vec_chunks WHERE rowid IN ({placeholders})", rowids)
                except Exception as e:
                    logger.warning(f"vec_chunks delete failed: {e}")
                conn.execute(f"DELETE FROM kb_chunks WHERE task_id = ?", (task_id,))

            for c in chunks:
                cur = conn.execute(
                    "INSERT INTO kb_chunks (task_id, archive_path, source_type, chunk_index, start_ts, end_ts, text) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        task_id,
                        c["archive_path"],
                        c["source_type"],
                        c["chunk_index"],
                        c.get("start_ts"),
                        c.get("end_ts"),
                        c["text"],
                    ),
                )
                rowid = cur.lastrowid
                emb = c.get("embedding")
                if emb and rowid:
                    try:
                        conn.execute(
                            "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
                            (rowid, _serialize_vec(emb)),
                        )
                    except Exception as e:
                        logger.warning(f"vec_chunks insert failed for rowid {rowid}: {e}")
            conn.commit()

    def delete_task(self, task_id: str) -> None:
        conn = self._get_conn()
        with _db_lock:
            rowids = [r[0] for r in conn.execute(
                "SELECT id FROM kb_chunks WHERE task_id = ?", (task_id,)
            ).fetchall()]
            if rowids:
                placeholders = ",".join("?" for _ in rowids)
                try:
                    conn.execute(f"DELETE FROM vec_chunks WHERE rowid IN ({placeholders})", rowids)
                except Exception:
                    pass
            conn.execute("DELETE FROM kb_chunks WHERE task_id = ?", (task_id,))
            conn.commit()

    def search(
        self,
        query_vec: list[float],
        top_k: int = 10,
        filters: dict | None = None,
    ) -> list[dict]:
        """Vector search. Returns top_k nearest chunks with metadata."""
        conn = self._get_conn()
        try:
            vec_results = conn.execute(
                f"SELECT rowid, distance FROM vec_chunks WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (_serialize_vec(query_vec), top_k * 5),  # over-fetch for filter headroom
            ).fetchall()
        except Exception as e:
            logger.warning(f"vec search failed: {e}")
            return []

        if not vec_results:
            return []

        rowid_map = {r[0]: r[1] for r in vec_results}
        placeholders = ",".join("?" for _ in rowid_map)
        rows = conn.execute(
            f"SELECT * FROM kb_chunks WHERE id IN ({placeholders})",
            list(rowid_map.keys()),
        ).fetchall()

        results = []
        for row in rows:
            results.append({
                "rowid": row["id"],
                "task_id": row["task_id"],
                "archive_path": row["archive_path"],
                "source_type": row["source_type"],
                "chunk_index": row["chunk_index"],
                "start_ts": row["start_ts"],
                "end_ts": row["end_ts"],
                "text": row["text"],
                "score": 1.0 - rowid_map.get(row["id"], 1.0),  # convert distance to similarity
            })

        # Apply post-search filters
        if filters:
            platform = filters.get("platform")
            uploader_id = filters.get("uploader_id")
            if platform or uploader_id:
                from app.core.database import get_task_store
                store = get_task_store()
                filtered = []
                for r in results:
                    task = store.get(__import__("uuid").UUID(r["task_id"])) if r["task_id"] else None
                    if task:
                        if platform and task.platform != platform:
                            continue
                        if uploader_id and task.uploader_id != uploader_id:
                            continue
                    filtered.append(r)
                results = filtered

        return sorted(results, key=lambda x: -x["score"])[:top_k]

    def chunk_count(self) -> int:
        conn = self._get_conn()
        return conn.execute("SELECT COUNT(*) FROM kb_chunks").fetchone()[0]

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


_store: KBStore | None = None


def get_kb_store() -> KBStore:
    global _store
    if _store is None:
        from app.core.settings import get_runtime_settings
        rt = get_runtime_settings()
        db_path = Path(rt.data_root).resolve() / "kb.db"
        _store = KBStore(db_path, rt.kb_embedding_dim)
    return _store


def reset_kb_store() -> None:
    global _store
    if _store:
        _store.close()
    _store = None
