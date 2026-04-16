"""Centralised logging setup.

Header format (every line, same shape for first/third-party/uvicorn):

    HH:MM:SS.mmm +ZZZZ LEVEL [t:taskid8 w:worker] logger  (file:line) message

Conventions:
  - Local time + numeric offset (UTC+8 shows `+0800`) so logs align with
    BBDown / BBDown-like child processes that timestamp in local time.
  - Millisecond precision. Seconds-only hid a cleanup-then-read race once,
    never again.
  - task_id and worker are injected from contextvars. No manual prefixes.
  - file:line only shown for WARNING and above, to keep INFO rows tight.
  - `app.` prefix is stripped from logger names.
"""
from __future__ import annotations

import logging
import sys
import time
from contextvars import ContextVar
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ---- Context vars ---------------------------------------------------------

# 8-char task id, or "-" when no task is active
task_id_var: ContextVar[str] = ContextVar("task_id", default="-")
# worker name e.g. "dl-1", "gpu-1", or "-"
worker_var: ContextVar[str] = ContextVar("worker", default="-")

NO_TASK_DISPLAY = "--------"
NO_WORKER_DISPLAY = "----"


def set_task_context(task_id: str | None) -> object:
    """Set the current task_id. Returns a token for reset()."""
    if not task_id:
        return task_id_var.set(NO_TASK_DISPLAY)
    short = str(task_id).replace("-", "")[:8]
    return task_id_var.set(short)


def set_worker_context(name: str | None) -> object:
    if not name:
        return worker_var.set(NO_WORKER_DISPLAY)
    return worker_var.set(name)


def reset_context(token: object, var: ContextVar) -> None:
    try:
        var.reset(token)  # type: ignore[arg-type]
    except Exception:
        pass


# ---- Filter: inject context into record ----------------------------------

class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.task_id = task_id_var.get()
        record.worker = worker_var.get()
        # Strip `app.` prefix so [core.pipeline] not [app.core.pipeline]
        name = record.name
        if name.startswith("app."):
            name = name[4:]
        record.logger_short = name
        return True


# ---- Formatter -----------------------------------------------------------

_LEVEL_ALIAS = {
    "WARNING": "WARN",
    "CRITICAL": "CRIT",
}


class ConsoleFormatter(logging.Formatter):
    """Readable one-line formatter.

    WARNING+ appends `(file.py:line)` between logger and message; INFO does not.
    Multi-line messages are kept as-is (traceback stays legible) — the first
    line gets the full header, subsequent lines are indented for readability.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Time: local, ms, numeric offset
        lt = time.localtime(record.created)
        ms = int((record.created - int(record.created)) * 1000)
        # %z on Windows works on time.strftime via datetime; use datetime for portability
        dt = datetime.fromtimestamp(record.created).astimezone()
        offset = dt.strftime("%z") or "+0000"
        ts = f"{time.strftime('%H:%M:%S', lt)}.{ms:03d} {offset}"

        level = _LEVEL_ALIAS.get(record.levelname, record.levelname)
        level = f"{level:<5}"

        task = getattr(record, "task_id", NO_TASK_DISPLAY) or NO_TASK_DISPLAY
        worker = getattr(record, "worker", NO_WORKER_DISPLAY) or NO_WORKER_DISPLAY
        ctx = f"[t:{task:<8} w:{worker:<5}]"

        logger_name = getattr(record, "logger_short", record.name)

        msg = record.getMessage()

        if record.levelno >= logging.WARNING:
            src = f" ({record.filename}:{record.lineno})"
        else:
            src = ""

        first_line = f"{ts} {level} {ctx} {logger_name}{src}  {msg}"

        # Preserve exception text if any
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
        extra = []
        if record.exc_text:
            extra.append(record.exc_text)
        if record.stack_info:
            extra.append(self.formatStack(record.stack_info))
        if extra:
            # indent continuation lines for visual grouping under the header
            body = "\n".join(extra)
            body = "\n".join("    " + ln if ln else ln for ln in body.splitlines())
            return first_line + "\n" + body
        return first_line


# ---- Public setup --------------------------------------------------------

# Third-party libraries that are chatty at default levels
_NOISY = {
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "openai": logging.WARNING,
    "uvicorn.access": logging.WARNING,
    "speechbrain": logging.INFO,
    "speechbrain.utils.checkpoints": logging.WARNING,
    "speechbrain.utils.quirks": logging.WARNING,
    "audio_separator": logging.INFO,
    "audio_separator.separator.separator": logging.INFO,
    "lightning_fabric": logging.WARNING,
    "pytorch_lightning": logging.WARNING,
    "pyannote": logging.INFO,
    "urllib3": logging.WARNING,
    "filelock": logging.WARNING,
    "asyncio": logging.WARNING,
}


def setup_logging(log_dir: Path | None = None) -> Path | None:
    """Configure root logger with ContextFilter + ConsoleFormatter.

    Creates (and returns path to) a rotating file handler under `log_dir` if
    provided. Returns None if no file handler was configured.
    """
    root = logging.root
    root.setLevel(logging.INFO)
    # Wipe any pre-existing handlers so we own the pipeline
    for h in list(root.handlers):
        root.removeHandler(h)

    formatter = ConsoleFormatter()
    ctx_filter = ContextFilter()

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    stream.addFilter(ctx_filter)
    root.addHandler(stream)

    log_file: Path | None = None
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / time.strftime("mpp_%Y%m%d_%H%M%S.log", time.localtime())
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(ctx_filter)
        root.addHandler(file_handler)

    # Raise levels for noisy third-party loggers
    for name, level in _NOISY.items():
        logging.getLogger(name).setLevel(level)

    # Bind uvicorn's own loggers to our handlers (clear their default ones)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True  # let root format it

    # Suppress Windows ProactorEventLoop ConnectionResetError spam
    if sys.platform == "win32":
        def _filter_connection_reset(record: logging.LogRecord) -> bool:
            exc_info = getattr(record, "exc_info", None)
            if exc_info and exc_info[0] is ConnectionResetError:
                return False
            try:
                msg = record.getMessage()
            except Exception:
                msg = str(getattr(record, "msg", ""))
            return "ConnectionResetError" not in msg
        logging.getLogger("asyncio").addFilter(_filter_connection_reset)

    return log_file
