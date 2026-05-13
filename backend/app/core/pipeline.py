"""Pipeline orchestration — extracted from api.routes.tasks.

This module owns the full processing pipeline (download → archive) and uses
TaskStore + EventBus for state management instead of in-memory dicts.
"""

import asyncio
import json
import logging
import re
import shutil
import subprocess
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.database import get_task_store
from app.core.events import TaskEvent, get_event_bus
from app.core.settings import get_runtime_settings
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

def _detect_source_type(source: str) -> str:
    """Detect the type of media source."""
    source_lower = source.lower()
    if "youtube.com" in source_lower or "youtu.be" in source_lower:
        return "youtube"
    elif "bilibili.com" in source_lower or "b23.tv" in source_lower:
        return "bilibili"
    elif source_lower.startswith(("http://", "https://")):
        return "url"
    elif any(source_lower.endswith(ext) for ext in [".mp4", ".mkv", ".avi", ".webm", ".mov"]):
        return "local_video"
    elif any(source_lower.endswith(ext) for ext in [".mp3", ".wav", ".flac", ".m4a", ".ogg"]):
        return "local_audio"
    else:
        return "unknown"


def _platform_prefer_subtitles(source_type: str) -> bool:
    """Resolve subtitle preference with per-platform config fallback."""
    rt = get_runtime_settings()
    try:
        configs = json.loads(rt.platform_configs or "{}")
    except Exception:
        configs = {}

    platform_cfg = configs.get(source_type) if source_type in {"bilibili", "youtube"} else None
    if isinstance(platform_cfg, dict) and "prefer_subtitle" in platform_cfg:
        return bool(platform_cfg["prefer_subtitle"])
    return bool(rt.prefer_platform_subtitles)


