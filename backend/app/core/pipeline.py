"""Pipeline orchestration — extracted from api.routes.tasks.

This module owns the full processing pipeline (download → archive) and uses
TaskStore + EventBus for state management instead of in-memory dicts.
"""

import asyncio
import inspect
import json
import logging
import re
import shutil
import subprocess
import time
import urllib.parse
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.database import get_task_store
from app.core.events import TaskEvent, get_event_bus
from app.core.settings import get_runtime_settings
from app.core.logging_setup import log_event
from app.core.source_normalization import normalize_source_input
from app.core.source_resolver import SourceFlow, flow_from_metadata, resolve_source_flow
from app.models import MediaMetadata, Task, TaskStatus, TaskType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline step definitions
# ---------------------------------------------------------------------------

class PipelineStep(StrEnum):
    """Pipeline processing steps."""
    DOWNLOAD = "download"
    SEPARATE = "separate"
    TRANSCRIBE = "transcribe"
    VOICEPRINT = "voiceprint"
    ANALYZE = "analyze"
    POLISH = "polish"
    ARCHIVE = "archive"


PIPELINE_STEPS = [
    {"id": PipelineStep.DOWNLOAD, "name": "下载媒体", "name_en": "Downloading"},
    {"id": PipelineStep.SEPARATE, "name": "分离人声", "name_en": "Separating vocals"},
    {"id": PipelineStep.TRANSCRIBE, "name": "转录音频", "name_en": "Transcribing"},
    {"id": PipelineStep.VOICEPRINT, "name": "声纹识别", "name_en": "Matching voiceprints"},
    {"id": PipelineStep.POLISH, "name": "润色字幕", "name_en": "Polishing transcript"},
    {"id": PipelineStep.ANALYZE, "name": "分析+摘要+脑图", "name_en": "Analyzing & summarizing"},
    {"id": PipelineStep.ARCHIVE, "name": "归档保存", "name_en": "Archiving"},
]


def pipeline_steps_schema() -> list[dict[str, str]]:
    """Return the public pipeline step schema in execution order."""
    return [{"id": str(s["id"]), "name": s["name"], "name_en": s["name_en"]} for s in PIPELINE_STEPS]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _canonical_image_url(value: Any) -> str:
    raw = str(value or "").strip()
    if raw.startswith("//"):
        raw = f"https:{raw}"
    if raw.startswith("http://"):
        raw = "https://" + raw[len("http://"):]
    if not raw.startswith("https://"):
        return ""
    parsed = urllib.parse.urlparse(raw)
    path = urllib.parse.unquote(parsed.path)
    if "@" in path:
        path = path.split("@", 1)[0]
    host = parsed.netloc.lower()
    if (host == "pbs.twimg.com" or host.endswith(".pbs.twimg.com")) and "/media/" in path:
        prefix, filename = path.rsplit("/", 1)
        filename = filename.split(":", 1)[0]
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        path = f"{prefix}/{stem}"
    return urllib.parse.urlunparse(("https", host, path, "", "", ""))


def _localize_note_markdown_image_refs(text: str, metadata: MediaMetadata, image_paths: list[Path]) -> str:
    extra = metadata.extra if isinstance(metadata.extra, dict) else {}
    image_urls = extra.get("image_urls")
    image_candidates = extra.get("image_url_candidates")
    if not isinstance(image_urls, list) and not isinstance(image_candidates, list):
        return text
    if not image_paths:
        return text

    mapping: dict[str, str] = {}
    for fallback_idx, path in enumerate(image_paths):
        idx = int(path.stem) if path.stem.isdigit() else fallback_idx
        local_path = f"images/{path.name}"
        urls: list[Any] = []
        if isinstance(image_urls, list) and 0 <= idx < len(image_urls):
            urls.append(image_urls[idx])
        if isinstance(image_candidates, list) and 0 <= idx < len(image_candidates):
            group = image_candidates[idx]
            if isinstance(group, list):
                urls.extend(group)
        for url in urls:
            key = _canonical_image_url(url)
            if key:
                mapping[key] = local_path
    if not mapping:
        return text

    def replace(match: re.Match[str]) -> str:
        key = _canonical_image_url(match.group(2))
        local_path = mapping.get(key)
        if not local_path:
            return match.group(0)
        return f"{match.group(1)}{local_path}{match.group(3)}"

    return re.sub(r"(!\[[^\]]*]\()([^)]+)(\))", replace, text)

def _detect_source_type(source: str) -> str:
    """Detect the type of media source."""
    source = _clean_source_path(source)
    source_lower = source.lower()
    if source_lower.startswith(("http://", "https://")):
        return "url"
    if any(source_lower.endswith(ext) for ext in [".mp4", ".mkv", ".avi", ".webm", ".mov"]):
        return "local_video"
    if any(source_lower.endswith(ext) for ext in [".mp3", ".wav", ".flac", ".m4a", ".ogg"]):
        return "local_audio"
    return "unknown"


def _platform_prefer_subtitles(source_type: str) -> bool:
    """Resolve subtitle preference with per-platform config fallback."""
    rt = get_runtime_settings()
    try:
        configs = json.loads(rt.platform_configs or "{}")
    except Exception:
        configs = {}

    if source_type == "webpage":
        return False

    platform_config_key = "bilibili" if source_type in {"bilibili", "bilibili_video", "bilibili_opus"} else source_type
    supported_platforms = {"bilibili", "youtube", "xiaoyuzhou", "xiaohongshu", "zhihu", "apple_podcast"}
    platform_cfg = configs.get(platform_config_key) if platform_config_key in supported_platforms else None
    if isinstance(platform_cfg, dict) and "prefer_subtitle" in platform_cfg:
        return bool(platform_cfg["prefer_subtitle"])
    return bool(rt.prefer_platform_subtitles)


_DOWNLOAD_RESOLVES_TITLE_ROUTES = {
    "xiaohongshu",
    "zhihu",
    "bilibili_opus",
    "xiaoyuzhou",
    "apple_podcast",
    "webpage",
    "twitter",
}


def _download_resolves_url_title(route_type: str) -> bool:
    return route_type in _DOWNLOAD_RESOLVES_TITLE_ROUTES


_WIN_RESERVED_FILENAME_RE = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?$",
    re.IGNORECASE,
)


def _sanitize_filename(name: str) -> str:
    """Sanitize string for use as filename."""
    name = str(name or "")
    name = re.sub(r"[\x00-\x1f]+", " ", name)
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name)
    name = name.strip(" .")
    if _WIN_RESERVED_FILENAME_RE.match(name):
        name = f"_{name}"
    name = name[:100].rstrip(" .")
    return name


def _unique_child_dir(parent: Path, dir_name: str, current_dir: Path | None = None) -> Path:
    """Return an available child directory path under parent."""
    candidate = parent / dir_name
    if current_dir is not None and candidate.resolve() == current_dir.resolve():
        return current_dir
    if not candidate.exists():
        return candidate

    counter = 2
    while True:
        candidate = parent / f"{dir_name} ({counter})"
        if current_dir is not None and candidate.resolve() == current_dir.resolve():
            return current_dir
        if not candidate.exists():
            return candidate
        counter += 1


def create_task_dir(task_id: UUID, title: str | None = None) -> Path:
    """Create a dedicated directory for this task under data/{title}/."""
    settings = get_runtime_settings()
    data_root = Path(settings.data_root).resolve()

    if title:
        dir_name = _sanitize_filename(title) or str(task_id)[:8]
    else:
        dir_name = str(task_id)[:8]

    task_dir = _unique_child_dir(data_root, dir_name)
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


def _rename_task_dir_to_title(task_dir: Path, title: str | None) -> tuple[Path, Path | None]:
    """Move a placeholder task directory to a unique metadata-title directory."""
    if not title:
        return task_dir, None

    dir_name = _sanitize_filename(title)
    if not dir_name:
        return task_dir, None

    target = _unique_child_dir(task_dir.parent, dir_name, current_dir=task_dir)
    if target.resolve() == task_dir.resolve():
        return task_dir, None

    task_dir.rename(target)
    return target, task_dir


def write_metadata_json(
    task_dir: Path,
    metadata: "MediaMetadata | dict",
    status: str = "processing",
    task_id: str | None = None,
) -> Path:
    """Write or update metadata.json in the task directory."""
    import json
    meta_path = task_dir / "metadata.json"
    if isinstance(metadata, MediaMetadata):
        data = metadata.model_dump(mode="json")
    else:
        data = dict(metadata)
    data["status"] = status
    if task_id:
        data["task_id"] = task_id
    elif meta_path.exists():
        # Preserve existing task_id on update
        try:
            existing = json.loads(meta_path.read_text(encoding="utf-8"))
            if existing.get("task_id"):
                data["task_id"] = existing["task_id"]
        except Exception:
            pass
    meta_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta_path


def _sync_task_from_metadata(task: "Task", metadata: "MediaMetadata") -> None:
    """Copy denormalized metadata fields onto the task object for DB + SSE exposure."""
    if metadata.platform:
        task.platform = metadata.platform
    if metadata.uploader_id:
        task.uploader_id = metadata.uploader_id
    if metadata.content_subtype:
        task.content_subtype = metadata.content_subtype


def update_metadata_status(task_dir: Path | None, status: str) -> None:
    """Update only metadata.json status when a task ends outside the normal archive path."""
    if task_dir is None:
        return
    meta_path = task_dir / "metadata.json"
    if not meta_path.exists():
        return
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        data["status"] = status
        meta_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        log_event(logger, logging.DEBUG, "metadata_status.update_failed", status=status, path=meta_path, exc_info=True)


async def _raise_if_cancelled(task_id: UUID) -> None:
    """Honor cancellation/pause requests between blocking pipeline phases."""
    task = get_task_store().get(task_id)
    if task and task.status in {TaskStatus.CANCELLED, TaskStatus.PAUSED}:
        raise asyncio.CancelledError()


def _task_download_cancelled(task_id: UUID) -> bool:
    """Return True when a blocking downloader should stop promptly."""
    task = get_task_store().get(task_id)
    return task is None or task.status in {TaskStatus.CANCELLED, TaskStatus.PAUSED}


async def _write_summary_files(
    task: Task,
    task_dir: Path,
    metadata: MediaMetadata,
    summary: dict[str, Any],
) -> None:
    """Persist structured and rendered summary outputs."""
    summary_json_path = task_dir / "summary.json"
    summary_json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    await _emit_file_ready(task, "summary.json", str(summary_json_path))

    from app.services.archiving.archive import SUMMARY_TEMPLATE, get_archive_service
    _svc = get_archive_service()
    sum_path = task_dir / "summary.md"
    sum_content = SUMMARY_TEMPLATE.format(
        title=metadata.title,
        source_url=metadata.source_url or "",
        date=datetime.now().strftime("%Y-%m-%d"),
        tldr=summary.get("tldr", ""),
        key_facts=_svc._fmt_list(summary.get("key_facts", [])),
    )
    sum_path.write_text(sum_content, encoding="utf-8")
    await _emit_file_ready(task, "summary.md", str(sum_path))


async def _emit_file_ready(task: Task, filename: str, file_path: str) -> None:
    """Emit a file_ready SSE event when a file is written to disk."""
    bus = get_event_bus()
    await bus.publish(TaskEvent(task.id, "file_ready", {
        "file": filename,
        "path": file_path,
    }))


def _artifact_content_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".json":
        return "application/json"
    if suffix == ".md":
        return "text/markdown"
    if suffix == ".srt":
        return "application/x-subrip"
    return "text/plain"


def _persist_text_artifact(task: Task, filename: str, content: str) -> None:
    """Save a generated text artifact into SQLite alongside file output."""
    get_task_store().save_artifact(
        task.id,
        filename,
        content,
        content_type=_artifact_content_type(filename),
    )


async def _write_text_artifact(task: Task, task_dir: Path, filename: str, content: str) -> Path:
    """Write text artifact to disk, mirror it to SQLite, then emit file_ready."""
    artifact_path = task_dir / filename
    artifact_path.write_text(content, encoding="utf-8")
    _persist_text_artifact(task, filename, content)
    await _emit_file_ready(task, filename, str(artifact_path))
    return artifact_path


def _rewrite_path_after_dir_move(value: Any, old_dir: Path, new_dir: Path) -> Any:
    if not isinstance(value, str) or not value:
        return value
    try:
        path = Path(value)
        if not path.is_absolute():
            return value
        relative = path.resolve().relative_to(old_dir.resolve())
        return str(new_dir / relative)
    except Exception:
        return value


def _rewrite_ingest_paths_after_task_dir_move(ingest: dict[str, Any], metadata: "MediaMetadata", old_dir: Path, new_dir: Path) -> None:
    """Keep note/webpage asset paths valid after renaming the task directory."""

    def rewrite_extra(extra: Any) -> None:
        if not isinstance(extra, dict):
            return
        extra["source_markdown_path"] = _rewrite_path_after_dir_move(
            extra.get("source_markdown_path"),
            old_dir,
            new_dir,
        )
        images = extra.get("images")
        if isinstance(images, list):
            for item in images:
                if isinstance(item, dict):
                    item["path"] = _rewrite_path_after_dir_move(item.get("path"), old_dir, new_dir)

    rewrite_extra(metadata.extra)
    info = ingest.get("info") if isinstance(ingest, dict) else None
    if isinstance(info, dict):
        rewrite_extra(info.get("extra"))
        info["thumbnail"] = _rewrite_path_after_dir_move(info.get("thumbnail"), old_dir, new_dir)


async def _write_mindmap_files(task: Task, task_dir: Path, mindmap: str) -> None:
    """Persist frontend tree JSON plus clean Markdown export for the mindmap."""
    from app.services.analysis.llm import (
        mindmap_markdown_to_timed_tree,
        mindmap_markdown_without_timestamps,
    )

    export_markdown = mindmap_markdown_without_timestamps(mindmap) or mindmap
    await _write_text_artifact(task, task_dir, "mindmap.md", export_markdown)

    tree = mindmap_markdown_to_timed_tree(mindmap)
    await _write_text_artifact(
        task,
        task_dir,
        "mindmap.json",
        json.dumps(tree, indent=2, ensure_ascii=False),
    )


async def _write_detail_file(task: Task, task_dir: Path, detail: str) -> None:
    """Persist optional former deep mindmap as detail.md."""
    await _write_text_artifact(task, task_dir, "detail.md", detail)


def _clean_source_path(source: str) -> str:
    """Clean up source path by removing quotes and whitespace.

    Also extracts the first URL from share-text blobs like the ones copied from
    the Xiaohongshu mobile/web app:
      '77 【标题 | 小红书】 😆 n7715oGO82X4J5v 😆 https://www.xiaohongshu.com/...'
    """
    return normalize_source_input(source)


