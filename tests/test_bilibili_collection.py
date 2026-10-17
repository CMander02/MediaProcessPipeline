from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.ingestion.platform.bilibili import collection  # noqa: E402


def test_inspect_bilibili_multipart_collection(monkeypatch):
    view = Mock(return_value={
        "bvid": "BV1DK4y1b7bY",
        "title": "零基础平面设计入门系列",
        "pic": "//i0.hdslb.com/cover.jpg",
        "pubdate": 1_720_000_000,
        "pages": [
            {"page": 1, "cid": 1, "part": "第一集 文字排版", "duration": 384},
            {"page": 2, "cid": 2, "part": "第二集 色彩理论", "duration": 393},
        ],
    })
    monkeypatch.setattr(collection, "view", view)

    result = collection.inspect_bilibili_collection(
        "https://www.bilibili.com/video/BV1DK4y1b7bY/?p=2",
    )

    assert result["is_collection"] is True
    assert result["collection_type"] == "multipart"
    assert result["collection_key"] == "bilibili:BV1DK4y1b7bY"
    assert result["entry_kind"] == "video"
    assert result["total"] == 2
    assert result["complete"] is True
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
    assert [item["content_key"] for item in result["items"]] == [
        "bilibili:BV1DK4y1b7bY:p1",
        "bilibili:BV1DK4y1b7bY:p2",
    ]
    assert [item["position"] for item in result["items"]] == [1, 2]
    assert [item["published_at"] for item in result["items"]] == [
        1_720_000_000, 1_720_000_000,
    ]
    view.assert_called_once_with("BV1DK4y1b7bY")


def test_inspect_bilibili_ugc_season_collection(monkeypatch):
    monkeypatch.setattr(collection, "view", lambda _bvid: {
        "bvid": "BV1234567890",
        "title": "当前视频",
        "pages": [{"page": 1, "part": "当前视频"}],
        "ugc_season": {
            "id": 12345,
            "title": "系列课程",
            "sections": [{
                "title": "第一章",
                "episodes": [
                    {
                        "bvid": "BV1234567890", "title": "课程一",
                        "pubdate": 1_700_000_000,
                        "arc": {"duration": 120, "pubdate": 1_720_000_000},
                    },
                    {
                        "bvid": "BV0987654321", "title": "课程二",
                        "pubdate": 1_710_000_000, "arc": {"duration": 180},
                    },
                ],
            }],
        },
    })

    result = collection.inspect_bilibili_collection(
        "https://www.bilibili.com/video/BV1234567890/",
    )

    assert result["is_collection"] is True
    assert result["collection_type"] == "ugc_season"
    assert result["collection_key"] == "bilibili:ugc:12345"
    assert result["entry_kind"] == "video"
    assert result["total"] == 2
    assert result["complete"] is True
    assert result["title"] == "系列课程"
    assert [item["bvid"] for item in result["items"]] == [
        "BV1234567890",
        "BV0987654321",
    ]
    assert all(item["section"] == "第一章" for item in result["items"])
    assert [item["published_at"] for item in result["items"]] == [
        1_720_000_000, 1_710_000_000,
    ]
    another_entry = collection.inspect_bilibili_collection(
        "https://www.bilibili.com/video/BV0987654321/",
    )
    assert another_entry["collection_key"] == result["collection_key"]
    assert another_entry["current_item_id"] == "BV0987654321"


def test_ugc_metadata_keeps_unknown_dates_and_original_order(monkeypatch):
    view = Mock(return_value={
        "pubdate": 1_720_000_000,
        "pages": [{"page": 1}],
        "ugc_season": {
            "title": "未提供ID的合集",
            "pubdate": 1_720_000_000,
            "sections": [
                {"episodes": [
                    {"bvid": "BV1234567890", "arc": {"ctime": 1_710_000_000}},
                    {"bvid": "BV0987654321", "arc": {"pubdate": 0}},
                ]},
                {"episodes": [
                    {"bvid": "BV1234567890"},
                    {"bvid": "BV1122334455", "pubdate": "invalid"},
                ]},
            ],
        },
    })
    monkeypatch.setattr(collection, "view", view)

    result = collection.inspect_bilibili_collection(
        "https://www.bilibili.com/video/BV1234567890/",
    )

    assert result["collection_key"] is None
    assert [item["position"] for item in result["items"]] == [1, 2, 4]
    assert [item["id"] for item in result["items"]] == [
        "BV1234567890", "BV0987654321", "BV1122334455",
    ]
    assert all(item["published_at"] is None for item in result["items"])
    assert result["total"] == 3
    view.assert_called_once_with("BV1234567890")


def test_ugc_current_item_preserves_explicit_page(monkeypatch):
    monkeypatch.setattr(collection, "view", lambda _bvid: {
        "pages": [{"page": 1}],
        "ugc_season": {"sections": [{"episodes": [
            {"bvid": "BV1234567890"},
            {"bvid": "BV0987654321"},
        ]}]},
    })

    result = collection.inspect_bilibili_collection(
        "https://www.bilibili.com/video/BV1234567890/?p=3",
    )

    assert result["current_item_id"] == "BV1234567890"
    current, other = result["items"]
    assert current["id"] == "BV1234567890"
    assert current["page"] == 3
    assert current["content_key"] == "bilibili:BV1234567890:p3"
    assert current["url"] == "https://www.bilibili.com/video/BV1234567890?p=3"
    assert other["content_key"] == "bilibili:BV0987654321:p1"
    assert other["url"] == "https://www.bilibili.com/video/BV0987654321"


@pytest.mark.parametrize("episodes", [
    [{"bvid": "BV1234567890"}],
    [
        {"bvid": "BV1234567890"},
        {"bvid": "BV0987654321"},
        {"bvid": "BV1234567890"},
    ],
])
def test_ugc_declared_total_exposes_incomplete_directory(monkeypatch, episodes):
    monkeypatch.setattr(collection, "view", lambda _bvid: {
        "pages": [{"page": 1}],
        "ugc_season": {
            "ep_count": 5,
            "sections": [{"episodes": episodes}],
        },
    })

    result = collection.inspect_bilibili_collection(
        "https://www.bilibili.com/video/BV1234567890/",
    )

    assert result["is_collection"] is True
    assert len(result["items"]) == len({episode["bvid"] for episode in episodes})
    assert result["total"] == 5
    assert result["complete"] is False


def test_multipart_falls_back_to_positive_page_numbers(monkeypatch):
    monkeypatch.setattr(collection, "view", lambda _bvid: {
        "pages": [{"page": 0}, {"page": -1}],
    })

    result = collection.inspect_bilibili_collection(
        "https://www.bilibili.com/video/BV1234567890/",
    )

    assert [item["content_key"] for item in result["items"]] == [
        "bilibili:BV1234567890:p1", "bilibili:BV1234567890:p2",
    ]
    assert all(item["published_at"] is None for item in result["items"])


def test_inspect_non_bilibili_url_skips_api(monkeypatch):
    view = lambda _bvid: (_ for _ in ()).throw(AssertionError("view should not run"))
    monkeypatch.setattr(collection, "view", view)

    result = collection.inspect_bilibili_collection("https://example.com/video/1")

    assert result == {"is_bilibili": False, "is_collection": False, "items": []}
