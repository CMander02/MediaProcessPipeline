"""Voiceprint library — independent SQLite-vec store.

Lives at {data_root}/voiceprints/library.db. Audio clips kept under
{data_root}/voiceprints/clips/{sample_id}.wav for human audit.

Schema:
  persons            — logical person (user-named, e.g. "张三" or "Unknown-a1b2")
  voiceprint_samples — vec0 virtual table, one row per extracted embedding
  sample_meta        — non-numeric fields for samples (clip path, timestamps)
  task_speaker_map   — (task_id, speaker_label) → sample_id/person_id
"""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_DIM = 256
_VEC_DIM_RE = re.compile(r"embedding\s+float\[(\d+)\]", re.IGNORECASE)


@dataclass
class Person:
    id: str
    name: str
    notes: str
    created_at: str
    sample_count: int = 0


@dataclass
class MatchResult:
    person_id: str
    person_name: str
    sample_id: str
    similarity: float  # cosine similarity (higher = more similar)


def _gen_person_id() -> str:
    return "p_" + hashlib.sha1(str(datetime.now().timestamp()).encode()).hexdigest()[:10]


def _gen_sample_id() -> str:
    return "s_" + hashlib.sha1(str(datetime.now().timestamp()).encode()).hexdigest()[:12]


def _short_hash(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:6]


def _cosine_to_distance(sim: float) -> float:
    """sqlite-vec uses L2 distance by default; we normalize embeddings so
    cosine_similarity = 1 - 0.5 * L2_squared(x, y). We stored unit-norm vectors."""
    return max(0.0, 1.0 - sim)


def _distance_to_cosine(dist: float) -> float:
    return 1.0 - dist


def _normalize(vec: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vec)
    if norm < 1e-8:
        return vec.astype(np.float32)
    return (vec / norm).astype(np.float32)


