from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.settings import RuntimeSettings  # noqa: E402
from app.models import Task, TaskStatus, TaskType  # noqa: E402
from app.services.remote_archive_upload import (  # noqa: E402
    RemoteArchiveUploadService,
    _cloudflare_error_code,
    _retry_delay,
)


class _Store:
    def __init__(self, task: Task) -> None:
        self.task = task

    def list(self, **_kwargs):
        return [self.task]


def test_remote_upload_backoff_and_cloudflare_code() -> None:
    request = httpx.Request("GET", "https://mpp.example/health")
    response = httpx.Response(530, text="error code: 1033", request=request)
    error = httpx.HTTPStatusError("remote unavailable", request=request, response=response)

    assert _cloudflare_error_code(error) == "1033"
    assert _retry_delay(15.0, 1) == 30.0
    assert _retry_delay(15.0, 10) == 900.0
    assert _retry_delay(15.0, 10_000) == 900.0


@pytest.mark.asyncio
async def test_remote_health_is_checked_before_archive_upload(tmp_path, monkeypatch) -> None:
    output_dir = tmp_path / "archive"
    output_dir.mkdir()
    (output_dir / "metadata.json").write_text("{}", encoding="utf-8")
    task = Task(
        task_type=TaskType.PIPELINE,
        status=TaskStatus.COMPLETED,
        source="demo.mp4",
        result={"output_dir": str(output_dir)},
    )
    settings = RuntimeSettings(
        data_root=str(tmp_path),
        remote_sync_enabled=True,
        remote_server_url="https://mpp.example",
    )
    service = RemoteArchiveUploadService(
        settings_getter=lambda: settings,
        store_getter=lambda: _Store(task),
    )

    request = httpx.Request("GET", "https://mpp.example/health")
    response = httpx.Response(530, text="error code: 1033", request=request)
    error = httpx.HTTPStatusError("remote unavailable", request=request, response=response)

    async def fail_health(_server_url: str) -> None:
        raise error

    async def fail_upload(*_args, **_kwargs) -> None:
        raise AssertionError("archive upload started while health check was failing")

    monkeypatch.setattr(service, "_check_server_health", fail_health)
    monkeypatch.setattr(service, "_upload", fail_upload)

    with pytest.raises(httpx.HTTPStatusError):
        await service.sync_once(settings)
