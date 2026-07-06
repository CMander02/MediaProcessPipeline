import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.ingestion import ytdlp  # noqa: E402
from app.services.ingestion.platform.webpage import api as webpage_api  # noqa: E402
from app.core.source_resolver import resolve_source_flow  # noqa: E402
from app.core.pipeline import (  # noqa: E402
    _detect_source_type,
    _download_resolves_url_title,
    _localize_note_markdown_image_refs,
    _rename_task_dir_to_title,
    _rewrite_ingest_paths_after_task_dir_move,
)
from app.models import MediaMetadata  # noqa: E402


def test_fetch_metadata_prefers_defuddle(monkeypatch):
    markdown = "# Defuddle Title\n\n" + ("Clean paragraph. " * 20)

    monkeypatch.setattr(webpage_api, "_run_defuddle_markdown", lambda url: markdown)
    monkeypatch.setattr(
        webpage_api,
        "_run_defuddle_prop",
        lambda url, prop: {
            "title": "Defuddle Title",
            "description": "Clean page",
            "domain": "example.com",
        }.get(prop, ""),
    )

    info = webpage_api.fetch_metadata("https://example.com/article")

    assert info["title"] == "Defuddle Title"
    assert info["platform"] == "webpage"
    assert info["content_subtype"] == "text_note"
    assert info["extra"]["scrape_engine"] == "defuddle"
    assert info["description"] == markdown


def test_defuddle_command_prefers_bundled_node_cli(tmp_path, monkeypatch):
    cli = tmp_path / "cli.js"
    cli.write_text("#!/usr/bin/env node\n", encoding="utf-8")

    monkeypatch.setattr(webpage_api, "_bundled_defuddle_cli", lambda: cli)
    monkeypatch.setattr(webpage_api.shutil, "which", lambda name: "node.exe" if name == "node" else "")

    assert webpage_api._defuddle_command() == ["node.exe", str(cli)]


def test_fetch_metadata_falls_back_to_jina(monkeypatch):
    markdown = "# Jina Title\n\n" + ("Fallback paragraph. " * 20)

    def fail_defuddle(url: str) -> str:
        raise RuntimeError("defuddle failed")

    monkeypatch.setattr(webpage_api, "_run_defuddle_markdown", fail_defuddle)
    monkeypatch.setattr(webpage_api, "_fetch_jina_markdown", lambda url: (markdown, {"title": "Jina Title"}))

    info = webpage_api.fetch_metadata("https://example.com/article")

    assert info["title"] == "Jina Title"
    assert info["extra"]["scrape_engine"] == "jina"
    assert info["extra"]["defuddle_error"] == "defuddle failed"
    assert info["description"] == markdown


def test_download_webpage_localizes_images_and_writes_source(tmp_path, monkeypatch):
    markdown = "# Post\n\n![Diagram](/assets/diagram.png)\n\n<img src=\"photo.webp\" />\n"
    monkeypatch.setattr(
        webpage_api,
        "fetch_metadata",
        lambda url: {
            "title": "Post",
            "description": markdown,
            "webpage_url": "https://example.com/posts/post",
            "platform": "webpage",
            "content_subtype": "text_note",
            "extra": {"platform": "webpage", "scrape_engine": "defuddle"},
        },
    )

    downloaded: list[str] = []

    def fake_download(url: str, *, referer: str):
        downloaded.append(url)
        return b"fake-image", "image/png"

    monkeypatch.setattr(webpage_api, "_download_binary", fake_download)

    info = webpage_api.download_webpage("https://example.com/posts/post", tmp_path)

    source = (tmp_path / "source.md").read_text(encoding="utf-8")
    assert "![Diagram](images/00.png)" in source
    assert "![image](images/01.webp)" in source
    assert (tmp_path / "images" / "00.png").read_bytes() == b"fake-image"
    assert (tmp_path / "images" / "01.webp").exists()
    assert downloaded == [
        "https://example.com/assets/diagram.png",
        "https://example.com/posts/photo.webp",
    ]
    assert info["thumbnail"].endswith("00.png")
    assert info["extra"]["image_count"] == 2


