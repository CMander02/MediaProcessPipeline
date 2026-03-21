"""mpp serve — start the daemon with a Rich Live dashboard."""

from __future__ import annotations

import socket
import sys
import uvicorn


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

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
    )
