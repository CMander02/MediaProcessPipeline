import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.pipeline import _localize_note_markdown_image_refs  # noqa: E402
from app.models import MediaMetadata  # noqa: E402
from app.services.ingestion.platform.bilibili import note as bili_note  # noqa: E402
from app.services.ingestion.ytdlp import YtdlpService  # noqa: E402


def test_bilibili_opus_metadata_extracts_text_and_original_image_candidates(monkeypatch):
    image_url = "https://i0.hdslb.com/bfs/new_dyn/abc123.jpg@1048w_!web-dynamic.webp"

    monkeypatch.setattr(
        bili_note,
        "_fetch_dynamic_detail",
        lambda _opus_id: {
            "type": "DYNAMIC_TYPE_DRAW",
            "modules": {
                "module_author": {"name": "作者", "mid": 123, "pub_ts": 1783000000},
                "module_dynamic": {
                    "desc": {"text": "正文第一段"},
                    "major": {
                        "opus": {
                            "title": "图文标题",
                            "summary": {"text": "专栏摘要"},
                            "pics": [{"url": image_url}],
                        }
                    },
                },
            },
        },
    )

    info = bili_note.fetch_metadata("https://www.bilibili.com/opus/1220490883646881792")

    assert info["platform"] == "bilibili_opus"
    assert info["extra"]["platform"] == "bilibili_opus"
    assert info["content_subtype"] == "image_note"
    assert info["title"] == "图文标题"
    assert "正文第一段" in info["description"]
    assert info["uploader"] == "作者"
    candidates = info["extra"]["image_url_candidates"][0]
    assert candidates[0] == "https://i0.hdslb.com/bfs/new_dyn/abc123.jpg"
    assert candidates[1] == image_url


def test_bilibili_draw_opus_uses_item_opus_style_title_summary_and_stats(monkeypatch):
    image_url = "http://i0.hdslb.com/bfs/new_dyn/model.png"

    monkeypatch.setattr(
        bili_note,
        "_fetch_dynamic_detail",
        lambda _opus_id: {
            "type": "DYNAMIC_TYPE_DRAW",
            "basic": {"comment_id_str": "400136653"},
            "modules": {
                "module_author": {
                    "name": "Slophegor",
                    "mid": 58163,
                    "pub_ts": 1783005109,
                    "pub_time": "2026年07月02日 23:11",
                    "face": "https://i2.hdslb.com/bfs/face/avatar.jpg",
                    "jump_url": "//space.bilibili.com/58163/dynamic",
                },
                "module_stat": {
                    "like": {"count": 70},
                    "comment": {"count": 3},
                    "forward": {"count": 0},
                },
                "module_dynamic": {
                    "desc": None,
                    "major": {
                        "type": "MAJOR_TYPE_OPUS",
                        "opus": {
                            "title": "四视图 想玩的自己拿去做ai吧,不管了",
                            "summary": {
                                "text": "自己整合下就可以丢ai了,不是我不想做,是有人针对,资料都送了,送了,\n我要上班没时间去跟人搞针对"
                            },
                            "pics": [{"url": image_url}],
                        },
                    },
                },
            },
        },
    )

    info = bili_note.fetch_metadata("https://www.bilibili.com/opus/1220486721810989080")

    assert info["title"] == "四视图 想玩的自己拿去做ai吧,不管了"
    assert "自己整合下就可以丢ai了" in info["description"]
    assert "我要上班没时间去跟人搞针对" in info["description"]
    assert info["uploader"] == "Slophegor"
    assert info["timestamp"] == 1783005109
    bili_meta = info["extra"]["bilibili_metadata"]
    assert bili_meta["pub_time"] == "2026年07月02日 23:11"
    assert bili_meta["author_space_url"] == "https://space.bilibili.com/58163/dynamic"
    assert bili_meta["stats"] == {"like": 70, "comment": 3, "forward": 0}