def test_generic_webpage_detection_skips_direct_media():
    assert ytdlp._is_generic_webpage_url("https://lilianweng.github.io/posts/2025-05-01-thinking/")
    assert not ytdlp._is_generic_webpage_url("https://example.com/video.mp4")
    assert not ytdlp._is_generic_webpage_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert not ytdlp._is_generic_webpage_url("https://x.com/i/status/2073100352921215386")


def test_x_status_link_uses_platform_video_flow():
    flow = resolve_source_flow("https://x.com/i/status/2073100352921215386")

    assert ytdlp._is_twitter_url("https://x.com/i/status/2073100352921215386")
    assert flow.platform == "twitter"
    assert flow.route_type == "twitter"
    assert flow.flow_id == "url_platform_video_asr"
    assert flow.content_subtype == "video"
    assert flow.requires_uvr is True


def test_x_title_is_resolved_by_downloader():
    assert _download_resolves_url_title("twitter")


def test_x_status_article_fallback_writes_text_note(tmp_path, monkeypatch):
    def fake_scrape(self, url: str):
        return {
            "url": "https://x.com/trq212/status/2073100352921215386",
            "title": 'Thariq on X: "https://t.co/hPiZr1kG7r" / X',
            "text": (
                "Post\nThariq\n@trq212\nArticle\nA Field Guide to Fable: Finding Your Unknowns\n"
                "Working with Claude Fable 5 keeps re-teaching me an old lesson.\n"
                "5:43 PM · Jul 3, 2026\nNew to X?\nSign up now"
            ),
            "uploader": "Thariq",
            "thumbnail": "https://pbs.twimg.com/profile_images/demo.jpg",
            "article_url": "https://x.com/i/article/2073090223194755072",
            "type": "article_card",
        }

    monkeypatch.setattr(ytdlp.YtdlpService, "_scrape_twitter_page", fake_scrape)

    service = ytdlp.YtdlpService()
    result = service._download_twitter_webpage_note(
        "https://x.com/i/status/2073100352921215386",
        tmp_path,
        RuntimeError("unsupported"),
    )

    source = (tmp_path / "source.md").read_text(encoding="utf-8")
    assert "A Field Guide to Fable" in source
    assert "New to X?" not in source
    assert result["title"] == "A Field Guide to Fable: Finding Your Unknowns"
    assert result["info"]["title"] == "A Field Guide to Fable: Finding Your Unknowns"
    assert result["info"]["platform"] == "twitter"
    assert result["info"]["content_subtype"] == "text_note"
    assert result["info"]["extra"]["source_markdown_path"] == str(tmp_path / "source.md")
    metadata = service.extract_metadata(result["info"])
    assert metadata.platform == "twitter"
    assert metadata.content_subtype == "text_note"


def test_x_status_image_article_uses_card_title(tmp_path, monkeypatch):
    image_url = "https://pbs.twimg.com/media/HMXOZ8-aYAAp9VJ.jpg"

    def fake_scrape(self, url: str):
        return {
            "url": "https://x.com/CompleteSkeptic/status/2073442518117884197",
            "title": 'Diogo Almeida on X: "https://t.co/lkDM4VXS4e" / X',
            "text": (
                "Diogo Almeida\n@CompleteSkeptic\nArticle\nScaling Laws, Honestly\n"
                "TL;DR: The original scaling laws were wrong due to a bug\n"
                "4:23 PM · Jul 4, 2026\nRead 8 replies"
            ),
            "uploader": "Diogo Almeida",
            "thumbnail": image_url,
            "image_urls": [image_url],
            "article_url": "https://x.com/i/article/2073276453131780096",
            "article_text": (
                "Article\nScaling Laws, Honestly\n"
                "TL;DR: The original scaling laws were wrong due to a bug"
            ),
            "type": "image_status",
        }

    monkeypatch.setattr(ytdlp.YtdlpService, "_scrape_twitter_page", fake_scrape)

    service = ytdlp.YtdlpService()
    result = service._download_twitter_webpage_note(
        "https://x.com/CompleteSkeptic/status/2073442518117884197",
        tmp_path,
        RuntimeError("unsupported"),
    )

    source = (tmp_path / "source.md").read_text(encoding="utf-8")
    assert source.startswith("# Scaling Laws, Honestly")
    assert result["title"] == "Scaling Laws, Honestly"
    assert result["info"]["title"] == "Scaling Laws, Honestly"
    assert result["info"]["content_subtype"] == "image_note"
    assert result["info"]["extra"]["article_title"] == "Scaling Laws, Honestly"


