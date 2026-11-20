"""Enroll a voiceprint from a stored embedding or a local audio interval."""

from __future__ import annotations

import logging
import math
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID, uuid4

import numpy as np

from app.core.database import get_task_store
from app.core.media_retention import MANIFEST, MEDIA_SUFFIXES
from app.core.paths import get_workspace_paths, managed_child
from app.core.workspace_lifecycle import run_in_thread
from app.services.analysis.speaker_cli_profile import (
    cue_interval,
    read_task_json,
    read_task_transcript,
    transcript_cues,
    validate_speaker_name,
)
from app.services.analysis.speaker_repair import repair_directory
from app.services.analysis.transcript_outputs import _split_speaker_prefix
from app.services.recognition import get_diarization_service
from app.services.voiceprint.extractor import (
    MIN_EMBED_SECONDS,
    ExtractedVoiceprint,
    extract_voiceprints,
)
from app.services.voiceprint.store import Person, VoiceprintStore, get_voiceprint_store

logger = logging.getLogger(__name__)


class EnrollmentConflict(ValueError):
    pass


class EnrollmentUnavailable(ValueError):
    pass


def _task_audio(task_id: UUID, directory: Path) -> Path:
    metadata = read_task_json(task_id, directory, "metadata.json")
    extra = metadata.get("extra") or {}
    assets = read_task_json(task_id, directory, MANIFEST).get("assets") or {}
    ordered = [extra.get("uvr_vocals_file"), metadata.get("file_path")]
    listed = [directory / Path(value).name for value in ordered if value]
    priority = {"separated": 0, "working": 1, "source": 2, "playback": 3}
    listed.extend(
        directory / name
        for name, entry in sorted(
            assets.items(),
            key=lambda item: priority.get(item[1].get("role"), 4),
        )
        if entry.get("role") != "segment"
    )
    listed.extend(
        sorted(directory.iterdir(), key=lambda path: (path.suffix.lower() != ".wav", path))
    )
    for candidate in listed:
        try:
            path = managed_child(candidate, directory)
        except ValueError:
            continue
        if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES:
            relative = path.relative_to(directory).as_posix()
            if (assets.get(relative) or {}).get("role") != "segment":
                return path
    raise EnrollmentConflict("任务没有可用音频或媒体文件，原始媒体可能已清理")


def _task_intervals(task_id: UUID, directory: Path, labels: set[str]) -> list[tuple[float, float]]:
    _, srt = read_task_transcript(task_id, directory)
    intervals = []
    for cue in transcript_cues(srt):
        if _split_speaker_prefix(cue["text"])[0] in labels:
            interval = cue_interval(cue["timestamp"])
            if interval is None:
                raise ValueError(f"字幕片段 #{cue['index']} 的时间范围无效")
            intervals.append(interval)
    if not intervals:
        raise ValueError("当前字幕找不到该人物的发言")
    return intervals


def _clip_intervals(
    intervals: list[tuple[float, float | None]],
    duration: float,
) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals, key=lambda span: span[0]):
        end = min(end, duration) if end is not None else duration
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    if sum(end - start for start, end in merged) < MIN_EMBED_SECONDS:
        raise ValueError(f"可用语音少于 {MIN_EMBED_SECONDS:g} 秒")
    return merged


def _readable_audio(source: Path, directory: Path) -> tuple[Path, float]:
    try:
        import soundfile
    except ImportError as exc:
        raise EnrollmentUnavailable("缺少 Pyannote 声纹提取所需的 soundfile") from exc
    try:
        return source, soundfile.info(source).duration
    except (OSError, RuntimeError):
        wav = directory / "enrollment.wav"
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe,crypto,data",
                    "-i",
                    str(source),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    str(wav),
                ],
                check=True,
                capture_output=True,
                timeout=300,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return wav, soundfile.info(wav).duration
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            raise ValueError("无法读取指定媒体中的音频") from exc


def _extract_sample(
    source: Path,
    intervals: list[tuple[float, float | None]],
    clips_dir: Path,
) -> ExtractedVoiceprint:
    tmp_root = get_workspace_paths().temporary("pyannote")
    tmp_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=tmp_root, prefix="voiceprint-enroll-") as temporary:
        audio, duration = _readable_audio(source, Path(temporary))
        spans = _clip_intervals(intervals, duration)
        try:
            import pandas as pd

            service = get_diarization_service()
            pipeline = service.get_pyannote_pipeline()
            if pipeline is None:
                pipeline = service._ensure_pipeline()
            if pipeline is None:
                raise RuntimeError("Pyannote pipeline is unavailable")
            frame = pd.DataFrame(
                [(start, end, "enrollment") for start, end in spans],
                columns=["start", "end", "speaker"],
            )
            samples = extract_voiceprints(audio, frame, pipeline, clips_dir, f"{uuid4().hex}_")
        except Exception as exc:
            logger.warning("Voiceprint extraction failed: %s", exc)
            raise EnrollmentUnavailable("无法加载或运行 Pyannote 声纹模型") from exc
    if not samples:
        raise ValueError(f"未提取到声纹，请选择至少 {MIN_EMBED_SECONDS:g} 秒的清晰发言")
    return samples[0]


