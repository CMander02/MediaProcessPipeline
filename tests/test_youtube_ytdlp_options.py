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
    monkeypatch.setattr(ytdlp, "shared_runtime_proxy_url", lambda: None)
    monkeypatch.setattr(
        ytdlp,
        "get_runtime_settings",
        lambda: RuntimeSettings(youtube_proxy="127.0.0.1:7897"),
    )

    assert ytdlp.ytdlp_base_opts()["proxy"] == "http://127.0.0.1:7897"


def test_empty_youtube_proxy_uses_shared_runtime_proxy(monkeypatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setattr(ytdlp, "shared_runtime_proxy_url", lambda: "http://127.0.0.1:7897")
    monkeypatch.setattr(ytdlp, "get_runtime_settings", lambda: RuntimeSettings(youtube_proxy=""))

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


def test_ytdlp_base_opts_routes_output_to_logger(monkeypatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setattr(ytdlp, "shared_runtime_proxy_url", lambda: None)
    monkeypatch.setattr(ytdlp, "get_runtime_settings", lambda: RuntimeSettings(youtube_proxy=""))

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
