"""URL source resolver and flow registry."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.core.source_normalization import normalize_source_input


@dataclass(frozen=True)
class FlowStep:
    id: str
    label: str


@dataclass(frozen=True)
class SourceFlow:
    source_type: str
    platform: str
    branch: str
    content_subtype: str
    flow_id: str
    label: str
    ingestor: str
    requires_download: bool
    try_subtitles: bool
    requires_uvr: bool
    preferred_asr_provider: str | None = None

    @property
    def route_type(self) -> str:
        if self.source_type != "url":
            return self.source_type
        if self.platform in {
            "youtube",
            "bilibili_video",
            "bilibili_opus",
            "xiaohongshu",
            "zhihu",
            "xiaoyuzhou",
            "apple_podcast",
            "webpage",
            "twitter",
        }:
            return self.platform
        return "url"

    @property
    def steps(self) -> list[FlowStep]:
        return FLOW_STEPS.get(self.flow_id, FLOW_STEPS["url_media_asr"])

    def snapshot(self, *, status: str = "pending", current_step: str | None = None) -> dict[str, Any]:
        steps = [{"id": step.id, "label": step.label} for step in self.steps]
        step_ids = {step["id"] for step in steps}
        step_id = current_step if current_step in step_ids else (steps[0]["id"] if steps else None)
        index = next((i for i, step in enumerate(steps) if step["id"] == step_id), 0)
        total = len(steps)
        return {
            "id": self.flow_id,
            "label": self.label,
            "platform": self.platform,
            "branch": self.branch,
            "content_subtype": self.content_subtype,
            "current_step": step_id,
            "current_step_index": index,
            "current_step_label": steps[index]["label"] if steps else "",
            "total_steps": total,
            "progress": (index / total) if total else 0.0,
            "status": status,
            "steps": steps,
        }

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


FLOW_LABELS = {
    "url_webpage_note": "网页文本抽取",
    "url_platform_video_subtitle": "平台字幕优先",
    "url_platform_video_asr": "平台音视频 ASR",
    "url_media_asr": "通用媒体 ASR",
    "url_media_asr_api_fallback": "API ASR fallback",
    "url_image_note": "图文笔记解析",
    "url_text_note": "文本笔记解析",
    "podcast_asr": "播客音频 ASR",
}

FLOW_STEPS: dict[str, list[FlowStep]] = {
    "url_webpage_note": [
        FlowStep("resolve", "识别网页"),
        FlowStep("download", "抽取正文"),
        FlowStep("analyze", "生成摘要与导图"),
        FlowStep("archive", "归档"),
    ],
    "url_platform_video_subtitle": [
        FlowStep("resolve", "识别平台"),
        FlowStep("subtitle_probe", "探测字幕"),
        FlowStep("download", "下载媒体"),
        FlowStep("transcribe", "处理字幕"),
        FlowStep("analyze", "生成摘要与导图"),
        FlowStep("archive", "归档"),
    ],
    "url_platform_video_asr": [
        FlowStep("resolve", "识别平台"),
        FlowStep("download", "下载媒体"),
        FlowStep("separate", "处理人声"),
        FlowStep("transcribe", "转录音频"),
        FlowStep("polish", "润色字幕"),
        FlowStep("analyze", "生成摘要与导图"),
        FlowStep("archive", "归档"),
    ],
    "url_media_asr": [
        FlowStep("resolve", "识别媒体"),
        FlowStep("download", "下载媒体"),
        FlowStep("separate", "处理人声"),
        FlowStep("transcribe", "转录音频"),
        FlowStep("polish", "润色字幕"),
        FlowStep("analyze", "生成摘要与导图"),
        FlowStep("archive", "归档"),
    ],
    "url_media_asr_api_fallback": [
        FlowStep("resolve", "识别媒体"),
        FlowStep("download", "下载媒体"),
        FlowStep("transcribe", "API 转录"),
        FlowStep("polish", "润色字幕"),
        FlowStep("analyze", "生成摘要与导图"),
        FlowStep("archive", "归档"),
    ],
    "url_image_note": [
        FlowStep("resolve", "识别图文"),
        FlowStep("download", "下载图片"),
        FlowStep("analyze", "理解图文"),
        FlowStep("archive", "归档"),
    ],
    "url_text_note": [
        FlowStep("resolve", "识别文本"),
        FlowStep("download", "抽取正文"),
        FlowStep("analyze", "生成摘要与导图"),
        FlowStep("archive", "归档"),
    ],
    "podcast_asr": [
        FlowStep("resolve", "识别播客"),
        FlowStep("download", "下载音频"),
        FlowStep("transcribe", "转录音频"),
        FlowStep("polish", "润色字幕"),
        FlowStep("analyze", "生成摘要与导图"),
        FlowStep("archive", "归档"),
    ],
}


def flow_steps_schema(flow_id: str) -> list[dict[str, str]]:
    return [{"id": step.id, "label": step.label} for step in FLOW_STEPS.get(flow_id, [])]


def resolve_source_flow(
    source: str,
    *,
    prefer_platform_subtitles: bool = True,
    force_asr: bool = False,
    task_options: dict[str, Any] | None = None,
) -> SourceFlow:
    source = normalize_source_input(source)
    source_type = (
        "url"
        if source.lower().startswith(("http://", "https://"))
        or re.fullmatch(r"(?:BV[0-9A-Za-z]{10}|av\d+)", source.strip(), re.IGNORECASE)
        else _local_source_type(source)
    )
    if source_type != "url":
        media_subtype = "audio" if source_type == "local_audio" else "video"
        return _flow(
            source_type=source_type,
            platform="local",
            branch="asr",
            content_subtype=media_subtype,
            flow_id="url_media_asr",
            ingestor="local",
            requires_download=False,
            try_subtitles=False,
            requires_uvr=source_type == "local_video",
            preferred_asr_provider=_preferred_asr(task_options),
        )

    from app.services.ingestion import ytdlp

    platform = "generic"
    content_subtype = "video"
    ingestor = "ytdlp"
    flow_id = "url_media_asr"
    branch = "asr"
    try_subtitles = False
    requires_uvr = True

    if ytdlp._is_youtube_url(source):
        platform = "youtube"
        ingestor = "ytdlp"
        try_subtitles = bool(prefer_platform_subtitles and not force_asr)
        flow_id = "url_platform_video_subtitle" if try_subtitles else "url_platform_video_asr"
        branch = "subtitle" if try_subtitles else "asr"
    elif ytdlp._is_twitter_url(source):
        platform = "twitter"
        ingestor = "ytdlp"
        flow_id = "url_platform_video_asr"
        branch = "asr"
    elif ytdlp._is_bilibili_article_url(source):
        platform = "bilibili_opus"
        content_subtype = "text_note"
        ingestor = "bilibili_opus"
        flow_id = "url_webpage_note"
        branch = "note"
        requires_uvr = False
    elif ytdlp._is_bilibili_image_note_url(source):
        platform = "bilibili_opus"
        content_subtype = "image_note"
        ingestor = "bilibili_opus"
        flow_id = "url_image_note"
        branch = "note"
        requires_uvr = False
    elif ytdlp._is_bilibili_url(source):
        platform = "bilibili_video"
        ingestor = "bilibili_video"
        try_subtitles = bool(prefer_platform_subtitles and not force_asr)
        flow_id = "url_platform_video_subtitle" if try_subtitles else "url_platform_video_asr"
        branch = "subtitle" if try_subtitles else "asr"
    elif ytdlp._is_xiaoyuzhou_url(source):
        platform = "xiaoyuzhou"
        content_subtype = "podcast_episode"
        ingestor = "xiaoyuzhou"
        flow_id = "podcast_asr"
        requires_uvr = False
    elif ytdlp._is_apple_podcast_url(source):
        platform = "apple_podcast"
        content_subtype = "podcast_episode"
        ingestor = "apple_podcast"
        flow_id = "podcast_asr"
        requires_uvr = False
    elif ytdlp._is_xiaohongshu_url(source):
        platform = "xiaohongshu"
        content_subtype = "image_note"
        ingestor = "xiaohongshu"
        flow_id = "url_image_note"
        branch = "note"
        requires_uvr = False
    elif ytdlp._is_zhihu_url(source):
        platform = "zhihu"
        content_subtype = "text_note"
        ingestor = "zhihu"
        flow_id = "url_text_note"
        branch = "note"
        requires_uvr = False
    elif ytdlp._is_generic_webpage_url(source):
        platform = "webpage"
        content_subtype = "text_note"
        ingestor = "webpage"
        flow_id = "url_webpage_note"
        branch = "note"
        requires_uvr = False
    elif ytdlp._is_direct_media_url(source):
        platform = "direct_media"
        content_subtype = _content_subtype_from_url(source)
        requires_uvr = content_subtype == "video"

    return _flow(
        source_type=source_type,
        platform=platform,
        branch=branch,
        content_subtype=content_subtype,
        flow_id=flow_id,
        ingestor=ingestor,
        requires_download=True,
        try_subtitles=try_subtitles,
        requires_uvr=requires_uvr,
        preferred_asr_provider=_preferred_asr(task_options),
    )


def flow_from_metadata(
    base: SourceFlow,
    metadata: Any,
    *,
    has_subtitle: bool = False,
    force_asr: bool = False,
    api_fallback: bool = False,
    preferred_asr_provider: str | None = None,
) -> SourceFlow:
    platform = getattr(metadata, "platform", None) or base.platform
    subtype = getattr(metadata, "content_subtype", None) or base.content_subtype
    media_type = str(getattr(metadata, "media_type", "") or "").lower()

    if subtype == "image_note":
        flow_id = "url_image_note"
        branch = "note"
        requires_uvr = False
    elif subtype == "text_note" or platform in {"webpage", "zhihu"}:
        flow_id = "url_webpage_note" if platform == "webpage" else "url_text_note"
        branch = "note"
        requires_uvr = False
    elif subtype == "podcast_episode" or media_type.endswith("podcast"):
        flow_id = "podcast_asr"
        branch = "asr"
        requires_uvr = False
    elif api_fallback:
        flow_id = "url_media_asr_api_fallback"
        branch = "asr_api_fallback"
        requires_uvr = False
    elif has_subtitle and not force_asr:
        flow_id = "url_platform_video_subtitle"
        branch = "subtitle"
        requires_uvr = False
    elif platform in {"youtube", "bilibili", "bilibili_video", "xiaohongshu", "douyin", "tiktok", "twitter", "weibo"}:
        flow_id = "url_platform_video_asr"
        branch = "asr"
        requires_uvr = True
    else:
        flow_id = "url_media_asr"
        branch = "asr"
        requires_uvr = subtype == "video" or media_type.endswith("video")

    return _flow(
        source_type=base.source_type,
        platform=platform,
        branch=branch,
        content_subtype=subtype,
        flow_id=flow_id,
        ingestor=base.ingestor,
        requires_download=base.requires_download,
        try_subtitles=has_subtitle,
        requires_uvr=requires_uvr,
        preferred_asr_provider=preferred_asr_provider or base.preferred_asr_provider,
    )


def _flow(
    *,
    source_type: str,
    platform: str,
    branch: str,
    content_subtype: str,
    flow_id: str,
    ingestor: str,
    requires_download: bool,
    try_subtitles: bool,
    requires_uvr: bool,
    preferred_asr_provider: str | None = None,
) -> SourceFlow:
    return SourceFlow(
        source_type=source_type,
        platform=platform,
        branch=branch,
        content_subtype=content_subtype,
        flow_id=flow_id,
        label=FLOW_LABELS[flow_id],
        ingestor=ingestor,
        requires_download=requires_download,
        try_subtitles=try_subtitles,
        requires_uvr=requires_uvr,
        preferred_asr_provider=preferred_asr_provider,
    )


def _preferred_asr(task_options: dict[str, Any] | None) -> str | None:
    if not task_options:
        return None
    value = task_options.get("asr_provider")
    return str(value).strip() if value else None


def _local_source_type(source: str) -> str:
    suffix = Path(source).suffix.lower()
    if suffix in {".mp4", ".mkv", ".avi", ".webm", ".mov"}:
        return "local_video"
    if suffix in {".mp3", ".wav", ".flac", ".m4a", ".ogg"}:
        return "local_audio"
    return "unknown"


def _content_subtype_from_url(source: str) -> str:
    suffix = Path(source.split("?", 1)[0]).suffix.lower()
    if suffix in {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"}:
        return "audio"
    return "video"
