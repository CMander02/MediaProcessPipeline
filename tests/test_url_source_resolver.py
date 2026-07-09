import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.source_resolver import flow_from_metadata, resolve_source_flow  # noqa: E402
from app.models import MediaMetadata, MediaType  # noqa: E402
from app.services.ingestion import ytdlp  # noqa: E402


def test_resolver_maps_generic_webpage_to_webpage_note_flow():
    flow = resolve_source_flow("https://example.com/articles/post")

    assert flow.source_type == "url"
    assert flow.platform == "webpage"
    assert flow.content_subtype == "text_note"
    assert flow.flow_id == "url_webpage_note"
    assert flow.ingestor == "webpage"


def test_resolver_maps_direct_media_url_to_media_asr_flow():
    flow = resolve_source_flow("https://cdn.example.com/video.mp4")

    assert flow.source_type == "url"
    assert flow.platform == "direct_media"
    assert flow.content_subtype == "video"
    assert flow.flow_id == "url_media_asr"
    assert flow.requires_download is True


def test_resolver_keeps_platform_identity_for_known_urls():
    bili = resolve_source_flow("https://www.bilibili.com/video/BV1xx411c7mD")
    xhs = resolve_source_flow("https://www.xiaohongshu.com/explore/abc")
    xyz = resolve_source_flow("https://www.xiaoyuzhoufm.com/episode/6a045472e1eb34a939553f46")
    apple = resolve_source_flow("https://podcasts.apple.com/us/podcast/show/id123456?i=654321")

    assert bili.platform == "bilibili_video"
    assert bili.flow_id == "url_platform_video_subtitle"
    assert xhs.platform == "xiaohongshu"
    assert xhs.content_subtype == "image_note"
    assert xyz.platform == "xiaoyuzhou"
    assert xyz.flow_id == "podcast_asr"
    assert apple.platform == "apple_podcast"
    assert apple.flow_id == "podcast_asr"


def test_resolver_maps_bare_bvid_to_bilibili_video_flow():
    flow = resolve_source_flow("BV1eERyBnEBZ")

    assert flow.source_type == "url"
    assert flow.platform == "bilibili_video"
    assert flow.route_type == "bilibili_video"
    assert flow.ingestor == "bilibili_video"
    assert flow.flow_id == "url_platform_video_subtitle"


def test_resolver_recovers_malformed_https_bvid_as_bilibili_video_flow():
    flow = resolve_source_flow("https://BV1XM411M7eD")

    assert flow.source_type == "url"
    assert flow.platform == "bilibili_video"
    assert flow.route_type == "bilibili_video"
    assert flow.ingestor == "bilibili_video"


def test_resolver_maps_bilibili_opus_to_image_note_flow():
    flow = resolve_source_flow("https://www.bilibili.com/opus/1220490883646881792")

    assert flow.platform == "bilibili_opus"
    assert flow.route_type == "bilibili_opus"
    assert flow.content_subtype == "image_note"
    assert flow.flow_id == "url_image_note"
    assert flow.ingestor == "bilibili_opus"
    assert flow.requires_uvr is False


def test_resolver_maps_schemeless_bilibili_opus_to_image_note_flow():
    flow = resolve_source_flow("bilibili.com/opus/1220469846869803016?spm_id_from=333.1365.0.0")

    assert flow.source_type == "url"
    assert flow.platform == "bilibili_opus"
    assert flow.route_type == "bilibili_opus"
    assert flow.content_subtype == "image_note"
    assert flow.flow_id == "url_image_note"


def test_resolver_maps_bilibili_read_to_note_flow():
    flow = resolve_source_flow("https://www.bilibili.com/read/cv12345678")

    assert flow.platform == "bilibili_opus"
    assert flow.route_type == "bilibili_opus"
    assert flow.content_subtype == "text_note"
    assert flow.flow_id == "url_webpage_note"
    assert flow.ingestor == "bilibili_opus"
    assert flow.requires_uvr is False


def test_resolver_maps_bilibili_short_opus_to_note_flow(monkeypatch):
    monkeypatch.setattr(
        ytdlp,
        "_resolve_bilibili_short_url",
        lambda url: "https://m.bilibili.com/opus/1222911198638374913?share_source=COPY",
    )

    flow = resolve_source_flow("https://b23.tv/fUzR8hQ")

    assert flow.platform == "bilibili_opus"
    assert flow.route_type == "bilibili_opus"
    assert flow.content_subtype == "image_note"
    assert flow.flow_id == "url_image_note"


def test_flow_from_metadata_promotes_url_media_to_api_fallback():
    base = resolve_source_flow("https://cdn.example.com/video.mp4")
    metadata = MediaMetadata(
        title="Video",
        platform="direct_media",
        media_type=MediaType.VIDEO,
        content_subtype="video",
    )

    flow = flow_from_metadata(base, metadata, api_fallback=True, preferred_asr_provider="siliconflow")

    assert flow.flow_id == "url_media_asr_api_fallback"
    assert flow.branch == "asr_api_fallback"
    assert flow.preferred_asr_provider == "siliconflow"
