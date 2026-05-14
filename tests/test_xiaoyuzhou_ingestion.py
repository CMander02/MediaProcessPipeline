import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.models import MediaType  # noqa: E402
from app.services.ingestion.platform.xiaoyuzhou.api import (  # noqa: E402
    _extract_chapters,
    _extract_json_ld,
    _extract_next_episode,
    is_xiaoyuzhou_url,
)
from app.services.ingestion.ytdlp import YtdlpService  # noqa: E402


def test_xiaoyuzhou_url_detection():
    assert is_xiaoyuzhou_url("https://www.xiaoyuzhoufm.com/episode/6a045472e1eb34a939553f46")
    assert not is_xiaoyuzhou_url("https://www.xiaoyuzhoufm.com/podcast/670f3da40d2f24f28978736f")


def test_xiaoyuzhou_page_json_sources_are_parsed():
    ld = {
        "@context": "https://schema.org/",
        "@type": "PodcastEpisode",
        "url": "https://www.xiaoyuzhoufm.com/episode/abc",
        "name": "标题",
        "datePublished": "2026-05-13T10:56:22.102Z",
        "timeRequired": "PT15M",
        "description": "00:00 开场\n01:32 正题",
        "associatedMedia": {
            "@type": "MediaObject",
            "contentUrl": "https://media.xyzcdn.net/show/audio.m4a",
        },
        "partOfSeries": {"@type": "PodcastSeries", "name": "节目名"},
    }
    next_data = {
        "props": {
            "pageProps": {
                "episode": {
                    "id": "abc",
                    "type": "EPISODE",
                    "title": "标题",
                    "duration": 906,
                    "media": {
                        "size": 123,
                        "mimeType": "audio/mp4",
                        "source": {"url": "https://media.xyzcdn.net/show/audio.m4a"},
                    },
                    "podcast": {"pid": "pod", "title": "节目名", "author": "作者"},
                    "transcriptMediaId": "show/audio.m4a",
                }
            }
        }
    }
    import json

    html = (
        '<script name="schema:podcast-show" type="application/ld+json">'
        f"{json.dumps(ld, ensure_ascii=False)}"
        "</script>"
        '<script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(next_data, ensure_ascii=False)}"
        "</script>"
    )

    json_ld = _extract_json_ld(html)
    episode = _extract_next_episode(html, "abc")

    assert json_ld["name"] == "标题"
    assert json_ld["associatedMedia"]["contentUrl"].endswith("audio.m4a")
    assert episode["duration"] == 906
    assert episode["transcriptMediaId"] == "show/audio.m4a"


def test_xiaoyuzhou_description_timestamps_become_chapters():
    chapters = _extract_chapters("00:00 开场\n01:32 正题\n10:30 结语")

    assert chapters == [
        {"title": "开场", "start_time": 0.0},
        {"title": "正题", "start_time": 92.0},
        {"title": "结语", "start_time": 630.0},
    ]


def test_xiaoyuzhou_metadata_maps_to_podcast_media_type():
    metadata = YtdlpService().extract_metadata(
        {
            "title": "标题",
            "webpage_url": "https://www.xiaoyuzhoufm.com/episode/abc",
            "uploader": "节目名",
            "duration": 906,
            "media_type": "podcast",
            "upload_date": "20260513",
            "chapters": [{"title": "开场", "start_time": 0}],
            "extra": {"platform": "xiaoyuzhou"},
        }
    )

    assert metadata.media_type == MediaType.PODCAST
    assert metadata.chapters[0].title == "开场"
    assert metadata.extra["platform"] == "xiaoyuzhou"
