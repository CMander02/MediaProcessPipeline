import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.settings import RuntimeSettings  # noqa: E402
from app.services.analysis._openai_client import make_async_openai_client  # noqa: E402


def test_async_openai_client_honors_direct_network_setting(monkeypatch):
    import httpx
    import openai
    from app.core import settings as settings_module

    captured: dict[str, object] = {}
    fake_http_client = object()

    monkeypatch.setattr(
        settings_module,
        "get_runtime_settings",
        lambda: RuntimeSettings(network_proxy="direct"),
    )

    def fake_async_client(**kwargs):
        captured["httpx"] = kwargs
        return fake_http_client

    def fake_openai(**kwargs):
        captured["openai"] = kwargs
        return object()

    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)
    monkeypatch.setattr(openai, "AsyncOpenAI", fake_openai)

    make_async_openai_client(
        "https://api.deepseek.example",
        "secret",
        max_retries=3,
        timeout=12.0,
    )

    assert captured["httpx"] == {
        "trust_env": False,
        "proxy": None,
        "timeout": 12.0,
    }
    assert captured["openai"]["http_client"] is fake_http_client
    assert captured["openai"]["max_retries"] == 3
