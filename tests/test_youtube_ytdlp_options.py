import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.settings import RuntimeSettings  # noqa: E402
from app.services.ingestion import ytdlp  # noqa: E402


def _clear_proxy_env(monkeypatch):
    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(key, raising=False)


def test_youtube_proxy_setting_is_passed_to_ytdlp(monkeypatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setattr(ytdlp, "_proxy_from_windows_user_settings", lambda: "")
    monkeypatch.setattr(
        ytdlp,
        "get_runtime_settings",
        lambda: RuntimeSettings(youtube_proxy="127.0.0.1:7897"),
    )

    assert ytdlp.ytdlp_base_opts()["proxy"] == "http://127.0.0.1:7897"


def test_windows_user_proxy_is_used_when_setting_is_empty(monkeypatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setattr(ytdlp, "_proxy_from_windows_user_settings", lambda: "http://127.0.0.1:7897")
    monkeypatch.setattr(ytdlp, "get_runtime_settings", lambda: RuntimeSettings(youtube_proxy=""))

    assert ytdlp.youtube_proxy_url() == "http://127.0.0.1:7897"


def test_youtube_network_errors_are_classified():
    error = RuntimeError(
        "Unable to download API page: HTTPSConnection(host='www.youtube.com', port=443): "
        "Failed to establish a new connection: [WinError 10061]"
    )

    assert ytdlp.is_youtube_network_error(error, "https://www.youtube.com/watch?v=DPe_srf0GlI")
    assert not ytdlp.is_youtube_network_error(error, "https://www.bilibili.com/video/BV123")
