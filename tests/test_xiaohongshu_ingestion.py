import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.models import MediaType  # noqa: E402
from app.services.ingestion.platform.xiaohongshu.api import (  # noqa: E402
    _extract_initial_state,
    _extract_note,
    _extract_video_url,
    extract_post_info,
    is_xiaohongshu_url,
)
from app.services.ingestion.ytdlp import YtdlpService  # noqa: E402


def test_xiaohongshu_url_detection_and_post_info():
    url = (
        "https://www.xiaohongshu.com/discovery/item/6a04606b000000003503392c"
        "?xsec_token=ABLQRfvRqGgEZfMdnklHHK2gAVm2dVI65ti2IHgicBFks%3D&xsec_source=pc_share"
    )

    assert is_xiaohongshu_url(url)
    info = extract_post_info(url)

    assert info.post_id == "6a04606b000000003503392c"
    assert info.xsec_token == "ABLQRfvRqGgEZfMdnklHHK2gAVm2dVI65ti2IHgicBFks="


def test_xiaohongshu_initial_state_video_stream_is_parsed():
    post_id = "6a04606b000000003503392c"
    state = {
        "note": {
            "noteDetailMap": {
                post_id: {
                    "note": {
                        "noteId": post_id,
                        "type": "video",
                        "title": "31岁不上班",
                        "video": {
                            "media": {
                                "stream": {
                                    "h264": [
                                        {
                                            "format": "mp4",
                                            "duration": 135489,
                                            "masterUrl": "http://sns-video-qc.xhscdn.com/a.mp4",
                                        }
                                    ]
                                }
                            }
                        },
                    }
                }
            }
        }
    }
    html = (
        "<script>window.__INITIAL_STATE__="
        f"{json.dumps(state, ensure_ascii=False)}"
        "</script>"
    )

    parsed = _extract_initial_state(html)
    note = _extract_note(parsed, post_id)
    video_url, stream = _extract_video_url(note)

    assert note["title"] == "31岁不上班"
    assert video_url == "https://sns-video-qc.xhscdn.com/a.mp4"
    assert stream["duration"] == 135489


def test_xiaohongshu_metadata_maps_to_video_media_type():
    metadata = YtdlpService().extract_metadata(
        {
            "title": "小红书视频",
            "webpage_url": "https://www.xiaohongshu.com/explore/abc",
            "uploader": "作者",
            "duration": 135.4,
            "media_type": "video",
            "upload_date": "20260513",
            "extra": {"platform": "xiaohongshu"},
        }
    )

    assert metadata.media_type == MediaType.VIDEO
    assert metadata.extra["platform"] == "xiaohongshu"
