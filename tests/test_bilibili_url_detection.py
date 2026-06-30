import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.ingestion import ytdlp  # noqa: E402


def test_bilibili_bvid_extraction_accepts_bilibili_inputs():
    bvid = "BV1xx411c7mD"

    assert ytdlp._is_bilibili_url(f"https://www.bilibili.com/video/{bvid}/?spm_id_from=333")
    assert ytdlp._extract_bilibili_bvid(f"https://www.bilibili.com/video/{bvid}/?spm_id_from=333") == bvid
    assert ytdlp._is_bilibili_url(bvid)
    assert ytdlp._extract_bilibili_bvid(bvid) == bvid
    assert ytdlp._extract_bilibili_bvid(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}") == bvid


def test_bilibili_bvid_extraction_ignores_other_hosts():
    url = "https://example.com/share?xsec_token=ABV1xx411c7mDzzz"

    assert not ytdlp._is_bilibili_url(url)
    assert ytdlp._extract_bilibili_bvid(url) is None
