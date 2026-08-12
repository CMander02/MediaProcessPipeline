"""Authenticated HTTP/SSE client for the MPP daemon."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Generator
from urllib.parse import quote

import httpx

from app.cli.context import get_cli_context, normalize_server_url


class MppClientError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        detail: Any = None,
        retryable: bool = False,
        exit_code: int = 1,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail
        self.retryable = retryable
        self.exit_code = exit_code


class MppClient:
    """Thin, authenticated wrapper around every registered MPP API domain."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        api_token: str | None = None,
    ) -> None:
        ctx = get_cli_context()
        self.base_url = normalize_server_url(base_url or ctx.server_url)
        self.timeout = float(timeout if timeout is not None else ctx.timeout)
        self.api_token = ctx.api_token if api_token is None else api_token
        self._client = httpx.Client(
            base_url=self.base_url,
            proxy=None,
            timeout=self.timeout,
            trust_env=False,
            headers=self._headers(),
        )

    def close(self) -> None:
        self._client.close()

    def _headers(self, *, json_request: bool = False) -> dict[str, str]:
        headers = {"X-Requested-With": "mpp-cli", "Accept": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        if json_request:
            headers["Content-Type"] = "application/json"
        return headers

    def _raise_http_error(self, response: httpx.Response) -> None:
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            payload = response.text.strip()
        detail = payload.get("detail") if isinstance(payload, dict) else payload
        message = str(detail or f"HTTP {response.status_code}")
        mapping = {
            400: ("invalid_request", 2),
            401: ("authentication_failed", 3),
            403: ("capability_denied", 4),
            404: ("not_found", 4),
            409: ("state_conflict", 4),
            413: ("payload_too_large", 2),
            422: ("validation_error", 2),
        }
        code, exit_code = mapping.get(response.status_code, ("server_error", 1))
        raise MppClientError(
            code,
            message,
            status_code=response.status_code,
            detail=payload,
            retryable=response.status_code >= 500,
            exit_code=exit_code,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        data: Any = None,
        files: Any = None,
        timeout: float | None = None,
    ) -> Any:
        try:
            response = self._client.request(
                method,
                path,
                params=params,
                json=json_body,
                data=data,
                files=files,
                timeout=timeout if timeout is not None else self.timeout,
            )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError, OSError) as exc:
            raise MppClientError(
                "connection_failed",
                f"Cannot connect to {self.base_url}: {exc}",
                detail={"server": self.base_url},
                retryable=True,
                exit_code=3,
            ) from exc
        if response.is_error:
            self._raise_http_error(response)
        if response.status_code == 204 or not response.content:
            return None
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()
        return response.text

    def download(self, path: str, *, params: dict[str, Any] | None = None) -> bytes:
        try:
            response = self._client.get(path, params=params, timeout=self.timeout)
        except (httpx.HTTPError, OSError) as exc:
            raise MppClientError(
                "connection_failed",
                f"Cannot download from {self.base_url}: {exc}",
                retryable=True,
                exit_code=3,
            ) from exc
        if response.is_error:
            self._raise_http_error(response)
        return response.content

    # Health, access, and capabilities

    def health(self) -> dict[str, Any]:
        return self.request("GET", "/health")

    def ping(self) -> bool:
        try:
            return self.health().get("status") == "healthy"
        except MppClientError:
            return False

    def auth_status(self) -> dict[str, Any]:
        return self.request("GET", "/api/auth/status")

    def auth_unlock(self, token: str, client: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"token": token}
        if client:
            body["client"] = client
        return self.request("POST", "/api/auth/unlock", json_body=body)

    def auth_logout(self) -> dict[str, Any]:
        return self.request("POST", "/api/auth/logout", json_body={})

    def capabilities(self) -> dict[str, Any]:
        return self.request("GET", "/api/capabilities")

    # Tasks

    def create_task(
        self,
        source: str,
        options: dict[str, Any] | None = None,
        webhook_url: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task_type": "pipeline",
            "source": source,
            "options": options or {},
        }
        if webhook_url:
            payload["webhook_url"] = webhook_url
        return self.request("POST", "/api/tasks", json_body=payload)

    def create_tasks_batch(
        self,
        sources: list[str],
        options: dict[str, Any] | None = None,
        webhook_url: str | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "task_type": "pipeline",
            "sources": sources,
            "options": options or {},
        }
        if webhook_url:
            payload["webhook_url"] = webhook_url
        return self.request("POST", "/api/tasks/batch", json_body=payload)

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self.request("GET", f"/api/tasks/{task_id}")

    def list_tasks(
        self,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        statuses: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if statuses:
            params["statuses"] = ",".join(statuses)
        return self.request("GET", "/api/tasks", params=params)

    def task_stats(self) -> dict[str, Any]:
        return self.request("GET", "/api/tasks/stats")

    get_stats = task_stats

    def task_steps(self) -> dict[str, Any]:
        return self.request("GET", "/api/tasks/steps")

    def task_history(
        self,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        return self.request("GET", "/api/tasks/history", params=params)

    def task_history_stats(self) -> dict[str, Any]:
        return self.request("GET", "/api/tasks/history/stats")

    def task_timeline(self, task_id: str, limit: int = 1000) -> dict[str, Any]:
        return self.request("GET", f"/api/tasks/{task_id}/timeline", params={"limit": limit})

    def task_action(self, task_id: str, action: str) -> dict[str, Any]:
        if action not in {"cancel", "pause", "resume", "checkpoint-rerun"}:
            raise ValueError(f"Unsupported task action: {action}")
        return self.request("POST", f"/api/tasks/{task_id}/{action}", json_body={})

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        return self.task_action(task_id, "cancel")

    def pause_task(self, task_id: str) -> dict[str, Any]:
        return self.task_action(task_id, "pause")

    def resume_task(self, task_id: str) -> dict[str, Any]:
        return self.task_action(task_id, "resume")

    def checkpoint_rerun_task(self, task_id: str) -> dict[str, Any]:
        return self.task_action(task_id, "checkpoint-rerun")

    def delete_task(self, task_id: str) -> dict[str, Any]:
        return self.request("DELETE", f"/api/tasks/{task_id}", json_body={})

    def delete_history(self, task_id: str) -> dict[str, Any]:
        return self.request("DELETE", f"/api/tasks/history/{task_id}", json_body={})

    # SSE

    def _stream_events(self, path: str) -> Generator[dict[str, Any], None, None]:
        timeout = httpx.Timeout(
            connect=max(self.timeout, 10.0), read=None, write=self.timeout, pool=self.timeout
        )
        try:
            with httpx.Client(
                base_url=self.base_url,
                proxy=None,
                timeout=timeout,
                trust_env=False,
                headers=self._headers(),
            ) as client:
                with client.stream("GET", path) as response:
                    if response.is_error:
                        response.read()
                        self._raise_http_error(response)
                    for line in response.iter_lines():
                        if line.startswith("data: "):
                            try:
                                yield json.loads(line[6:])
                            except json.JSONDecodeError:
                                continue
        except MppClientError:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise MppClientError(
                "connection_failed",
                f"SSE connection to {self.base_url} failed: {exc}",
                retryable=True,
                exit_code=3,
            ) from exc

    def stream_task_events(self, task_id: str) -> Generator[dict[str, Any], None, None]:
        yield from self._stream_events(f"/api/tasks/{task_id}/events")

    def stream_all_events(self) -> Generator[dict[str, Any], None, None]:
        yield from self._stream_events("/api/tasks/events")

    # Settings and models

    def get_settings(self) -> dict[str, Any]:
        return self.request("GET", "/api/settings")

    def patch_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        return self.request("PATCH", "/api/settings", json_body=updates)

    def put_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        return self.request("PUT", "/api/settings", json_body=settings)

    def ytdlp_status(self) -> dict[str, Any]:
        return self.request("GET", "/api/settings/ytdlp")

    def upgrade_ytdlp(self) -> dict[str, Any]:
        return self.request("POST", "/api/settings/ytdlp/upgrade", json_body={})

    def local_asr_models(self) -> dict[str, Any]:
        return self.request("GET", "/api/settings/asr/models")

    def detect_local_uvr(self) -> dict[str, Any]:
        return self.request("GET", "/api/settings/uvr/local")

    def provider_catalog(self, provider_id: str, capability: str = "") -> dict[str, Any]:
        params = {"capability": capability} if capability else None
        return self.request(
            "GET", f"/api/settings/providers/{provider_id}/models/catalog", params=params
        )

    def provider_oauth_status(self, provider_id: str) -> dict[str, Any]:
        return self.request("GET", f"/api/settings/providers/{provider_id}/oauth/status")

    def sync_provider_models(self, provider_id: str) -> dict[str, Any]:
        return self.request(
            "POST", f"/api/settings/providers/{provider_id}/models/sync", json_body={}
        )

    def infer_model_metadata(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/api/settings/providers/models/metadata", json_body=payload)

    def provider_balance(self, provider_id: str) -> dict[str, Any]:
        return self.request("POST", f"/api/settings/providers/{provider_id}/balance", json_body={})

    # Pipeline intake, archives, sources, and maintenance

    def stage_file(self, file_path: Path) -> dict[str, Any]:
        try:
            with file_path.open("rb") as handle:
                return self.request(
                    "POST",
                    "/api/pipeline/stage",
                    files={"file": (file_path.name, handle, "application/octet-stream")},
                    timeout=max(self.timeout, 3600.0),
                )
        except OSError as exc:
            raise MppClientError("file_error", str(exc), exit_code=2) from exc

    def delete_staged(self, staging_id: str) -> dict[str, Any]:
        return self.request("DELETE", f"/api/pipeline/stage/{staging_id}", json_body={})

    def probe_source(self, url: str) -> dict[str, Any]:
        return self.request(
            "GET", "/api/pipeline/probe", params={"url": url}, timeout=max(self.timeout, 120.0)
        )

    def bilibili_collection(self, url: str) -> dict[str, Any]:
        return self.request(
            "GET",
            "/api/pipeline/bilibili/collection",
            params={"url": url},
            timeout=max(self.timeout, 120.0),
        )

    def list_archives(self, lite: bool = False) -> list[dict[str, Any]]:
        payload = self.request("GET", "/api/pipeline/archives", params={"lite": str(lite).lower()})
        return payload.get("archives", [])

    def get_archive(self, path: str) -> dict[str, Any]:
        payload = self.request("GET", "/api/pipeline/archives/detail", params={"path": path})
        return payload.get("archive", payload)

    def archive_thumbnail(self, path: str) -> bytes:
        return self.download("/api/pipeline/archives/thumbnail", params={"path": path})

    def siliconflow_models(self) -> dict[str, Any]:
        return self.request("GET", "/api/settings/providers/siliconflow/models")

    def rename_archive(self, path: str, title: str) -> dict[str, Any]:
        return self.request(
            "POST", "/api/pipeline/archives/rename", json_body={"path": path, "title": title}
        )

    def delete_archive(self, path: str) -> dict[str, Any]:
        return self.request("DELETE", "/api/pipeline/archives", json_body={"path": path})

    def pipeline_action(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return self.request(
            "POST",
            f"/api/pipeline/{action}",
            json_body=payload,
            params=params,
            timeout=max(self.timeout, 3600.0),
        )

    def disk_usage(self) -> dict[str, Any]:
        return self.request("GET", "/api/pipeline/disk-usage")

    def cleanup_task(self, task_id: str, dry_run: bool = False) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/api/pipeline/cleanup/{task_id}",
            params={"dry_run": str(dry_run).lower()},
            json_body={},
        )

    def cleanup_all(self, max_age_hours: int, dry_run: bool = False) -> dict[str, Any]:
        return self.request(
            "POST",
            "/api/pipeline/cleanup",
            params={"max_age_hours": max_age_hours, "dry_run": str(dry_run).lower()},
            json_body={},
        )

    def platforms(self) -> list[dict[str, Any]]:
        return self.request("GET", "/api/pipeline/platforms").get("platforms", [])

    def update_platform(self, platform_id: str, config: dict[str, Any]) -> dict[str, Any]:
        return self.request("PUT", f"/api/pipeline/platforms/{platform_id}", json_body=config)

    def source_auth_status(self, platform: str) -> dict[str, Any]:
        paths = {
            "bilibili": "/api/pipeline/bilibili/status",
            "xiaohongshu": "/api/pipeline/xiaohongshu/auth/status",
            "twitter": "/api/pipeline/twitter/auth/status",
            "x": "/api/pipeline/twitter/auth/status",
        }
        if platform not in paths:
            raise MppClientError(
                "unsupported_platform", f"No auth status endpoint for {platform}", exit_code=2
            )
        return self.request("GET", paths[platform])

    def source_auth_login(self, platform: str, timeout_sec: int = 180) -> dict[str, Any]:
        normalized = "twitter" if platform == "x" else platform
        if normalized not in {"xiaohongshu", "twitter"}:
            raise MppClientError(
                "unsupported_platform",
                f"Interactive login is unavailable for {platform}",
                exit_code=2,
            )
        return self.request(
            "POST",
            f"/api/pipeline/{normalized}/auth/login",
            json_body={"timeout_sec": timeout_sec},
            timeout=timeout_sec + 30,
        )

    # Filesystem

    def fs_drives(self) -> dict[str, Any]:
        return self.request("GET", "/api/filesystem/drives")

    def fs_list(self, path: str, mode: str = "all") -> dict[str, Any]:
        return self.request("GET", "/api/filesystem/browse", params={"path": path, "mode": mode})

    def fs_scan(self, path: str, recursive: bool = True) -> dict[str, Any]:
        return self.request(
            "GET",
            "/api/filesystem/scan-folder",
            params={"path": path, "recursive": str(recursive).lower()},
        )

    def fs_read(self, path: str) -> dict[str, Any]:
        return self.request("GET", "/api/filesystem/read", params={"path": path})

    def fs_write(self, path: str, content: str) -> dict[str, Any]:
        return self.request(
            "POST", "/api/filesystem/write", json_body={"path": path, "content": content}
        )

    def fs_download(self, path: str) -> bytes:
        return self.download("/api/filesystem/media", params={"path": path})

    def fs_open(self, path: str) -> dict[str, Any]:
        return self.request("POST", "/api/filesystem/open-folder", json_body={"path": path})

    # Voiceprints and speaker updates

    def voiceprint_persons(self) -> list[dict[str, Any]]:
        return self.request("GET", "/api/voiceprints/persons")

    def update_voiceprint_person(self, person_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        return self.request("PATCH", f"/api/voiceprints/persons/{person_id}", json_body=patch)

    def delete_voiceprint_person(self, person_id: str) -> dict[str, Any]:
        return self.request("DELETE", f"/api/voiceprints/persons/{person_id}", json_body={})

    def merge_voiceprint_persons(self, dst_id: str, src_id: str) -> dict[str, Any]:
        return self.request(
            "POST", f"/api/voiceprints/persons/{dst_id}/merge", json_body={"src_person_id": src_id}
        )

    def voiceprint_sample(self, sample_id: str) -> bytes:
        return self.download(f"/api/voiceprints/samples/{sample_id}/clip")

    def rename_task_speaker(
        self, task_id: str, old_name: str, new_name: str, on_conflict: str
    ) -> dict[str, Any]:
        return self.request(
            "PATCH",
            f"/api/tasks/{task_id}/speakers",
            json_body={"old_name": old_name, "new_name": new_name, "on_conflict": on_conflict},
        )

    # Knowledge base

    def kb_search(
        self,
        query: str,
        top_k: int = 10,
        platform: str | None = None,
        uploader_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"q": query, "top_k": top_k}
        if platform:
            params["platform"] = platform
        if uploader_id:
            params["uploader_id"] = uploader_id
        return self.request(
            "GET", "/api/kb/search", params=params, timeout=max(self.timeout, 120.0)
        )

    def kb_stats(self) -> dict[str, Any]:
        return self.request("GET", "/api/kb/stats")

    def kb_reindex(self) -> dict[str, Any]:
        return self.request(
            "POST", "/api/kb/reindex", json_body={}, timeout=max(self.timeout, 3600.0)
        )

    # Logs

    def log_files(self) -> dict[str, Any]:
        return self.request("GET", "/api/logs/files")

    def read_logs(
        self, file: str | None = None, cursor: int | None = None, max_bytes: int | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if file:
            params["file"] = file
        if cursor is not None:
            params["cursor"] = cursor
        if max_bytes is not None:
            params["max_bytes"] = max_bytes
        return self.request("GET", "/api/logs", params=params)

    # Sync

    def sync_changes(self, cursor: int = 0, limit: int = 100) -> dict[str, Any]:
        return self.request("GET", "/api/sync/changes", params={"cursor": cursor, "limit": limit})

    def sync_manifest(self, archive_id: str) -> dict[str, Any]:
        return self.request("GET", f"/api/sync/archives/{archive_id}/manifest")

    def sync_file(self, archive_id: str, relative_path: str) -> bytes:
        encoded_archive = quote(archive_id, safe="")
        encoded_path = "/".join(
            quote(part, safe="") for part in relative_path.replace("\\", "/").split("/")
        )
        return self.download(f"/api/sync/archives/{encoded_archive}/files/{encoded_path}")

    def sync_rebuild(self) -> dict[str, Any]:
        return self.request("POST", "/api/sync/rebuild", json_body={})
