import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.settings import RuntimeSettings  # noqa: E402
from app.services.ingestion import ytdlp  # noqa: E402


def _clear_proxy_env(monkeypatch):
    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(key, raising=False)


def test_youtube_proxy_setting_is_passed_to_ytdlp(monkeypatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setattr("app.services.ingestion.ytdlp_options.shared_runtime_proxy_url", lambda: None)
    monkeypatch.setattr(
        "app.services.ingestion.ytdlp_options.get_runtime_settings",
        lambda: RuntimeSettings(youtube_proxy="127.0.0.1:7897"),
    )

    assert ytdlp.ytdlp_base_opts()["proxy"] == "http://127.0.0.1:7897"


def test_empty_youtube_proxy_uses_shared_runtime_proxy(monkeypatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setattr("app.services.ingestion.ytdlp_options.shared_runtime_proxy_url", lambda: "http://127.0.0.1:7897")
    monkeypatch.setattr("app.services.ingestion.ytdlp_options.get_runtime_settings", lambda: RuntimeSettings(youtube_proxy=""))

    assert ytdlp.youtube_proxy_url() == "http://127.0.0.1:7897"


def test_youtube_network_errors_are_classified():
    error = RuntimeError(
        "Unable to download API page: HTTPSConnection(host='www.youtube.com', port=443): "
        "Failed to establish a new connection: [WinError 10061]"
    )

    assert ytdlp.is_youtube_network_error(error, "https://www.youtube.com/watch?v=DPe_srf0GlI")
    assert not ytdlp.is_youtube_network_error(error, "https://www.bilibili.com/video/BV123")


def test_youtube_rate_limit_errors_are_classified():
    error = RuntimeError("Unable to download webpage: HTTP Error 429: Too Many Requests")

    assert ytdlp.is_youtube_network_error(error, "https://www.youtube.com/watch?v=gQgKkUsx5q0")


@pytest.mark.parametrize("apostrophe", ["'", "’"])
def test_youtube_access_challenge_requires_user_action(apostrophe):
    from app.services.ingestion.ytdlp_options import raise_youtube_access_error

    error = RuntimeError(f"Sign in to confirm you{apostrophe}re not a bot")
    with pytest.raises(ytdlp.YoutubeAccessError, match="人机验证"):
        raise_youtube_access_error(error, "https://www.youtube.com/watch?v=ZAzWPE9CItg")
    raise_youtube_access_error(error, "https://example.com/video")
    raise_youtube_access_error(RuntimeError("No video formats found"), "https://youtu.be/ZAzWPE9CItg")


@pytest.mark.parametrize("operation", ["video", "audio", "metadata", "subtitle_probe", "subtitle_download"])
def test_access_challenge_stops_media_and_subtitle_fallbacks(monkeypatch, tmp_path, operation):
    from unittest.mock import Mock

    challenge = RuntimeError("ERROR: [youtube] ZAzWPE9CItg: Sign in to confirm you’re not a bot")
    extract = Mock(side_effect=challenge)
    if operation == "subtitle_download":
        extract.side_effect = None
        extract.return_value = {"subtitles": {"en": [{}]}, "automatic_captions": {"zh": [{}]}}
    download_call = Mock(side_effect=challenge)

    class FakeYoutubeDL:
        extract_info = staticmethod(extract)
        download = staticmethod(download_call)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("app.services.ingestion.public_ytdlp.public_youtube_dl", lambda _opts: FakeYoutubeDL())
    service = ytdlp.YtdlpService()
    url = "https://www.youtube.com/watch?v=ZAzWPE9CItg"
    with pytest.raises(ytdlp.YoutubeAccessError, match="下载已停止"):
        if operation == "video":
            service.download(url, tmp_path)
        elif operation == "audio":
            service._download_audio_only(url, tmp_path, "fixture")
        elif operation == "metadata":
            service.fetch_metadata(url)
        else:
            service.download_subtitles(url, tmp_path)
    extract.assert_called_once()
    assert download_call.call_count == (1 if operation == "subtitle_download" else 0)


def test_ytdlp_base_opts_routes_output_to_logger(monkeypatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setattr("app.services.ingestion.ytdlp_options.shared_runtime_proxy_url", lambda: None)
    monkeypatch.setattr("app.services.ingestion.ytdlp_options.get_runtime_settings", lambda: RuntimeSettings(youtube_proxy=""))

    opts = ytdlp.ytdlp_base_opts()

    assert "logger" in opts
    assert opts["noprogress"] is True
    assert opts["no_color"] is True


def test_ytdlp_logger_captures_rate_limit_warning():
    logger = ytdlp._YtdlpLogger()

    logger.warning("[youtube] Unable to download webpage: HTTP Error 429: Too Many Requests")

    assert logger.has_youtube_network_error("https://www.youtube.com/watch?v=gQgKkUsx5q0")
    assert "429" in logger.network_error_summary()


def test_subtitle_rate_limit_falls_back_to_media_download_and_asr(monkeypatch, tmp_path):
    import yt_dlp

    class FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            assert download is False
            return {
                "subtitles": {},
                "automatic_captions": {"zh-Hans": [{}]},
            }

        def download(self, _urls):
            raise RuntimeError("Unable to download video subtitles for 'zh-Hans': HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYoutubeDL)
    service = ytdlp.YtdlpService()

    result = service.download_subtitles(
        "https://www.youtube.com/watch?v=vif8NQcjVf0",
        tmp_path,
        ["zh-Hans"],
    )

    assert result["subtitle_path"] is None
    assert result["subtitle_engine"] == "yt-dlp"
    assert result["diagnostics"] == [{
        "stage": "subtitle",
        "status": "failed",
        "reason": "rate_limited_or_unreachable",
        "detail": result["diagnostics"][0]["detail"],
        "fallback": "media_download_asr",
    }]
    assert "429" in result["diagnostics"][0]["detail"]
