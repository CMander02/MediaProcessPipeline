from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import database, system_status  # noqa: E402
from app.models import Task, TaskStatus, TaskType  # noqa: E402


def _line(clock: str, level: str, module: str, event: str, message: str) -> str:
    return (
        f"{clock} +0800 {level:<5} [t:-------- w:---- ] {module}  "
        f'event={event} message="{message}"\n'
    )


def test_dated_entries_count_back_across_midnight():
    entries = [
        {"timestamp": "2026-09-25 23:59:58.000 +0800"},
        {"timestamp": "2026-09-25 00:00:03.000 +0800"},
        # Threads can log a few milliseconds out of order; that is not a new day.
        {"timestamp": "2026-09-25 00:00:02.999 +0800"},
    ]
    modified = datetime(2026, 9, 27, 0, 0, 5)

    dated = [moment for _entry, moment in system_status.dated_entries(entries, modified)]

    assert dated == [
        datetime(2026, 9, 27, 0, 0, 2, 999000),
        datetime(2026, 9, 27, 0, 0, 3),
        datetime(2026, 9, 26, 23, 59, 58),
    ]


def test_recent_errors_counts_the_last_day_and_groups_repeats(tmp_path: Path):
    now = datetime.now().replace(microsecond=0)
    old = now - timedelta(hours=30)
    recent = now - timedelta(minutes=10)
    earlier = tmp_path / f"mpp_{old:%Y%m%d_%H%M%S}.log"
    earlier.write_text(
        _line(old.strftime("%H:%M:%S.000"), "ERROR", "core.queue", "queue.old", "too old"),
        encoding="utf-8",
    )
    os.utime(earlier, (old.timestamp(), old.timestamp()))
    log = tmp_path / f"mpp_{recent:%Y%m%d_%H%M%S}.log"
    log.write_text(
        _line(recent.strftime("%H:%M:%S.000"), "ERROR", "core.pipeline", "task.failed", "下载失败")
        + "    Traceback (most recent call last):\n"
        + _line(recent.strftime("%H:%M:%S.100"), "INFO", "core.queue", "queue.drained", "ok")
        + _line(recent.strftime("%H:%M:%S.200"), "ERROR", "core.pipeline", "task.failed",
                "下载失败")
        + _line(now.strftime("%H:%M:%S.000"), "CRIT", "main", "boom", "显存不足"),
        encoding="utf-8",
    )
    os.utime(log, (now.timestamp(), now.timestamp()))

    errors = system_status.recent_errors(now - timedelta(hours=24), log_dir=tmp_path)

    assert errors["count"] == 3
    assert [item["message"] for item in errors["recent"]] == ["显存不足", "下载失败"]
    assert errors["recent"][1]["count"] == 2
    assert errors["recent"][1]["event"] == "task.failed"


def test_error_messages_are_one_clean_line():
    one_line = system_status._one_line
    assert one_line('event=audio.extract.failed stderr="boom"\nmore') == 'stderr="boom"'
    assert one_line("Exception in ASGI application\\n") == "Exception in ASGI application"
    assert one_line('stderr="a\\\\r\\\\n  b" path=C:\\new') == 'stderr="a b" path=C:\\new'


def test_loaded_models_reads_only_services_already_holding_models(monkeypatch):
    class Uvr:
        _separator = object()
        _current_model = "UVR-MDX-NET-Voc_FT.onnx"

    class Diarization:
        _pipeline = object()
        _pipeline_key = ("D:/models/pyannote/config.yaml", "", "", "cuda", 16)

    class Idle:
        _separator = None

    uvr_module = type(sys)("uvr")
    diarization_module = type(sys)("diarization")
    uvr_module._service = Uvr()
    diarization_module._service = Diarization()
    monkeypatch.setitem(sys.modules, "app.services.preprocessing.uvr", uvr_module)
    monkeypatch.setitem(sys.modules, "app.services.recognition.diarization", diarization_module)

    models = system_status.loaded_models()

    assert {"kind": "人声分离", "name": "UVR-MDX-NET-Voc_FT.onnx"} in models
    assert {"kind": "说话人分离", "name": "config.yaml"} in models

    uvr_module._service = Idle()
    assert all(model["kind"] != "人声分离" for model in system_status.loaded_models())


def test_gpu_status_reads_nvidia_smi(monkeypatch):
    class Result:
        stdout = "0, NVIDIA GeForce RTX 4090, 24564, 17739, 91, 59\n"

    monkeypatch.setattr(system_status.shutil, "which", lambda _name: "nvidia-smi")
    monkeypatch.setattr(system_status.subprocess, "run", lambda *args, **kwargs: Result())

    status = system_status.gpu_status()

    assert status["available"] is True
    assert status["devices"] == [{
        "index": 0, "name": "NVIDIA GeForce RTX 4090", "memory_total_mb": 24564.0,
        "memory_used_mb": 17739.0, "utilization": 91.0, "temperature": 59.0,
    }]


def test_gpu_status_without_nvidia_smi(monkeypatch):
    monkeypatch.setattr(system_status.shutil, "which", lambda _name: None)
    status = system_status.gpu_status()
    assert status == {"available": False, "devices": [], "error": "没有找到 nvidia-smi"}


@pytest.fixture
def store(tmp_path):
    database.reset_db_path(tmp_path)
    yield database.get_task_store()
    database.close_db()


def test_count_since_counts_failures_that_finished_recently(store):
    now = datetime.now()
    for finished, status in [
        (now - timedelta(hours=2), TaskStatus.FAILED),
        (now - timedelta(hours=30), TaskStatus.FAILED),
        (now - timedelta(hours=1), TaskStatus.COMPLETED),
    ]:
        task = Task(task_type=TaskType.PIPELINE, source="a.mp4", status=status)
        task.completed_at = finished
        store.save(task)

    assert store.count_since("failed", now - timedelta(hours=24)) == 1