def _looks_like_local_path(source: str) -> bool:
    """Check if source looks like a local file path (not a URL)."""
    source = _clean_source_path(source)
    if source.startswith(('http://', 'https://', 'ftp://', 'rtmp://')):
        return False
    if len(source) >= 2 and source[1] == ':':
        return True
    if source.startswith('/'):
        return True
    if '.' in source and '://' not in source:
        ext = source.rsplit('.', 1)[-1].lower()
        media_exts = {'mp4', 'mkv', 'avi', 'webm', 'mov', 'mp3', 'wav', 'flac', 'm4a', 'ogg'}
        if ext in media_exts:
            return True
    return False


# Map of locale codes → human-readable names fed into English prompts.
# The model gets plain names (no codes) so the language-policy clause in
# the prompt reads naturally and handles mixed / unknown cases gracefully.
_LANG_NAME = {
    "zh": "Chinese",
    "zh-cn": "Chinese",
    "zh-hans": "Chinese",
    "zh-hant": "Traditional Chinese",
    "zh-tw": "Traditional Chinese",
    "en": "English",
    "en-us": "English",
    "en-gb": "English",
    "ja": "Japanese",
    "ja-jp": "Japanese",
    "ko": "Korean",
    "ko-kr": "Korean",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "ru": "Russian",
    "pt": "Portuguese",
    "it": "Italian",
}


def _user_language_hint(analysis: dict | None) -> str | None:
    """Pull a human-readable primary-language name out of analysis output.

    Falls back to whatever the analyze step returned (raw code/name) when we
    don't have a mapping, and to ``None`` when analysis is empty.
    """
    if not analysis:
        return None
    raw = str(analysis.get("language") or "").strip()
    if not raw or raw.lower() == "unknown":
        return None
    return _LANG_NAME.get(raw.lower(), raw)


def _extract_internal_asr_error(recognition_segments: list[dict[str, Any]] | None) -> str | None:
    """Detect mock/error placeholder text emitted by ASR backends.

    These placeholders are useful in isolated service tests, but they should
    not be treated as a valid transcript for downstream LLM analysis.
    """
    if not recognition_segments:
        return None

    texts = [str(seg.get("text", "")).strip() for seg in recognition_segments if seg.get("text")]
    if not texts:
        return None

    prefixes = (
        "[Qwen3-ASR error:",
        "[Mock - Qwen3-ASR not installed]",
    )
    for text in texts:
        if text.startswith(prefixes):
            return text.strip("[]")
    return None


def _plain_text_from_srt(srt_content: str) -> str:
    """Extract readable transcript text from SRT content."""
    return " ".join(
        line.strip() for line in srt_content.splitlines()
        if line.strip() and not line.strip().isdigit()
        and "-->" not in line
    )


def _extract_audio_from_video(video_path: Path, output_path: Path) -> Path:
    """Extract audio from video file using ffmpeg."""
    # Resolve to absolute paths so filenames starting with '-' can't be
    # misinterpreted as ffmpeg options.
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path.resolve()),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(output_path.resolve()),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    return output_path


async def _select_polish_track(
    tracks: list[dict],
    srt_text_hint: str = "",
) -> tuple[dict, str]:
    """Pick the subtitle track to polish based on LLM-detected language.

    Detects the video's spoken language from a sample of the first (best)
    track's subtitle content; then matches the detected language against
    the available tracks. Falls back to the first track when no match or
    detection fails.

    Returns (selected_track, detected_lang). detected_lang may be "unknown".
    """
    from app.services.analysis.language_detect import detect_transcript_language, match_track_by_language

    if not tracks:
        raise ValueError("no tracks to select from")
    if len(tracks) == 1:
        # No point running LLM — still detect lang so we can tag metadata
        try:
            sample_srt = Path(tracks[0]["path"]).read_text(encoding="utf-8", errors="ignore")
            detected = await detect_transcript_language(srt=sample_srt)
        except Exception:
            detected = tracks[0].get("lang") or "unknown"
        return tracks[0], detected

    # Use the first (best — CC before AI) track's content as detection sample
    # if no external srt_text_hint was provided.
    sample = srt_text_hint
    if not sample:
        try:
            sample = Path(tracks[0]["path"]).read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            log_event(logger, logging.WARNING, "subtitle.lang_detect.sample_read_failed", error=e)
            return tracks[0], tracks[0].get("lang") or "unknown"

    detected = await detect_transcript_language(srt=sample)
    if detected == "unknown":
        log_event(logger, logging.INFO, "subtitle.track.select_default", reason="unknown_language")
        return tracks[0], detected

    matched = match_track_by_language(tracks, detected)
    if matched:
        log_event(
            logger,
            logging.INFO,
            "subtitle.track.selected",
            lang=matched.get("lang"),
            detected_lang=detected,
        )
        return matched, detected

    log_event(logger, logging.INFO, "subtitle.track.select_default", reason="no_match", detected_lang=detected)
    return tracks[0], detected


def _save_all_tracks_as_transcripts(tracks: list[dict], task_dir: Path) -> list[dict]:
    """Copy each platform subtitle track into task_dir as transcript.{lang}.srt.

    The one chosen for polish will additionally get transcript_polished.srt
    (written elsewhere). Returns a list of manifest entries for metadata.json.
    """
    from shutil import copyfile
    manifest: list[dict] = []
    for t in tracks:
        src = Path(t["path"])
        if not src.exists():
            continue
        lang = t.get("lang") or "unknown"
        dest = task_dir / f"transcript.{lang}.srt"
        try:
            if str(src.resolve()) != str(dest.resolve()):
                copyfile(src, dest)
        except Exception as e:
            log_event(logger, logging.WARNING, "subtitle.track.copy_failed", src=src, dest=dest, error=e)
            continue
        manifest.append({
            "lang": lang,
            "type": t.get("type") or "cc",
            "filename": dest.name,
            "polished": False,
            "source_engine": t.get("source_engine"),
            "validation": t.get("validation"),
        })
    return manifest


def _cleanup_vocals(task_dir: Path, audio_path: str | None, vocals_path: str | None) -> None:
    """Clean up UVR vocals and ASR segment files after transcription.

    Called right after TRANSCRIBE completes — these large WAVs are no longer
    needed once ASR is done.
    """
    cleaned_files = []
    cleaned_size = 0

    # Delete UVR vocals output (only if it's a separate file from the source audio)
    if vocals_path and vocals_path != audio_path:
        vocals_file = Path(vocals_path)
        if vocals_file.exists():
            size = vocals_file.stat().st_size
            vocals_file.unlink()
            cleaned_files.append(vocals_file.name)
            cleaned_size += size

    # Delete ASR segment files
    for segment_file in task_dir.glob("segment_*.wav"):
        size = segment_file.stat().st_size
        segment_file.unlink()
        cleaned_files.append(segment_file.name)
        cleaned_size += size

    if cleaned_files:
        size_mb = cleaned_size / (1024 * 1024)
        log_event(
            logger,
            logging.INFO,
            "cleanup.vocals.done",
            files=len(cleaned_files),
            size_mb=round(size_mb, 1),
        )


def _release_uvr_gpu_resources() -> None:
    """Unload UVR before ASR/local LLM steps that need the same GPU memory."""
    import gc

    try:
        from app.services.preprocessing.uvr import release_uvr_service

        release_uvr_service()
        log_event(logger, logging.INFO, "gpu.uvr.release")
    except Exception as e:
        log_event(logger, logging.WARNING, "gpu.uvr.release_failed", error=e)

    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            log_event(logger, logging.INFO, "gpu.cuda_cache.cleared")
    except Exception as e:
        log_event(logger, logging.WARNING, "gpu.cuda_cache.clear_failed", error=e)

    try:
        gc.collect()
        log_event(logger, logging.INFO, "runtime.gc.collected")
    except Exception:
        pass


def _is_transcript_too_short_for_uvr_fallback(transcript: str, *, min_chars: int = 30) -> bool:
    normalized = re.sub(r"\s+", "", transcript or "")
    return len(normalized) < min_chars


def _require_audio_file(path: str | None, *, stage: str) -> str:
    """Return a concrete audio path or raise a stage-specific error."""
    if not path:
        raise RuntimeError(f"{stage} requires an audio file, but no audio path is available")
    audio_file = Path(path)
    if not audio_file.exists():
        raise FileNotFoundError(f"{stage} audio file does not exist: {path}")
    if not audio_file.is_file():
        raise ValueError(f"{stage} audio path is not a file: {path}")
    return str(audio_file)


def _cleanup_extracted_audio(task_dir: Path, audio_path: str | None, media_type: str | None) -> None:
    """Clean up the working WAV when a compressed source is preserved.

    Two cases:
    1. Video sources — pipeline always extracts a WAV for ASR; the source mp4
       is kept, so the WAV is disposable.
    2. Podcast / audio sources where the platform downloader saved both the
       original compressed file (.m4a/.mp3/...) and a derived working WAV.
       The original is the canonical media — delete the WAV.

    A bare .wav source (no sibling compressed file) is the only copy of the
    media and must be kept.
    """
    if not audio_path:
        return
    audio_file = Path(audio_path)
    if not audio_file.exists() or audio_file.suffix.lower() != ".wav":
        return

    # Always safe to delete the WAV when we came from a video source.
    can_delete = media_type == "video"

    # For audio sources, only delete the WAV if a sibling compressed source
    # exists in the same archive dir (same stem, lossy/lossless container).
    if not can_delete:
        sibling_exts = {".m4a", ".mp3", ".flac", ".ogg", ".opus", ".aac"}
        stem = audio_file.stem
        parent = audio_file.parent
        if any((parent / f"{stem}{ext}").exists() for ext in sibling_exts):
            can_delete = True

    if not can_delete:
        return

    size = audio_file.stat().st_size
    audio_file.unlink()
    log_event(
        logger,
        logging.INFO,
        "cleanup.working_audio.done",
        file=audio_file.name,
        size_mb=round(size / (1024 * 1024), 1),
    )




# ---------------------------------------------------------------------------
# Step update — writes to TaskStore + publishes events
# ---------------------------------------------------------------------------

def _flow_step_ids(task: Task) -> list[str]:
    flow = task.flow or {}
    return [str(step.get("id")) for step in flow.get("steps", []) if isinstance(step, dict) and step.get("id")]


async def _set_task_flow(
    task: Task,
    source_flow: SourceFlow,
    *,
    status: str = "processing",
    current_step: str | None = None,
) -> None:
    previous = task.flow or {}
    previous_done = previous.get("completed_steps") if isinstance(previous.get("completed_steps"), list) else []
    snapshot = source_flow.snapshot(status=status, current_step=current_step or previous.get("current_step"))
    snapshot["completed_steps"] = [step for step in previous_done if step in {s["id"] for s in snapshot["steps"]}]
    task.flow = snapshot
    task.platform = source_flow.platform
    task.content_subtype = source_flow.content_subtype
    get_task_store().update_status(
        task.id,
        task.status,
        flow=task.flow,
        platform=task.platform,
        content_subtype=task.content_subtype,
    )

    if previous.get("id") != source_flow.flow_id:
        await get_event_bus().publish(TaskEvent(task.id, "flow_selected", {
            "stage": "resolve",
            "step_id": snapshot.get("current_step"),
            "level": "info",
            "message": source_flow.label,
            "flow": snapshot,
            "platform": source_flow.platform,
            "content_subtype": source_flow.content_subtype,
        }))


async def _update_flow_step(
    task: Task,
    step_id: str,
    *,
    completed: bool = False,
    status: str | None = None,
    message: str | None = None,
    level: str = "info",
) -> None:
    if not task.flow:
        return

    flow = dict(task.flow)
    step_ids = _flow_step_ids(task)
    if step_id not in step_ids:
        return

    completed_steps = flow.get("completed_steps")
    if not isinstance(completed_steps, list):
        completed_steps = []
    if completed and step_id not in completed_steps:
        completed_steps = [*completed_steps, step_id]

    index = step_ids.index(step_id)
    total = len(step_ids)
    flow["current_step"] = step_id
    flow["current_step_index"] = index
    flow["current_step_label"] = next(
        (step.get("label") for step in flow.get("steps", []) if step.get("id") == step_id),
        step_id,
    )
    flow["completed_steps"] = completed_steps
    flow["total_steps"] = total
    flow["progress"] = (len(completed_steps) / total) if total else 0.0
    flow["status"] = status or flow.get("status") or "processing"
    task.flow = flow
    get_task_store().update_status(task.id, task.status, flow=task.flow)

    await get_event_bus().publish(TaskEvent(task.id, "flow_step", {
        "stage": step_id,
        "step_id": step_id,
        "completed": completed,
        "level": level,
        "message": message or flow["current_step_label"],
        "flow": flow,
    }))


async def _emit_timeline_event(
    task: Task,
    event_type: str,
    *,
    stage: str,
    step_id: str | None = None,
    level: str = "info",
    message: str,
    data: dict[str, Any] | None = None,
) -> None:
    payload = {
        "stage": stage,
        "step_id": step_id or stage,
        "level": level,
        "message": message,
    }
    if data:
        payload.update(data)
    await get_event_bus().publish(TaskEvent(task.id, event_type, payload))


async def _update_flow_from_metadata(
    task: Task,
    source_flow: SourceFlow,
    metadata: MediaMetadata,
    *,
    has_subtitle: bool = False,
    force_asr: bool = False,
    api_fallback: bool = False,
    current_step: str | None = None,
    preferred_asr_provider: str | None = None,
) -> SourceFlow:
    resolved = flow_from_metadata(
        source_flow,
        metadata,
        has_subtitle=has_subtitle,
        force_asr=force_asr,
        api_fallback=api_fallback,
        preferred_asr_provider=preferred_asr_provider,
    )
    await _set_task_flow(task, resolved, status="processing", current_step=current_step)
    return resolved


def _select_asr_provider_for_fallback(task: Task) -> tuple[str | None, str, bool]:
    """Choose the ASR provider when URL media enters API fallback."""
    from app.core.model_router import resolve_asr_binding

    rt = get_runtime_settings()
    explicit = str(task.options.get("asr_provider") or "").strip()
    if explicit:
        return explicit, "task_option", explicit == "siliconflow"

    try:
        runtime_binding = resolve_asr_binding(rt)
        if runtime_binding.provider == "siliconflow" and runtime_binding.configured:
            return "siliconflow", "runtime_api_provider", True
    except Exception as exc:
        log_event(logger, logging.DEBUG, "asr.runtime_provider.resolve_failed", error=exc)

    try:
        siliconflow_binding = resolve_asr_binding(rt, task_options={"asr_provider": "siliconflow"})
        if siliconflow_binding.configured:
            return "siliconflow", "siliconflow_configured", True
    except Exception as exc:
        log_event(logger, logging.DEBUG, "asr.siliconflow_provider.resolve_failed", error=exc)

    default_provider = str(getattr(rt, "asr_provider", "") or "").strip() or None
    return default_provider, "default_asr_provider", False


