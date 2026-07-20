from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.ingestion.platform.bilibili import collection  # noqa: E402


def test_inspect_bilibili_multipart_collection(monkeypatch):
    monkeypatch.setattr(collection, "view", lambda _bvid: {
        "bvid": "BV1DK4y1b7bY",
        "title": "零基础平面设计入门系列",
        "pic": "//i0.hdslb.com/cover.jpg",
        "pages": [
            {"page": 1, "cid": 1, "part": "第一集 文字排版", "duration": 384},
            {"page": 2, "cid": 2, "part": "第二集 色彩理论", "duration": 393},
        ],
    })

    result = collection.inspect_bilibili_collection(
        "https://www.bilibili.com/video/BV1DK4y1b7bY/?p=2",
    )

    assert result["is_collection"] is True
    assert result["collection_type"] == "multipart"
    assert result["current_item_id"] == "BV1DK4y1b7bY:p2"
    assert [item["title"] for item in result["items"]] == [
        "第一集 文字排版",
        "第二集 色彩理论",
    ]
    assert [item["url"] for item in result["items"]] == [
        "https://www.bilibili.com/video/BV1DK4y1b7bY",
        "https://www.bilibili.com/video/BV1DK4y1b7bY?p=2",
    ]
    assert result["items"][0]["cover"] == "https://i0.hdslb.com/cover.jpg"


def test_inspect_bilibili_ugc_season_collection(monkeypatch):
    monkeypatch.setattr(collection, "view", lambda _bvid: {
        "bvid": "BV1234567890",
        "title": "当前视频",
        "pages": [{"page": 1, "part": "当前视频"}],
        "ugc_season": {
            "title": "系列课程",
            "sections": [{
                "title": "第一章",
                "episodes": [
                    {"bvid": "BV1234567890", "title": "课程一", "arc": {"duration": 120}},
                    {"bvid": "BV0987654321", "title": "课程二", "arc": {"duration": 180}},
                ],
            }],
        },
    })

    result = collection.inspect_bilibili_collection(
        "https://www.bilibili.com/video/BV1234567890/",
    )

    assert result["is_collection"] is True
    assert result["collection_type"] == "ugc_season"
    assert result["title"] == "系列课程"
    assert [item["bvid"] for item in result["items"]] == [
        "BV1234567890",
        "BV0987654321",
    ]
    assert all(item["section"] == "第一章" for item in result["items"])


def test_inspect_non_bilibili_url_skips_api(monkeypatch):
    view = lambda _bvid: (_ for _ in ()).throw(AssertionError("view should not run"))
    monkeypatch.setattr(collection, "view", view)

    result = collection.inspect_bilibili_collection("https://example.com/video/1")

    assert result == {"is_bilibili": False, "is_collection": False, "items": []}
