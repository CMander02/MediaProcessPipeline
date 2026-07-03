import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core import database, pipeline as pipeline_core, settings as settings_module  # noqa: E402
from app.core.source_resolver import resolve_source_flow  # noqa: E402
from app.core.settings import RuntimeSettings  # noqa: E402
from app.models import MediaMetadata, MediaType, Task, TaskStatus, TaskType  # noqa: E402
from app.core.pipeline import (  # noqa: E402
    _clean_source_path,
    _detect_source_type,
    _fallback_note_mindmap,
    _fallback_note_summary,
)
from app.services.ingestion.platform.xiaohongshu import api as xhs_api  # noqa: E402
from app.services.ingestion.platform.xiaohongshu.api import (  # noqa: E402
    _extract_initial_state,
    _extract_note,
    _extract_video_url,
    extract_post_info,
    is_xiaohongshu_url,
)
from app.services.ingestion import ytdlp  # noqa: E402
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


def test_xiaohongshu_share_text_with_bili_like_tokens_stays_xiaohongshu():
    share_text = (
        "77 【标题 | 小红书】 av12345 BV1xx411c7mD "
        "https://xhslink.com/a/ABcdEFgH，复制本条消息打开小红书"
    )

    cleaned = _clean_source_path(share_text)

    assert cleaned == "https://xhslink.com/a/ABcdEFgH"
    assert _detect_source_type(cleaned) == "url"
    assert resolve_source_flow(cleaned).platform == "xiaohongshu"
    assert ytdlp._is_xiaohongshu_url(share_text)
    assert not ytdlp._is_bilibili_url(share_text)


def test_schemeless_bilibili_opus_source_is_normalized_to_https():
    cleaned = _clean_source_path("bilibili.com/opus/1220469846869803016?spm_id_from=333.1365.0.0")

    assert cleaned == "https://bilibili.com/opus/1220469846869803016?spm_id_from=333.1365.0.0"
    assert _detect_source_type(cleaned) == "url"
    assert resolve_source_flow(cleaned).platform == "bilibili_opus"


def test_xiaohongshu_pc_share_text_routes_to_xiaohongshu():
    share_text = (
        "26 【传说中deepseek融资会议Q&A - blh | 小红书 - 你的生活兴趣社区】 "
        "😆 CNRCqayKNUVytbk 😆 "
        "https://www.xiaohongshu.com/discovery/item/6a37ac8b000000001101d922"
        "?source=webshare&xhsshare=pc_web"
        "&xsec_token=AB3Gr9I0rGdAYkcJ7Z7sd3ppChGDarTcN_3lGMsI2CEiU="
        "&xsec_source=pc_share"
    )

    cleaned = _clean_source_path(share_text)

    assert cleaned.startswith("https://www.xiaohongshu.com/discovery/item/6a37ac8b000000001101d922")
    assert _detect_source_type(cleaned) == "url"
    assert resolve_source_flow(cleaned).platform == "xiaohongshu"
    assert ytdlp._is_xiaohongshu_url(share_text)
    assert not ytdlp._is_bilibili_url(share_text)


def test_xiaohongshu_xsec_token_starting_with_abv_does_not_extract_bvid(tmp_path, monkeypatch):
    url = (
        "https://www.xiaohongshu.com/discovery/item/6a3e475c000000001503faf2"
        "?source=webshare&xhsshare=pc_web"
        "&xsec_token=ABVnVzCcWrPXmbuqPK3H9LOfshIiskh9vVVJgSPqzxMj0="
        "&xsec_source=pc_share"
    )

    cleaned = _clean_source_path(url)
    assert _detect_source_type(cleaned) == "url"
    assert resolve_source_flow(cleaned).platform == "xiaohongshu"
    assert ytdlp._is_xiaohongshu_url(url)
    assert not ytdlp._is_bilibili_url(url)
    assert ytdlp._extract_bilibili_bvid(url) is None

    service = YtdlpService()

    def fail_bilibili(*_args, **_kwargs):
        raise AssertionError("bilibili branch should not be used")

    monkeypatch.setattr(service, "_download_bilibili", fail_bilibili)
    monkeypatch.setattr(
        service,
        "_download_xiaohongshu",
        lambda source, output_dir: {
            "url": source,
            "title": "xhs",
            "file_path": None,
            "video_path": None,
            "info": {"platform": "xiaohongshu"},
        },
    )

    result = service.download(url, tmp_path)

    assert result["info"]["platform"] == "xiaohongshu"


