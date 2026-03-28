"""HTTP client for communicating with the MPP daemon."""

from __future__ import annotations

import json
from typing import Any, Generator

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:18000"

# Disable proxy for localhost — system proxy (e.g. Clash) would intercept otherwise.
_NO_PROXY = httpx.Client(proxy=None, timeout=10.0)


class MppClient:
    """Thin httpx wrapper for the MPP daemon API."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(
            base_url=self.base_url,
            proxy=None,
            timeout=timeout,
            headers={"X-Requested-With": "mpp-cli"},
        )

    def _url(self, path: str) -> str:
        return path

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        """Check if the daemon is reachable."""
        try:
            r = self._client.get("/health", timeout=3.0)
            return r.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError, OSError):
            return False

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def create_task(self, source: str, options: dict[str, Any] | None = None) -> dict:
        """Submit a new pipeline task."""
        payload = {
            "task_type": "pipeline",
            "source": source,
            "options": options or {},
        }
        r = self._client.post("/api/tasks", json=payload)
        r.raise_for_status()
        return r.json()

    def get_task(self, task_id: str) -> dict:
        """Get a single task."""
        r = self._client.get(f"/api/tasks/{task_id}")
        r.raise_for_status()
        return r.json()

    def list_tasks(self, status: str | None = None, limit: int = 50) -> list[dict]:
        """List tasks."""
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        r = self._client.get("/api/tasks", params=params)
        r.raise_for_status()
        return r.json()

    def cancel_task(self, task_id: str) -> dict:
        """Cancel a task."""
        r = self._client.post(f"/api/tasks/{task_id}/cancel")
        r.raise_for_status()
        return r.json()

    def get_stats(self) -> dict:
        """Get task statistics."""
        r = self._client.get("/api/tasks/stats")
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # SSE streaming
    # ------------------------------------------------------------------

    def stream_task_events(self, task_id: str) -> Generator[dict, None, None]:
        """Stream SSE events for a specific task. Yields parsed event dicts."""
        with httpx.Client(base_url=self.base_url, proxy=None, timeout=None) as client:
            with client.stream("GET", f"/api/tasks/{task_id}/events") as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        try:
                            yield json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue

    def stream_all_events(self) -> Generator[dict, None, None]:
        """Stream SSE events for ALL tasks. Yields parsed event dicts."""
        with httpx.Client(base_url=self.base_url, proxy=None, timeout=None) as client:
            with client.stream("GET", "/api/tasks/events") as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        try:
                            yield json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def get_settings(self) -> dict:
        """Get current runtime settings."""
        r = self._client.get("/api/settings")
        r.raise_for_status()
        return r.json()

    def patch_settings(self, updates: dict[str, Any]) -> dict:
        """Partially update runtime settings."""
        r = self._client.patch("/api/settings", json=updates)
        r.raise_for_status()
        return r.json()