async def _update_step(
    task: Task,
    step: PipelineStep,
    completed: bool = False,
) -> None:
    """Update task step progress, persist to DB, and publish event."""
    task.current_step = step
    task.message = next(
        (s["name"] for s in PIPELINE_STEPS if s["id"] == step),
        str(step),
    )
    if completed and step not in task.completed_steps:
        task.completed_steps.append(step)

    total_steps = len(PIPELINE_STEPS)
    completed_count = len(task.completed_steps)
    task.progress = completed_count / total_steps
    task.updated_at = datetime.now()

    # Persist to SQLite
    store = get_task_store()
    store.update_status(
        task.id,
        task.status,
        progress=task.progress,
        message=task.message,
        current_step=task.current_step,
        completed_steps=task.completed_steps,
    )

    # Publish SSE event
    bus = get_event_bus()
    await bus.publish(TaskEvent(task.id, "step", {
        "step": step,
        "completed": completed,
        "progress": task.progress,
        "message": task.message,
    }))
    await _update_flow_step(task, str(step), completed=completed, message=task.message)


async def _run_voiceprint_step(
    task: Task,
    recognition_segments: list,
    task_dir: Path,
) -> list:
    """Extract speaker embeddings, match against library, rewrite segment speakers.

    Gracefully no-ops when:
      - voiceprint disabled in settings
      - no diarization was run (no speaker labels present)
      - ASR service didn't cache a diarize_df (e.g. platform subtitle path)
    """
    from app.core.settings import get_runtime_settings
    rt = get_runtime_settings()
    if not getattr(rt, "enable_voiceprint", True):
        return recognition_segments
    if not recognition_segments:
        return recognition_segments

    # Only run if diarization produced speaker labels
    has_speakers = any(s.get("speaker") for s in recognition_segments)
    if not has_speakers:
        log_event(logger, logging.INFO, "voiceprint.skipped", reason="no_speaker_labels")
        return recognition_segments

    from app.services.recognition import get_asr_service
    service = get_asr_service()
    pipeline_obj = service.get_pyannote_pipeline() if hasattr(service, "get_pyannote_pipeline") else None
    if pipeline_obj is None:
        log_event(logger, logging.INFO, "voiceprint.skipped", reason="pyannote_not_loaded")
        return recognition_segments

    diarize_df, audio_path = service.get_last_diarization() if hasattr(service, "get_last_diarization") else (None, None)
    if diarize_df is None or audio_path is None:
        log_event(logger, logging.INFO, "voiceprint.skipped", reason="no_cached_diarization")
        return recognition_segments

    from app.services.voiceprint import get_voiceprint_store
    from app.services.voiceprint.extractor import extract_voiceprints
    from app.services.voiceprint.matcher import resolve_speakers, apply_to_segments

    store = get_voiceprint_store()
    clips_dir = store.clips_dir

    voiceprints = extract_voiceprints(
        audio_path=audio_path,
        diarize_df=diarize_df,
        pyannote_pipeline=pipeline_obj,
        clips_dir=clips_dir,
        sample_id_prefix=f"{task.id}_",
    )
    if not voiceprints:
        log_event(logger, logging.INFO, "voiceprint.skipped", reason="no_voiceprints")
        return recognition_segments

    resolutions = resolve_speakers(
        task_id=str(task.id),
        voiceprints=voiceprints,
        store=store,
        match_threshold=float(getattr(rt, "voiceprint_match_threshold", 0.75)),
        suggest_threshold=float(getattr(rt, "voiceprint_suggest_threshold", 0.60)),
    )
    recognition_segments = apply_to_segments(recognition_segments, resolutions)
    log_event(logger, logging.INFO, "voiceprint.resolved", speakers=len(resolutions))
    return recognition_segments


# ---------------------------------------------------------------------------
# Fast-path subtitle runner (no GPU needed)
# ---------------------------------------------------------------------------