def test_x_status_image_fallback_writes_image_note(tmp_path, monkeypatch):
    image_url = "https://pbs.twimg.com/media/GxDemoAaYAA1abc.jpg:large"
    duplicate_url = "https://pbs.twimg.com/media/GxDemoAaYAA1abc?format=jpg&name=small"

    def fake_scrape(self, url: str):
        return {
            "url": "https://x.com/musora233/status/2073382404572717243",
            "title": 'musora on X: "new image post" / X',
            "text": "Post\nmusora\n@musora233\nA note with images.\nNew to X?",
            "uploader": "musora",
            "thumbnail": image_url,
            "image_urls": [image_url, duplicate_url],
            "article_url": "",
            "type": "image_status",
        }

    monkeypatch.setattr(ytdlp.YtdlpService, "_scrape_twitter_page", fake_scrape)

    service = ytdlp.YtdlpService()
    result = service._download_twitter_webpage_note(
        "https://x.com/musora233/status/2073382404572717243",
        tmp_path,
        RuntimeError("unsupported"),
    )

    source = (tmp_path / "source.md").read_text(encoding="utf-8")
    assert "A note with images." in source
    assert f"![X image 1]({image_url})" in source
    assert result["info"]["platform"] == "twitter"
    assert result["info"]["content_subtype"] == "image_note"
    assert result["info"]["extra"]["image_count"] == 1
    metadata = service.extract_metadata(result["info"])
    assert metadata.platform == "twitter"
    assert metadata.content_subtype == "image_note"
    assert metadata.extra["image_urls"] == [image_url]


