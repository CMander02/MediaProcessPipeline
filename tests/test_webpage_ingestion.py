import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.ingestion import ytdlp  # noqa: E402
from app.services.ingestion.platform.webpage import api as webpage_api  # noqa: E402
from app.core.pipeline import _detect_source_type, _rewrite_ingest_paths_after_task_dir_move  # noqa: E402
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


def test_pipeline_detects_generic_webpage_before_ytdlp_title_probe():
    assert _detect_source_type("https://lilianweng.github.io/posts/2026-06-24-scaling-laws/") == "webpage"
    assert _detect_source_type("https://example.com/video.mp4") == "url"


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
