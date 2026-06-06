"""Rebuild tasks.db from archive directories without regenerating artifacts.

This script rebuilds the SQLite task index from existing archive folders under
settings.data_root. It intentionally does not call ASR/LLM services, does not
create detail.md, and leaves task_artifacts empty for historical tasks.

Usage:
    cd backend
    uv run python ../scripts/rebuild_tasks_db.py --dry-run
    uv run python ../scripts/rebuild_tasks_db.py --yes
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import sqlite3
import sys
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen

if sys.platform == "win32":
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.database import SCHEMA, _MIGRATIONS  # noqa: E402
from app.core.settings import get_runtime_settings  # noqa: E402


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
PIPELINE_STEPS = [
    "download",
    "separate",
    "transcribe",
    "voiceprint",
    "polish",
    "analyze",
    "archive",
]
SKIP_DIRS = {"_staging", "uploads", "manual_task"}
ARCHIVE_MARKERS = (
    "metadata.json",
    "transcript.srt",
    "transcript_polished.srt",
    "summary.md",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", help="Override settings.data_root")
    parser.add_argument("--dry-run", action="store_true", help="Scan and report without writing")
    parser.add_argument("--yes", action="store_true", help="Confirm destructive DB rebuild")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
        try:
            data = json.loads(raw.decode(enc))
            return data if isinstance(data, dict) else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value)
        except (OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    for candidate in (
        text,
        text.replace("/", "-"),
    ):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            pass
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def file_dt(path: Path, attr: str) -> datetime:
    stat = path.stat()
    timestamp = stat.st_ctime if attr == "ctime" else stat.st_mtime
    return datetime.fromtimestamp(timestamp)


def is_archive_dir(path: Path) -> bool:
    if not path.is_dir() or path.name.startswith(".") or path.name in SKIP_DIRS:
        return False
    return any((path / marker).exists() for marker in ARCHIVE_MARKERS)


def valid_uuid(value: Any) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def stable_task_id(path: Path) -> uuid.UUID:
    normalized = str(path.resolve()).replace("\\", "/").lower()
    return uuid.uuid5(uuid.NAMESPACE_URL, normalized)


def infer_platform(source: str, metadata: dict[str, Any]) -> str | None:
    extra = metadata.get("extra") if isinstance(metadata.get("extra"), dict) else {}
    platform = metadata.get("platform") or extra.get("platform")
    if platform:
        return str(platform)

    lower = source.lower()
    if "youtube.com" in lower or "youtu.be" in lower:
        return "youtube"
    if "bilibili.com" in lower or "b23.tv" in lower:
        return "bilibili"
    if "xiaoyuzhoufm.com" in lower:
        return "xiaoyuzhou"
    if "podcasts.apple.com" in lower:
        return "apple_podcast"
    if "xiaohongshu.com" in lower or "xhslink.com" in lower:
        return "xiaohongshu"
    if "zhihu.com" in lower:
        return "zhihu"
    if source and "://" not in source:
        return "local"
    return None


def file_manifest(task_dir: Path) -> dict[str, str]:
    candidates = {
        "metadata": "metadata.json",
        "analysis": "analysis.json",
        "srt": "transcript.srt",
        "polished_srt": "transcript_polished.srt",
        "polished_md": "transcript_polished.md",
        "summary_json": "summary.json",
        "summary": "summary.md",
        "mindmap": "mindmap.md",
        "mindmap_json": "mindmap.json",
        "detail": "detail.md",
    }
    return {
        key: str((task_dir / filename).resolve())
        for key, filename in candidates.items()
        if (task_dir / filename).exists()
    }


def light_metadata(metadata: dict[str, Any], task_dir: Path, source: str) -> dict[str, Any]:
    extra = metadata.get("extra") if isinstance(metadata.get("extra"), dict) else {}
    media_type = metadata.get("media_type") or "other"
    platform = infer_platform(source, metadata)
    content_subtype = metadata.get("content_subtype") or extra.get("content_subtype")
    if not content_subtype:
        content_subtype = "local_file" if platform == "local" else media_type

    keys = [
        "title",
        "source_url",
        "uploader",
        "uploader_id",
        "platform",
        "upload_date",
        "duration_seconds",
        "media_type",
        "content_subtype",
        "file_path",
        "file_hash",
    ]
    result = {key: metadata.get(key) for key in keys if metadata.get(key) is not None}
    result.setdefault("title", task_dir.name)
    result.setdefault("source_url", source)
    result.setdefault("media_type", media_type)
    if platform:
        result["platform"] = platform
    if content_subtype:
        result["content_subtype"] = content_subtype
    return result


def infer_status(metadata: dict[str, Any], task_dir: Path) -> str:
    raw = str(metadata.get("status") or "").strip().lower()
    if raw in TERMINAL_STATUSES:
        return raw
    if (task_dir / "summary.md").exists() or (task_dir / "transcript_polished.srt").exists():
        return "completed"
    if any((task_dir / marker).exists() for marker in ARCHIVE_MARKERS):
        return "completed"
    return "failed"


def build_row(task_dir: Path, metadata: dict[str, Any], used_ids: set[str]) -> dict[str, Any]:
    requested_id = valid_uuid(metadata.get("task_id"))
    task_id = requested_id or stable_task_id(task_dir)
    if str(task_id) in used_ids:
        task_id = stable_task_id(task_dir)
    if str(task_id) in used_ids:
        task_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{task_dir.resolve()}::{len(used_ids)}")
    used_ids.add(str(task_id))

    source = str(metadata.get("source_url") or metadata.get("file_path") or task_dir.resolve())
    status = infer_status(metadata, task_dir)
    created = (
        parse_dt(metadata.get("created_at"))
        or parse_dt(metadata.get("task_created_at"))
        or parse_dt(metadata.get("started_at"))
        or parse_dt(metadata.get("upload_date"))
        or file_dt(task_dir, "ctime")
    )
    updated = (
        parse_dt(metadata.get("updated_at"))
        or parse_dt(metadata.get("completed_at"))
        or parse_dt(metadata.get("finished_at"))
        or file_dt(task_dir, "mtime")
    )
    completed = (
        parse_dt(metadata.get("completed_at"))
        or parse_dt(metadata.get("finished_at"))
        or (updated if status in TERMINAL_STATUSES else None)
    )

    meta = light_metadata(metadata, task_dir, source)
    files = file_manifest(task_dir)
    result = {
        "metadata": meta,
        "archive": {
            "output_dir": str(task_dir.resolve()),
            "files": files,
        },
        "output_dir": str(task_dir.resolve()),
        "rebuilt_from_archive": True,
    }

    platform = meta.get("platform")
    uploader_id = meta.get("uploader_id")
    content_subtype = meta.get("content_subtype")

    return {
        "id": str(task_id),
        "task_type": "pipeline",
        "status": status,
        "source": source,
        "options": "{}",
        "progress": 1.0 if status == "completed" else 0.0,
        "message": "rebuilt from archive",
        "result": json.dumps(result, ensure_ascii=False),
        "error": metadata.get("error") if status == "failed" else None,
        "webhook_url": None,
        "created_at": created.isoformat(),
        "updated_at": updated.isoformat(),
        "completed_at": completed.isoformat() if completed else None,
        "current_step": None,
        "steps": json.dumps(PIPELINE_STEPS, ensure_ascii=False),
        "completed_steps": json.dumps(PIPELINE_STEPS if status == "completed" else [], ensure_ascii=False),
        "platform": platform,
        "uploader_id": uploader_id,
        "content_subtype": content_subtype,
    }


def scan_archives(data_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    used_ids: set[str] = set()

    for task_dir in sorted((p for p in data_root.iterdir() if is_archive_dir(p)), key=lambda p: p.name.lower()):
        meta_path = task_dir / "metadata.json"
        metadata: dict[str, Any]
        if meta_path.exists():
            parsed = read_json(meta_path)
            if parsed is None:
                skipped.append(f"unreadable metadata: {task_dir.name}")
                continue
            metadata = parsed
        else:
            metadata = {
                "title": task_dir.name,
                "source_url": str(task_dir.resolve()),
                "status": "completed",
                "media_type": "other",
            }
        rows.append(build_row(task_dir, metadata, used_ids))

    return rows, skipped


def daemon_port_open() -> bool:
    try:
        with urlopen("http://127.0.0.1:18000/health", timeout=1) as response:
            if 200 <= response.status < 500:
                return True
    except Exception:
        pass
    try:
        with socket.create_connection(("127.0.0.1", 18000), timeout=1):
            return True
    except OSError:
        return False


def apply_migrations(conn: sqlite3.Connection) -> None:
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as exc:
            text = str(exc).lower()
            if "duplicate column" not in text and "already exists" not in text:
                raise


def backup_and_remove(db_path: Path) -> Path | None:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path: Path | None = None
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = Path(f"{db_path}{suffix}")
        if not path.exists():
            continue
        backup = Path(f"{db_path}.backup-{timestamp}{suffix}")
        shutil.copy2(path, backup)
        path.unlink()
        if suffix == "":
            backup_path = backup
    return backup_path


def rebuild_db(db_path: Path, rows: list[dict[str, Any]]) -> Path | None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = backup_and_remove(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        apply_migrations(conn)

        columns = list(rows[0].keys()) if rows else [
            "id", "task_type", "status", "source", "options", "progress", "message",
            "result", "error", "webhook_url", "created_at", "updated_at", "completed_at",
            "current_step", "steps", "completed_steps", "platform", "uploader_id",
            "content_subtype",
        ]
        placeholders = ", ".join(f":{column}" for column in columns)
        conn.executemany(
            f"INSERT INTO tasks ({', '.join(columns)}) VALUES ({placeholders})",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return backup_path


def main() -> int:
    args = parse_args()
    data_root = Path(args.data_root or get_runtime_settings().data_root).resolve()
    if not data_root.is_dir():
        print(f"Data root not found: {data_root}", file=sys.stderr)
        return 1

    rows, skipped = scan_archives(data_root)
    status_counts = Counter(row["status"] for row in rows)
    print(f"data_root={data_root}")
    print(f"planned_tasks={len(rows)}")
    print(f"skipped={len(skipped)}")
    print(f"status_counts={dict(sorted(status_counts.items()))}")
    for item in skipped[:20]:
        print(f"skip: {item}")
    if len(skipped) > 20:
        print(f"skip: ... {len(skipped) - 20} more")

    if args.dry_run:
        print("dry_run=true")
        return 0

    if not args.yes:
        print("Refusing to rebuild without --yes. Run --dry-run first, then add --yes.", file=sys.stderr)
        return 2

    if daemon_port_open():
        print("Backend daemon appears to be running on 127.0.0.1:18000. Stop it before rebuilding.", file=sys.stderr)
        return 3

    db_path = data_root / "tasks.db"
    backup_path = rebuild_db(db_path, rows)

    conn = sqlite3.connect(str(db_path))
    try:
        task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        artifact_count = conn.execute("SELECT COUNT(*) FROM task_artifacts").fetchone()[0]
    finally:
        conn.close()

    print(f"rebuilt={db_path}")
    print(f"backup={backup_path}")
    print(f"tasks={task_count}")
    print(f"task_artifacts={artifact_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