async def _run_subtitle_fast_path(
    task: Task,
    task_dir: Path,
    platform_subtitle: dict,
    metadata: "MediaMetadata",
) -> dict:
    """Run subtitle processing + LLM analysis — no GPU needed.

    This is the 'Branch A' of the fast path: processes platform subtitles
    through LLM for polish/analysis/summary/mindmap. Runs concurrently with
    video download (Branch B).

    Returns the text-related portion of the task result.
    """
    from app.services.recognition.subtitle_processor import process_subtitles
    from app.services.analysis import polish_text, summarize_text, generate_mindmap, generate_detail, analyze_content

    # -- SEPARATE: skip (no audio to separate) --
    await _raise_if_cancelled(task.id)
    await _update_step(task, PipelineStep.SEPARATE, completed=True)

    # -- TRANSCRIBE: process platform subtitle --
    await _update_step(task, PipelineStep.TRANSCRIBE)

    # Multi-track handling: save every track as transcript.{lang}.srt and
    # pick the one matching the video's spoken language for polish.
    tracks = platform_subtitle.get("tracks") or []
    if not tracks and platform_subtitle.get("subtitle_path"):
        # Legacy single-track shape — synthesize a 1-item list
        tracks = [{
            "path": platform_subtitle["subtitle_path"],
            "lang": platform_subtitle.get("subtitle_lang") or "unknown",
            "format": platform_subtitle.get("subtitle_format") or "srt",
            "type": "cc",
        }]
    tracks_manifest = _save_all_tracks_as_transcripts(tracks, task_dir)

    selected_track, detected_lang = await _select_polish_track(tracks)
    for entry in tracks_manifest:
        if entry["lang"] == (selected_track.get("lang") or "unknown"):
            entry["polished"] = True
    # Attach tracks + detected language to metadata for archive/UI
    metadata.extra["subtitle_tracks"] = tracks_manifest
    metadata.extra["detected_language"] = detected_lang
    metadata.extra["subtitle_engine"] = platform_subtitle.get("subtitle_engine")
    metadata.extra["subtitle_diagnostics"] = platform_subtitle.get("diagnostics") or []

    sub_result = await process_subtitles(
        subtitle_path=selected_track["path"],
        subtitle_format=selected_track.get("format") or "srt",
        metadata=metadata,
    )
    await _raise_if_cancelled(task.id)
    transcript = " ".join(s["text"] for s in sub_result.get("segments", []))
    srt = sub_result.get("srt", "")
    polished = sub_result.get("polished_srt", "")
    polished_md = sub_result.get("polished_md", "")
    recognition_segments = sub_result.get("segments", [])

    # Write transcript files
    if srt:
        await _write_text_artifact(task, task_dir, "transcript.srt", srt)
    if polished:
        await _write_text_artifact(task, task_dir, "transcript_polished.srt", polished)
        if polished_md:
            await _write_text_artifact(task, task_dir, "transcript_polished.md", polished_md)

    await _update_step(task, PipelineStep.TRANSCRIBE, completed=True)

    # -- VOICEPRINT: platform subtitles have no diarization, mark skipped --
    await _update_step(task, PipelineStep.VOICEPRINT, completed=True)

    # Guard: skip LLM if transcript is empty
    if not transcript or len(transcript.strip()) < 10:
        log_event(
            logger,
            logging.WARNING,
            "pipeline.llm.skipped",
            reason="fast_path_transcript_too_short",
            chars=len(transcript),
        )
        await _update_step(task, PipelineStep.POLISH, completed=True)
        await _update_step(task, PipelineStep.ANALYZE, completed=True)
        empty_analysis = {"language": "unknown", "content_type": "unknown", "main_topics": [],
                          "keywords": [], "proper_nouns": [], "speakers_detected": 0, "tone": "unknown"}
        empty_summary = {"tldr": "未检测到有效语音内容", "key_facts": [], "action_items": [], "topics": []}
        return {
            "transcript": transcript,
            "srt": srt,
            "polished": polished,
            "polished_md": polished_md,
            "recognition_segments": recognition_segments,
            "analysis": empty_analysis,
            "summary": empty_summary,
            "mindmap": "",
            "subtitle_source": "platform",
        }

    # -- POLISH: platform subtitle was polished by process_subtitles above. --
    await _update_step(task, PipelineStep.POLISH, completed=True)
    await _raise_if_cancelled(task.id)

    # -- ANALYZE: analyze after polish so summary/mindmap use the cleaned text.
    # Analyze still runs first within this step (cheap, ~8k-char prompt) so the detected
    # language can be injected into the summarize+mindmap prompts. Running
    # analyze serially before the other two adds ~1-2s but prevents the
    # summarize/mindmap steps from collapsing multilingual transcripts into
    # one language.
    await _update_step(task, PipelineStep.ANALYZE)
    video_metadata = {
        "uploader": metadata.uploader,
        "description": metadata.description,
        "tags": metadata.tags,
        "chapters": [{"title": ch.title, "start_time": ch.start_time}
                     for ch in metadata.chapters] if metadata.chapters else None,
    }
    mindmap_metadata = {
        "title": metadata.title,
        "uploader": metadata.uploader,
        "description": metadata.description,
        "chapters": [{"title": ch.title, "start_time": ch.start_time}
                     for ch in metadata.chapters] if metadata.chapters else None,
    }

    analysis_text = _plain_text_from_srt(polished) if polished else transcript
    mindmap_text = polished or srt or transcript
    rt = get_runtime_settings()

    analysis = await analyze_content(analysis_text, metadata.title, metadata=video_metadata)
    await _raise_if_cancelled(task.id)
    user_language = _user_language_hint(analysis)

    # Write analysis first so the frontend can surface language/topics early
    import json as _json
    if analysis:
        analysis_path = task_dir / "analysis.json"
        analysis_path.write_text(_json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
        await _emit_file_ready(task, "analysis.json", str(analysis_path))

    tasks = [
        summarize_text(analysis_text, user_language=user_language),
        generate_mindmap(mindmap_text, metadata=mindmap_metadata, user_language=user_language),
    ]
    if rt.generate_video_detail:
        tasks.append(generate_detail(mindmap_text, user_language=user_language))
    results = await asyncio.gather(*tasks)
    summary = results[0]
    mindmap = results[1]
    detail = results[2] if len(results) > 2 else ""
    await _raise_if_cancelled(task.id)

    if summary:
        await _write_summary_files(task, task_dir, metadata, summary)
    if mindmap:
        await _write_mindmap_files(task, task_dir, mindmap)
    if detail:
        await _write_detail_file(task, task_dir, detail)

    await _update_step(task, PipelineStep.ANALYZE, completed=True)

    return {
        "transcript": transcript,
        "srt": srt,
        "polished": polished,
        "polished_md": polished_md,
        "recognition_segments": recognition_segments,
        "analysis": analysis,
        "summary": summary,
        "mindmap": mindmap,
        "subtitle_source": "platform",
    }


# ---------------------------------------------------------------------------
# Note pipeline (XHS/Zhihu notes: optional VLM → summary/mindmap → archive)
# ---------------------------------------------------------------------------

def _note_text_excerpt(text: str, limit: int = 600) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "..."


def _fallback_note_analysis(text: str) -> dict[str, Any]:
    return {
        "language": "zh-CN" if re.search(r"[\u4e00-\u9fff]", text) else "unknown",
        "content_type": "note",
        "main_topics": [],
        "keywords": [],
        "proper_nouns": [],
        "speakers_detected": 1,
        "tone": "unknown",
    }


def _fallback_note_summary(text: str) -> dict[str, Any]:
    return {
        "tldr": _note_text_excerpt(text),
        "key_facts": [],
        "action_items": [],
        "topics": [],
    }


def _fallback_note_mindmap(metadata: "MediaMetadata", image_count: int, text: str) -> str:
    title = metadata.title or "图片笔记"
    return "\n".join([
        f"# {title}",
        "- 原始笔记正文已归档",
        f"- 图片数量: {image_count}",
        f"- 正文长度: {len(text)} 字符",
    ])


def _safe_pipeline_error(error: Exception) -> str:
    message = str(error) or error.__class__.__name__
    return re.sub(r"sk-[A-Za-z0-9_-]{8,}", lambda m: f"{m.group(0)[:6]}...{m.group(0)[-4:]}", message)


def _append_task_warning(task: Task, code: str, message: str, **details: Any) -> None:
    result = dict(task.result or {})
    warnings = list(result.get("warnings") or [])
    warning: dict[str, Any] = {"code": code, "message": message}
    if details:
        warning["details"] = {
            key: str(value) if isinstance(value, (Path, Exception)) else value
            for key, value in details.items()
            if value is not None
        }
    warnings.append(warning)
    result["warnings"] = warnings
    task.result = result
    get_task_store().update_status(task.id, task.status, result=task.result)


async def _write_note_fallback_outputs(
    task: Task,
    task_dir: Path,
    metadata: "MediaMetadata",
    image_count: int,
    combined_text: str,
    *,
    reason: str,
    analysis: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    if analysis is None:
        analysis = _fallback_note_analysis(combined_text)
        analysis["_fallback"] = {"reason": reason}
        await _write_text_artifact(
            task,
            task_dir,
            "analysis.json",
            json.dumps(analysis, indent=2, ensure_ascii=False),
        )

    summary = _fallback_note_summary(combined_text)
    mindmap = _fallback_note_mindmap(metadata, image_count, combined_text)
    await _write_summary_files(task, task_dir, metadata, summary)
    await _write_mindmap_files(task, task_dir, mindmap)
    return analysis, summary, mindmap


def _image_note_index(position: int, path: Path) -> int:
    try:
        return int(path.stem)
    except ValueError:
        return position


def _note_image_download_diagnostics(ingest_info: dict) -> dict[str, Any] | None:
    if not isinstance(ingest_info, dict):
        return None
    extra = ingest_info.get("extra")
    if not isinstance(extra, dict):
        return None
    diagnostics = extra.get("image_download_diagnostics")
    return diagnostics if isinstance(diagnostics, dict) else None


def _note_expected_image_count(ingest_info: dict) -> int:
    if not isinstance(ingest_info, dict):
        return 0
    extra = ingest_info.get("extra")
    if not isinstance(extra, dict):
        return 0
    candidates = extra.get("image_url_candidates")
    if isinstance(candidates, list) and candidates:
        return len(candidates)
    urls = extra.get("image_urls")
    if isinstance(urls, list):
        return len(urls)
    return 0


def _note_should_fail_on_missing_images(ingest_info: dict) -> bool:
    diagnostics = _note_image_download_diagnostics(ingest_info)
    if isinstance(diagnostics, dict) and "fail_on_missing_images" in diagnostics:
        return bool(diagnostics.get("fail_on_missing_images"))
    return True


def _note_image_download_failure_message(
    expected: int,
    downloaded: int,
    diagnostics: dict[str, Any] | None,
    fallback: dict[str, Any] | None = None,
) -> str:
    summary = diagnostics or fallback or {"expected": expected, "downloaded": downloaded}
    return (
        f"图文图片下载不完整：{downloaded}/{expected}，已停止后续处理。"
        f"诊断: {json.dumps(summary, ensure_ascii=False)[:800]}"
    )


def _downloaded_note_image_paths(ingest_info: dict) -> list[Path]:
    if not isinstance(ingest_info, dict):
        return []
    extra = ingest_info.get("extra")
    if not isinstance(extra, dict):
        return []
    raw_paths = extra.get("downloaded_image_paths")
    if not isinstance(raw_paths, list):
        return []
    paths: list[Path] = []
    for value in raw_paths:
        try:
            path = Path(str(value))
        except Exception:
            continue
        if path.exists() and path.is_file():
            paths.append(path)
    return paths


def _existing_note_image_paths(task_dir: Path) -> list[Path]:
    images_dir = task_dir / "images"
    if not images_dir.exists():
        return []
    image_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
    return sorted(
        (
            path
            for path in images_dir.iterdir()
            if path.is_file() and path.suffix.lower() in image_exts
        ),
        key=lambda path: (_image_note_index(0, path), path.name),
    )


def _restored_note_image_descriptions(task: Task, task_dir: Path) -> dict[int, dict[str, Any]]:
    restored: dict[int, dict[str, Any]] = {}
    result = task.result if isinstance(task.result, dict) else {}
    raw_descriptions = result.get("image_descriptions")
    if isinstance(raw_descriptions, list):
        for item in raw_descriptions:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            text = str(item.get("text") or "").strip()
            status = str(item.get("status") or ("completed" if text else "")).lower()
            if status == "completed" and text:
                restored[idx] = dict(item)

    desc_dir = task_dir / "descriptions"
    if desc_dir.exists():
        for path in sorted(desc_dir.glob("*.md")):
            try:
                idx = int(path.stem)
            except ValueError:
                continue
            if idx in restored:
                continue
            try:
                text = path.read_text(encoding="utf-8").strip()
            except Exception:
                continue
            if (
                not text
                or text.startswith("VLM caption 失败")
                or text.startswith("VLM caption 跳过")
            ):
                continue
            restored[idx] = {
                "index": idx,
                "image_path": "",
                "kind": "content",
                "text": text,
                "status": "completed",
            }
    return restored


def _note_image_downloader(metadata: MediaMetadata):
    if metadata.platform == "zhihu":
        from app.services.ingestion.platform.zhihu.api import download_images
    elif metadata.platform in {"bilibili", "bilibili_opus"}:
        from app.services.ingestion.platform.bilibili.note import download_images
    elif metadata.platform == "twitter":
        from app.services.ingestion.platform.twitter.api import download_images
    else:
        from app.services.ingestion.platform.xiaohongshu.api import download_images
    return download_images


def _downloader_accepts_cancel(downloader: Any) -> bool:
    try:
        params = inspect.signature(downloader).parameters
    except (TypeError, ValueError):
        return False
    return "should_cancel" in params or any(
        param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()
    )


def _run_note_image_downloader(
    downloader: Any,
    ingest_info: dict,
    task_dir: Path,
    task_id: UUID,
) -> list[Path]:
    should_cancel = lambda: _task_download_cancelled(task_id)
    if should_cancel():
        raise RuntimeError("note image download cancelled")
    if _downloader_accepts_cancel(downloader):
        return downloader(ingest_info, task_dir, should_cancel=should_cancel)
    return downloader(ingest_info, task_dir)


async def _download_note_images_for_download_step(
    task: Task,
    metadata: MediaMetadata,
    task_dir: Path,
    ingest_info: dict,
) -> list[Path]:
    """Download note images during the pipeline DOWNLOAD step."""
    if metadata.content_subtype != "image_note":
        return []
    if not isinstance(ingest_info, dict):
        ingest_info = {}
    extra = ingest_info.get("extra")
    if not isinstance(extra, dict):
        extra = {}
        ingest_info["extra"] = extra

    expected = _note_expected_image_count(ingest_info)
    if expected <= 0:
        raise RuntimeError("图文笔记没有可下载的图片候选 URL")

    await _emit_timeline_event(
        task,
        "note.images.download_started",
        stage="download",
        step_id="download",
        level="info",
        message="开始下载图文图片",
        data={"platform": metadata.platform, "expected": expected},
    )

    try:
        downloader = _note_image_downloader(metadata)
        image_paths = await asyncio.get_event_loop().run_in_executor(
            None,
            _run_note_image_downloader,
            downloader,
            ingest_info,
            task_dir,
            task.id,
        )
    except Exception as exc:
        if _task_download_cancelled(task.id):
            raise asyncio.CancelledError() from exc
        error_message = _safe_pipeline_error(exc)
        extra["image_download_error"] = {
            "error_type": type(exc).__name__,
            "error": error_message,
        }
        metadata.extra["image_download_error"] = extra["image_download_error"]
        image_paths = []
        _append_task_warning(
            task,
            "note_images_download_failed",
            "图片下载失败，已停止后续图像理解。",
            platform=metadata.platform,
            expected=expected,
            error=error_message,
        )

    diagnostics = _note_image_download_diagnostics(ingest_info)
    result = {
        "expected": expected,
        "downloaded": len(image_paths),
        "failed": max(expected - len(image_paths), 0),
    }
    extra["downloaded_image_paths"] = [str(path) for path in image_paths]
    extra["image_download_result"] = result
    metadata.extra["downloaded_image_paths"] = extra["downloaded_image_paths"]
    metadata.extra["image_download_result"] = result
    if diagnostics:
        metadata.extra["image_download_diagnostics"] = diagnostics

    task.result = dict(task.result or {})
    task.result["output_dir"] = str(task_dir)
    task.result["_image_ingest_info"] = ingest_info
    task.result["image_download_result"] = result
    if diagnostics:
        task.result["image_download_diagnostics"] = diagnostics
    get_task_store().update_status(task.id, task.status, result=task.result)
    write_metadata_json(task_dir, metadata, status="processing", task_id=str(task.id))

    await _emit_timeline_event(
        task,
        "note.images.download_completed" if image_paths else "note.images.download_empty",
        stage="download",
        step_id="download",
        level="info" if image_paths else "error",
        message=f"图片下载完成：{len(image_paths)}/{expected}",
        data={"platform": metadata.platform, **result, "diagnostics": diagnostics or {}},
    )

    if result["failed"] > 0 and _note_should_fail_on_missing_images(ingest_info):
        raise RuntimeError(
            _note_image_download_failure_message(
                expected,
                len(image_paths),
                diagnostics,
                extra.get("image_download_error") or result,
            )
        )

    return image_paths


async def _process_image_note(
    task: Task,
    metadata: "MediaMetadata",
    task_dir: Path,
    ingest_info: dict,
) -> None:
    """Process a note-style source: download images when present, summarize, archive."""
    import asyncio as _aio
    from app.services.analysis import summarize_text, generate_mindmap, generate_detail, analyze_content
    from app.services.archiving import archive_result

    # Mark all audio steps as skipped immediately
    for step in (PipelineStep.SEPARATE, PipelineStep.TRANSCRIBE, PipelineStep.VOICEPRINT, PipelineStep.POLISH):
        await _update_step(task, step, completed=True)
    await _raise_if_cancelled(task.id)

    await _update_step(task, PipelineStep.ANALYZE)

    source_text = ""
    extra = ingest_info.get("extra") if isinstance(ingest_info, dict) else None
    source_path_value = extra.get("source_markdown_path") if isinstance(extra, dict) else None
    if source_path_value:
        try:
            source_path = Path(str(source_path_value))
            if source_path.exists():
                source_text = source_path.read_text(encoding="utf-8")
        except Exception as e:
            log_event(logger, logging.WARNING, "note.source.read_failed", path=source_path_value, error=e)
    if not source_text and metadata.description:
        source_text = metadata.description
    if source_text:
        await _write_text_artifact(task, task_dir, "source.md", source_text)

    # Download images when the note actually has image media.
    image_warning_recorded = False
    if metadata.content_subtype == "text_note":
        image_paths = []
    else:
        image_paths = _downloaded_note_image_paths(ingest_info)
        if not image_paths:
            image_paths = _existing_note_image_paths(task_dir)
        if not image_paths:
            download_images = _note_image_downloader(metadata)
            try:
                image_paths = await _aio.get_event_loop().run_in_executor(
                    None, _run_note_image_downloader, download_images, ingest_info, task_dir, task.id
                )
            except Exception as e:
                if _task_download_cancelled(task.id):
                    raise _aio.CancelledError() from e
                error_message = _safe_pipeline_error(e)
                log_event(logger, logging.WARNING, "note.images.download_failed", error=error_message)
                _append_task_warning(
                    task,
                    "note_images_download_failed",
                    "图片下载失败，已继续处理正文。",
                    error=error_message,
                )
                image_warning_recorded = True
                image_paths = []
    image_download_diagnostics = _note_image_download_diagnostics(ingest_info)
    if image_download_diagnostics:
        metadata.extra["image_download_diagnostics"] = image_download_diagnostics
        task.result = dict(task.result or {})
        task.result["image_download_diagnostics"] = image_download_diagnostics
        get_task_store().update_status(task.id, task.status, result=task.result)
        write_metadata_json(task_dir, metadata, status="processing")
    if metadata.content_subtype == "image_note":
        if not isinstance(extra, dict):
            extra = {}
            if isinstance(ingest_info, dict):
                ingest_info["extra"] = extra
        expected = _note_expected_image_count(ingest_info)
        if expected > 0:
            result = {
                "expected": expected,
                "downloaded": len(image_paths),
                "failed": max(expected - len(image_paths), 0),
            }
            extra["downloaded_image_paths"] = [str(path) for path in image_paths]
            extra["image_download_result"] = result
            metadata.extra["downloaded_image_paths"] = extra["downloaded_image_paths"]
            metadata.extra["image_download_result"] = result
            task.result = dict(task.result or {})
            task.result["image_download_result"] = result
            if image_download_diagnostics:
                task.result["image_download_diagnostics"] = image_download_diagnostics
            get_task_store().update_status(task.id, task.status, result=task.result)
            if result["failed"] > 0 and _note_should_fail_on_missing_images(ingest_info):
                raise RuntimeError(
                    _note_image_download_failure_message(
                        expected,
                        len(image_paths),
                        image_download_diagnostics,
                        extra.get("image_download_error") or result,
                    )
                )
        elif not image_paths and not image_warning_recorded:
            raise RuntimeError("图文笔记没有可下载的图片候选 URL")

    task.result = dict(task.result or {})
    task.result["output_dir"] = str(task_dir)
    task.result["_image_ingest_info"] = ingest_info
    get_task_store().update_status(task.id, task.status, result=task.result)

    if source_text and image_paths:
        localized_source_text = _localize_note_markdown_image_refs(source_text, metadata, image_paths)
        if localized_source_text != source_text:
            source_text = localized_source_text
            metadata.description = source_text
            await _write_text_artifact(task, task_dir, "source.md", source_text)

    await _raise_if_cancelled(task.id)

    # Run VLM on each image (limited by vlm_concurrency)
    rt = get_runtime_settings()
    descriptions: list[dict] = []
    restored_descriptions = _restored_note_image_descriptions(task, task_dir)
    from app.core.model_router import resolve_deepseek_llm_binding, resolve_llm_binding, resolve_vlm_binding

    vlm_binding = resolve_vlm_binding(rt)
    if image_paths and vlm_binding.configured:
        from app.services.analysis.vlm import get_vlm_service
        vlm = get_vlm_service()
        try:
            vlm_concurrency = max(1, int(vlm_binding.request_kwargs.get("concurrency") or rt.vlm_concurrency))
        except (TypeError, ValueError):
            vlm_concurrency = 1
        sem = _aio.Semaphore(vlm_concurrency)

        async def _describe(position: int, path: Path) -> dict:
            idx = _image_note_index(position, path)
            restored = restored_descriptions.get(idx)
            if restored and str(restored.get("text") or "").strip():
                await _emit_timeline_event(
                    task,
                    "vlm.image.reused",
                    stage="analyze",
                    step_id="analyze",
                    level="info",
                    message=f"图片 {idx + 1} caption 复用",
                    data={"index": idx, "file": path.name},
                )
                return {
                    **restored,
                    "index": idx,
                    "image_path": str(path),
                    "status": "completed",
                    "reused": True,
                }
            queued_at = time.monotonic()
            async with sem:
                queue_wait_ms = int((time.monotonic() - queued_at) * 1000)
                await _emit_timeline_event(
                    task,
                    "vlm.image.started",
                    stage="analyze",
                    step_id="analyze",
                    level="info",
                    message=f"图片 {idx + 1} caption 开始",
                    data={
                        "index": idx,
                        "file": path.name,
                        "queue_wait_ms": queue_wait_ms,
                        "concurrency": vlm_concurrency,
                        "timeout_sec": vlm_binding.request_kwargs.get("timeout_sec"),
                    },
                )
                try:
                    result = await _aio.get_event_loop().run_in_executor(
                        None,
                        vlm.describe_image,
                        path,
                        vlm_binding,
                    )
                    if not str(result.get("text") or "").strip():
                        error_message = "VLM returned empty caption text"
                        payload = {
                            "index": idx,
                            "file": path.name,
                            "error": error_message,
                            "payload_meta": result.get("payload_meta"),
                            "duration_ms": result.get("duration_ms"),
                            "queue_wait_ms": queue_wait_ms,
                        }
                        log_event(logger, logging.WARNING, "vlm.image.failed", index=idx, file=path.name, error=error_message)
                        await _emit_timeline_event(
                            task,
                            "vlm.image.failed",
                            stage="analyze",
                            step_id="analyze",
                            level="warning",
                            message=f"图片 {idx + 1} caption 失败",
                            data=payload,
                        )
                        return {
                            "index": idx,
                            "image_path": str(path),
                            "kind": result.get("kind", "content"),
                            "text": "",
                            "status": "failed",
                            "error": error_message,
                            "payload_meta": result.get("payload_meta"),
                            "duration_ms": result.get("duration_ms"),
                            "queue_wait_ms": queue_wait_ms,
                        }
                    payload = {
                        "index": idx,
                        "file": path.name,
                        "chars": len(result.get("text") or ""),
                        "queue_wait_ms": queue_wait_ms,
                    }
                    if result.get("payload_meta"):
                        payload["payload_meta"] = result["payload_meta"]
                    if result.get("duration_ms") is not None:
                        payload["duration_ms"] = result["duration_ms"]
                    await _emit_timeline_event(
                        task,
                        "vlm.image.completed",
                        stage="analyze",
                        step_id="analyze",
                        level="info",
                        message=f"图片 {idx + 1} caption 完成",
                        data=payload,
                    )
                    return {"index": idx, "image_path": str(path), "status": "completed", "queue_wait_ms": queue_wait_ms, **result}
                except Exception as e:
                    error_message = _safe_pipeline_error(e)
                    log_event(logger, logging.WARNING, "vlm.image.failed", index=idx, file=path.name, error=error_message)
                    await _emit_timeline_event(
                        task,
                        "vlm.image.failed",
                        stage="analyze",
                        step_id="analyze",
                        level="warning",
                        message=f"图片 {idx + 1} caption 失败",
                        data={"index": idx, "file": path.name, "error": error_message, "queue_wait_ms": queue_wait_ms},
                    )
                    return {
                        "index": idx,
                        "image_path": str(path),
                        "kind": "content",
                        "text": "",
                        "status": "failed",
                        "error": error_message,
                        "queue_wait_ms": queue_wait_ms,
                    }

        descriptions = list(await _aio.gather(*[_describe(i, p) for i, p in enumerate(image_paths)]))
    else:
        for i, p in enumerate(image_paths):
            idx = _image_note_index(i, p)
            restored = restored_descriptions.get(idx)
            if restored and str(restored.get("text") or "").strip():
                descriptions.append({
                    **restored,
                    "index": idx,
                    "image_path": str(p),
                    "status": "completed",
                    "reused": True,
                })
            else:
                descriptions.append({
                    "index": idx,
                    "image_path": str(p),
                    "kind": "content",
                    "text": "",
                    "status": "skipped",
                    "error": vlm_binding.reason or "VLM not configured",
                })
        if image_paths:
            log_event(
                logger,
                logging.WARNING,
                "vlm.skipped",
                reason=vlm_binding.reason or "not_configured",
                images=len(image_paths),
            )

    await _raise_if_cancelled(task.id)

    # Write per-image description files
    desc_dir = task_dir / "descriptions"
    desc_dir.mkdir(parents=True, exist_ok=True)
    for d in descriptions:
        desc_path = desc_dir / f"{d['index']:02d}.md"
        if d.get("text"):
            desc_path.write_text(d["text"], encoding="utf-8")
        elif d.get("status") == "failed":
            desc_path.write_text(f"VLM caption 失败：{d.get('error') or 'unknown error'}\n", encoding="utf-8")
        elif d.get("status") == "skipped":
            desc_path.write_text(f"VLM caption 跳过：{d.get('error') or 'not configured'}\n", encoding="utf-8")

    # Combine all descriptions into a pseudo-transcript
    combined_parts = []
    for d in descriptions:
        if d.get("text"):
            label = f"图片 {d['index'] + 1}"
            combined_parts.append(f"### {label}\n{d['text']}")
    if source_text:
        body_label = "网页正文" if metadata.platform == "webpage" else "笔记正文"
        combined_parts.insert(0, f"### {body_label}\n{source_text}")
    combined_text = "\n\n".join(combined_parts)
    if combined_text:
        combined_path = desc_dir / "combined.md"
        combined_path.write_text(combined_text, encoding="utf-8")

    # Write descriptions/ into task result early
    task.result = task.result or {}
    task.result["image_descriptions"] = descriptions
    task.result["output_dir"] = str(task_dir)
    if image_download_diagnostics:
        task.result["image_download_diagnostics"] = image_download_diagnostics
    failed_vlm = [d for d in descriptions if d.get("status") == "failed"]
    if failed_vlm:
        _append_task_warning(
            task,
            "note_vlm_partial_failed",
            "图片 caption 失败，已停止后续总结。",
            failed=len(failed_vlm),
            total=len(descriptions),
        )
    get_task_store().update_status(task.id, task.status, result=task.result)
    if failed_vlm:
        detail = [
            {
                "index": d.get("index"),
                "file": Path(str(d.get("image_path") or "")).name,
                "error": d.get("error"),
                "queue_wait_ms": d.get("queue_wait_ms"),
            }
            for d in failed_vlm[:5]
        ]
        raise RuntimeError(
            f"VLM caption 失败：{len(failed_vlm)}/{len(descriptions)}，已停止后续总结。"
            f"诊断: {json.dumps(detail, ensure_ascii=False)[:800]}"
        )

    # Analyze + summarize + mindmap using combined text
    video_metadata = {
        "uploader": metadata.uploader,
        "description": source_text or metadata.description,
        "tags": metadata.tags,
        "chapters": None,
    }
    mindmap_metadata = {
        "title": metadata.title,
        "uploader": metadata.uploader,
        "description": source_text or metadata.description,
        "chapters": None,
    }

    analysis = None
    summary: dict = {}
    mindmap = ""
    detail = ""

    if combined_text and len(combined_text.strip()) >= 10:
        import json as _json

        deepseek_summary_binding = resolve_deepseek_llm_binding(rt, stage="summary")
        llm_provider_override = "deepseek" if deepseek_summary_binding.configured else ""
        llm_binding = deepseek_summary_binding if llm_provider_override else resolve_llm_binding(rt, stage="summary")
        if not llm_binding.configured:
            log_event(
                logger,
                logging.WARNING,
                "image_note.llm.skipped",
                provider=llm_binding.provider,
                reason=llm_binding.reason,
            )
            analysis, summary, mindmap = await _write_note_fallback_outputs(
                task,
                task_dir,
                metadata,
                len(image_paths),
                combined_text,
                reason=llm_binding.reason or "not_configured",
            )
        else:
            try:
                analysis = await analyze_content(
                    combined_text,
                    metadata.title,
                    metadata=video_metadata,
                    provider_override=llm_provider_override,
                )
                await _raise_if_cancelled(task.id)
                user_language = _user_language_hint(analysis)

                if analysis:
                    await _write_text_artifact(
                        task,
                        task_dir,
                        "analysis.json",
                        _json.dumps(analysis, indent=2, ensure_ascii=False),
                    )

                tasks = [
                    summarize_text(
                        combined_text,
                        user_language=user_language,
                        provider_override=llm_provider_override,
                    ),
                    generate_mindmap(
                        combined_text,
                        metadata=mindmap_metadata,
                        user_language=user_language,
                        provider_override=llm_provider_override,
                    ),
                ]
                if rt.generate_video_detail:
                    tasks.append(
                        generate_detail(
                            combined_text,
                            user_language=user_language,
                            provider_override=llm_provider_override,
                        )
                    )
                results = await _aio.gather(*tasks)
                summary = results[0]
                mindmap = results[1]
                detail = results[2] if len(results) > 2 else ""
                await _raise_if_cancelled(task.id)

                if summary:
                    await _write_summary_files(task, task_dir, metadata, summary)
                if mindmap:
                    await _write_mindmap_files(task, task_dir, mindmap)
                if detail:
                    await _write_detail_file(task, task_dir, detail)
            except Exception as e:
                error_message = _safe_pipeline_error(e)
                log_event(
                    logger,
                    logging.WARNING,
                    "image_note.llm.failed_fallback",
                    provider=llm_binding.provider,
                    model=llm_binding.model,
                    error=error_message,
                )
                _append_task_warning(
                    task,
                    "note_llm_failed",
                    "模型分析失败，已使用正文生成基础结果。",
                    provider=llm_binding.provider,
                    model=llm_binding.model,
                    error=error_message,
                )
                analysis, summary, mindmap = await _write_note_fallback_outputs(
                    task,
                    task_dir,
                    metadata,
                    len(image_paths),
                    combined_text,
                    reason="llm_failed",
                    analysis=analysis,
                )
    else:
        log_event(logger, logging.WARNING, "image_note.llm.skipped", reason="combined_text_too_short")

    await _update_step(task, PipelineStep.ANALYZE, completed=True)

    await _update_step(task, PipelineStep.ARCHIVE)
    archive = await archive_result(
        metadata,
        polished_srt=None,
        summary=summary,
        mindmap=mindmap,
        work_dir=task_dir,
        analysis=analysis,
    )
    write_metadata_json(task_dir, metadata, status="completed")
    await _update_step(task, PipelineStep.ARCHIVE, completed=True)

    existing_result = dict(task.result or {})
    warnings = list(existing_result.get("warnings") or [])
    image_download_diagnostics = existing_result.get("image_download_diagnostics")
    task.result = {
        "metadata": metadata.model_dump(mode="json"),
        "image_descriptions": descriptions,
        "archive": archive,
        "output_dir": str(task_dir),
        "analysis": analysis,
        "content_subtype": metadata.content_subtype,
    }
    if image_download_diagnostics:
        task.result["image_download_diagnostics"] = image_download_diagnostics
    if warnings:
        task.result["warnings"] = warnings

    # Async KB indexing (fail-soft)
    _schedule_kb_index(str(task.id), str(task_dir))


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

async def run_pipeline(task: Task, _download_worker_call: bool = False) -> None:
    """Run full pipeline: download → separate → transcribe → voiceprint → polish → analyze → archive.

    Supports:
    - Checkpoint resume: skips steps already in task.completed_steps and
      reconstructs needed variables from files already written to disk.
    - Two-stage execution: when _download_worker_call=True, runs only the
      DOWNLOAD step then calls advance_to_gpu() and returns. The GPU worker
      calls this again with _download_worker_call=False to run the rest.
    - GPU semaphore: UVR + ASR are protected by gpu_semaphore so concurrent
      workers never fight over VRAM.
    """
    import yt_dlp

    from app.services.ingestion import download_media
    from app.services.ingestion.ytdlp import (
        YoutubeNetworkError,
        download_subtitles,
        fetch_metadata as fetch_ytdlp_metadata,
        ytdlp_auth_opts,
        ytdlp_base_opts,
    )
    from app.services.ingestion.local import find_local_subtitle, parse_nfo
    from app.services.preprocessing import separate_vocals
    from app.services.recognition import transcribe_audio
    from app.services.recognition.subtitle_processor import process_subtitles
    from app.services.analysis import polish_text, summarize_text, generate_mindmap, generate_detail, analyze_content
    from app.services.archiving import archive_result
    from app.core.queue import get_task_queue

    rt = get_runtime_settings()
    source = _clean_source_path(task.source)
    if source != task.source:
        task.source = source
        get_task_store().save(task)
    platform_subtitle = None
    source_type = _detect_source_type(source)
    force_asr = bool(rt.force_asr or task.options.get("force_asr", False))
    initial_flow = resolve_source_flow(
        source,
        prefer_platform_subtitles=True,
        force_asr=force_asr,
        task_options=task.options,
    )
    use_platform_subtitles = (
        initial_flow.try_subtitles
        and _platform_prefer_subtitles(initial_flow.route_type)
        and not force_asr
    )
    source_flow = resolve_source_flow(
        source,
        prefer_platform_subtitles=use_platform_subtitles,
        force_asr=force_asr,
        task_options=task.options,
    )
    route_type = source_flow.route_type

    # Resolve pre-created task dir
    task_dir = None
    if task.result and task.result.get("output_dir"):
        candidate = Path(task.result["output_dir"])
        if candidate.exists():
            task_dir = candidate

    done = set(task.completed_steps or [])
    log_event(
        logger,
        logging.INFO,
        "pipeline.started",
        source_type=source_type,
        platform=source_flow.platform,
        flow_id=source_flow.flow_id,
        completed_steps=",".join(sorted(str(s) for s in done)) or "none",
        download_worker_call=_download_worker_call,
    )
    await _set_task_flow(task, source_flow, status="processing", current_step=(task.flow or {}).get("current_step") or "resolve")
    if "resolve" not in ((task.flow or {}).get("completed_steps") or []):
        await _update_flow_step(task, "resolve", completed=True, message="来源已识别")
    await _raise_if_cancelled(task.id)

    # Variables that later steps depend on — populated either by running the
    # step or by reading back files written in a previous run.
    audio_path: str | None = None
    vocals_path: str | None = None
    metadata: "MediaMetadata | None" = None
    has_subtitle: bool = False
    uvr_fallback_reason: str | None = None
    srt: str = ""
    transcript: str = ""
    polished: str | None = None
    polished_md: str | None = None
    subtitle_source: str = "asr"
    recognition_segments: list = []
    analysis: dict = {}
    summary: dict = {}
    mindmap: str = ""
    detail: str = ""

    # ── Checkpoint restore helpers ─────────────────────────────────────────
    def _restore_metadata() -> bool:
        """Read metadata.json back from disk into `metadata`. Returns True on success."""
        nonlocal metadata
        if task_dir is None:
            return False
        meta_path = task_dir / "metadata.json"
        if not meta_path.exists():
            return False
        try:
            import json as _json
            raw = _json.loads(meta_path.read_text(encoding="utf-8"))
            raw.pop("status", None)
            if "duration" in raw and "duration_seconds" not in raw:
                raw["duration_seconds"] = raw.pop("duration")
            if raw.get("media_type") == "unknown":
                raw["media_type"] = "other"
            raw.setdefault("title", task_dir.name)
            metadata = MediaMetadata.model_validate(raw)
            return True
        except Exception as e:
            log_event(logger, logging.WARNING, "pipeline.restore.metadata_failed", error=e)
            return False

    def _restore_audio_paths() -> bool:
        """Find audio/vocals files on disk. Returns True if usable paths found."""
        nonlocal audio_path, vocals_path
        if task_dir is None:
            return False
        # Vocals (post-UVR)
        for candidate in task_dir.glob("vocals*.wav"):
            vocals_path = str(candidate)
            audio_path = vocals_path
            return True
        # Raw extracted audio
        for candidate in task_dir.glob("*.wav"):
            audio_path = str(candidate)
            vocals_path = audio_path
            return True
        # Original audio (mp3/m4a/etc.)
        for f in task_dir.iterdir():
            if f.is_file() and f.suffix.lower() in {".mp3", ".flac", ".m4a", ".ogg"}:
                audio_path = str(f)
                vocals_path = audio_path
                return True
        return False

    def _restore_transcript() -> bool:
        """Read transcript SRT back from disk. Returns True if found."""
        nonlocal srt, transcript, polished, polished_md, subtitle_source
        if task_dir is None:
            return False
        polished_path = task_dir / "transcript_polished.srt"
        raw_path = task_dir / "transcript.srt"
        if polished_path.exists():
            polished = polished_path.read_text(encoding="utf-8")
            subtitle_source = "asr"
        if raw_path.exists():
            srt = raw_path.read_text(encoding="utf-8")
            transcript = _plain_text_from_srt(srt)
            return True
        return bool(polished)

    def _restore_analysis() -> bool:
        nonlocal analysis
        if task_dir is None:
            return False
        path = task_dir / "analysis.json"
        if path.exists():
            try:
                import json as _j
                analysis = _j.loads(path.read_text(encoding="utf-8"))
                return True
            except Exception:
                pass
        return False

    def _restore_summary() -> bool:
        nonlocal summary
        if task_dir is None:
            return False
        path = task_dir / "summary.json"
        if path.exists():
            try:
                import json as _j
                loaded = _j.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    summary = loaded
                    return True
            except Exception:
                pass
        # Backward compatibility for old archives that only have rendered markdown.
        return (task_dir / "summary.md").exists()

    def _restore_mindmap() -> bool:
        nonlocal mindmap
        if task_dir is None:
            return False
        path = task_dir / "mindmap.md"
        if path.exists():
            mindmap = path.read_text(encoding="utf-8")
            return True
        return False

    # ── Step 1: DOWNLOAD ───────────────────────────────────────────────────
    if PipelineStep.DOWNLOAD in done:
        log_event(logger, logging.INFO, "pipeline.step.skipped", step=PipelineStep.DOWNLOAD, reason="already_done")
        _restore_metadata()
        _restore_audio_paths()
        # Restore has_subtitle + platform_subtitle from disk
        if task_dir:
            sub_dir = task_dir / "subtitles"
            if sub_dir.exists():
                for ext in ("*.srt", "*.ass", "*.vtt"):
                    srt_files = list(sub_dir.glob(ext))
                    if srt_files:
                        sub_file = srt_files[0]
                        platform_subtitle = {
                            "subtitle_path": str(sub_file),
                            "subtitle_lang": "zh",
                            "subtitle_format": sub_file.suffix.lstrip("."),
                        }
                        has_subtitle = True
                        break

        # Fast-path resume: LLM steps done, just need video + archive
        fast_path_steps = {PipelineStep.TRANSCRIBE, PipelineStep.ANALYZE, PipelineStep.POLISH}
        if fast_path_steps.issubset(done) and PipelineStep.ARCHIVE not in done:
            log_event(logger, logging.INFO, "pipeline.fast_path.redownload")
            ingest = await download_media(source, output_dir=task_dir)
            await _raise_if_cancelled(task.id)
            audio_path = ingest.get("file_path")
            if not metadata:
                metadata = MediaMetadata(**ingest.get("metadata", {"title": source}))
            if ingest.get("video_path"):
                metadata.file_path = ingest["video_path"]

            # Restore text outputs from disk
            _restore_transcript()
            _restore_analysis()
            _restore_summary()
            _restore_mindmap()

            if PipelineStep.VOICEPRINT not in done:
                await _update_step(task, PipelineStep.VOICEPRINT, completed=True)

            # Archive
            from app.services.archiving import archive_result
            await _raise_if_cancelled(task.id)
            await _update_step(task, PipelineStep.ARCHIVE)
            archive = await archive_result(
                metadata,
                polished_srt=polished or "",
                summary=summary,
                mindmap=mindmap,
                original_srt=srt,
                work_dir=task_dir,
                analysis=analysis,
            )
            write_metadata_json(task_dir, metadata, status="completed")
            _cleanup_extracted_audio(task_dir, audio_path, metadata.media_type if metadata else None)
            await _update_step(task, PipelineStep.ARCHIVE, completed=True)

            task.result = {
                "metadata": metadata.model_dump(mode="json"),
                "transcript_segments": len(recognition_segments),
                "archive": archive,
                "output_dir": str(task_dir),
                "analysis": analysis,
                "subtitle_source": "platform",
            }
            return
    else:
        await _update_step(task, PipelineStep.DOWNLOAD)

        if source.startswith("upload://") or _looks_like_local_path(task.source):
            # Two sub-cases:
            #  1) upload:// — file already lives inside task_dir (browser upload)
            #  2) local path — file on disk, move it into task_dir
            is_browser_upload = source.startswith("upload://")

            if is_browser_upload:
                # File is already in task_dir — find it
                upload_name = source.removeprefix("upload://")
                if task_dir is None:
                    raise RuntimeError("upload:// source but task_dir is None")
                dest_source = task_dir / upload_name
                if not dest_source.exists():
                    raise FileNotFoundError(f"上传文件不存在: {dest_source}")
                source_path = dest_source  # for subtitle/nfo search (won't find any — that's fine)
            else:
                source_path = Path(source)
                if not source_path.exists():
                    raise FileNotFoundError(f"本地文件不存在: {source}")
                if not source_path.is_file():
                    raise ValueError(f"路径不是文件: {source}")
                title = source_path.stem
                if not task_dir:
                    task_dir = create_task_dir(task.id, title)
                dest_source = task_dir / source_path.name
                # If the source lives in the upload staging area, move it
                # (cheap on same volume) and drop the empty staging dir.
                # Otherwise copy so user's original file is preserved.
                staging_root = (Path(get_runtime_settings().data_root) / "_staging").resolve()
                try:
                    source_path.resolve().relative_to(staging_root)
                    is_staged = True
                except ValueError:
                    is_staged = False
                if is_staged:
                    shutil.move(str(source_path), str(dest_source))
                    try:
                        source_path.parent.rmdir()
                    except OSError:
                        pass
                else:
                    shutil.copy2(str(source_path), str(dest_source))

            title = dest_source.stem
            video_exts = {".mp4", ".mkv", ".avi", ".webm", ".mov"}
            audio_exts = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".opus", ".wma"}

            if dest_source.suffix.lower() in video_exts:
                audio_path = task_dir / f"{title}.wav"
                await asyncio.to_thread(_extract_audio_from_video, dest_source, audio_path)
                await _raise_if_cancelled(task.id)
                audio_path = str(audio_path)
                metadata = MediaMetadata(
                    title=title,
                    source_url=str(source_path),
                    media_type="video",
                    platform="local",
                    content_subtype="video",
                    file_path=str(dest_source),
                )

                # Search for local subtitle and NFO metadata
                # For browser uploads source_path == dest_source (no original dir to search)
                if not is_browser_upload and use_platform_subtitles:
                    platform_subtitle = find_local_subtitle(source_path)
                    if platform_subtitle:
                        log_event(
                            logger,
                            logging.INFO,
                            "subtitle.local.found",
                            path=platform_subtitle["subtitle_path"],
                        )

                if not is_browser_upload:
                    nfo_meta = parse_nfo(source_path)
                    if nfo_meta:
                        if nfo_meta.get("title"):
                            metadata.title = nfo_meta["title"]
                        if nfo_meta.get("description"):
                            metadata.description = nfo_meta["description"]
                        if nfo_meta.get("tags"):
                            metadata.tags = nfo_meta["tags"]
                        if nfo_meta.get("uploader"):
                            metadata.uploader = nfo_meta["uploader"]
                        if nfo_meta.get("upload_date"):
                            metadata.upload_date = nfo_meta["upload_date"]
                        if nfo_meta.get("source_url"):
                            metadata.source_url = nfo_meta["source_url"]
                            # Try to infer platform from NFO source_url
                            su = nfo_meta["source_url"]
                            if "bilibili.com" in su:
                                metadata.platform = "bilibili_video"
                            elif "youtube.com" in su or "youtu.be" in su:
                                metadata.platform = "youtube"

            elif dest_source.suffix.lower() in audio_exts:
                audio_path = str(dest_source)
                metadata = MediaMetadata(
                    title=title,
                    source_url=str(source_path),
                    media_type="audio",
                    platform="local",
                    content_subtype="audio",
                    file_path=str(dest_source),
                )
            else:
                raise ValueError(f"Unsupported file format: {dest_source.suffix}")

            has_subtitle = platform_subtitle is not None

            # Write metadata.json immediately after local file processing
            _sync_task_from_metadata(task, metadata)
            meta_path = write_metadata_json(task_dir, metadata, status="processing")
            await _emit_file_ready(task, "metadata.json", str(meta_path))

        else:
            # ── URL source: probe metadata + subtitle first ──
            # 1. Resolve title for task_dir naming
            if route_type in {"bilibili", "bilibili_video"}:
                bv_match = re.search(r'(BV[0-9A-Za-z]+)', source)
                title = bv_match.group(1) if bv_match else None
            elif route_type == "youtube":
                yt_match = re.search(r'(?:v=|youtu\.be/)([\w-]{11})', source)
                title = yt_match.group(1) if yt_match else None
                if not title:
                    ydl_opts = {"quiet": True, **ytdlp_base_opts(), **ytdlp_auth_opts()}
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(source, download=False)
                        title = info.get("title", "unknown") if info else "unknown"
            elif _download_resolves_url_title(route_type):
                # Title will be resolved during the actual download step; use task id as placeholder.
                title = None
            else:
                ydl_opts = {"quiet": True, **ytdlp_base_opts(), **ytdlp_auth_opts()}
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(source, download=False)
                    title = info.get("title", "unknown") if info else "unknown"

            if not task_dir:
                task_dir = create_task_dir(task.id, title or str(task.id))

            if use_platform_subtitles and not force_asr and not _download_resolves_url_title(route_type):
                await _update_flow_step(task, "subtitle_probe", message="探测平台字幕")
                # Probe: fetch metadata + subtitle (lightweight, no video download)
                try:
                    probe_metadata = await fetch_ytdlp_metadata(source)
                except YoutubeNetworkError:
                    raise
                except Exception as e:
                    log_event(logger, logging.WARNING, "metadata.probe.failed", error=e, fallback="full_pipeline")
                    probe_metadata = None

                probe_subtitle = None
                if probe_metadata:
                    try:
                        sub_dir = task_dir / "subtitles"
                        probe_subtitle = await download_subtitles(source, sub_dir)
                        if not probe_subtitle or not probe_subtitle.get("subtitle_path"):
                            if probe_metadata and probe_subtitle:
                                probe_metadata.extra["subtitle_engine"] = probe_subtitle.get("subtitle_engine")
                                probe_metadata.extra["subtitle_diagnostics"] = (
                                    probe_subtitle.get("diagnostics") or []
                                )
                            probe_subtitle = None
                            if sub_dir.exists() and not any(sub_dir.iterdir()):
                                sub_dir.rmdir()
                    except Exception as e:
                        if isinstance(e, YoutubeNetworkError):
                            raise
                        log_event(logger, logging.WARNING, "subtitle.probe.failed", error=e)
                        probe_subtitle = None

                if probe_metadata and not probe_subtitle:
                    await _emit_timeline_event(
                        task,
                        "subtitle.missing",
                        stage="subtitle",
                        step_id="subtitle_probe",
                        level="warning",
                        message="未发现可用平台字幕",
                        data={"diagnostics": getattr(probe_metadata, "extra", {}).get("subtitle_diagnostics", [])},
                    )
                    await _update_flow_step(task, "subtitle_probe", completed=True, level="warning", message="平台字幕不可用")

                if probe_metadata and probe_subtitle:
                    await _update_flow_step(task, "subtitle_probe", completed=True, message="平台字幕可用")
                    # ── FAST PATH: subtitle + video download in parallel ──
                    log_event(
                        logger,
                        logging.INFO,
                        "pipeline.fast_path.started",
                        subtitle_path=probe_subtitle.get("subtitle_path"),
                    )
                    metadata = probe_metadata
                    source_flow = await _update_flow_from_metadata(
                        task,
                        source_flow,
                        metadata,
                        has_subtitle=True,
                        force_asr=force_asr,
                        current_step="subtitle_probe",
                    )

                    # Rename task_dir to real title
                    real_title = metadata.title
                    task_dir, old_dir = _rename_task_dir_to_title(task_dir, real_title)
                    if old_dir:
                        # Update all subtitle paths after rename: tracks[].path + back-compat subtitle_path
                        new_sub_dir = task_dir / "subtitles"
                        for tr in probe_subtitle.get("tracks") or []:
                            if tr.get("path"):
                                tr["path"] = str(new_sub_dir / Path(tr["path"]).name)
                        if probe_subtitle.get("subtitle_path"):
                            old_sub_path = Path(probe_subtitle["subtitle_path"])
                            probe_subtitle["subtitle_path"] = str(new_sub_dir / old_sub_path.name)
                        log_event(logger, logging.INFO, "task_dir.renamed", from_path=old_dir, path=task_dir)

                    log_event(
                        logger,
                        logging.INFO,
                        "subtitle.downloaded",
                        path=probe_subtitle["subtitle_path"],
                        engine=probe_subtitle.get("subtitle_engine"),
                    )

                    # Write metadata.json
                    _sync_task_from_metadata(task, metadata)
                    meta_path = write_metadata_json(task_dir, metadata, status="processing")
                    await _emit_file_ready(task, "metadata.json", str(meta_path))

                    await _update_step(task, PipelineStep.DOWNLOAD, completed=True)

                    # Persist output_dir so resume can find task_dir
                    task.result = {"output_dir": str(task_dir)}
                    store = get_task_store()
                    store.update_status(task.id, task.status, result=task.result)

                    # Fork: Branch A (subtitle→LLM) + Branch B (video download)
                    async def _branch_video_download():
                        nonlocal audio_path
                        ingest = await download_media(source, output_dir=task_dir)
                        await _raise_if_cancelled(task.id)
                        audio_path = ingest.get("file_path")
                        # Update metadata with file paths from download
                        if ingest.get("video_path"):
                            metadata.file_path = ingest["video_path"]
                        write_metadata_json(task_dir, metadata, status="processing")

                    results = await asyncio.gather(
                        _run_subtitle_fast_path(task, task_dir, probe_subtitle, metadata),
                        _branch_video_download(),
                        return_exceptions=True,
                    )
                    await _raise_if_cancelled(task.id)
                    text_result, video_result = results

                    # Text branch is the core output — if it fails, the task fails.
                    if isinstance(text_result, BaseException):
                        raise text_result
                    # Video branch is auxiliary: log but don't fail the task.
                    # The transcript/summary/mindmap are already produced.
                    if isinstance(video_result, BaseException):
                        log_event(
                            logger,
                            logging.WARNING,
                            "pipeline.fast_path.video_failed",
                            error_type=type(video_result).__name__,
                            error=video_result,
                        )
                        metadata.file_path = None

                    # Archive
                    await _raise_if_cancelled(task.id)
                    await _update_step(task, PipelineStep.ARCHIVE)
                    archive = await archive_result(
                        metadata,
                        polished_srt=text_result.get("polished", ""),
                        summary=text_result.get("summary", {}),
                        mindmap=text_result.get("mindmap", ""),
                        original_srt=text_result.get("srt", ""),
                        work_dir=task_dir,
                        analysis=text_result.get("analysis", {}),
                    )
                    write_metadata_json(task_dir, metadata, status="completed")
                    _cleanup_extracted_audio(task_dir, audio_path, metadata.media_type if metadata else None)
                    await _update_step(task, PipelineStep.ARCHIVE, completed=True)

                    task.result = {
                        "metadata": metadata.model_dump(mode="json"),
                        "transcript_segments": len(text_result.get("recognition_segments", [])),
                        "archive": archive,
                        "output_dir": str(task_dir),
                        "analysis": text_result.get("analysis"),
                        "subtitle_source": "platform",
                    }
                    return  # Done — skip the rest of run_pipeline

            # ── FULL PIPELINE: no subtitle or force_asr ──
            # (existing code path, unchanged)
            ingest = await download_media(source, output_dir=task_dir)
            await _raise_if_cancelled(task.id)
            audio_path = ingest.get("file_path")
            metadata = MediaMetadata(**ingest.get("metadata", {"title": source}))
            if ingest.get("video_path"):
                metadata.file_path = ingest["video_path"]

            # Rename task_dir from temp name (BV号/video ID) to real title
            real_title = metadata.title
            task_dir, old_dir = _rename_task_dir_to_title(task_dir, real_title)
            if old_dir:
                if audio_path:
                    audio_path = str(task_dir / Path(audio_path).name)
                if metadata.file_path:
                    metadata.file_path = str(task_dir / Path(metadata.file_path).name)
                _rewrite_ingest_paths_after_task_dir_move(ingest, metadata, old_dir, task_dir)
                log_event(logger, logging.INFO, "task_dir.renamed", from_path=old_dir, path=task_dir)

            # Notes take a different branch entirely — no GPU, no audio.
            if metadata.content_subtype in {"image_note", "text_note"}:
                ingest_info = ingest.get("info") or ingest
                source_flow = await _update_flow_from_metadata(
                    task,
                    source_flow,
                    metadata,
                    force_asr=force_asr,
                    current_step="download",
                )
                _sync_task_from_metadata(task, metadata)
                meta_path = write_metadata_json(task_dir, metadata, status="processing")
                await _emit_file_ready(task, "metadata.json", str(meta_path))

                existing_result = dict(task.result or {})
                existing_result["output_dir"] = str(task_dir)
                existing_result["_image_ingest_info"] = ingest_info
                task.result = existing_result
                get_task_store().update_status(task.id, task.status, result=task.result)

                if metadata.content_subtype == "image_note":
                    await _download_note_images_for_download_step(task, metadata, task_dir, ingest_info)
                    meta_path = write_metadata_json(task_dir, metadata, status="processing")
                    await _emit_file_ready(task, "metadata.json", str(meta_path))

                await _update_step(task, PipelineStep.DOWNLOAD, completed=True)
                await _raise_if_cancelled(task.id)
                if _download_worker_call:
                    await get_task_queue().advance_to_gpu(task.id)
                    return
                await _process_image_note(task, metadata, task_dir, ingest_info)
                return

            # Try to download platform subtitles (for full pipeline, still useful)
            if use_platform_subtitles:
                await _update_flow_step(task, "subtitle_probe", message="探测平台字幕")
                try:
                    sub_dir = task_dir / "subtitles"
                    platform_subtitle = await download_subtitles(source, sub_dir)
                    if platform_subtitle.get("subtitle_path"):
                        log_event(
                            logger,
                            logging.INFO,
                            "subtitle.downloaded",
                            path=platform_subtitle["subtitle_path"],
                            engine=platform_subtitle.get("subtitle_engine"),
                        )
                    else:
                        metadata.extra["subtitle_engine"] = platform_subtitle.get("subtitle_engine")
                        metadata.extra["subtitle_diagnostics"] = platform_subtitle.get("diagnostics") or []
                        platform_subtitle = None
                        if sub_dir.exists() and not any(sub_dir.iterdir()):
                            sub_dir.rmdir()
                except Exception as e:
                    log_event(logger, logging.WARNING, "subtitle.download_failed", error=e)
                    platform_subtitle = None

            has_subtitle = platform_subtitle is not None
            if use_platform_subtitles:
                await _update_flow_step(
                    task,
                    "subtitle_probe",
                    completed=True,
                    level="info" if has_subtitle else "warning",
                    message="平台字幕可用" if has_subtitle else "平台字幕不可用",
                )
            source_flow = await _update_flow_from_metadata(
                task,
                source_flow,
                metadata,
                has_subtitle=has_subtitle,
                force_asr=force_asr,
                current_step="download",
            )
            if use_platform_subtitles and not has_subtitle:
                await _emit_timeline_event(
                    task,
                    "subtitle.missing",
                    stage="subtitle",
                    step_id="subtitle_probe",
                    level="warning",
                    message="未发现可用平台字幕",
                    data={"diagnostics": metadata.extra.get("subtitle_diagnostics", [])},
                )

            # Write metadata.json immediately after download
            _sync_task_from_metadata(task, metadata)
            meta_path = write_metadata_json(task_dir, metadata, status="processing")
            await _emit_file_ready(task, "metadata.json", str(meta_path))

        await _update_step(task, PipelineStep.DOWNLOAD, completed=True)
        await _raise_if_cancelled(task.id)
    # end if DOWNLOAD not in done

    # Sanity: we must have a task_dir by now
    if task_dir is None or metadata is None:
        raise RuntimeError("task_dir or metadata missing after DOWNLOAD step — cannot continue")

    # Note GPU-worker re-entry: DOWNLOAD is done, route directly to note branch.
    if metadata.content_subtype in {"image_note", "text_note"} and not _download_worker_call:
        source_flow = await _update_flow_from_metadata(
            task,
            source_flow,
            metadata,
            force_asr=force_asr,
            current_step="download",
        )
        _sync_task_from_metadata(task, metadata)
        ingest_info = (task.result or {}).get("_image_ingest_info") or {"extra": metadata.extra or {}}
        await _process_image_note(task, metadata, task_dir, ingest_info)
        return

    # Hand off to GPU queue if we were called from a download worker.
    # The GPU worker will call process_task again; at that point DOWNLOAD is
    # in completed_steps so this block is skipped and we continue below.
    if _download_worker_call:
        # Persist output_dir so the GPU worker can restore task_dir from DB
        # (task_dir may have been renamed after download, so we must save the
        # current — possibly renamed — path before handing off).
        task.result = {"output_dir": str(task_dir)}
        store = get_task_store()
        store.update_status(task.id, task.status, result=task.result)
        await get_task_queue().advance_to_gpu(task.id)
        return

    # ── Steps 2+3: SEPARATE + TRANSCRIBE — GPU-bound, serialised by semaphore ──
    gpu_sem = get_task_queue().gpu_semaphore

    if PipelineStep.SEPARATE in done and PipelineStep.TRANSCRIBE in done:
        log_event(logger, logging.INFO, "pipeline.step.skipped", step="separate,transcribe", reason="already_done")
        _restore_transcript()
        _restore_audio_paths()
    else:
        async with gpu_sem:
            log_event(logger, logging.INFO, "gpu.semaphore.acquired")

            # Step 2: Separate vocals
            if PipelineStep.SEPARATE in done:
                log_event(logger, logging.INFO, "pipeline.step.skipped", step=PipelineStep.SEPARATE, reason="already_done")
                _restore_audio_paths()
            else:
                await _update_step(task, PipelineStep.SEPARATE)
                skip_separation = (
                    task.options.get("skip_separation", False)
                    or task.options.get("api_flow", False)
                    or has_subtitle
                    or not source_flow.requires_uvr
                )
                if skip_separation:
                    vocals_path = audio_path
                    if not has_subtitle:
                        uvr_fallback_reason = "uvr.skipped"
                        await _emit_timeline_event(
                            task,
                            uvr_fallback_reason,
                            stage="uvr",
                            step_id="separate",
                            level="warning",
                            message="已跳过 UVR，人声分离不参与本次转录",
                            data={
                                "skip_separation": bool(task.options.get("skip_separation", False)),
                                "api_flow": bool(task.options.get("api_flow", False)),
                                "requires_uvr": source_flow.requires_uvr,
                            },
                        )
                else:
                    source_audio = _require_audio_file(audio_path, stage="UVR separation")
                    try:
                        try:
                            preprocess = await separate_vocals(source_audio, output_dir=task_dir)
                        except Exception as e:
                            log_event(
                                logger,
                                logging.WARNING,
                                "uvr.separation.failed_fallback",
                                error=e,
                                fallback="original_audio",
                            )
                            metadata.extra["uvr_error"] = str(e)
                            metadata.extra["uvr_fallback"] = "original_audio"
                            vocals_path = source_audio
                            uvr_fallback_reason = "uvr.failed"
                            await _emit_timeline_event(
                                task,
                                uvr_fallback_reason,
                                stage="uvr",
                                step_id="separate",
                                level="warning",
                                message="UVR 处理失败，转录将使用原始音频",
                                data={"error": str(e)},
                            )
                        else:
                            vocals_path = preprocess.get("vocals_path") or source_audio
                            vocals_path = _require_audio_file(
                                vocals_path,
                                stage="UVR separation output",
                            )
                            if preprocess.get("model_used") == "mock" or Path(vocals_path) == Path(source_audio):
                                uvr_fallback_reason = "uvr.unavailable"
                                await _emit_timeline_event(
                                    task,
                                    uvr_fallback_reason,
                                    stage="uvr",
                                    step_id="separate",
                                    level="warning",
                                    message="UVR 当前不可用，转录将使用原始音频",
                                    data={"model_used": preprocess.get("model_used")},
                                )
                    finally:
                        await asyncio.to_thread(_release_uvr_gpu_resources)
                    await _raise_if_cancelled(task.id)
                await _update_step(task, PipelineStep.SEPARATE, completed=True)

            # Step 3: Transcribe
            if PipelineStep.TRANSCRIBE in done:
                log_event(logger, logging.INFO, "pipeline.step.skipped", step=PipelineStep.TRANSCRIBE, reason="already_done")
                _restore_transcript()
            else:
                await _update_step(task, PipelineStep.TRANSCRIBE)
                if has_subtitle:
                    log_event(logger, logging.INFO, "asr.skipped", reason="platform_subtitle")
                    pst_tracks = platform_subtitle.get("tracks") or []
                    if not pst_tracks and platform_subtitle.get("subtitle_path"):
                        pst_tracks = [{
                            "path": platform_subtitle["subtitle_path"],
                            "lang": platform_subtitle.get("subtitle_lang") or "unknown",
                            "format": platform_subtitle.get("subtitle_format") or "srt",
                            "type": "cc",
                        }]
                    tracks_manifest = _save_all_tracks_as_transcripts(pst_tracks, task_dir)
                    selected_track, detected_lang = await _select_polish_track(pst_tracks)
                    for entry in tracks_manifest:
                        if entry["lang"] == (selected_track.get("lang") or "unknown"):
                            entry["polished"] = True
                    metadata.extra["subtitle_tracks"] = tracks_manifest
                    metadata.extra["detected_language"] = detected_lang
                    metadata.extra["subtitle_engine"] = platform_subtitle.get("subtitle_engine")
                    metadata.extra["subtitle_diagnostics"] = platform_subtitle.get("diagnostics") or []

                    sub_result = await process_subtitles(
                        subtitle_path=selected_track["path"],
                        subtitle_format=selected_track.get("format") or "srt",
                        metadata=metadata,
                    )
                    transcript = " ".join(s["text"] for s in sub_result.get("segments", []))
                    srt = sub_result.get("srt", "")
                    polished = sub_result.get("polished_srt", "")
                    polished_md = sub_result.get("polished_md", "")
                    subtitle_source = "platform"
                    recognition_segments = sub_result.get("segments", [])
                else:
                    num_speakers = task.options.get("num_speakers")
                    asr_provider = task.options.get("asr_provider")
                    if task.options.get("api_flow", False) and not asr_provider:
                        asr_provider = "siliconflow"
                    asr_selection_reason = "task_option" if asr_provider else "settings"
                    if (
                        source_type == "url"
                        and uvr_fallback_reason
                        and source_flow.flow_id in {"url_media_asr", "url_platform_video_asr"}
                    ):
                        selected_provider, asr_selection_reason, selected_api = (
                            (str(asr_provider), asr_selection_reason, str(asr_provider) == "siliconflow")
                            if asr_provider
                            else _select_asr_provider_for_fallback(task)
                        )
                        asr_provider = selected_provider
                        if selected_api:
                            source_flow = await _update_flow_from_metadata(
                                task,
                                source_flow,
                                metadata,
                                force_asr=force_asr,
                                api_fallback=True,
                                current_step="transcribe",
                                preferred_asr_provider=asr_provider,
                            )
                            await _emit_timeline_event(
                                task,
                                "asr.api_fallback.selected",
                                stage="asr",
                                step_id="transcribe",
                                level="info",
                                message="已选择 API ASR fallback",
                                data={
                                    "provider": asr_provider,
                                    "reason": asr_selection_reason,
                                    "uvr_reason": uvr_fallback_reason,
                                },
                            )
                        else:
                            await _emit_timeline_event(
                                task,
                                "diagnostic",
                                stage="asr",
                                step_id="transcribe",
                                level="info",
                                message="继续使用当前默认 ASR",
                                data={
                                    "provider": asr_provider,
                                    "reason": asr_selection_reason,
                                    "uvr_reason": uvr_fallback_reason,
                                },
                            )
                    asr_audio_path = _require_audio_file(vocals_path, stage="ASR transcription")
                    await _emit_timeline_event(
                        task,
                        "asr.started",
                        stage="asr",
                        step_id="transcribe",
                        level="info",
                        message="开始 ASR 转录",
                        data={"provider": asr_provider or "settings", "selection_reason": asr_selection_reason},
                    )
                    try:
                        recognition = await transcribe_audio(
                            asr_audio_path,
                            output_dir=task_dir,
                            num_speakers=num_speakers,
                            provider=asr_provider,
                            diarize=not task.options.get("disable_diarization", False),
                            chunk_strategy=task.options.get("asr_chunk_strategy"),
                            hotwords=task.options.get("hotwords"),
                        )
                    except Exception as e:
                        await _emit_timeline_event(
                            task,
                            "asr.failed",
                            stage="asr",
                            step_id="transcribe",
                            level="error",
                            message="ASR 转录失败",
                            data={"provider": asr_provider or "settings", "error": str(e)},
                        )
                        raise
                    await _raise_if_cancelled(task.id)
                    transcript = " ".join(s["text"] for s in recognition.get("segments", []))
                    if (
                        Path(asr_audio_path) != Path(audio_path)
                        and _is_transcript_too_short_for_uvr_fallback(transcript)
                    ):
                        original_asr_audio = _require_audio_file(
                            audio_path,
                            stage="ASR fallback original audio",
                        )
                        metadata.extra["uvr_fallback"] = "asr_too_short_original_audio"
                        metadata.extra["uvr_transcript_chars"] = len(re.sub(r"\s+", "", transcript or ""))
                        uvr_fallback_reason = "uvr.asr_too_short"
                        await _emit_timeline_event(
                            task,
                            uvr_fallback_reason,
                            stage="asr",
                            step_id="transcribe",
                            level="warning",
                            message="UVR 后转写文本过短，改用原始音频重新 ASR",
                            data={
                                "provider": asr_provider or "settings",
                                "segments": len(recognition.get("segments", [])),
                                "transcript_chars": metadata.extra["uvr_transcript_chars"],
                                "fallback_audio": original_asr_audio,
                            },
                        )
                        recognition = await transcribe_audio(
                            original_asr_audio,
                            output_dir=task_dir,
                            num_speakers=num_speakers,
                            provider=asr_provider,
                            diarize=not task.options.get("disable_diarization", False),
                            chunk_strategy=task.options.get("asr_chunk_strategy"),
                            hotwords=task.options.get("hotwords"),
                        )
                        await _raise_if_cancelled(task.id)
                        transcript = " ".join(s["text"] for s in recognition.get("segments", []))
                    srt = recognition.get("srt", "")
                    polished = None
                    polished_md = None
                    subtitle_source = "asr"
                    recognition_segments = recognition.get("segments", [])
                    await _emit_timeline_event(
                        task,
                        "asr.completed",
                        stage="asr",
                        step_id="transcribe",
                        level="info",
                        message="ASR 转录完成",
                        data={
                            "provider": asr_provider or "settings",
                            "segments": len(recognition_segments),
                            "language": recognition.get("language"),
                        },
                    )

                    asr_error = _extract_internal_asr_error(recognition_segments)
                    if asr_error:
                        raise RuntimeError(f"ASR backend produced an internal error placeholder: {asr_error}")

                    # Detect transcript language (non-fatal, populates metadata for UI)
                    if srt:
                        try:
                            from app.services.analysis.language_detect import detect_transcript_language
                            detected_lang = await detect_transcript_language(srt=srt)
                            metadata.extra["detected_language"] = detected_lang
                            metadata.extra["subtitle_tracks"] = [{
                                "lang": detected_lang if detected_lang != "unknown" else "asr",
                                "type": "asr",
                                "filename": "transcript.srt",
                                "polished": True,  # polish step will populate
                            }]
                        except Exception as e:
                            log_event(logger, logging.WARNING, "asr.lang_detect.failed", error=e)

                # Write transcript.srt immediately
                if srt:
                    await _write_text_artifact(task, task_dir, "transcript.srt", srt)
                if has_subtitle and polished:
                    await _write_text_artifact(task, task_dir, "transcript_polished.srt", polished)
                    if polished_md:
                        await _write_text_artifact(task, task_dir, "transcript_polished.md", polished_md)

                await _update_step(task, PipelineStep.TRANSCRIBE, completed=True)
                await _raise_if_cancelled(task.id)
            # end if TRANSCRIBE not in done

            # ── Voiceprint step: temporarily disabled ──
            # The matcher/library work is currently being reconsidered.
            # Mark the step complete so the UI progress bar still advances,
            # but skip the embedding extraction + person registration.
            # Re-enable by reverting this block (see git history for the
            # original _run_voiceprint_step call + SRT speaker-name rewrite).
            if PipelineStep.VOICEPRINT not in done:
                await _update_step(task, PipelineStep.VOICEPRINT)
                log_event(logger, logging.INFO, "voiceprint.skipped", reason="disabled")
                await _update_step(task, PipelineStep.VOICEPRINT, completed=True)
        # end async with gpu_sem

    # Clean up UVR vocals and segment files immediately after ASR is done —
    # these large WAVs are no longer needed and can free significant disk space.
    _cleanup_vocals(task_dir, audio_path, vocals_path)

    # end if SEPARATE+TRANSCRIBE not both done

    # Ensure VOICEPRINT is marked complete even in the "both already done" resume path
    if PipelineStep.VOICEPRINT not in task.completed_steps:
        await _update_step(task, PipelineStep.VOICEPRINT, completed=True)

    # Guard: skip LLM if transcript is empty or trivially short
    if not transcript or len(transcript.strip()) < 10:
        log_event(logger, logging.WARNING, "pipeline.llm.skipped", reason="transcript_too_short", chars=len(transcript))
        await _update_step(task, PipelineStep.POLISH, completed=True)
        await _update_step(task, PipelineStep.ANALYZE, completed=True)

        await _update_step(task, PipelineStep.ARCHIVE)
        empty_analysis = {"language": "unknown", "content_type": "unknown", "main_topics": [],
                          "keywords": [], "proper_nouns": [], "speakers_detected": 0, "tone": "unknown"}
        empty_summary = {"tldr": "未检测到有效语音内容", "key_facts": [], "action_items": [], "topics": []}
        archive = await archive_result(
            metadata,
            polished_srt="",
            summary=empty_summary,
            mindmap="",
            original_srt=srt,
            work_dir=task_dir,
            analysis=empty_analysis,
        )
        write_metadata_json(task_dir, metadata, status="completed")
        _cleanup_extracted_audio(task_dir, audio_path, metadata.media_type if metadata else None)
        await _update_step(task, PipelineStep.ARCHIVE, completed=True)

        task.result = {
            "metadata": metadata.model_dump(mode="json"),
            "transcript_segments": 0,
            "archive": archive,
            "output_dir": str(task_dir),
            "analysis": empty_analysis,
            "warning": "未检测到有效语音内容，跳过 LLM 分析",
        }
        return

    # ── Step 4: Polish transcript (CPU/network) ────────────────────────────
    polish_ran = False
    if PipelineStep.POLISH in done:
        log_event(logger, logging.INFO, "pipeline.step.skipped", step=PipelineStep.POLISH, reason="already_done")
        _restore_transcript()  # picks up polished if present
    else:
        await _update_step(task, PipelineStep.POLISH)
        if has_subtitle:
            log_event(logger, logging.INFO, "pipeline.step.skipped", step=PipelineStep.POLISH, reason="platform_subtitle_prepolished")
        else:
            hotwords = task.options.get("hotwords")
            if hotwords and analysis:
                existing = analysis.get("proper_nouns", []) or []
                analysis["proper_nouns"] = list(set(existing + hotwords))
            elif hotwords:
                analysis = {"proper_nouns": hotwords}
            polished = await polish_text(srt, context=analysis)
            await _raise_if_cancelled(task.id)
        if not has_subtitle and polished:
            from app.services.analysis import srt_to_markdown
            await _write_text_artifact(task, task_dir, "transcript_polished.srt", polished)
            polished_md_content = srt_to_markdown(polished, metadata.title)
            await _write_text_artifact(task, task_dir, "transcript_polished.md", polished_md_content)
            polish_ran = True
        await _update_step(task, PipelineStep.POLISH, completed=True)
        await _raise_if_cancelled(task.id)
    # end if POLISH not in done

    # ── Step 5: Analyze + Summarize + Mindmap from polished text ─────────────
    # If an older interrupted task already completed ANALYZE before POLISH,
    # regenerate analysis outputs now so summary/mindmap reflect the polished SRT.
    if PipelineStep.ANALYZE in done and not polish_ran:
        log_event(logger, logging.INFO, "pipeline.step.skipped", step=PipelineStep.ANALYZE, reason="already_done")
        _restore_analysis()
        _restore_summary()
        _restore_mindmap()
    else:
        await _update_step(task, PipelineStep.ANALYZE)
        video_metadata = {
            "uploader": metadata.uploader,
            "description": metadata.description,
            "tags": metadata.tags,
            "chapters": [{"title": ch.title, "start_time": ch.start_time} for ch in metadata.chapters] if metadata.chapters else None,
        }
        mindmap_metadata = {
            "title": metadata.title,
            "uploader": metadata.uploader,
            "description": metadata.description,
            "chapters": [{"title": ch.title, "start_time": ch.start_time} for ch in metadata.chapters] if metadata.chapters else None,
        }

        analysis_text = _plain_text_from_srt(polished) if polished else transcript
        mindmap_text = polished or srt or transcript

        analysis = await analyze_content(analysis_text, metadata.title, metadata=video_metadata)
        user_language = _user_language_hint(analysis)

        import json as _json
        if analysis:
            analysis_path = task_dir / "analysis.json"
            analysis_path.write_text(_json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
            await _emit_file_ready(task, "analysis.json", str(analysis_path))

        tasks = [
            summarize_text(analysis_text, user_language=user_language),
            generate_mindmap(mindmap_text, metadata=mindmap_metadata, user_language=user_language),
        ]
        if rt.generate_video_detail:
            tasks.append(generate_detail(mindmap_text, user_language=user_language))
        results = await asyncio.gather(*tasks)
        summary = results[0]
        mindmap = results[1]
        detail = results[2] if len(results) > 2 else ""
        await _raise_if_cancelled(task.id)

        if summary:
            await _write_summary_files(task, task_dir, metadata, summary)
        if mindmap:
            await _write_mindmap_files(task, task_dir, mindmap)
        if detail:
            await _write_detail_file(task, task_dir, detail)

        await _update_step(task, PipelineStep.ANALYZE, completed=True)
    # end if ANALYZE not in done

    # Step 6: Archive (finalize — writes any missing files, sets status to completed)
    await _raise_if_cancelled(task.id)
    await _update_step(task, PipelineStep.ARCHIVE)
    archive = await archive_result(
        metadata,
        polished_srt=polished or "",
        summary=summary,
        mindmap=mindmap,
        original_srt=srt,
        work_dir=task_dir,
        analysis=analysis,
    )

    # Update metadata status to completed
    meta_path = write_metadata_json(task_dir, metadata, status="completed")
    await _emit_file_ready(task, "metadata.json", str(meta_path))

    _cleanup_extracted_audio(task_dir, audio_path, metadata.media_type if metadata else None)

    await _update_step(task, PipelineStep.ARCHIVE, completed=True)

    task.result = {
        "metadata": metadata.model_dump(mode="json"),
        "transcript_segments": len(recognition_segments),
        "archive": archive,
        "output_dir": str(task_dir),
        "analysis": analysis,
        "subtitle_source": subtitle_source,
    }

    # Async KB indexing (fail-soft)
    _schedule_kb_index(str(task.id), str(task_dir))


def _schedule_kb_index(task_id: str, archive_path: str) -> None:
    """Fire-and-forget KB indexing after archive completes."""
    import asyncio

    async def _do_index():
        try:
            from app.core.settings import get_runtime_settings
            rt = get_runtime_settings()
            if not rt.kb_enabled or not rt.kb_embedding_api_base:
                return
            from app.services.kb.indexer import index_task
            log_event(logger, logging.INFO, "kb.index.started", archive_path=archive_path)
            await asyncio.to_thread(index_task, task_id, archive_path)
            log_event(logger, logging.INFO, "kb.index.completed", archive_path=archive_path)
        except Exception as e:
            log_event(logger, logging.WARNING, "kb.index.failed", task_id=task_id, error=e)

    asyncio.ensure_future(_do_index())


# ---------------------------------------------------------------------------
# Task processor — called by queue worker
# ---------------------------------------------------------------------------

async def process_task(task_id: UUID, _download_worker_call: bool = False) -> None:
    """Process a single task — called by both download workers and GPU worker.

    download worker  → process_task(id, _download_worker_call=True)
                         runs DOWNLOAD, then advance_to_gpu(), returns
    GPU worker       → process_task(id, _download_worker_call=False)
                         DOWNLOAD already in completed_steps, skips it,
                         runs SEPARATE → TRANSCRIBE → VOICEPRINT → POLISH → ANALYZE → ARCHIVE
    """
    from app.services.ingestion import download_media
    from app.services.preprocessing import separate_vocals
    from app.services.recognition import transcribe_audio
    from app.services.analysis import polish_text, summarize_text, generate_mindmap

    store = get_task_store()
    bus = get_event_bus()

    task = store.get(task_id)
    if not task:
        return

    # Only set PROCESSING status on first entry (download worker call).
    # On GPU worker re-entry the task is already PROCESSING.
    if task.status != TaskStatus.PROCESSING:
        store.update_status(task_id, TaskStatus.PROCESSING, error=None)
        await bus.publish(TaskEvent(task_id, "processing"))

    # Re-read from DB to get latest completed_steps
    task = store.get(task_id)
    started_at = time.perf_counter()
    log_event(
        logger,
        logging.INFO,
        "task.started",
        task_type=task.task_type,
        download_worker_call=_download_worker_call,
    )

    try:
        if task.task_type == TaskType.PIPELINE:
            await run_pipeline(task, _download_worker_call=_download_worker_call)
        elif task.task_type == TaskType.INGESTION:
            task.result = await download_media(task.source)
        elif task.task_type == TaskType.PREPROCESSING:
            task.result = await separate_vocals(task.source)
        elif task.task_type == TaskType.RECOGNITION:
            task.result = await transcribe_audio(task.source)
        elif task.task_type == TaskType.ANALYSIS:
            polished = await polish_text(task.source)
            summary = await summarize_text(task.source)
            mindmap = await generate_mindmap(task.source)
            task.result = {"polished": polished, "summary": summary, "mindmap": mindmap}

        # If this was the download-worker call, run_pipeline normally returned
        # early after advance_to_gpu() — don't mark COMPLETED yet. The subtitle
        # fast path is the exception: it can finish ARCHIVE inside the download
        # worker, so it must fall through to the normal completion write below.
        if _download_worker_call and task.task_type == TaskType.PIPELINE:
            if PipelineStep.ARCHIVE not in (task.completed_steps or []):
                return

        task.status = TaskStatus.COMPLETED
        task.progress = 1.0
        task.completed_at = datetime.now()
        if task.flow:
            flow = dict(task.flow)
            flow["status"] = "completed"
            flow["progress"] = 1.0
            flow["completed_steps"] = [step.get("id") for step in flow.get("steps", []) if isinstance(step, dict)]
            task.flow = flow

        store.update_status(
            task_id,
            TaskStatus.COMPLETED,
            progress=1.0,
            result=task.result,
            completed_at=task.completed_at,
            error=None,
            flow=task.flow,
            platform=task.platform,
            uploader_id=task.uploader_id,
            content_subtype=task.content_subtype,
        )
        await bus.publish(TaskEvent(task_id, "completed", {
            "output_dir": task.result.get("output_dir") if task.result else None,
        }))
        log_event(
            logger,
            logging.INFO,
            "task.completed",
            task_type=task.task_type,
            duration_ms=round((time.perf_counter() - started_at) * 1000),
            output_dir=task.result.get("output_dir") if task.result else None,
        )

    except asyncio.CancelledError:
        log_event(
            logger,
            logging.INFO,
            "task.paused" if (store.get(task_id) or task).status == TaskStatus.PAUSED else "task.cancelled",
            duration_ms=round((time.perf_counter() - started_at) * 1000),
        )
        current = store.get(task_id) or task
        output_dir = current.result.get("output_dir") if current.result else None
        paused = current.status == TaskStatus.PAUSED
        final_status = TaskStatus.PAUSED if paused else TaskStatus.CANCELLED
        status_text = "paused" if paused else "cancelled"
        update_metadata_status(Path(output_dir) if output_dir else None, status_text)
        flow = current.flow or task.flow
        if flow:
            flow = dict(flow)
            flow["status"] = status_text
        store.update_status(
            task_id,
            final_status,
            completed_at=None if paused else datetime.now(),
            message="已暂停" if paused else "已取消",
            flow=flow,
        )
        await bus.publish(TaskEvent(task_id, status_text, {"status": status_text}))
        raise

    except Exception as e:
        log_event(
            logger,
            logging.ERROR,
            "task.failed",
            task_type=task.task_type,
            duration_ms=round((time.perf_counter() - started_at) * 1000),
            error=e,
            exc_info=True,
        )
        task.status = TaskStatus.FAILED
        task.error = str(e)

        # Update metadata.json status to failed
        current = store.get(task_id) or task
        output_dir = current.result.get("output_dir") if current.result else None
        update_metadata_status(Path(output_dir) if output_dir else None, "failed")
        flow = current.flow or task.flow
        if flow:
            flow = dict(flow)
            flow["status"] = "failed"

        store.update_status(
            task_id,
            TaskStatus.FAILED,
            error=str(e),
            completed_at=datetime.now(),
            flow=flow,
        )
        await bus.publish(TaskEvent(task_id, "failed", {"error": str(e), "stage": task.current_step}))

    finally:
        # Offload local GGUF model after each task to free VRAM.
        # No-op when using API providers.
        if not _download_worker_call:
            from app.services.analysis.llm import offload_local_llm
            offload_local_llm()
