"""What the machine and the daemon are doing now, for the 系统 view.

Only facts someone can act on: GPU memory, which models MPP holds, worker slots, free space
where the library lives, and errors from the last day. Everything here only reads; nothing
loads a model or initialises CUDA to answer.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from typing import Any, TypeVar

from app.core.paths import get_workspace_paths

T = TypeVar("T")

_GPU_QUERY = "index,name,memory.total,memory.used,utilization.gpu,temperature.gpu"
_ERROR_LEVELS = {"ERROR", "CRIT"}
_EVENT_PREFIX = re.compile(r"^event=\S+\s*")
# "\r\n" pairs, or "\n" at the end: a lone "\n" elsewhere may be part of a Windows path.
_ESCAPED_BREAK = re.compile(r"(?:\\+r)+\\+n|\\+n$")
# Only the end of each log file is read; a day of errors fits comfortably.
_LOG_TAIL_BYTES = 16 * 1024 * 1024

_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def _cached(key: str, ttl: float, compute: Callable[[], T]) -> T:
    """Several open pages poll this; nvidia-smi and log scans need not run for each."""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
    value = compute()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def _number(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None  # "[N/A]" on some drivers


def gpu_status() -> dict[str, Any]:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return {"available": False, "devices": [], "error": "没有找到 nvidia-smi"}
    try:
        output = subprocess.run(
            [executable, f"--query-gpu={_GPU_QUERY}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "devices": [], "error": str(exc)}

    devices = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 6:
            continue
        index, name, total, used, utilization, temperature = parts[:6]
        devices.append({
            "index": int(index) if index.isdigit() else len(devices),
            "name": name,
            "memory_total_mb": _number(total),
            "memory_used_mb": _number(used),
            "utilization": _number(utilization),
            "temperature": _number(temperature),
        })
    return {"available": bool(devices), "devices": devices, "error": None}


def _torch_reserved_mb() -> float | None:
    """CUDA memory this process holds through torch, if torch already uses the GPU."""
    torch = sys.modules.get("torch")
    if torch is None:
        return None
    try:
        if not torch.cuda.is_initialized():
            return None
        return round(torch.cuda.memory_reserved() / 1024 / 1024)
    except Exception:
        return None


def _module_attr(module_name: str, attribute: str) -> Any:
    # A module that was never imported cannot be holding a model.
    module = sys.modules.get(module_name)
    return getattr(module, attribute, None) if module is not None else None


def _argument(cmdline: list[str], *names: str) -> str | None:
    for index, part in enumerate(cmdline[:-1]):
        if part in names:
            return cmdline[index + 1]
    return None


def loaded_models() -> list[dict[str, Any]]:
    models: list[dict[str, Any]] = []

    uvr = _module_attr("app.services.preprocessing.uvr", "_service")
    if uvr is not None and getattr(uvr, "_separator", None) is not None:
        name = getattr(uvr, "_current_model", None) or "UVR"
        models.append({"kind": "人声分离", "name": str(name)})

    sherpa = _module_attr("app.services.recognition.sherpa_runtime", "_runtime")
    if sherpa is not None and getattr(sherpa, "_recognizer", None) is not None:
        info = getattr(sherpa, "info", None)
        name = getattr(info, "model_id", None) or "sherpa-onnx"
        models.append({"kind": "语音识别", "name": str(name)})

    diarization = _module_attr("app.services.recognition.diarization", "_service")
    if diarization is not None and getattr(diarization, "_pipeline", None) is not None:
        key = getattr(diarization, "_pipeline_key", None)
        path = str(key[0]) if isinstance(key, tuple) and key else ""
        models.append({"kind": "说话人分离", "name": Path(path).name or "pyannote"})

    # llama.cpp servers and other model runners that MPP started.
    try:
        import psutil

        for child in psutil.Process().children(recursive=True):
            try:
                model = _argument(child.cmdline(), "--model", "-m")
                name = child.name()
            except (psutil.Error, OSError):
                continue
            if model:
                models.append({"kind": Path(name).stem, "name": Path(model).name, "pid": child.pid})
    except Exception:
        pass
    return models


def disk_status() -> dict[str, Any]:
    root = get_workspace_paths().root
    try:
        usage = shutil.disk_usage(root)
    except OSError as exc:
        return {"path": str(root), "total": None, "used": None, "free": None, "error": str(exc)}
    return {
        "path": str(root), "total": usage.total, "used": usage.used, "free": usage.free,
        "error": None,
    }


def _clock(stamp: str) -> clock_time | None:
    # "2026-09-27 22:21:27.123 +0800": the date is the log file's start date, not the entry's.
    try:
        return datetime.strptime(stamp[11:23], "%H:%M:%S.%f").time()
    except ValueError:
        return None


def _seconds(value: clock_time) -> float:
    return value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1e6


def dated_entries(
    entries: list[dict[str, Any]], modified: datetime,
) -> Iterator[tuple[dict[str, Any], datetime]]:
    """Log entries newest first with their full date, counted back from the file's last write.

    Entries only carry a time of day, and a daemon can run for days, so the date is found by
    walking back from the end and stepping to the previous day whenever the clock jumps
    forward by more than half a day (midnight).
    """
    day: date = modified.date()
    later: clock_time | None = None
    for entry in reversed(entries):
        moment = _clock(str(entry.get("timestamp") or ""))
        if moment is None:
            continue
        if later is None:
            if datetime.combine(day, moment) > modified + timedelta(minutes=5):
                day -= timedelta(days=1)
        elif _seconds(moment) - _seconds(later) > 12 * 3600:
            day -= timedelta(days=1)
        later = moment
        yield entry, datetime.combine(day, moment)


def _tail_lines(path: Path, max_bytes: int) -> list[tuple[int, str]]:
    size = path.stat().st_size
    start = max(0, size - max_bytes)
    lines: list[tuple[int, str]] = []
    with path.open("rb") as handle:
        handle.seek(start)
        if start:
            handle.readline()  # partial line
        while True:
            offset = handle.tell()
            raw = handle.readline()
            if not raw:
                break
            lines.append((offset, raw.decode("utf-8", errors="replace")))
    return lines


def _one_line(message: str) -> str:
    """First line of a log message, without the event= field the view shows separately."""
    text = message.strip().split("\n")[0]
    # Escaped line breaks inside logged values, however many times they were escaped.
    text = _ESCAPED_BREAK.sub(" ", _EVENT_PREFIX.sub("", text))
    return " ".join(text.split())


def recent_errors(since: datetime, limit: int = 8, log_dir: Path | None = None) -> dict[str, Any]:
    """Errors logged since `since`: how many, and the latest distinct ones."""
    from app.api.routes.logs import parse_log_lines

    directory = log_dir or get_workspace_paths().logs
    files = sorted(directory.glob("mpp_*.log*")) if directory.is_dir() else []
    found: list[tuple[datetime, dict[str, Any]]] = []
    for path in files:
        try:
            if not path.is_file():
                continue
            modified = datetime.fromtimestamp(path.stat().st_mtime)
            if modified < since:
                continue
            entries = parse_log_lines(_tail_lines(path, _LOG_TAIL_BYTES), path.name)
        except OSError:
            continue
        for entry, moment in dated_entries(entries, modified):
            if moment < since:
                break
            if entry.get("level") in _ERROR_LEVELS:
                found.append((moment, entry))

    found.sort(key=lambda item: item[0], reverse=True)
    recent: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for moment, entry in found:
        message = _one_line(str(entry.get("message") or ""))
        key = (str(entry.get("module") or ""), str(entry.get("event") or ""), message)
        if key in by_key:
            by_key[key]["count"] += 1
            continue
        item = {
            "time": moment.isoformat(timespec="seconds"),
            "module": key[0],
            "event": key[1],
            "message": message[:300],
            "task_id": entry.get("task_id") or "",
            "count": 1,
        }
        by_key[key] = item
        if len(recent) < limit:
            recent.append(item)
    return {"since": since.isoformat(timespec="seconds"), "count": len(found), "recent": recent}


def _task_title(task: Any) -> str:
    result = task.result or {}
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    title = str(metadata.get("title") or "").strip()
    if title:
        return title
    output_dir = str(result.get("output_dir") or "")
    return Path(output_dir).name if output_dir else task.source


def worker_status() -> dict[str, Any]:
    """Call on the event loop: the queue's sets change there."""
    from app.core.database import get_task_store
    from app.core.queue import get_task_queue
    from app.core.settings import get_runtime_settings

    queue = get_task_queue()
    store = get_task_store()
    gpu_task = store.get(queue.gpu_task_id) if queue.gpu_task_id else None
    return {
        "gpu": {
            "busy": gpu_task is not None,
            "task_id": str(gpu_task.id) if gpu_task else None,
            "title": _task_title(gpu_task) if gpu_task else None,
            "step": gpu_task.current_step if gpu_task else None,
        },
        "downloads": {
            "active": len(queue.download_task_ids),
            "slots": max(1, int(get_runtime_settings().max_download_concurrency or 1)),
        },
        "waiting": queue.pending_count,
        "paused": store.count("paused"),
    }


def _started_at() -> str | None:
    try:
        import psutil

        return datetime.fromtimestamp(psutil.Process().create_time()).isoformat(timespec="seconds")
    except Exception:
        return None


def system_status(workers: dict[str, Any]) -> dict[str, Any]:
    """Everything but the workers, which the caller reads on the event loop."""
    from app.core.database import get_task_store
    from app.version import __version__

    since = datetime.now() - timedelta(hours=24)
    return {
        "version": __version__,
        "pid": os.getpid(),
        "started_at": _started_at(),
        "gpu": {**_cached("gpu", 2.0, gpu_status), "mpp_reserved_mb": _torch_reserved_mb()},
        "models": loaded_models(),
        "workers": workers,
        "disk": disk_status(),
        "errors": _cached("errors", 15.0, lambda: recent_errors(since)),
        "failed_tasks_24h": get_task_store().count_since("failed", since),
    }
