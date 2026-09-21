"""Run the existing daemon with a lifetime owned by the desktop's stdin pipe."""

from __future__ import annotations

import os
import sys
import threading
import time

import uvicorn

from app.cli.serve import _setup_win32_job_object


def wait_for_parent(pipe: int) -> None:
    """Wait for a shutdown byte or EOF without a pending Windows pipe read.

    On Windows, NumPy DLL initialization can stall while another thread has a
    blocking stdin read. PeekNamedPipe observes data/disconnection without
    leaving a pending read on the standard input handle.
    """
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        peek = ctypes.windll.kernel32.PeekNamedPipe
        peek.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
            ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        ]
        peek.restype = wintypes.BOOL
        handle = msvcrt.get_osfhandle(pipe)
        available = wintypes.DWORD()
        while peek(handle, None, 0, None, ctypes.byref(available), None):
            if available.value:
                return
            time.sleep(0.2)
    else:
        os.read(pipe, 1)


def watch_parent(server: uvicorn.Server, pipe: int, finished: threading.Event) -> None:
    """EOF also covers a crashed desktop. Give FastAPI time to release its workers."""
    wait_for_parent(pipe)
    server.should_exit = True
    if not finished.wait(20):
        # Native inference can remain blocked in a thread during shutdown. The
        # Windows Job Object releases model/ffmpeg descendants on this exit.
        os._exit(1)


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    _setup_win32_job_object()
    # A Windows venv's redirector can have a different PID from its interpreter.
    print(f"MPP_DESKTOP_PID={os.getpid()}", flush=True)
    server = uvicorn.Server(uvicorn.Config(
        "app.main:app", host="localhost", port=18000,
        # Other browser clients can keep SSE responses open during desktop exit.
        timeout_graceful_shutdown=5,
    ))
    finished = threading.Event()
    threading.Thread(
        target=watch_parent, args=(server, sys.stdin.fileno(), finished), daemon=True,
        name="mpp-desktop-parent",
    ).start()
    try:
        server.run()
    finally:
        finished.set()


if __name__ == "__main__":
    main()
