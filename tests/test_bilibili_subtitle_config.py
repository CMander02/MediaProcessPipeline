import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.settings import RuntimeSettings
from app.services.ingestion.ytdlp import (
    YtdlpService,
    _empty_subtitle_result,
    _filter_and_sort_subtitle_tracks,
)
from app.services.ingestion.platform.bilibili import api as bilibili_api


def test_bilibili_subtitle_tracks_prefer_configured_language_then_cc():
    tracks = [
        {"lan": "en", "type": 0, "subtitle_url": "https://example.test/en.json"},
        {"lan": "zh-CN", "type": 1, "subtitle_url": "https://example.test/zh-ai.json"},
        {"lan": "zh-CN", "type": 0, "subtitle_url": "https://example.test/zh-cc.json"},
    ]

    ordered = _filter_and_sort_subtitle_tracks(tracks, ["zh", "en"])

    assert ordered[0]["subtitle_url"].endswith("zh-cc.json")
    assert ordered[1]["subtitle_url"].endswith("zh-ai.json")
    assert ordered[2]["subtitle_url"].endswith("en.json")


def test_empty_subtitle_result_carries_diagnostics():
    result = _empty_subtitle_result(
        engine="native_wbi",
        diagnostics=[{"reason": "aid_cid_mismatch"}],
    )

    assert result["subtitle_engine"] == "native_wbi"
    assert result["subtitle_path"] is None
    assert result["diagnostics"] == [{"reason": "aid_cid_mismatch"}]


def test_bilibili_subtitle_coverage_setting_is_bounded():
    with pytest.raises(ValidationError):
        RuntimeSettings(bilibili_subtitle_min_coverage=1.5)

    assert RuntimeSettings(bilibili_subtitle_min_coverage=0.7).bilibili_subtitle_min_coverage == 0.7


def test_bilibili_subtitle_reports_login_required(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.ingestion.ytdlp.get_runtime_settings",
        lambda: RuntimeSettings(),
    )
    monkeypatch.setattr(
        bilibili_api,
        "view",
        lambda _bvid: {
            "aid": 123,
            "duration": 60,
            "pages": [{"page": 1, "cid": 456, "duration": 60}],
        },
    )
    monkeypatch.setattr(
        bilibili_api,
        "player_v2",
        lambda _bvid, _aid, _cid: {
            "need_login_subtitle": True,
            "subtitle": {"subtitles": []},
        },
    )

    result = YtdlpService()._download_bilibili_subtitle(
        "https://www.bilibili.com/video/BV12N4y1M7rh",
        tmp_path,
    )

    assert result["subtitle_path"] is None
    assert result["diagnostics"] == [
        {"stage": "track_list", "status": "empty", "reason": "login_required"}
    ]
