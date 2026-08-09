import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.api.routes import logs  # noqa: E402


def test_parse_log_lines_groups_traceback_and_extracts_context():
    entries = logs.parse_log_lines(
        [
            (
                0,
                '21:16:46.808 +0800 INFO  [t:-------- w:---- ] core.settings  '
                'event=settings.loaded message="配置加载完成"\n',
            ),
            (
                128,
                '21:16:47.100 +0800 WARN  [t:710eda73 w:gpu-1] core.pipeline (pipeline.py:42)  '
                'event=pipeline.failed message="处理失败"\n',
            ),
            (256, "    Traceback (most recent call last):\n"),
        ],
        "mpp_20260809_211646.log",
    )

    assert entries[0]["timestamp"] == "2026-08-09 21:16:46.808 +0800"
    assert entries[0]["module"] == "core.settings"
    assert entries[0]["task_id"] == ""
    assert entries[0]["message"] == "配置加载完成"
    assert entries[1]["level"] == "WARN"
    assert entries[1]["task_id"] == "710eda73"
    assert entries[1]["worker"] == "gpu-1"
    assert entries[1]["source"] == "pipeline.py:42"
    assert "Traceback" in entries[1]["raw"]


def test_log_file_resolution_only_accepts_discovered_files(tmp_path: Path, monkeypatch):
    log_file = tmp_path / "mpp_20260809_211646.log"
    log_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(logs, "LOG_DIR", tmp_path)

    selected, active = logs._resolve_log_file(None)
    assert selected == log_file
    assert active == log_file

    with pytest.raises(HTTPException) as exc_info:
        logs._resolve_log_file("../config.json")
    assert exc_info.value.status_code == 404


def test_active_log_file_prefers_file_owned_by_current_process(tmp_path: Path, monkeypatch):
    current = tmp_path / "mpp_20260809_210000.log"
    newer = tmp_path / "mpp_20260809_220000.log"
    current.write_text("current", encoding="utf-8")
    newer.write_text("newer", encoding="utf-8")
    monkeypatch.setattr(logs, "get_current_log_file", lambda: current)

    assert logs._active_log_file([newer, current]) == current


def test_incremental_read_uses_byte_cursor(tmp_path: Path):
    log_file = tmp_path / "mpp_20260809_211646.log"
    first = "21:16:46.808 +0800 INFO  [t:-------- w:---- ] main  event=log message=ready\n"
    second = "21:16:47.808 +0800 INFO  [t:-------- w:---- ] main  event=log message=next\n"
    log_file.write_text(first, encoding="utf-8")

    lines, cursor, reset, truncated = logs._read_lines(log_file, None, logs.MAX_READ_BYTES)
    assert len(lines) == 1
    assert reset is False
    assert truncated is False

    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(second)

    incremental, next_cursor, reset, truncated = logs._read_lines(
        log_file, cursor, logs.MAX_READ_BYTES
    )
    assert len(incremental) == 1
    assert "message=next" in incremental[0][1]
    assert next_cursor > cursor
    assert reset is False
    assert truncated is False
