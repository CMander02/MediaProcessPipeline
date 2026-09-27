"""Keyword search over every archive's transcript, for the global Ctrl+K search.

The knowledge base searches by meaning but needs an embedding service; this plain full-text
index is always available. It lives in the workspace's search cache (safe to delete, it is
rebuilt) and catches up with the library in the background, by each transcript's size and
modification time.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from os import stat_result
from pathlib import Path
from typing import Any

from app.core.logging_setup import log_event
from app.core.paths import get_workspace_paths, iter_archive_directories

logger = logging.getLogger(__name__)

# The polished transcript when there is one, else the raw one.
TRANSCRIPT_FILES = ("transcript_polished.srt", "transcript.srt")
SCHEMA_VERSION = 1
# Catch up with the library at most this often.
REFRESH_SECONDS = 30.0

_SPEAKER = re.compile(r"^\[([^\]]+)\]\s*")
_TIMING = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS archive (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    file TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL,
    title TEXT NOT NULL,
    speakers TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cue (
    id INTEGER PRIMARY KEY,
    archive_id INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    speaker TEXT,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cue_archive ON cue(archive_id);
CREATE VIRTUAL TABLE IF NOT EXISTS cue_text USING fts5(
    text, content='cue', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS cue_added AFTER INSERT ON cue BEGIN
    INSERT INTO cue_text(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS cue_removed AFTER DELETE ON cue BEGIN
    INSERT INTO cue_text(cue_text, rowid, text) VALUES ('delete', old.id, old.text);
END;
"""


def parse_cues(content: str) -> list[tuple[int, str | None, str]]:
    """(start in ms, speaker, text) for each cue of an SRT transcript."""
    cues = []
    for block in re.split(r"\n\s*\n", content.replace("\r\n", "\n").replace("\r", "\n")):
        lines = block.strip().split("\n")
        timing = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            continue
        match = _TIMING.search(lines[timing])
        if match is None:
            continue
        hours, minutes, seconds, millis = (int(part) for part in match.groups()[:4])
        text = " ".join(line.strip() for line in lines[timing + 1:] if line.strip())
        text = re.sub(r"<[^>]+>", "", text)
        speaker_match = _SPEAKER.match(text)
        speaker = speaker_match.group(1) if speaker_match else None
        if speaker_match:
            text = text[speaker_match.end():]
        if text:
            start = ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis
            cues.append((start, speaker, text))
    return cues


def _title(directory: Path) -> str:
    try:
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        title = str(metadata.get("title") or "").strip()
    except (OSError, ValueError, AttributeError):
        title = ""
    return title or directory.name


def _phrase(query: str) -> str:
    return '"' + query.replace('"', '""') + '"'


def _like(query: str) -> str:
    return "%" + re.sub(r"([\\%_])", r"\\\1", query) + "%"