def test_bilibili_article_item_opus_style_keeps_article_fallback_for_full_body(monkeypatch):
    calls: list[tuple[str, str | None]] = []

    def fake_request(opus_id: str, features: str | None = None):
        calls.append((opus_id, features))
        if features:
            return {
                "type": "DYNAMIC_TYPE_ARTICLE",
                "modules": {
                    "module_dynamic": {
                        "major": {
                            "type": "MAJOR_TYPE_OPUS",
                            "opus": {"title": "专栏标题", "summary": {"text": "摘要"}},
                        }
                    }
                },
            }
        return {
            "type": "DYNAMIC_TYPE_ARTICLE",
            "modules": {
                "module_dynamic": {
                    "major": {
                        "article": {
                            "id": 48853692,
                            "title": "专栏标题",
                            "jump_url": "//www.bilibili.com/read/cv48853692/",
                        }
                    }
                }
            },
        }

    monkeypatch.setattr(bili_note, "_fetch_dynamic_detail_request", fake_request)

    item = bili_note._fetch_dynamic_detail("1200840557820117011")
    major = item["modules"]["module_dynamic"]["major"]

    assert calls == [
        ("1200840557820117011", "itemOpusStyle"),
        ("1200840557820117011", None),
    ]
    assert major["opus"]["title"] == "专栏标题"
    assert major["article"]["id"] == 48853692


def test_bilibili_image_candidates_are_original_first():
    candidates = bili_note.bilibili_image_candidates(
        "https://i0.hdslb.com/bfs/new_dyn/demo.png@672w_378h_1c.webp"
    )

    assert candidates == [
        "https://i0.hdslb.com/bfs/new_dyn/demo.png",
        "https://i0.hdslb.com/bfs/new_dyn/demo.png@672w_378h_1c.webp",
    ]


def test_bilibili_article_uses_full_article_api_body_and_images(monkeypatch):
    api_image_url = "https://i0.hdslb.com/bfs/new_dyn/banner/full.png@960w_540h.webp"

    monkeypatch.setattr(
        bili_note,
        "_fetch_dynamic_detail",
        lambda _opus_id: {
            "type": "DYNAMIC_TYPE_ARTICLE",
            "modules": {
                "module_author": {"name": "作者", "mid": 396895143, "pub_ts": 1783000000},
                "module_dynamic": {
                    "desc": {"text": "动态摘要"},
                    "major": {
                        "article": {
                            "id": 48853692,
                            "title": "2026年，带你一起玩 Agent (1) Agent 是什么？",
                            "desc": "短摘要，停在半句",
                            "jump_url": "//www.bilibili.com/read/cv48853692/",
                        }
                    },
                },
            },
        },
    )
    monkeypatch.setattr(
        bili_note,
        "_fetch_article_api_data",
        lambda _article_id, _article_url="": {
            "title": "2026年，带你一起玩 Agent (1) Agent 是什么？",
            "origin_image_urls": [api_image_url],
            "opus": {
                "content": {
                    "paragraphs": [
                        {
                            "para_type": 9,
                            "format": {"heading_type": 1},
                            "text": {"nodes": [{"word": {"words": "1. AI != Agent"}}]},
                        },
                        {
                            "para_type": 1,
                            "text": {"nodes": [{"word": {"words": "完整正文第一段。"}}]},
                        },
                        {
                            "para_type": 1,
                            "text": {"nodes": [{"word": {"words": "完整正文后半段，应当进入 source.md。"}}]},
                        },
                    ]
                }
            },
        },
    )

    info = bili_note.fetch_metadata("https://www.bilibili.com/opus/1200840557820117011")

    assert info["platform"] == "bilibili_opus"
    assert info["extra"]["bilibili_type"] == "article"
    assert info["extra"]["article_id"] == "48853692"
    assert "完整正文后半段，应当进入 source.md。" in info["description"]
    assert "短摘要，停在半句" not in info["description"]
    candidates = info["extra"]["image_url_candidates"][0]
    assert candidates[0] == "https://i0.hdslb.com/bfs/new_dyn/banner/full.png"
    assert candidates[1] == api_image_url


