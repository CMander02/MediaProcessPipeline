from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core import settings as settings_module  # noqa: E402
from app.core.model_router import EndpointBinding  # noqa: E402
from app.services.analysis import vlm as vlm_module  # noqa: E402


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content="KIND: content\n一张测试图片")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.mark.parametrize(
    ("configured_max_tokens", "expected_max_tokens"),
    [(1024, 1024), (64, 256)],
)
def test_describe_image_honors_configured_token_budget(
    tmp_path,
    monkeypatch,
    configured_max_tokens,
    expected_max_tokens,
):
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image")
    completions = _FakeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    binding = EndpointBinding(
        capability="vlm",
        model="vision-model",
        api_base="https://vlm.example/v1",
        api_key="key",
        configured=True,
        request_kwargs={
            "max_tokens": configured_max_tokens,
            "timeout_sec": 180,
        },
    )

    service = vlm_module.VLMService()
    monkeypatch.setattr(
        settings_module,
        "get_runtime_settings",
        lambda: SimpleNamespace(vlm_max_tokens=4096),
    )
    monkeypatch.setattr(service, "_get_client", lambda _binding: (client, "vision-model"))
    monkeypatch.setattr(
        vlm_module,
        "_encode_image",
        lambda _path: (
            "aW1hZ2U=",
            "image/jpeg",
            {"source_bytes": 5, "payload_bytes": 5},
        ),
    )

    result = service.describe_image(image_path, binding)

    assert result["kind"] == "content"
    assert result["text"] == "一张测试图片"
    assert len(completions.calls) == 1
    assert completions.calls[0]["max_tokens"] == expected_max_tokens
    assert completions.calls[0]["timeout"] == 180
