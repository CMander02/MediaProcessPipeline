import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.ingestion import ytdlp  # noqa: E402


def test_bilibili_bvid_extraction_accepts_bilibili_inputs():
    bvid = "BV1xx411c7mD"
    recent_bvid = "BV1eERyBnEBZ"

    assert ytdlp._is_bilibili_url(f"https://www.bilibili.com/video/{bvid}/?spm_id_from=333")
    assert ytdlp._extract_bilibili_bvid(f"https://www.bilibili.com/video/{bvid}/?spm_id_from=333") == bvid
    assert ytdlp._extract_bilibili_page_number(f"https://www.bilibili.com/video/{bvid}/?p=2") == 2
    assert ytdlp._is_bilibili_url(bvid)
    assert ytdlp._extract_bilibili_bvid(bvid) == bvid
    assert ytdlp._is_bilibili_url(recent_bvid)
    assert ytdlp._extract_bilibili_bvid(recent_bvid) == recent_bvid
    assert ytdlp._extract_bilibili_bvid(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}") == bvid


def test_bilibili_opus_urls_route_as_image_notes_not_video():
    image_opus = "https://www.bilibili.com/opus/1220490883646881792"
    column_opus = "https://www.bilibili.com/opus/1200840557820117011"
    schemeless_opus = "bilibili.com/opus/1220469846869803016?spm_id_from=333.1365.0.0"

    assert ytdlp._is_bilibili_image_note_url(image_opus)
    assert ytdlp._is_bilibili_image_note_url(column_opus)
    assert ytdlp._is_bilibili_image_note_url(schemeless_opus)
    assert not ytdlp._is_bilibili_url(image_opus)
    assert not ytdlp._is_bilibili_url(column_opus)
    assert not ytdlp._is_bilibili_url(schemeless_opus)


def test_bilibili_legacy_read_urls_stay_supported_as_articles():
    url = "https://www.bilibili.com/read/cv12345678"

    assert ytdlp._is_bilibili_article_url(url)
    assert not ytdlp._is_bilibili_url(url)


def test_bilibili_bvid_extraction_ignores_other_hosts():
    url = "https://example.com/share?xsec_token=ABV1xx411c7mDzzz"

    assert not ytdlp._is_bilibili_url(url)
    assert ytdlp._extract_bilibili_bvid(url) is None


def test_bilibili_short_url_resolves_bvid_and_page(monkeypatch):
    short_url = "https://b23.tv/9obruXU"
    resolved_url = "https://www.bilibili.com/video/BV1eERyBnEBZ/?p=2&vd_source=sample"

    monkeypatch.setattr(ytdlp, "_resolve_bilibili_short_url", lambda url: resolved_url)

    assert ytdlp._is_bilibili_url(short_url)
    assert ytdlp._extract_bilibili_bvid(short_url) == "BV1eERyBnEBZ"
    assert ytdlp._extract_bilibili_page_number(short_url) == 2


def test_bilibili_short_url_uses_first_redirect_location(monkeypatch):
    short_url = "https://b23.tv/9obruXU"
    resolved_url = "https://www.bilibili.com/video/BV1eERyBnEBZ?p=1"

    def fake_urlopen_no_redirect(req, *, timeout):
        raise urllib.error.HTTPError(req.full_url, 302, "Found", {"Location": resolved_url}, None)

    monkeypatch.setattr(ytdlp, "_urllib_urlopen_no_redirect", fake_urlopen_no_redirect)

    assert ytdlp._resolve_bilibili_short_url(short_url) == resolved_url
