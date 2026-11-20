"""Validate subtitle choices and retain them across download/processing handoffs."""

import json
import logging
from pathlib import Path

from app.core.logging_setup import log_event
from app.core.pipeline_steps.artifacts import _write_text_artifact
from app.core.pipeline_steps.state import _emit_timeline_event
from app.services.recognition.subtitle_processor import parse_subtitle_file
from app.services.recognition.subtitle_validation import validate_subtitle_timing

logger = logging.getLogger(__name__)


def subtitle_tracks(subtitle: dict) -> list[dict]:
    if subtitle.get("tracks"):
        return subtitle["tracks"]
    if subtitle.get("subtitle_path"):
        return [
            {
                "path": subtitle["subtitle_path"],
                "lang": subtitle.get("subtitle_lang") or "unknown",
                "format": subtitle.get("subtitle_format") or "srt",
                "type": "cc",
            }
        ]
    return []


def _with_tracks(subtitle: dict, tracks: list[dict]) -> dict | None:
    if not tracks:
        return None
    first = tracks[0]
    return {
        **subtitle,
        "tracks": tracks,
        "subtitle_path": first["path"],
        "subtitle_lang": first.get("lang", "unknown"),
        "subtitle_format": first.get("format", "srt"),
    }


async def validate_platform_subtitles(task, task_dir: Path, subtitle: dict | None, metadata):
    if subtitle:
        metadata.extra["subtitle_engine"] = subtitle.get("subtitle_engine")
        metadata.extra["subtitle_diagnostics"] = subtitle.get("diagnostics") or []
    if not subtitle or not subtitle_tracks(subtitle):
        return None
    accepted, checks = [], []
    diagnostics = list(subtitle.get("diagnostics") or [])
    for track in subtitle_tracks(subtitle):
        try:
            segments = parse_subtitle_file(track["path"], track.get("format") or "srt")
            check = validate_subtitle_timing(segments, metadata.duration_seconds)
        except (ValueError, OSError, TypeError, KeyError) as exc:
            check = {
                "valid": False,
                "status": "rejected",
                "reason": "parse_failed",
                "error": str(exc),
            }
        descriptor = {key: value for key, value in track.items() if key != "path"}
        descriptor["filename"] = Path(track["path"]).name
        checks.append({**descriptor, "timing": check})
        if check["valid"]:
            accepted.append(
                {**track, "validation": {**(track.get("validation") or {}), "timing": check}}
            )
        else:
            diagnostics.append({"stage": "validate_duration", "lang": track.get("lang"), **check})

    report = {
        "status": "accepted" if accepted else "rejected",
        "video_duration": metadata.duration_seconds,
        "subtitle_engine": subtitle.get("subtitle_engine"),
        "tracks": checks,
        "diagnostics": diagnostics,
    }
    metadata.extra["subtitle_validation"] = report
    metadata.extra["subtitle_diagnostics"] = diagnostics
    await _write_text_artifact(
        task,
        task_dir,
        "subtitle_validation.json",
        json.dumps(report, ensure_ascii=False, indent=2),
    )
    log_event(
        logger,
        logging.INFO if accepted else logging.WARNING,
        "subtitle.duration_validated",
        accepted_tracks=len(accepted),
        rejected_tracks=len(checks) - len(accepted),
        video_duration=metadata.duration_seconds,
    )
    if not accepted:
        await _emit_timeline_event(
            task,
            "subtitle.duration_fallback",
            stage="subtitle",
            step_id="subtitle_probe",
            level="warning",
            message="字幕时间范围校验未通过，转入音频识别流程",
            data={"validation": report},
        )
    return _with_tracks({**subtitle, "diagnostics": diagnostics}, accepted)


def restore_validated_subtitles(task_dir: Path) -> tuple[bool, dict | None]:
    path = task_dir / "subtitle_validation.json"
    if not path.exists():
        return False, None
    report = json.loads(path.read_text(encoding="utf-8"))
    tracks = []
    for item in report.get("tracks", []):
        file = task_dir / "subtitles" / Path(item["filename"]).name
        if item["timing"]["valid"] and file.is_file():
            tracks.append(
                {
                    **{
                        key: value
                        for key, value in item.items()
                        if key not in {"filename", "timing"}
                    },
                    "path": str(file),
                }
            )
    return True, _with_tracks(
        {
            "subtitle_engine": report.get("subtitle_engine"),
            "diagnostics": report.get("diagnostics") or [],
        },
        tracks,
    )


def restore_checkpoint_srt(task_dir: Path, metadata: dict) -> str | None:
    from app.core.paths import managed_child
    from app.services.recognition.subtitle_processor import _segments_to_original_srt

    validated, subtitle = restore_validated_subtitles(task_dir)
    extra = metadata.get("extra") or {}
    archived_tracks = [
        {**track, "path": str(task_dir / Path(track["filename"]).name)}
        for track in extra.get("subtitle_tracks", [])
        if track.get("filename") and track.get("type") != "asr"
    ]
    selected_lang = extra.get("selected_subtitle_lang") or next(
        (track.get("lang") for track in archived_tracks if track.get("polished")), None,
    )
    tracks = [*subtitle_tracks(subtitle or {}), *archived_tracks]
    if not tracks and not validated:
        tracks = [
            {"path": str(path), "format": path.suffix[1:]}
            for path in sorted((task_dir / "subtitles").glob("*"))
            if path.suffix.lower() in {".srt", ".vtt", ".json3"}
        ]
    if selected_lang:
        tracks = [track for track in tracks if track.get("lang") == selected_lang]
    for track in tracks:
        path = managed_child(track["path"], task_dir)
        if path.is_file():
            fmt = track.get("format") or "srt"
            if fmt == "srt":
                with path.open(encoding="utf-8-sig") as stream:
                    header = stream.read(256).lstrip()
                if header.startswith("{"):
                    fmt = "json3"
                elif header.startswith("WEBVTT"):
                    fmt = "vtt"
            if fmt not in {"srt", "json3", "vtt"}:
                continue
            segments = parse_subtitle_file(str(path), fmt)
            if segments:
                return _segments_to_original_srt(segments)
    return None


def has_checkpoint_cues(content: str | None) -> bool:
    from app.services.analysis.transcript_outputs import (
        _parse_srt,
        _timestamp_bounds,
        _timestamp_to_seconds,
    )

    normalized = (content or "").removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    for cue in _parse_srt(normalized):
        start, end = map(_timestamp_to_seconds, _timestamp_bounds(cue["timestamp"]))
        if start is not None and end is not None and end > start and cue["text"].strip():
            return True
    return False