class VoiceprintStore:
    """Thread-safe singleton store for voiceprint samples and persons."""

    def __init__(self, db_path: Path, clips_dir: Path, embedding_dim: int | None = None):
        self.db_path = db_path
        self.clips_dir = clips_dir
        self.embedding_dim: int | None = embedding_dim
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def _open_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _get_existing_embedding_dim(self, conn: sqlite3.Connection) -> int | None:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'voiceprint_samples'"
        ).fetchone()
        if not row or not row["sql"]:
            return None
        match = _VEC_DIM_RE.search(row["sql"])
        return int(match.group(1)) if match else None

    def _archive_incompatible_db(self, old_dim: int, new_dim: int) -> None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = self.db_path.with_name(
            f"{self.db_path.stem}.dim{old_dim}.bak-{timestamp}{self.db_path.suffix}"
        )
        if self.db_path.exists():
            shutil.move(str(self.db_path), str(backup_path))
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self.db_path}{suffix}")
            if sidecar.exists():
                sidecar.unlink()
        logger.warning(
            f"Voiceprint DB embedding dim changed {old_dim} -> {new_dim}; "
            f"archived incompatible DB to {backup_path}"
        )

    def _close_conn(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        self._conn.close()
        self._conn = None

    def configure_embedding_dim(self, embedding_dim: int) -> None:
        new_dim = int(embedding_dim)
        if new_dim <= 0:
            raise ValueError(f"embedding_dim must be positive, got {new_dim}")

        if self.embedding_dim is None:
            self.embedding_dim = new_dim
        elif self.embedding_dim != new_dim:
            if self._conn is not None:
                current_dim = self._get_existing_embedding_dim(self._conn)
                self._close_conn()
                if current_dim is not None and current_dim != new_dim:
                    self._archive_incompatible_db(current_dim, new_dim)
            self.embedding_dim = new_dim

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.clips_dir.mkdir(parents=True, exist_ok=True)
            conn = self._open_conn()
            existing_dim = self._get_existing_embedding_dim(conn)
            if self.embedding_dim is None:
                self.embedding_dim = existing_dim or DEFAULT_EMBEDDING_DIM
            elif existing_dim is not None and existing_dim != self.embedding_dim:
                conn.close()
                self._archive_incompatible_db(existing_dim, self.embedding_dim)
                conn = self._open_conn()
            self._init_schema(conn)
            self._conn = conn
            logger.info(
                f"Voiceprint DB opened at {self.db_path} (embedding_dim={self.embedding_dim})"
            )
        return self._conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        embedding_dim = self.embedding_dim or DEFAULT_EMBEDDING_DIM
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS persons (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_persons_name ON persons(name);
        """)
        # vec0 virtual table for embeddings. rowid is int; we map via sample_meta.
        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS voiceprint_samples USING vec0(
                embedding float[{embedding_dim}]
            )
        """)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sample_meta (
                sample_id TEXT PRIMARY KEY,
                rowid INTEGER UNIQUE NOT NULL,
                person_id TEXT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
                task_id TEXT,
                duration_sec REAL,
                quality_score REAL,
                audio_clip_path TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sample_meta_person ON sample_meta(person_id);
            CREATE INDEX IF NOT EXISTS idx_sample_meta_task ON sample_meta(task_id);

            CREATE TABLE IF NOT EXISTS task_speaker_map (
                task_id TEXT NOT NULL,
                speaker_label TEXT NOT NULL,
                sample_id TEXT NOT NULL REFERENCES sample_meta(sample_id) ON DELETE CASCADE,
                person_id TEXT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
                PRIMARY KEY (task_id, speaker_label)
            );
            CREATE INDEX IF NOT EXISTS idx_tsmap_person ON task_speaker_map(person_id);
        """)
        conn.commit()

    # ---- Person CRUD ----

    def create_person(self, name: str, notes: str = "") -> Person:
        pid = _gen_person_id()
        now = datetime.now().isoformat()
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "INSERT INTO persons (id, name, notes, created_at) VALUES (?, ?, ?, ?)",
                (pid, name, notes, now),
            )
            conn.commit()
        return Person(id=pid, name=name, notes=notes, created_at=now)

    def get_person(self, person_id: str) -> Person | None:
        conn = self._get_conn()
        row = conn.execute(
            """SELECT p.*, (SELECT COUNT(*) FROM sample_meta m WHERE m.person_id = p.id) AS cnt
               FROM persons p WHERE p.id = ?""",
            (person_id,),
        ).fetchone()
        if not row:
            return None
        return Person(
            id=row["id"], name=row["name"], notes=row["notes"] or "",
            created_at=row["created_at"], sample_count=row["cnt"],
        )

    def find_person_by_name(self, name: str) -> Person | None:
        conn = self._get_conn()
        row = conn.execute(
            """SELECT p.*, (SELECT COUNT(*) FROM sample_meta m WHERE m.person_id = p.id) AS cnt
               FROM persons p WHERE p.name = ? LIMIT 1""",
            (name,),
        ).fetchone()
        if not row:
            return None
        return Person(
            id=row["id"], name=row["name"], notes=row["notes"] or "",
            created_at=row["created_at"], sample_count=row["cnt"],
        )

    def list_persons(self) -> list[Person]:
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT p.*, (SELECT COUNT(*) FROM sample_meta m WHERE m.person_id = p.id) AS cnt
               FROM persons p ORDER BY p.created_at DESC"""
        ).fetchall()
        return [
            Person(id=r["id"], name=r["name"], notes=r["notes"] or "",
                   created_at=r["created_at"], sample_count=r["cnt"])
            for r in rows
        ]

    def rename_person(self, person_id: str, new_name: str) -> None:
        conn = self._get_conn()
        with self._lock:
            conn.execute("UPDATE persons SET name = ? WHERE id = ?", (new_name, person_id))
            conn.commit()

    def delete_person(self, person_id: str) -> None:
        """Delete person, its samples, and all task mappings."""
        conn = self._get_conn()
        with self._lock:
            rows = conn.execute(
                "SELECT rowid, audio_clip_path FROM sample_meta WHERE person_id = ?",
                (person_id,),
            ).fetchall()
            for r in rows:
                conn.execute("DELETE FROM voiceprint_samples WHERE rowid = ?", (r["rowid"],))
                if r["audio_clip_path"]:
                    try:
                        Path(r["audio_clip_path"]).unlink(missing_ok=True)
                    except Exception:
                        pass
            conn.execute("DELETE FROM persons WHERE id = ?", (person_id,))
            conn.commit()

    def merge_persons(self, src_id: str, dst_id: str) -> None:
        """Merge src into dst: move all samples and mappings, delete src."""
        if src_id == dst_id:
            return
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "UPDATE sample_meta SET person_id = ? WHERE person_id = ?",
                (dst_id, src_id),
            )
            conn.execute(
                "UPDATE task_speaker_map SET person_id = ? WHERE person_id = ?",
                (dst_id, src_id),
            )
            conn.execute("DELETE FROM persons WHERE id = ?", (src_id,))
            conn.commit()

    # ---- Sample CRUD ----

    def add_sample(
        self,
        person_id: str,
        embedding: np.ndarray,
        task_id: str | None,
        duration_sec: float,
        quality_score: float,
        audio_clip_path: str | None,
    ) -> str:
        embedding = np.asarray(embedding, dtype=np.float32).reshape(-1)
        self.configure_embedding_dim(embedding.shape[0])
        if embedding.shape != (self.embedding_dim,):
            raise ValueError(
                f"embedding must be shape ({self.embedding_dim},), got {embedding.shape}"
            )
        vec = _normalize(embedding)
        sample_id = _gen_sample_id()
        now = datetime.now().isoformat()
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute(
                "INSERT INTO voiceprint_samples (embedding) VALUES (?)",
                (vec.tobytes(),),
            )
            rowid = cur.lastrowid
            conn.execute(
                """INSERT INTO sample_meta
                   (sample_id, rowid, person_id, task_id, duration_sec, quality_score, audio_clip_path, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (sample_id, rowid, person_id, task_id, duration_sec, quality_score, audio_clip_path, now),
            )
            conn.commit()
        return sample_id

    def delete_sample(self, sample_id: str) -> None:
        conn = self._get_conn()
        with self._lock:
            row = conn.execute(
                "SELECT rowid, audio_clip_path FROM sample_meta WHERE sample_id = ?",
                (sample_id,),
            ).fetchone()
            if row:
                conn.execute("DELETE FROM voiceprint_samples WHERE rowid = ?", (row["rowid"],))
                conn.execute("DELETE FROM sample_meta WHERE sample_id = ?", (sample_id,))
                if row["audio_clip_path"]:
                    try:
                        Path(row["audio_clip_path"]).unlink(missing_ok=True)
                    except Exception:
                        pass
                conn.commit()

    # ---- Matching ----

    def match(self, embedding: np.ndarray, top_k: int = 5) -> list[MatchResult]:
        """Find top_k nearest samples by cosine similarity. Caller applies thresholds."""
        embedding = np.asarray(embedding, dtype=np.float32).reshape(-1)
        self.configure_embedding_dim(embedding.shape[0])
        if embedding.shape != (self.embedding_dim,):
            raise ValueError(f"embedding must be shape ({self.embedding_dim},), got {embedding.shape}")
        vec = _normalize(embedding)
        conn = self._get_conn()
        rows = conn.execute(
            f"""SELECT v.rowid, v.distance, m.sample_id, m.person_id, p.name AS person_name
                FROM voiceprint_samples v
                JOIN sample_meta m ON m.rowid = v.rowid
                JOIN persons p ON p.id = m.person_id
                WHERE v.embedding MATCH ? AND k = {int(top_k)}
                ORDER BY v.distance""",
            (vec.tobytes(),),
        ).fetchall()
        return [
            MatchResult(
                person_id=r["person_id"],
                person_name=r["person_name"],
                sample_id=r["sample_id"],
                similarity=_distance_to_cosine(r["distance"]),
            )
            for r in rows
        ]

    def match_best_person(
        self,
        embedding: np.ndarray,
        top_k: int = 5,
    ) -> MatchResult | None:
        """Aggregate top-k by person_id (max similarity wins)."""
        candidates = self.match(embedding, top_k=top_k)
        if not candidates:
            return None
        # Pick max similarity per person, then overall best
        best: dict[str, MatchResult] = {}
        for c in candidates:
            if c.person_id not in best or c.similarity > best[c.person_id].similarity:
                best[c.person_id] = c
        return max(best.values(), key=lambda r: r.similarity)

    # ---- Task ↔ speaker map ----

    def set_task_speaker(
        self,
        task_id: str,
        speaker_label: str,
        sample_id: str,
        person_id: str,
    ) -> None:
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                """INSERT OR REPLACE INTO task_speaker_map
                   (task_id, speaker_label, sample_id, person_id)
                   VALUES (?, ?, ?, ?)""",
                (task_id, speaker_label, sample_id, person_id),
            )
            conn.commit()

    def get_task_speaker(self, task_id: str, speaker_label: str) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            """SELECT m.*, p.name AS person_name
               FROM task_speaker_map m JOIN persons p ON p.id = m.person_id
               WHERE m.task_id = ? AND m.speaker_label = ?""",
            (task_id, speaker_label),
        ).fetchone()
        return dict(row) if row else None

    def list_task_speakers(self, task_id: str) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT m.*, p.name AS person_name
               FROM task_speaker_map m JOIN persons p ON p.id = m.person_id
               WHERE m.task_id = ?""",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def clip_path_for_sample(self, sample_id: str) -> str | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT audio_clip_path FROM sample_meta WHERE sample_id = ?",
            (sample_id,),
        ).fetchone()
        return row["audio_clip_path"] if row else None


# ---- Singleton ----

_store: VoiceprintStore | None = None


def get_voiceprint_store() -> VoiceprintStore:
    global _store
    if _store is None:
        from app.core.settings import get_runtime_settings
        rt = get_runtime_settings()
        root = Path(rt.data_root).resolve() / "voiceprints"
        _store = VoiceprintStore(db_path=root / "library.db", clips_dir=root / "clips")
    return _store


def reset_voiceprint_store() -> None:
    """For tests or data_root changes."""
    global _store
    if _store and _store._conn:
        _store._conn.close()
    _store = None
