"""mpp serve — start the daemon with a Rich Live dashboard."""

from __future__ import annotations

import sys
import uvicorn


def run_server(host: str = "127.0.0.1", port: int = 18000, reload: bool = False) -> None:
    """Start the FastAPI daemon."""
    # Force UTF-8 on Windows
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    from rich.console import Console
    console = Console()
    console.print(f"\n[bold]MediaProcessPipeline[/bold]  :{port}")
    console.print(f"  API   http://{host}:{port}/docs")
    console.print(f"  SSE   http://{host}:{port}/api/tasks/events")
    console.print()

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
    )
