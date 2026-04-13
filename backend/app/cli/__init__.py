"""MPP CLI — command-line interface for MediaProcessPipeline."""


def _entry() -> None:
    """Setuptools console_scripts entry point.

    Runs the Windows encoding bootstrap before launching the Typer app,
    so that Rich output works correctly in all Windows terminal environments.
    """
    import sys
    import io
    import os

    if sys.platform == "win32":
        for stream_name in ("stdout", "stderr"):
            stream = getattr(sys, stream_name)
            try:
                if hasattr(stream, "reconfigure"):
                    stream.reconfigure(encoding="utf-8", errors="replace")
                else:
                    buf = stream.buffer
                    setattr(sys, stream_name,
                            io.TextIOWrapper(buf, encoding="utf-8", errors="replace"))
            except Exception:
                pass

        enc = getattr(sys.stdout, "encoding", "") or ""
        if enc.lower().replace("-", "") not in ("utf8", "utf16", "utf32"):
            os.environ["MPP_PLAIN_OUTPUT"] = "1"
            os.environ["MPP_NO_COLOR"] = "1"

    from app.cli.main import app
    app()