def _sanitize_filename(name: str) -> str:
    """Sanitize string for use as filename."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = name.strip(' .')
    return name[:100] if len(name) > 100 else name


def create_task_dir(task_id: UUID, title: str | None = None) -> Path:
    """Create a dedicated directory for this task under data/{title}/."""
    settings = get_runtime_settings()
    data_root = Path(settings.data_root).resolve()

    if title:
        dir_name = _sanitize_filename(title)
    else:
        dir_name = str(task_id)[:8]

    task_dir = data_root / dir_name
    # Handle duplicate names by appending (2), (3), etc.
    if task_dir.exists():
        counter = 2
        while True:
            candidate = data_root / f"{dir_name} ({counter})"
            if not candidate.exists():
                task_dir = candidate
                break
            counter += 1

    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


def write_metadata_json(task_dir: Path, metadata: MediaMetadata | dict, status: str = "processing") -> Path:
    """Write or update metadata.json in the task directory."""
    import json
    meta_path = task_dir / "metadata.json"
    if isinstance(metadata, MediaMetadata):
        data = metadata.model_dump(mode="json")
    else:
        data = dict(metadata)
    data["status"] = status
    meta_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta_path


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
        logger.debug("Failed to update metadata.json status", exc_info=True)


async def _raise_if_cancelled(task_id: UUID) -> None:
    """Honor cancellation requests between blocking pipeline phases."""
    task = get_task_store().get(task_id)
    if task and task.status == TaskStatus.CANCELLED:
        raise asyncio.CancelledError()


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


def _clean_source_path(source: str) -> str:
    """Clean up source path by removing quotes and whitespace."""
    source = source.strip()
    if (source.startswith('"') and source.endswith('"')) or \
       (source.startswith("'") and source.endswith("'")):
        source = source[1:-1]
    return source


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
            logger.warning(f"Failed to read sample track for lang detection: {e}")
            return tracks[0], tracks[0].get("lang") or "unknown"

    detected = await detect_transcript_language(srt=sample)
    if detected == "unknown":
        logger.info("Language detection returned unknown, using first track for polish")
        return tracks[0], detected

    matched = match_track_by_language(tracks, detected)
    if matched:
        logger.info(f"Polish track selected: lang={matched.get('lang')} (detected={detected})")
        return matched, detected

    logger.info(f"Detected lang '{detected}' has no matching track, using first")
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
            logger.warning(f"Failed to copy track {src} → {dest}: {e}")
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
        logger.info(f"Cleaned up vocals/segments ({size_mb:.1f} MB): {cleaned_files}")


def _cleanup_extracted_audio(task_dir: Path, audio_path: str | None, media_type: str | None) -> None:
    """Clean up the extracted WAV from video in the final archive step.

    Only deletes the ffmpeg-extracted WAV for video files. Audio-only files
    keep their source since it IS the original media.
    """
    if media_type != "video" or not audio_path:
        return
    audio_file = Path(audio_path)
    if audio_file.exists() and audio_file.suffix.lower() == ".wav":
        size = audio_file.stat().st_size
        audio_file.unlink()
        logger.info(f"Cleaned up extracted audio ({size / (1024*1024):.1f} MB): {audio_file.name}")




# ---------------------------------------------------------------------------
# Step update — writes to TaskStore + publishes events
# ---------------------------------------------------------------------------

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
        logger.info("Voiceprint: no speaker labels in segments, skipping")
        return recognition_segments

    from app.services.recognition import get_asr_service
    service = get_asr_service()
    pipeline_obj = service.get_pyannote_pipeline() if hasattr(service, "get_pyannote_pipeline") else None
    if pipeline_obj is None:
        logger.info("Voiceprint: pyannote pipeline not loaded, skipping")
        return recognition_segments

    diarize_df, audio_path = service.get_last_diarization() if hasattr(service, "get_last_diarization") else (None, None)
    if diarize_df is None or audio_path is None:
        logger.info("Voiceprint: no cached diarization, skipping")
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
        logger.info("Voiceprint: no voiceprints extracted, skipping")
        return recognition_segments

    resolutions = resolve_speakers(
        task_id=str(task.id),
        voiceprints=voiceprints,
        store=store,
        match_threshold=float(getattr(rt, "voiceprint_match_threshold", 0.75)),
        suggest_threshold=float(getattr(rt, "voiceprint_suggest_threshold", 0.60)),
    )
    recognition_segments = apply_to_segments(recognition_segments, resolutions)
    logger.info(f"Voiceprint: resolved {len(resolutions)} speaker(s)")
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
    from app.services.analysis import polish_text, summarize_text, generate_mindmap, analyze_content

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
        srt_path = task_dir / "transcript.srt"
        srt_path.write_text(srt, encoding="utf-8")
        await _emit_file_ready(task, "transcript.srt", str(srt_path))
    if polished:
        polished_srt_path = task_dir / "transcript_polished.srt"
        polished_srt_path.write_text(polished, encoding="utf-8")
        await _emit_file_ready(task, "transcript_polished.srt", str(polished_srt_path))
        if polished_md:
            polished_md_path = task_dir / "transcript_polished.md"
            polished_md_path.write_text(polished_md, encoding="utf-8")

    await _update_step(task, PipelineStep.TRANSCRIBE, completed=True)

    # -- VOICEPRINT: platform subtitles have no diarization, mark skipped --
    await _update_step(task, PipelineStep.VOICEPRINT, completed=True)

    # Guard: skip LLM if transcript is empty
    if not transcript or len(transcript.strip()) < 10:
        logger.warning(f"Fast path: transcript too short ({len(transcript)} chars), skipping LLM")
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

    analysis = await analyze_content(analysis_text, metadata.title, metadata=video_metadata)
    await _raise_if_cancelled(task.id)
    user_language = _user_language_hint(analysis)

    # Write analysis first so the frontend can surface language/topics early
    import json as _json
    if analysis:
        analysis_path = task_dir / "analysis.json"
        analysis_path.write_text(_json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
        await _emit_file_ready(task, "analysis.json", str(analysis_path))

    summary, mindmap = await asyncio.gather(
        summarize_text(analysis_text, user_language=user_language),
        generate_mindmap(mindmap_text, metadata=mindmap_metadata, user_language=user_language),
    )
    await _raise_if_cancelled(task.id)

    if summary:
        await _write_summary_files(task, task_dir, metadata, summary)
    if mindmap:
        mm_path = task_dir / "mindmap.md"
        mm_path.write_text(mindmap, encoding="utf-8")
        await _emit_file_ready(task, "mindmap.md", str(mm_path))

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
    from app.services.ingestion import download_media
    from app.services.ingestion.ytdlp import download_subtitles
    from app.services.ingestion.local import find_local_subtitle, parse_nfo
    from app.services.preprocessing import separate_vocals
    from app.services.recognition import transcribe_audio
    from app.services.recognition.subtitle_processor import process_subtitles
    from app.services.analysis import polish_text, summarize_text, generate_mindmap, analyze_content
    from app.services.archiving import archive_result
    from app.core.queue import get_task_queue

    rt = get_runtime_settings()
    source = _clean_source_path(task.source)
    platform_subtitle = None
    source_type = _detect_source_type(source)
    use_platform_subtitles = (
        _platform_prefer_subtitles(source_type)
        and not task.options.get("force_asr", False)
    )

    # Resolve pre-created task dir
    task_dir = None
    if task.result and task.result.get("output_dir"):
        candidate = Path(task.result["output_dir"])
        if candidate.exists():
            task_dir = candidate

    done = set(task.completed_steps or [])
    logger.info(f"starting pipeline, already done: {done}")
    await _raise_if_cancelled(task.id)

    # Variables that later steps depend on — populated either by running the
    # step or by reading back files written in a previous run.
    audio_path: str | None = None
    vocals_path: str | None = None
    metadata: "MediaMetadata | None" = None
    has_subtitle: bool = False
    srt: str = ""
    transcript: str = ""
    polished: str | None = None
    polished_md: str | None = None
    subtitle_source: str = "asr"
    recognition_segments: list = []
    analysis: dict = {}
    summary: dict = {}
    mindmap: str = ""

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
            logger.warning(f"Failed to restore metadata: {e}")
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
        logger.info(f"skipping DOWNLOAD (already done), restoring from disk")
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
            logger.info(f"fast-path resume — re-downloading video")
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
                    file_path=str(dest_source),
                )

                # Search for local subtitle and NFO metadata
                # For browser uploads source_path == dest_source (no original dir to search)
                if not is_browser_upload and use_platform_subtitles:
                    platform_subtitle = find_local_subtitle(source_path)
                    if platform_subtitle:
                        logger.info(f"Found local subtitle: {platform_subtitle['subtitle_path']}")

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

            elif dest_source.suffix.lower() in audio_exts:
                audio_path = str(dest_source)
                metadata = MediaMetadata(
                    title=title,
                    source_url=str(source_path),
                    media_type="audio",
                    file_path=str(dest_source),
                )
            else:
                raise ValueError(f"Unsupported file format: {dest_source.suffix}")

            has_subtitle = platform_subtitle is not None

            # Write metadata.json immediately after local file processing
            meta_path = write_metadata_json(task_dir, metadata, status="processing")
            await _emit_file_ready(task, "metadata.json", str(meta_path))

        else:
            # ── URL source: probe metadata + subtitle first ──
            # 1. Resolve title for task_dir naming
            if source_type == "bilibili":
                bv_match = re.search(r'(BV[0-9A-Za-z]+)', source)
                title = bv_match.group(1) if bv_match else None
            elif source_type == "youtube":
                yt_match = re.search(r'(?:v=|youtu\.be/)([\w-]{11})', source)
                title = yt_match.group(1) if yt_match else None
                if not title:
                    import yt_dlp
                    from app.services.ingestion.ytdlp import ytdlp_auth_opts
                    with yt_dlp.YoutubeDL({"quiet": True, **ytdlp_auth_opts()}) as ydl:
                        info = ydl.extract_info(source, download=False)
                        title = info.get("title", "unknown") if info else "unknown"
            else:
                import yt_dlp
                from app.services.ingestion.ytdlp import ytdlp_auth_opts
                with yt_dlp.YoutubeDL({"quiet": True, **ytdlp_auth_opts()}) as ydl:
                    info = ydl.extract_info(source, download=False)
                    title = info.get("title", "unknown") if info else "unknown"

            if not task_dir:
                task_dir = create_task_dir(task.id, title or str(task.id)[:8])

            # 2. Decide whether to attempt fast path
            force_asr = rt.force_asr or task.options.get("force_asr", False)

            if use_platform_subtitles and not force_asr:
                # Probe: fetch metadata + subtitle (lightweight, no video download)
                from app.services.ingestion.ytdlp import fetch_metadata as _fetch_meta
                try:
                    probe_metadata = await _fetch_meta(source)
                except Exception as e:
                    logger.warning(f"Metadata probe failed: {e}, falling back to full pipeline")
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
                        logger.warning(f"Subtitle probe failed: {e}")
                        probe_subtitle = None

                if probe_metadata and probe_subtitle:
                    # ── FAST PATH: subtitle + video download in parallel ──
                    logger.info(f"fast path — subtitle found, running parallel")
                    metadata = probe_metadata

                    # Rename task_dir to real title
                    real_title = metadata.title
                    if real_title and task_dir.name != _sanitize_filename(real_title):
                        new_dir = task_dir.parent / _sanitize_filename(real_title)
                        if not new_dir.exists():
                            task_dir.rename(new_dir)
                            task_dir = new_dir
                            # Update all subtitle paths after rename: tracks[].path + back-compat subtitle_path
                            new_sub_dir = task_dir / "subtitles"
                            for tr in probe_subtitle.get("tracks") or []:
                                if tr.get("path"):
                                    tr["path"] = str(new_sub_dir / Path(tr["path"]).name)
                            if probe_subtitle.get("subtitle_path"):
                                old_sub_path = Path(probe_subtitle["subtitle_path"])
                                probe_subtitle["subtitle_path"] = str(new_sub_dir / old_sub_path.name)
                            logger.info(f"Renamed task dir to: {new_dir}")
                        else:
                            logger.warning(f"Cannot rename to {new_dir} (already exists), keeping {task_dir}")

                    logger.info(f"Downloaded platform subtitle: {probe_subtitle['subtitle_path']}")

                    # Write metadata.json
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
                        logger.warning(
                            f"Video download branch failed (transcript still OK): "
                            f"{type(video_result).__name__}: {video_result}"
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
            if real_title and task_dir.name != _sanitize_filename(real_title):
                new_dir = task_dir.parent / _sanitize_filename(real_title)
                if not new_dir.exists():
                    task_dir.rename(new_dir)
                    task_dir = new_dir
                    if audio_path:
                        audio_path = str(new_dir / Path(audio_path).name)
                    if metadata.file_path:
                        metadata.file_path = str(new_dir / Path(metadata.file_path).name)
                    logger.info(f"Renamed task dir to: {new_dir}")
                else:
                    logger.warning(f"Cannot rename to {new_dir} (already exists), keeping {task_dir}")

            # Try to download platform subtitles (for full pipeline, still useful)
            if use_platform_subtitles:
                try:
                    sub_dir = task_dir / "subtitles"
                    platform_subtitle = await download_subtitles(source, sub_dir)
                    if platform_subtitle.get("subtitle_path"):
                        logger.info(f"Downloaded platform subtitle: {platform_subtitle['subtitle_path']}")
                    else:
                        metadata.extra["subtitle_engine"] = platform_subtitle.get("subtitle_engine")
                        metadata.extra["subtitle_diagnostics"] = platform_subtitle.get("diagnostics") or []
                        platform_subtitle = None
                        if sub_dir.exists() and not any(sub_dir.iterdir()):
                            sub_dir.rmdir()
                except Exception as e:
                    logger.warning(f"Subtitle download failed: {e}")
                    platform_subtitle = None

            has_subtitle = platform_subtitle is not None

            # Write metadata.json immediately after download
            meta_path = write_metadata_json(task_dir, metadata, status="processing")
            await _emit_file_ready(task, "metadata.json", str(meta_path))

        await _update_step(task, PipelineStep.DOWNLOAD, completed=True)
        await _raise_if_cancelled(task.id)
    # end if DOWNLOAD not in done

    # Sanity: we must have a task_dir by now
    if task_dir is None or metadata is None:
        raise RuntimeError("task_dir or metadata missing after DOWNLOAD step — cannot continue")

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
        logger.info(f"skipping SEPARATE+TRANSCRIBE (already done), restoring transcript")
        _restore_transcript()
        _restore_audio_paths()
    else:
        async with gpu_sem:
            logger.info(f"acquired GPU semaphore")

            # Step 2: Separate vocals
            if PipelineStep.SEPARATE in done:
                logger.info(f"skipping SEPARATE, restoring audio paths")
                _restore_audio_paths()
            else:
                await _update_step(task, PipelineStep.SEPARATE)
                skip_separation = task.options.get("skip_separation", False) or has_subtitle
                if skip_separation:
                    vocals_path = audio_path
                else:
                    preprocess = await separate_vocals(audio_path, output_dir=task_dir)
                    await _raise_if_cancelled(task.id)
                    vocals_path = preprocess.get("vocals_path", audio_path)
                await _update_step(task, PipelineStep.SEPARATE, completed=True)

            # Step 3: Transcribe
            if PipelineStep.TRANSCRIBE in done:
                logger.info(f"skipping TRANSCRIBE, restoring transcript")
                _restore_transcript()
            else:
                await _update_step(task, PipelineStep.TRANSCRIBE)
                if has_subtitle:
                    logger.info("Using platform subtitle path (skipping ASR)")
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
                    recognition = await transcribe_audio(vocals_path, output_dir=task_dir, num_speakers=num_speakers)
                    await _raise_if_cancelled(task.id)
                    transcript = " ".join(s["text"] for s in recognition.get("segments", []))
                    srt = recognition.get("srt", "")
                    polished = None
                    polished_md = None
                    subtitle_source = "asr"
                    recognition_segments = recognition.get("segments", [])

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
                            logger.warning(f"ASR language detection failed: {e}")

                # Write transcript.srt immediately
                if srt:
                    srt_path = task_dir / "transcript.srt"
                    srt_path.write_text(srt, encoding="utf-8")
                    await _emit_file_ready(task, "transcript.srt", str(srt_path))
                if has_subtitle and polished:
                    polished_srt_path = task_dir / "transcript_polished.srt"
                    polished_srt_path.write_text(polished, encoding="utf-8")
                    await _emit_file_ready(task, "transcript_polished.srt", str(polished_srt_path))
                    if polished_md:
                        polished_md_path = task_dir / "transcript_polished.md"
                        polished_md_path.write_text(polished_md, encoding="utf-8")

                await _update_step(task, PipelineStep.TRANSCRIBE, completed=True)
                await _raise_if_cancelled(task.id)
            # end if TRANSCRIBE not in done

            # ── Voiceprint step: run while vocals files are still on disk ──
            # Must run inside the GPU semaphore block (pyannote pipeline is loaded),
            # and before _cleanup_vocals() wipes the WAV files we just transcribed.
            if PipelineStep.VOICEPRINT not in done:
                await _update_step(task, PipelineStep.VOICEPRINT)
                try:
                    recognition_segments = await _run_voiceprint_step(
                        task=task,
                        recognition_segments=recognition_segments,
                        task_dir=task_dir,
                    )
                    # Rewrite SRT so downstream consumers see canonical names
                    if recognition_segments and subtitle_source == "asr":
                        from app.services.recognition import get_asr_service
                        service = get_asr_service()
                        if hasattr(service, "to_srt"):
                            from app.models import TranscriptSegment
                            segs_models = [
                                TranscriptSegment(**{k: v for k, v in s.items() if k in {"start", "end", "text", "speaker"}})
                                for s in recognition_segments
                            ]
                            new_srt = service.to_srt(segs_models)
                            if new_srt:
                                srt = new_srt
                                srt_path = task_dir / "transcript.srt"
                                srt_path.write_text(srt, encoding="utf-8")
                                await _emit_file_ready(task, "transcript.srt", str(srt_path))
                except Exception as e:
                    logger.warning(f"Voiceprint step failed (non-fatal): {e}", exc_info=True)
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
        logger.warning(f"Transcript is empty or too short ({len(transcript)} chars), skipping LLM analysis")
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
        logger.info(f"skipping POLISH, restoring from disk")
        _restore_transcript()  # picks up polished if present
    else:
        await _update_step(task, PipelineStep.POLISH)
        if has_subtitle:
            logger.info("Skipping POLISH step (platform subtitle already polished)")
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
            polished_srt_path = task_dir / "transcript_polished.srt"
            polished_srt_path.write_text(polished, encoding="utf-8")
            await _emit_file_ready(task, "transcript_polished.srt", str(polished_srt_path))
            polished_md_content = srt_to_markdown(polished, metadata.title)
            polished_md_path = task_dir / "transcript_polished.md"
            polished_md_path.write_text(polished_md_content, encoding="utf-8")
            polish_ran = True
        await _update_step(task, PipelineStep.POLISH, completed=True)
        await _raise_if_cancelled(task.id)
    # end if POLISH not in done

    # ── Step 5: Analyze + Summarize + Mindmap from polished text ─────────────
    # If an older interrupted task already completed ANALYZE before POLISH,
    # regenerate analysis outputs now so summary/mindmap reflect the polished SRT.
    if PipelineStep.ANALYZE in done and not polish_ran:
        logger.info(f"skipping ANALYZE, restoring from disk")
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

        summary, mindmap = await asyncio.gather(
            summarize_text(analysis_text, user_language=user_language),
            generate_mindmap(mindmap_text, metadata=mindmap_metadata, user_language=user_language),
        )
        await _raise_if_cancelled(task.id)

        if summary:
            await _write_summary_files(task, task_dir, metadata, summary)
        if mindmap:
            mm_path = task_dir / "mindmap.md"
            mm_path.write_text(mindmap, encoding="utf-8")
            await _emit_file_ready(task, "mindmap.md", str(mm_path))

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
        store.update_status(task_id, TaskStatus.PROCESSING)
        await bus.publish(TaskEvent(task_id, "processing"))

    # Re-read from DB to get latest completed_steps
    task = store.get(task_id)

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

        store.update_status(
            task_id,
            TaskStatus.COMPLETED,
            progress=1.0,
            result=task.result,
            completed_at=task.completed_at,
        )
        await bus.publish(TaskEvent(task_id, "completed", {
            "output_dir": task.result.get("output_dir") if task.result else None,
        }))

    except asyncio.CancelledError:
        logger.info(f"Task {task_id} cancelled")
        current = store.get(task_id) or task
        output_dir = current.result.get("output_dir") if current.result else None
        update_metadata_status(Path(output_dir) if output_dir else None, "cancelled")
        store.update_status(
            task_id,
            TaskStatus.CANCELLED,
            completed_at=datetime.now(),
            message="已取消",
        )
        await bus.publish(TaskEvent(task_id, "cancelled"))
        raise

    except Exception as e:
        logger.exception(f"Task {task_id} failed")
        task.status = TaskStatus.FAILED
        task.error = str(e)

        # Update metadata.json status to failed
        current = store.get(task_id) or task
        output_dir = current.result.get("output_dir") if current.result else None
        update_metadata_status(Path(output_dir) if output_dir else None, "failed")

        store.update_status(
            task_id,
            TaskStatus.FAILED,
            error=str(e),
            completed_at=datetime.now(),
        )
        await bus.publish(TaskEvent(task_id, "failed", {"error": str(e)}))

    finally:
        # Offload local GGUF model after each task to free VRAM.
        # No-op when using API providers.
        if not _download_worker_call:
            from app.services.analysis.llm import offload_local_llm
            offload_local_llm()
