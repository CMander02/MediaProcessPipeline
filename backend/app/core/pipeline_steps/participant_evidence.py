"""Collect joint visual and transcript evidence before speaker assignment."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from pathlib import Path

from app.core.atomic_file import atomic_write_text
from app.core.pipeline_steps.state import PipelineStep, _raise_if_cancelled
from app.services.analysis.participant_evidence import extract_identity_evidence, frame_times

logger = logging.getLogger(__name__)


def _extract_frame(video: Path, directory: Path, timestamp: float) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    identifier = f"frame:{timestamp:.2f}"
    path = directory / f"frame-{timestamp:.2f}.jpg"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-protocol_whitelist", "file,pipe,crypto,data", "-ss", str(timestamp),
         "-i", str(video), "-frames:v", "1", "-vf", "scale=1280:-2", str(path)],
        check=True, timeout=60, capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return {"id": identifier, "time": timestamp, "path": str(path)}


async def prepare_participant_evidence(ctx) -> None:
    if PipelineStep.POLISH in ctx.done or PipelineStep.ANALYZE in ctx.done:
        return
    metadata = ctx.metadata
    video = Path(metadata.file_path or "")
    if not video.is_file() or video.suffix.lower() not in {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}:
        return
    duration = float(metadata.duration_seconds or 0)
    source = {
        "platform": str(metadata.platform or ""), "uploader": metadata.uploader or "",
        "title": metadata.title or "", "description": metadata.description or "",
        "classification": ctx.source_context.get("content_type", ""),
    }
    candidate = re.search(r"播客|访谈|采访|对谈|圆桌|podcast|interview|panel", " ".join(source.values()), re.I)
    # Long videos trigger inspection, not an assumption that they are podcasts.
    if not candidate and duration < 1800 and not ctx.task.options.get("participant_evidence"):
        return
    stat = video.stat()
    from app.core.model_router import resolve_vlm_binding
    from app.services.analysis.participant_evidence import PARTICIPANT_PROMPT
    binding = resolve_vlm_binding(ctx.rt)
    inputs = {"version": 1, "video": str(video), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
              "duration": duration, "metadata": source, "srt": ctx.srt,
              "model": binding.model, "endpoint": binding.api_base, "prompt": PARTICIPANT_PROMPT,
              "parameters": {key: value for key, value in binding.request_kwargs.items() if key != "api_key"},
              "user_speaker_count": ctx.task.options.get("num_speakers")}
    path = ctx.task_dir / "participant_evidence.json"
    report = None
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("inputs") == inputs and saved.get("status") == "completed":
            report = saved
    except (OSError, ValueError, AttributeError):
        pass
    if report is None:
        from app.core.queue import _release_local_llm_runtime, _release_transcription_models
        from app.services.analysis.vlm import get_vlm_service

        await _raise_if_cancelled(ctx.task.id)
        await asyncio.to_thread(_release_transcription_models)
        try:
            evidence = await extract_identity_evidence(ctx.srt, ctx.task_dir / "identity_evidence.checkpoint.json")
            payload = {"metadata": source, "evidence": evidence["evidence"],
                       "acoustic_speakers": sorted({segment.get("speaker") for segment in ctx.recognition_segments if segment.get("speaker") and segment["speaker"].casefold() != "unknown"}),
                       "user_speaker_count": inputs["user_speaker_count"]}
            frames = []
            for timestamp in frame_times(duration):
                await _raise_if_cancelled(ctx.task.id)
                frames.append(await asyncio.to_thread(_extract_frame, video, ctx.task_dir / "participant_frames", timestamp))
            report = await asyncio.to_thread(get_vlm_service().describe_participants, frames, payload)
            unusable_intro = frames[0]["id"] in report["unusable_frames"]
            more = frame_times(duration, replace_intro=unusable_intro,
                               supplement=bool(report["conflicts"] or report["uncertainties"] or not report["roster_complete"]))
            for timestamp in more:
                if timestamp not in {f["time"] for f in frames}:
                    await _raise_if_cancelled(ctx.task.id)
                    frames.append(await asyncio.to_thread(_extract_frame, video, ctx.task_dir / "participant_frames", timestamp))
            if len(frames) > len(report["images"]):
                report = await asyncio.to_thread(get_vlm_service().describe_participants, frames, payload)
            if evidence["status"] != "completed":
                report["status"] = "partial"
                report["uncertainties"].append("全文身份依据提取有未完成批次")
            report["text_evidence"] = evidence
        except Exception as exc:
            logger.warning("参与者联合分析未完成：%s", exc)
            report = {"status": "failed", "error": str(exc), "participants": []}
        finally:
            await asyncio.to_thread(_release_local_llm_runtime)
        report["inputs"] = inputs
        atomic_write_text(path, json.dumps(report, ensure_ascii=False, indent=2))
    await _raise_if_cancelled(ctx.task.id)
    # Candidate presence and voice binding are separate facts.
    ctx.source_context["participant_evidence"] = {
        key: report.get(key) for key in (
            "status", "participants", "count_estimate", "roster_complete", "conflicts", "uncertainties"
        )
    }
    ctx.metadata.extra["participant_evidence"] = ctx.source_context["participant_evidence"]
