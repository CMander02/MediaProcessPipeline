"""Context responsibilities for the media pipeline."""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.database import get_task_store
from app.core.logging_setup import log_event
from app.core.pipeline_steps.sources import (
    _clean_source_path,
    _detect_source_type,
    _platform_prefer_subtitles,
)
from app.core.pipeline_steps.state import _raise_if_cancelled, _set_task_flow, _update_flow_step
from app.core.pipeline_steps.transcript import _plain_text_from_srt
from app.core.source_resolver import SourceFlow, resolve_source_flow
from app.models import MediaMetadata, Task

logger = logging.getLogger(__name__)

from dataclasses import dataclass, field

from app.core.settings import RuntimeSettings


@dataclass
class PipelineContext:
    task: Task
    rt: RuntimeSettings
    download_worker_call: bool = False
    stop_after_transcribe: bool = False
    source: str = ""
    source_type: str = ""
    initial_flow: SourceFlow | None = None
    source_flow: SourceFlow | None = None
    route_type: str = ""
    force_asr: bool = False
    use_platform_subtitles: bool = False
    collect_platform_subtitles: bool = False
    subtitle_reference_mode: bool = False
    task_dir: Path | None = None
    done: set = field(default_factory=set)
    audio_path: str | None = None
    vocals_path: str | None = None
    metadata: MediaMetadata | None = None
    has_subtitle: bool = False
    uvr_fallback_reason: str | None = None
    srt: str = ""
    transcript: str = ""
    polished: str | None = None
    polished_md: str | None = None
    subtitle_source: str = "asr"
    recognition_segments: list = field(default_factory=list)
    subtitle_reference_segments: list = field(default_factory=list)
    analysis: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    mindmap: str = ""
    detail: str = ""
    source_context: dict = field(default_factory=dict)
    platform_subtitle: dict | None = None

    def restore_metadata(self) -> bool:
        """Read metadata.json back from disk into `metadata`. Returns True on success."""
        if self.task_dir is None:
            return False
        meta_path = self.task_dir / "metadata.json"
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
            raw.setdefault("title", self.task_dir.name)
            self.metadata = MediaMetadata.model_validate(raw)
            return True
        except Exception as e:
            log_event(logger, logging.WARNING, "pipeline.restore.metadata_failed", error=e)
            return False

    def restore_audio_paths(self) -> bool:
        """Find audio/vocals files on disk. Returns True if usable paths found."""
        if self.task_dir is None:
            return False
        # Vocals (post-UVR)
        for candidate in self.task_dir.glob("vocals*.wav"):
            self.vocals_path = str(candidate)
            self.audio_path = self.vocals_path
            return True
        # Raw extracted audio
        for candidate in self.task_dir.glob("*.wav"):
            self.audio_path = str(candidate)
            self.vocals_path = self.audio_path
            return True
        # Original audio (mp3/m4a/etc.)
        for f in self.task_dir.iterdir():
            if f.is_file() and f.suffix.lower() in {".mp3", ".flac", ".m4a", ".ogg"}:
                self.audio_path = str(f)
                self.vocals_path = self.audio_path
                return True
        return False

    def restore_transcript(self) -> bool:
        """Read transcript SRT back from disk. Returns True if found."""
        if self.task_dir is None:
            return False
        polished_path = self.task_dir / "transcript_polished.srt"
        raw_path = self.task_dir / "transcript.srt"
        if polished_path.exists():
            self.polished = polished_path.read_text(encoding="utf-8")
            self.subtitle_source = "asr"
        if raw_path.exists():
            self.srt = raw_path.read_text(encoding="utf-8")
            self.transcript = _plain_text_from_srt(self.srt)
            return True
        return bool(self.polished)

    def restore_analysis(self) -> bool:
        if self.task_dir is None:
            return False
        path = self.task_dir / "analysis.json"
        if path.exists():
            try:
                import json as _j

                self.analysis = _j.loads(path.read_text(encoding="utf-8"))
                return True
            except Exception:
                pass
        return False

    def restore_summary(self) -> bool:
        if self.task_dir is None:
            return False
        path = self.task_dir / "summary.json"
        if path.exists():
            try:
                import json as _j

                loaded = _j.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.summary = loaded
                    return True
            except Exception:
                pass
        # Backward compatibility for old archives that only have rendered markdown.
        return (self.task_dir / "summary.md").exists()

    def restore_mindmap(self) -> bool:
        if self.task_dir is None:
            return False
        path = self.task_dir / "mindmap.md"
        if path.exists():
            self.mindmap = path.read_text(encoding="utf-8")
            return True
        return False


async def create_context(task, rt, download_worker_call=False, stop_after_transcribe=False):
    from app.services.ingestion.ytdlp import (
        normalize_bilibili_source_url,
    )

    ctx = PipelineContext(
        task=task,
        rt=rt,
        download_worker_call=download_worker_call,
        stop_after_transcribe=stop_after_transcribe,
    )
    ctx.source = normalize_bilibili_source_url(_clean_source_path(ctx.task.source))
    if ctx.source != ctx.task.source:
        ctx.task.source = ctx.source
        get_task_store().save(ctx.task)
    ctx.platform_subtitle = None
    ctx.source_type = _detect_source_type(ctx.source)
    ctx.force_asr = bool(ctx.rt.force_asr or ctx.task.options.get("force_asr", False))
    ctx.initial_flow = resolve_source_flow(
        ctx.source,
        prefer_platform_subtitles=True,
        force_asr=ctx.force_asr,
        task_options=ctx.task.options,
    )
    ctx.use_platform_subtitles = (
        ctx.initial_flow.try_subtitles
        and _platform_prefer_subtitles(ctx.initial_flow.route_type)
        and not ctx.force_asr
    )
    ctx.source_flow = resolve_source_flow(
        ctx.source,
        prefer_platform_subtitles=ctx.use_platform_subtitles,
        force_asr=ctx.force_asr,
        task_options=ctx.task.options,
    )
    ctx.route_type = ctx.source_flow.route_type

    # Resolve pre-created task dir
    ctx.task_dir = None
    if ctx.task.result and ctx.task.result.get("output_dir"):
        candidate = Path(ctx.task.result["output_dir"])
        if candidate.exists():
            ctx.task_dir = candidate

    ctx.done = set(ctx.task.completed_steps or [])
    log_event(
        logger,
        logging.INFO,
        "pipeline.started",
        source_type=ctx.source_type,
        platform=ctx.source_flow.platform,
        flow_id=ctx.source_flow.flow_id,
        completed_steps=",".join(sorted(str(s) for s in ctx.done)) or "none",
        download_worker_call=ctx.download_worker_call,
    )
    await _set_task_flow(
        ctx.task,
        ctx.source_flow,
        status="processing",
        current_step=(ctx.task.flow or {}).get("current_step") or "resolve",
    )
    if "resolve" not in ((ctx.task.flow or {}).get("completed_steps") or []):
        await _update_flow_step(ctx.task, "resolve", completed=True, message="来源已识别")
    await _raise_if_cancelled(ctx.task.id)

    return ctx