def test_xiaohongshu_share_text_download_uses_xiaohongshu_branch(tmp_path, monkeypatch):
    share_text = (
        "26 【传说中deepseek融资会议Q&A - blh | 小红书 - 你的生活兴趣社区】 "
        "https://www.xiaohongshu.com/discovery/item/6a37ac8b000000001101d922"
        "?source=webshare&xhsshare=pc_web&xsec_token=AB3Gr9I0rGdAYkcJ7Z7sd3ppChGDarTcN_3lGMsI2CEiU="
    )
    service = YtdlpService()

    def fail_bilibili(*_args, **_kwargs):
        raise AssertionError("bilibili branch should not be used")

    monkeypatch.setattr(service, "_download_bilibili", fail_bilibili)
    monkeypatch.setattr(
        service,
        "_download_xiaohongshu",
        lambda url, output_dir: {
            "url": url,
            "title": "xhs",
            "file_path": None,
            "video_path": None,
            "info": {"platform": "xiaohongshu"},
        },
    )

    result = service.download(share_text, tmp_path)

    assert result["info"]["platform"] == "xiaohongshu"


def test_xiaohongshu_image_download_retries_transient_urlopen_error(tmp_path, monkeypatch):
    calls = 0

    class FakeResponse:
        def __init__(self):
            self.headers = {"Content-Length": "3"}
            self._sent = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            if self._sent:
                return b""
            self._sent = True
            return b"abc"

    def fake_urlopen(_req, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise xhs_api.urllib.error.URLError("ssl eof")
        return FakeResponse()

    monkeypatch.setattr(xhs_api, "urllib_urlopen", fake_urlopen)
    monkeypatch.setattr(xhs_api.time, "sleep", lambda _seconds: None)

    dest = tmp_path / "00.jpg"
    xhs_api._download_file("https://ci.xiaohongshu.com/image", dest, "https://www.xiaohongshu.com/")

    assert calls == 2
    assert dest.read_bytes() == b"abc"


def test_xiaohongshu_image_download_records_candidate_diagnostics(tmp_path, monkeypatch):
    calls: list[str] = []

    class FakeResponse:
        status = 200
        headers = {"Content-Length": "3"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            return b"abc" if not hasattr(self, "_sent") else b""

    def fake_urlopen(req, timeout):
        url = req.full_url
        calls.append(url)
        if "ci.xiaohongshu.com" in url:
            raise xhs_api.urllib.error.URLError("ssl eof")
        response = FakeResponse()
        response._sent = False

        def read_once(_size):
            if response._sent:
                return b""
            response._sent = True
            return b"abc"

        response.read = read_once
        return response

    monkeypatch.setattr(xhs_api, "urllib_urlopen", fake_urlopen)
    monkeypatch.setattr(xhs_api.time, "sleep", lambda _seconds: None)

    info = {
        "webpage_url": "https://www.xiaohongshu.com/explore/demo",
        "extra": {
            "image_url_candidates": [[
                "https://ci.xiaohongshu.com/abc/demo.jpg",
                "https://sns-webpic-qc.xhscdn.com/abc/demo.jpg?imageView2/2/w/1080",
            ]],
        },
    }

    paths = xhs_api.download_images(info, tmp_path)

    assert [p.name for p in paths] == ["00.jpg"]
    assert len(calls) == 2
    diagnostics = info["extra"]["image_download_diagnostics"]
    assert diagnostics["success"] == 1
    attempts = diagnostics["images"][0]["attempts"]
    assert [attempt["status"] for attempt in attempts] == ["failed", "success"]
    assert attempts[0]["classification"] == "tls_or_cdn_rejected"
    assert attempts[0]["request"]["cookie_state"] in {"anonymous_webId", "runtime_cookie"}
    assert attempts[1]["url_kind"] == "preview_or_cdn"


def test_image_note_fallback_outputs_do_not_use_llm_placeholder():
    text = "### 笔记正文\nDeepSeek 招聘讨论，包含大厂履历和 AI 公司经验等内容。"
    summary = _fallback_note_summary(text)
    mindmap = _fallback_note_mindmap(MediaMetadata(title="招聘讨论"), 3, text)

    assert "[LLM not configured]" not in summary["tldr"]
    assert "[LLM not configured]" not in mindmap
    assert "招聘讨论" in mindmap
    assert "图片数量: 3" in mindmap


@pytest.mark.asyncio
async def test_image_note_llm_connection_error_writes_fallback_archive(tmp_path, monkeypatch):
    database.reset_db_path(tmp_path)
    settings = RuntimeSettings(
        data_root=str(tmp_path),
        deepseek_api_key="sk-testkey1234567890",
        deepseek_summary_model="deepseek-v4-pro",
        kb_enabled=False,
    )
    monkeypatch.setattr(settings_module, "_runtime_settings", settings)
    monkeypatch.setattr(pipeline_core, "get_runtime_settings", lambda: settings)
    monkeypatch.setattr(pipeline_core, "_schedule_kb_index", lambda *_args, **_kwargs: None)

    import app.services.analysis as analysis_module

    async def fail_analyze(*_args, **_kwargs):
        raise ConnectionError("Connection error.")

    monkeypatch.setattr(analysis_module, "analyze_content", fail_analyze)

    def fail_download(*_args, **_kwargs):
        raise RuntimeError("expired image urls")

    monkeypatch.setattr(xhs_api, "download_images", fail_download)

    store = database.get_task_store()
    task = Task(
        id=uuid4(),
        task_type=TaskType.PIPELINE,
        status=TaskStatus.PROCESSING,
        source="https://www.xiaohongshu.com/explore/demo",
    )
    store.save(task)

    source_path = tmp_path / "source-input.md"
    source_path.write_text("招聘讨论，包含大厂履历和 AI 公司经验等内容。", encoding="utf-8")
    task_dir = tmp_path / "archive"
    task_dir.mkdir()
    metadata = MediaMetadata(
        title="招聘讨论",
        source_url=task.source,
        platform="xiaohongshu",
        media_type=MediaType.OTHER,
        content_subtype="image_note",
    )

    await pipeline_core._process_image_note(
        task,
        metadata,
        task_dir,
        {"extra": {"source_markdown_path": str(source_path), "image_urls": ["https://example.com/0.webp"]}},
    )

    warning_codes = [warning["code"] for warning in task.result["warnings"]]
    assert "note_images_download_failed" in warning_codes
    assert "note_llm_failed" in warning_codes
    assert task.result["archive"]["files"]["summary"].endswith("summary.md")
    assert (task_dir / "summary.md").exists()
    assert (task_dir / "mindmap.md").exists()
    analysis = json.loads((task_dir / "analysis.json").read_text(encoding="utf-8"))
    assert analysis["_fallback"]["reason"] == "llm_failed"


@pytest.mark.asyncio
async def test_image_note_vlm_records_each_image_status(tmp_path, monkeypatch):
    database.reset_db_path(tmp_path)
    settings = RuntimeSettings(
        data_root=str(tmp_path),
        vlm_api_base="https://vlm.example/v1",
        vlm_api_key="vlm-key",
        vlm_model="vision-model",
        kb_enabled=False,
    )
    monkeypatch.setattr(settings_module, "_runtime_settings", settings)
    monkeypatch.setattr(pipeline_core, "get_runtime_settings", lambda: settings)
    monkeypatch.setattr(pipeline_core, "_schedule_kb_index", lambda *_args, **_kwargs: None)

    from app.core.model_router import EndpointBinding
    import app.core.model_router as model_router
    import app.services.analysis.vlm as vlm_module

    def fake_resolve_vlm_binding(_rt):
        return EndpointBinding(
            capability="vlm",
            model="vision-model",
            api_base="https://vlm.example/v1",
            api_key="vlm-key",
            configured=True,
            request_kwargs={"concurrency": 2, "timeout_sec": 3},
        )

    class FakeVLM:
        def describe_image(self, path, _binding):
            if Path(path).name == "00.jpg":
                return {
                    "kind": "text",
                    "text": "第一张 OCR",
                    "payload_meta": {"source_bytes": 3, "payload_bytes": 3},
                    "duration_ms": 12,
                }
            return {
                "kind": "text",
                "text": "",
                "payload_meta": {"source_bytes": 3, "payload_bytes": 3},
                "duration_ms": 10,
            }

    def fake_download_images(_info, output_dir):
        images_dir = output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        first = images_dir / "00.jpg"
        second = images_dir / "01.jpg"
        first.write_bytes(b"abc")
        second.write_bytes(b"def")
        return [first, second]

    monkeypatch.setattr(model_router, "resolve_vlm_binding", fake_resolve_vlm_binding)
    monkeypatch.setattr(vlm_module, "get_vlm_service", lambda: FakeVLM())
    monkeypatch.setattr(xhs_api, "download_images", fake_download_images)

    store = database.get_task_store()
    task = Task(
        id=uuid4(),
        task_type=TaskType.PIPELINE,
        status=TaskStatus.PROCESSING,
        source="https://www.xiaohongshu.com/explore/demo",
    )
    store.save(task)

    task_dir = tmp_path / "archive"
    task_dir.mkdir()
    metadata = MediaMetadata(
        title="多图 caption",
        source_url=task.source,
        platform="xiaohongshu",
        media_type=MediaType.OTHER,
        content_subtype="image_note",
        description="正文足够触发 fallback 汇总",
    )

    await pipeline_core._process_image_note(task, metadata, task_dir, {"extra": {"image_urls": ["a", "b"]}})

    by_index = {item["index"]: item for item in task.result["image_descriptions"]}
    assert by_index[0]["status"] == "completed"
    assert by_index[1]["status"] == "failed"
    assert by_index[1]["error"] == "VLM returned empty caption text"
    assert (task_dir / "descriptions" / "00.md").read_text(encoding="utf-8") == "第一张 OCR"
    assert "VLM caption 失败" in (task_dir / "descriptions" / "01.md").read_text(encoding="utf-8")
    warning_codes = [warning["code"] for warning in task.result["warnings"]]
    assert "note_vlm_partial_failed" in warning_codes


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
