"""mpp serve — start the daemon."""

from __future__ import annotations

import ctypes
import signal
import socket
import sys
import threading
import time
import uvicorn


def _setup_win32_job_object() -> None:
    """Create a Windows Job Object so all child processes die with us.

    When the console window is closed, Windows terminates the lead process.
    Without a Job Object, child processes (ffmpeg, BBDown, etc.) can survive
    as orphans.  By assigning ourselves to a Job Object with the
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE flag, the kernel guarantees that
    every child process is killed when our process handle is closed.
    """
    if sys.platform != "win32":
        return

    kernel32 = ctypes.windll.kernel32

    # Job Object constants
    JobObjectExtendedLimitInformation = 9
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    try:
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        kernel32.SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )

        # Assign current process to the job
        current_process = kernel32.GetCurrentProcess()
        kernel32.AssignProcessToJobObject(job, current_process)

        # Keep a reference so the handle isn't garbage-collected
        _setup_win32_job_object._handle = job
    except Exception:
        pass  # Non-fatal — best effort


def _port_in_use(host: str, port: int) -> bool:
    """Check if a port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def run_server(host: str = "127.0.0.1", port: int = 18000, reload: bool = False) -> None:
    """Start the FastAPI daemon."""
    # Force UTF-8 on Windows
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    # Ensure child processes (ffmpeg, BBDown, etc.) die when we exit
    _setup_win32_job_object()

    from rich.console import Console
    console = Console()

    # Check if port is already in use
    if _port_in_use(host, port):
        console.print(f"[red bold]端口 {port} 已被占用[/red bold]")
        console.print(f"可能已有 daemon 在运行。检查: [cyan]mpp status[/cyan]")
        console.print(f"或手动关闭: [cyan]taskkill /F /PID $(netstat -ano | findstr :{port})[/cyan]")
        raise SystemExit(1)

    console.print(f"\n[bold]MediaProcessPipeline[/bold]  :{port}")
    console.print(f"  API   http://{host}:{port}/docs")
    console.print(f"  SSE   http://{host}:{port}/api/tasks/events")
    console.print()

    # On Windows, Ctrl+C can leave child threads hanging. Force immediate exit
    # on the second SIGINT (or SIGBREAK) so the process never gets stuck.
    if sys.platform == "win32":
        _first_sigint = [True]

        def _force_exit(signum, frame):
            if _first_sigint[0]:
                _first_sigint[0] = False
                console.print("\n[yellow]Shutting down… (press Ctrl+C again to force)[/yellow]")
                raise KeyboardInterrupt
            else:
                console.print("\n[red]Force exit[/red]")
                import os
                os._exit(1)

        signal.signal(signal.SIGINT, _force_exit)
        signal.signal(signal.SIGBREAK, _force_exit)

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
    )


# ---------------------------------------------------------------------------
# Background daemon (auto-start from mpp run/attach when no daemon is up)
# ---------------------------------------------------------------------------

_bg_thread: threading.Thread | None = None
_bg_started_by_cli: bool = False  # True if we launched it (so Ctrl+C can offer to stop it)


def start_daemon_background(host: str = "127.0.0.1", port: int = 18000, timeout: float = 15.0) -> bool:
    """Start uvicorn in a daemon thread. Returns True when /health is reachable.

    Safe to call if the daemon is already running — detects and skips.
    Sets the module-level _bg_started_by_cli flag so callers know whether
    they own the server process.
    """
    global _bg_thread, _bg_started_by_cli

    # Already reachable — nothing to do
    if _port_in_use(host, port):
        _bg_started_by_cli = False
        return True

    def _run() -> None:
        # Suppress uvicorn startup banner — the CLI prints its own message
        import logging
        logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        uvicorn.run(
            "app.main:app",
            host=host,
            port=port,
            reload=False,
            log_level="warning",
        )

    _bg_thread = threading.Thread(target=_run, name="mpp-daemon", daemon=True)
    _bg_thread.start()
    _bg_started_by_cli = True

    # Wait until /health responds (or timeout)
    import httpx
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"http://{host}:{port}/health", timeout=1.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.3)

    return False  # timed out


def daemon_was_started_by_cli() -> bool:
    """Return True if this process launched the background daemon."""
    return _bg_started_by_cli
