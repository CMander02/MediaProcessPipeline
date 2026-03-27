"""
Backfill duration_seconds into metadata.json for all archives.

Sources (in priority order):
1. Already present in metadata — skip
2. Derive from SRT file last timestamp
3. Probe media file with ffprobe

Usage:
    cd backend && uv run python ../scripts/backfill_duration.py [--dry-run]
"""

import json
import re
import subprocess
import sys
from pathlib import Path

# Resolve data root from settings
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app.core.settings import get_runtime_settings


def read_metadata(meta_path: Path) -> dict | None:
    """Read metadata.json with encoding fallback."""
    raw = meta_path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None


def write_metadata(meta_path: Path, data: dict) -> None:
    """Write metadata.json as UTF-8."""
    meta_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def duration_from_srt(task_dir: Path) -> float | None:
    """Derive duration from the max end-time across all subtitle entries."""
    for name in ("transcript_polished.srt", "transcript.srt"):
        srt_path = task_dir / name
        if srt_path.exists():
            try:
                content = srt_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    content = srt_path.read_bytes().decode("gbk")
                except Exception:
                    continue
            # Find all timestamps (HH:MM:SS,mmm) and take the max
            timestamps = re.findall(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", content)
            if timestamps:
                max_sec = 0.0
                for h, m, s, ms in timestamps:
                    t = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
                    if t > max_sec:
                        max_sec = t
                return max_sec if max_sec > 0 else None
    return None


def duration_from_ffprobe(task_dir: Path) -> float | None:
    """Probe media file with ffprobe for duration."""
    video_exts = {".mp4", ".mkv", ".avi", ".webm", ".mov"}
    audio_exts = {".mp3", ".wav", ".flac", ".m4a", ".ogg"}
    all_exts = video_exts | audio_exts

    # Check source/ directory first
    source_dir = task_dir / "source"
    media_file = None
    if source_dir.exists():
        for f in source_dir.iterdir():
            if f.suffix.lower() in all_exts:
                media_file = f
                break

    # Fallback: try source_url from metadata
    if not media_file:
        meta_path = task_dir / "metadata.json"
        if meta_path.exists():
            meta = read_metadata(meta_path)
            if meta and meta.get("source_url"):
                candidate = Path(meta["source_url"])
                if candidate.exists() and candidate.suffix.lower() in all_exts:
                    media_file = candidate

    if not media_file or not media_file.exists():
        return None

    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                str(media_file),
            ],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            info = json.loads(result.stdout)
            dur = info.get("format", {}).get("duration")
            if dur:
                return float(dur)
    except Exception:
        pass
    return None


def main():
    dry_run = "--dry-run" in sys.argv

    rt = get_runtime_settings()
    data_root = Path(rt.data_root).resolve()

    if not data_root.exists():
        print(f"Data root not found: {data_root}")
        return

    updated = 0
    skipped = 0
    failed = 0

    for task_dir in sorted(data_root.iterdir()):
        if not task_dir.is_dir():
            continue

        meta_path = task_dir / "metadata.json"
        if not meta_path.exists():
            continue

        meta = read_metadata(meta_path)
        if meta is None:
            print(f"  SKIP (unreadable)  {task_dir.name}")
            failed += 1
            continue

        existing = meta.get("duration_seconds") or meta.get("duration")
        if existing:
            print(f"  OK   {existing:>10.1f}s  {task_dir.name}")
            skipped += 1
            continue

        # Try SRT first
        dur = duration_from_srt(task_dir)
        source = "srt"

        # Fallback to ffprobe
        if dur is None:
            dur = duration_from_ffprobe(task_dir)
            source = "ffprobe"

        if dur is None:
            print(f"  FAIL (no source)   {task_dir.name}")
            failed += 1
            continue

        print(f"  SET  {dur:>10.1f}s  [{source:>7}]  {task_dir.name}")

        if not dry_run:
            meta["duration_seconds"] = round(dur, 3)
            write_metadata(meta_path, meta)

        updated += 1

    print(f"\nDone. updated={updated}  skipped={skipped}  failed={failed}"
          + ("  (dry-run)" if dry_run else ""))


if __name__ == "__main__":
    main()