def test_x_status_without_downloaded_video_falls_back_to_text_note(tmp_path, monkeypatch):
    class FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            return {
                "title": "Karpathy on X",
                "webpage_url": url,
                "platform": "twitter",
            }

    def fake_scrape(self, url: str):
        return {
            "url": url,
            "title": 'Andrej Karpathy on X: "Agents..." / X',
            "text": "Post\nAndrej Karpathy\n@karpathy\nLLM agents should keep an idea file.\nNew to X?",
            "uploader": "Andrej Karpathy",
            "thumbnail": "",
            "article_url": "",
            "type": "status",
        }

    import yt_dlp as yt_dlp_pkg

    monkeypatch.setattr(yt_dlp_pkg, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(ytdlp.YtdlpService, "_scrape_twitter_page", fake_scrape)

    service = ytdlp.YtdlpService()
    result = service.download("https://x.com/karpathy/status/2040470801506541998", tmp_path)

    assert result["file_path"] is None
    assert result["video_path"] is None
    assert result["info"]["platform"] == "twitter"
    assert result["info"]["content_subtype"] == "text_note"
    assert "LLM agents should keep an idea file" in (tmp_path / "source.md").read_text(encoding="utf-8")


def test_twitter_image_downloader_downloads_pbs_media(tmp_path, monkeypatch):
    from app.services.ingestion.platform.twitter import api as twitter_api

    class FakeResponse:
        headers = {"Content-Type": "image/jpeg"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size=-1):
            return b"fake-jpg"

    requested: list[str] = []

    def fake_urlopen(request, timeout=30):
        requested.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr(twitter_api, "urllib_urlopen", fake_urlopen)

    image_url = "https://pbs.twimg.com/media/GxDemoAaYAA1abc.jpg:large"
    info = {
        "webpage_url": "https://x.com/musora233/status/2073382404572717243",
        "extra": {
            "image_urls": [image_url],
            "image_url_candidates": [[image_url]],
        },
    }

    paths = twitter_api.download_images(info, tmp_path)

    assert paths == [tmp_path / "images" / "00.jpg"]
    assert paths[0].read_bytes() == b"fake-jpg"
    assert "name=4096x4096" in requested[0]
    assert info["extra"]["image_download_diagnostics"]["success"] == 1


def test_twitter_markdown_image_refs_localize_to_downloaded_images(tmp_path):
    image_url = "https://pbs.twimg.com/media/GxDemoAaYAA1abc?format=jpg&name=small"
    image_path = tmp_path / "images" / "00.jpg"
    image_path.parent.mkdir()
    image_path.write_bytes(b"fake")
    metadata = MediaMetadata(
        title="X image post",
        platform="twitter",
        content_subtype="image_note",
        extra={
            "image_urls": [image_url],
            "image_url_candidates": [[image_url]],
        },
    )

    localized = _localize_note_markdown_image_refs(
        f"# X image post\n\n![X image 1]({image_url})\n",
        metadata,
        [image_path],
    )

    assert "![X image 1](images/00.jpg)" in localized


def test_pipeline_detects_generic_webpage_before_ytdlp_title_probe():
    page = "https://lilianweng.github.io/posts/2026-06-24-scaling-laws/"
    assert _detect_source_type(page) == "url"
    assert _detect_source_type("https://example.com/video.mp4") == "url"
    flow = resolve_source_flow(page)
    assert flow.platform == "webpage"
    assert flow.content_subtype == "text_note"
    assert flow.flow_id == "url_webpage_note"


def test_rewrite_webpage_asset_paths_after_task_dir_rename(tmp_path):
    old_dir = tmp_path / "download"
    new_dir = tmp_path / "Scaling Laws"
    old_dir.mkdir()
    new_dir.mkdir()
    metadata = MediaMetadata(
        title="Scaling Laws",
        platform="webpage",
        content_subtype="text_note",
        extra={
            "source_markdown_path": str(old_dir / "source.md"),
            "images": [{"path": str(old_dir / "images" / "00.png")}],
        },
    )
    ingest = {
        "info": {
            "thumbnail": str(old_dir / "images" / "00.png"),
            "extra": {
                "source_markdown_path": str(old_dir / "source.md"),
                "images": [{"path": str(old_dir / "images" / "00.png")}],
            },
        }
    }

    _rewrite_ingest_paths_after_task_dir_move(ingest, metadata, old_dir, new_dir)

    assert metadata.extra["source_markdown_path"] == str(new_dir / "source.md")
    assert metadata.extra["images"][0]["path"] == str(new_dir / "images" / "00.png")
    assert ingest["info"]["extra"]["source_markdown_path"] == str(new_dir / "source.md")
    assert ingest["info"]["thumbnail"] == str(new_dir / "images" / "00.png")


def test_task_dir_rename_uses_unique_metadata_title_when_target_exists(tmp_path):
    existing_title_dir = tmp_path / "Scaling Laws"
    placeholder_dir = tmp_path / "download (11)"
    existing_title_dir.mkdir()
    placeholder_dir.mkdir()
    (placeholder_dir / "source.md").write_text("# Scaling Laws\n", encoding="utf-8")

    renamed_dir, old_dir = _rename_task_dir_to_title(placeholder_dir, "Scaling Laws")

    assert old_dir == placeholder_dir
    assert renamed_dir == tmp_path / "Scaling Laws (2)"
    assert not placeholder_dir.exists()
    assert (renamed_dir / "source.md").read_text(encoding="utf-8") == "# Scaling Laws\n"


def test_task_dir_rename_sanitizes_windows_invalid_title_chars(tmp_path):
    placeholder_dir = tmp_path / "25e9d512-ec9b-4124-9e87-98bedf8c0aba"
    placeholder_dir.mkdir()

    renamed_dir, old_dir = _rename_task_dir_to_title(
        placeholder_dir,
        "「2026鹰角嘉年华」《明日方舟：终末地》参展情报公开\nNH:2.1H\n【活动时间】：2026年7月30日-8月2日",
    )

    assert old_dir == placeholder_dir
    assert renamed_dir.parent == tmp_path
    assert "\n" not in renamed_dir.name
    assert ":" not in renamed_dir.name
    assert "：" in renamed_dir.name
    assert renamed_dir.exists()