def test_image_note_metadata_preserves_full_article_description():
    description = "完整专栏正文。" * 1000

    metadata = YtdlpService().extract_metadata({
        "id": "cv49760530",
        "title": "长专栏",
        "description": description,
        "content_subtype": "image_note",
        "media_type": "image",
        "extractor": "bilibili",
    })

    assert metadata.description == description


def test_bilibili_article_markdown_keeps_inline_image_paragraphs():
    markdown = bili_note._article_markdown_from_api_data(
        {
            "title": "专栏标题",
            "opus": {
                "content": {
                    "paragraphs": [
                        {
                            "para_type": 1,
                            "text": {"nodes": [{"word": {"words": "正文段落"}}]},
                        },
                        {
                            "para_type": 2,
                            "pic": {
                                "pics": [
                                    {
                                        "url": "http://i0.hdslb.com/bfs/new_dyn/demo.png",
                                        "comment": "图片说明",
                                    }
                                ]
                            },
                        },
                    ]
                }
            },
        },
    )

    assert "# 专栏标题" in markdown
    assert "正文段落" in markdown
    assert "![图片说明](https://i0.hdslb.com/bfs/new_dyn/demo.png)" in markdown
    assert "图片说明" in markdown


def test_bilibili_article_markdown_escapes_image_alt_but_keeps_caption_markdown():
    markdown = bili_note._article_markdown_from_api_data(
        {
            "title": "专栏标题",
            "opus": {
                "content": {
                    "paragraphs": [
                        {
                            "para_type": 2,
                            "pic": {
                                "pics": [
                                    {
                                        "url": "http://i0.hdslb.com/bfs/new_dyn/demo.png",
                                        "comment": "caption [link] **bold**",
                                    }
                                ]
                            },
                        },
                    ]
                }
            },
        },
    )

    assert "![caption \\[link\\] **bold**](https://i0.hdslb.com/bfs/new_dyn/demo.png)" in markdown
    assert "\n\ncaption [link] **bold**" in markdown


def test_bilibili_article_source_markdown_localizes_downloaded_images(tmp_path):
    metadata = MediaMetadata(
        title="专栏标题",
        platform="bilibili_opus",
        extra={
            "bilibili_type": "article",
            "image_urls": [
                "https://i0.hdslb.com/bfs/new_dyn/cover.png",
                "https://i0.hdslb.com/bfs/new_dyn/demo.png",
            ],
        },
    )
    image_paths = [tmp_path / "images" / "00.png", tmp_path / "images" / "01.png"]
    markdown = "正文\n\n![图片说明](http://i0.hdslb.com/bfs/new_dyn/demo.png)\n"

    localized = _localize_note_markdown_image_refs(markdown, metadata, image_paths)

    assert "![图片说明](images/01.png)" in localized
    assert "hdslb.com" not in localized


def test_bilibili_article_source_markdown_localizes_by_image_index_when_previous_image_failed(tmp_path):
    metadata = MediaMetadata(
        title="专栏标题",
        platform="bilibili_opus",
        extra={
            "bilibili_type": "article",
            "image_urls": [
                "https://i0.hdslb.com/bfs/new_dyn/cover.png",
                "https://i0.hdslb.com/bfs/new_dyn/demo.png",
            ],
            "image_url_candidates": [
                ["https://i0.hdslb.com/bfs/new_dyn/cover.png"],
                ["https://i0.hdslb.com/bfs/new_dyn/demo.png@960w.webp"],
            ],
        },
    )
    markdown = "正文\n\n![图片说明](https://i0.hdslb.com/bfs/new_dyn/demo.png@960w.webp)\n"

    localized = _localize_note_markdown_image_refs(markdown, metadata, [tmp_path / "images" / "01.png"])

    assert "![图片说明](images/01.png)" in localized
