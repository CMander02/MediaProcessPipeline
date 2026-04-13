"""Allow running via: python -m app.cli

This module is the entry point. It runs the Windows encoding bootstrap
BEFORE importing anything that touches Rich or sys.stdout.
"""
import sys
import io
import os


def _fix_windows_encoding() -> None:
    """Force UTF-8 on Windows stdout/stderr; activate plain mode on failure."""
    if sys.platform != "win32":
        return

    # Attempt to reconfigure to UTF-8 (Python 3.11+: reconfigure; older: wrap)
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
            else:
                buf = stream.buffer
                setattr(
                    sys,
                    stream_name,
                    io.TextIOWrapper(buf, encoding="utf-8", errors="replace"),
                )
        except Exception:
            pass

    # Probe: check if stdout encoding can represent Unicode.
    # Use the encoding attribute — don't write any bytes to stdout.
    enc = getattr(sys.stdout, "encoding", "") or ""
    if enc.lower().replace("-", "") not in ("utf8", "utf16", "utf32"):
        os.environ["MPP_PLAIN_OUTPUT"] = "1"
        os.environ["MPP_NO_COLOR"] = "1"


_fix_windows_encoding()

from app.cli.main import app  # noqa: E402

app()