class TranscriptSearchService:
    def __init__(self, root: Path | None = None) -> None:
        self._root = root
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._refreshed_at = 0.0
        self._progress: tuple[int, int] | None = None

    def _db_path(self) -> Path:
        return get_workspace_paths(self._root).temporary("search") / "transcripts.db"

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        path = self._db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                conn.executescript(
                    "DROP TABLE IF EXISTS cue_text; DROP TABLE IF EXISTS cue;"
                    " DROP TABLE IF EXISTS archive;"
                )
                conn.executescript(_SCHEMA)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            yield conn
        finally:
            conn.close()

    # -- keeping up with the library ---------------------------------------------------

    def refresh(self) -> dict[str, int]:
        """Index new and changed transcripts and forget removed ones; returns counts."""
        root = get_workspace_paths(self._root).root
        with self._connect() as conn:
            known = {row["path"]: row for row in conn.execute(
                "SELECT id, path, file, mtime_ns, size FROM archive"
            )}
            found: list[tuple[Path, Path, stat_result]] = []
            for directory in iter_archive_directories(root):
                transcript = next(
                    (directory / name for name in TRANSCRIPT_FILES if (directory / name).is_file()),
                    None,
                )
                if transcript is not None:
                    found.append((directory, transcript, transcript.stat()))

            changed = []
            for directory, transcript, stat in found:
                row = known.get(str(directory))
                if (
                    row is None or row["file"] != transcript.name
                    or row["mtime_ns"] != stat.st_mtime_ns or row["size"] != stat.st_size
                ):
                    changed.append((directory, transcript, stat, row["id"] if row else None))

            present = {str(directory) for directory, _transcript, _stat in found}
            removed = [row["id"] for path, row in known.items() if path not in present]
            for archive_id in removed:
                self._forget(conn, archive_id)
            conn.commit()

            self._progress = (0, len(changed))
            for done, (directory, transcript, stat, archive_id) in enumerate(changed, start=1):
                try:
                    self._index(conn, directory, transcript, stat, archive_id)
                    conn.commit()
                except (OSError, UnicodeDecodeError, sqlite3.Error) as exc:
                    conn.rollback()
                    log_event(logger, logging.WARNING, "search.index.failed",
                              path=str(directory), error=exc)
                self._progress = (done, len(changed))
            self._progress = None
            return {"archives": len(found), "indexed": len(changed), "removed": len(removed)}

    @staticmethod
    def _forget(conn: sqlite3.Connection, archive_id: int) -> None:
        conn.execute("DELETE FROM cue WHERE archive_id = ?", (archive_id,))
        conn.execute("DELETE FROM archive WHERE id = ?", (archive_id,))

    def _index(
        self, conn: sqlite3.Connection, directory: Path, transcript: Path,
        stat: stat_result, archive_id: int | None,
    ) -> None:
        cues = parse_cues(transcript.read_text(encoding="utf-8-sig"))
        speakers = sorted({speaker for _start, speaker, _text in cues if speaker})
        if archive_id is not None:
            self._forget(conn, archive_id)
        cursor = conn.execute(
            "INSERT INTO archive(path, file, mtime_ns, size, title, speakers)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (str(directory), transcript.name, stat.st_mtime_ns, stat.st_size,
             _title(directory), "\n".join(speakers)),
        )
        conn.executemany(
            "INSERT INTO cue(archive_id, start_ms, speaker, text) VALUES (?, ?, ?, ?)",
            [(cursor.lastrowid, start, speaker, text) for start, speaker, text in cues],
        )

    def _refresh_in_background(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if time.monotonic() - self._refreshed_at < REFRESH_SECONDS:
                return
            self._refreshed_at = time.monotonic()

            def run() -> None:
                try:
                    self.refresh()
                except Exception as exc:  # the next search tries again
                    self._progress = None
                    self._refreshed_at = 0.0
                    log_event(logger, logging.WARNING, "search.refresh.failed", error=exc)

            self._thread = threading.Thread(target=run, name="transcript-search", daemon=True)
            self._thread.start()

    # -- searching ----------------------------------------------------------------------

    def search(self, query: str, *, limit: int = 30, per_archive: int = 3) -> dict[str, Any]:
        text = " ".join(query.split())
        self._refresh_in_background()
        result: dict[str, Any] = {
            "query": text, "groups": [], "speakers": [],
            "indexing": self._progress is not None, "progress": self._progress,
        }
        if not text:
            return result
        with self._connect() as conn:
            if len(text) >= 3:
                rows = conn.execute(
                    "SELECT a.id, a.path, a.title, c.start_ms, c.speaker, c.text"
                    " FROM cue_text JOIN cue c ON c.id = cue_text.rowid"
                    " JOIN archive a ON a.id = c.archive_id"
                    " WHERE cue_text MATCH ? ORDER BY a.mtime_ns DESC, c.start_ms",
                    (_phrase(text),),
                )
            else:
                # Trigrams need three characters; shorter queries scan the text.
                rows = conn.execute(
                    "SELECT a.id, a.path, a.title, c.start_ms, c.speaker, c.text"
                    " FROM cue c JOIN archive a ON a.id = c.archive_id"
                    " WHERE c.text LIKE ? ESCAPE '\\' ORDER BY a.mtime_ns DESC, c.start_ms",
                    (_like(text),),
                )
            groups: dict[int, dict[str, Any]] = {}
            for row in rows:
                group = groups.get(row["id"])
                if group is None:
                    if len(groups) >= limit:
                        continue
                    group = groups[row["id"]] = {
                        "path": row["path"], "title": row["title"], "count": 0, "hits": [],
                    }
                group["count"] += 1
                if len(group["hits"]) < per_archive:
                    group["hits"].append({
                        "start_ms": row["start_ms"], "speaker": row["speaker"], "text": row["text"],
                    })
            result["groups"] = list(groups.values())

            folded = text.casefold()
            for row in conn.execute(
                "SELECT path, title, speakers FROM archive WHERE speakers LIKE ? ESCAPE '\\'"
                " ORDER BY mtime_ns DESC LIMIT 20",
                (_like(text),),
            ):
                names = [name for name in row["speakers"].split("\n") if folded in name.casefold()]
                for name in names:
                    result["speakers"].append(
                        {"path": row["path"], "title": row["title"], "speaker": name}
                    )
        return result


_service: TranscriptSearchService | None = None


def get_transcript_search_service() -> TranscriptSearchService:
    global _service
    if _service is None:
        _service = TranscriptSearchService()
    return _service