def _save_sample(
    store: VoiceprintStore,
    name: str,
    notes: str,
    task_id: str | None,
    label: str | None,
    sample: ExtractedVoiceprint,
) -> tuple[Person, str]:
    embedding = np.asarray(sample.embedding, dtype=np.float32).reshape(-1)
    if not embedding.size or not np.isfinite(embedding).all() or not np.linalg.norm(embedding):
        raise ValueError("声纹向量无效")
    try:
        store.ensure_enrollment_dimension(embedding.size)
    except ValueError as exc:
        raise EnrollmentConflict(str(exc)) from exc
    existing = store.find_person_by_name(name)
    person = existing or store.create_person(name, notes)
    sample_id = None
    try:
        sample_id = store.add_sample(
            person.id,
            embedding,
            task_id,
            sample.duration_sec,
            sample.quality_score,
            sample.clip_path,
        )
        if task_id is not None and label is not None:
            store.set_task_speaker(task_id, label, sample_id, person.id)
    except Exception:
        if sample_id is not None:
            store.delete_sample(sample_id)
        if existing is None:
            store.delete_person(person.id)
        raise
    person.sample_count += 1
    return person, sample_id


def _stored_voice(
    store: VoiceprintStore,
    task_id: str,
    labels: list[str],
) -> tuple[str, dict[str, Any], np.ndarray] | None:
    for label in labels:
        link = store.get_task_speaker(task_id, label)
        if link and (sample := store.get_sample(link["sample_id"])):
            vector = store.sample_embeddings([sample["sample_id"]]).get(sample["sample_id"])
            if vector is not None:
                return label, sample, vector
    return None


async def enroll_voiceprint(
    *,
    name: str,
    notes: str,
    task_id: UUID | None,
    speaker_label: str | None,
    audio_path: str | None,
    start_sec: float | None,
    end_sec: float | None,
) -> tuple[Person, str, bool]:
    name = validate_speaker_name(name)
    store = get_voiceprint_store()
    label = None
    task_key = str(task_id) if task_id is not None else None
    if task_id is not None:
        if (
            speaker_label is None
            or audio_path is not None
            or start_sec is not None
            or end_sec is not None
        ):
            raise ValueError("请提供任务和人物标签，或单独提供音频文件和时间范围")
        label = validate_speaker_name(speaker_label)
        if label.casefold() == "unknown":
            raise ValueError("请指定已归属的人物标签")
        task = get_task_store().get(task_id)
        if task is None:
            raise LookupError("任务不存在")
        stored = _stored_voice(store, task_key, [label])
        if stored is None:
            person_labels = [
                row["speaker_label"]
                for row in store.list_task_speakers(task_key)
                if row.get("person_name") == label
            ]
            stored = _stored_voice(store, task_key, person_labels)
        if stored is None:
            try:
                directory = repair_directory(task)
            except ValueError as exc:
                raise EnrollmentConflict("任务没有归档音频或已保存的声纹") from exc
            mappings = read_task_json(task_id, directory, "speaker_map.json").get("mappings") or []
            related = [
                row
                for row in mappings
                if label in (row.get("source_label"), row.get("current_name"))
            ]
            aliases = [row["source_label"] for row in related if row.get("source_label")]
            stored = _stored_voice(store, task_key, aliases)
        if stored is not None:
            label, old_sample, embedding = stored
            person = store.get_person(old_sample["person_id"])
            if person and person.name == name:
                sample_id = old_sample["sample_id"]
                store.set_task_speaker(task_key, label, sample_id, person.id)
                return person, sample_id, True
            # A copied embedding must not share the original sample's deletable clip.
            sample = ExtractedVoiceprint(
                label,
                embedding,
                old_sample["duration_sec"],
                old_sample["quality_score"],
                None,
            )
            person, sample_id = _save_sample(store, name, notes, task_key, label, sample)
            return person, sample_id, True
        source = _task_audio(task_id, directory)
        names = {row["current_name"] for row in related if row.get("current_name")}
        intervals = _task_intervals(task_id, directory, names | {label, *aliases})
        if aliases:
            label = aliases[0]
    else:
        if speaker_label is not None or not audio_path or not audio_path.strip():
            raise ValueError("请提供任务和人物标签，或单独提供音频文件和时间范围")
        start = start_sec if start_sec is not None else 0.0
        if (
            not math.isfinite(start)
            or start < 0
            or (end_sec is not None and (not math.isfinite(end_sec) or end_sec <= start))
        ):
            raise ValueError("音频时间范围无效")
        intervals = [(start, end_sec)]
        source = Path(audio_path).expanduser().resolve()
        if not source.is_file():
            raise ValueError("服务端音频文件不存在")

    sample = await run_in_thread(_extract_sample, source, intervals, store.clips_dir)
    try:
        person, sample_id = _save_sample(store, name, notes, task_key, label, sample)
    except Exception:
        if sample.clip_path:
            Path(sample.clip_path).unlink(missing_ok=True)
        raise
    return person, sample_id, False
