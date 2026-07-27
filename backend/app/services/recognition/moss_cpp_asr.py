"""MOSS-Transcribe-Diarize provider backed by the moss-transcribe.cpp CLI.

Each transcription launches a short-lived process. Loading, inference, and
GPU/CPU memory cleanup therefore follow the lifetime of one pipeline task.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from app.models import TranscriptSegment

logger = logging.getLogger(__name__)

_DEFAULT_MODELS_ROOT = Path("C:/zychen/AIGC/Models")
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_BUNDLED_ENGINE_DIR = _PROJECT_ROOT / "backend/tools/moss-transcribe"
_BINARY_ENV = "MOSS_TRANSCRIBE_CPP_BINARY"
_MODEL_ENV = "MOSS_TRANSCRIBE_GGUF"
MossProgressCallback = Callable[[dict[str, Any]], None]


def _normalize_speaker_label(value: Any) -> str | None:
    raw = str(value or "").strip()
    match = re.fullmatch(r"S(\d+)", raw, flags=re.IGNORECASE)
    if match:
        return f"SPEAKER_{max(0, int(match.group(1)) - 1):02d}"
    return raw or None


def _first_existing_file(candidates: list[str | Path]) -> str:
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path.resolve())
    return ""


def resolve_moss_cpp_binary(configured: str = "", *, required: bool = True) -> str:
    """Resolve the CLI path from settings, environment, PATH, or AIGC models."""
    executable = shutil.which("moss-transcribe") or shutil.which("moss-transcribe.exe")
    path = _first_existing_file([
        configured,
        os.getenv(_BINARY_ENV, ""),
        _BUNDLED_ENGINE_DIR / "moss-transcribe.exe",
        executable or "",
        _DEFAULT_MODELS_ROOT / "moss-transcribe.cpp/build-cuda/bin/Release/moss-transcribe.exe",
        _DEFAULT_MODELS_ROOT / "moss-transcribe.cpp/build-cuda/Release/moss-transcribe.exe",
        _DEFAULT_MODELS_ROOT / "moss-transcribe.cpp/build-cuda-ninja/moss-transcribe.exe",
        _DEFAULT_MODELS_ROOT / "moss-transcribe.cpp/build-cuda-release/moss-transcribe.exe",
        _DEFAULT_MODELS_ROOT / "moss-transcribe.cpp/build-cpu/bin/Release/moss-transcribe.exe",
        _DEFAULT_MODELS_ROOT / "moss-transcribe.cpp/build-cpu/Release/moss-transcribe.exe",
        _DEFAULT_MODELS_ROOT / "moss-transcribe.cpp/build-cpu/moss-transcribe.exe",
    ])
    if required and not path:
        raise RuntimeError(
            "找不到 moss-transcribe.cpp 可执行文件，请在音频流程设置中配置路径"
        )
    return path


def resolve_moss_cpp_model(configured: str = "", *, required: bool = True) -> str:
    """Resolve the quantized MOSS GGUF model path."""
    path = _first_existing_file([
        configured,
        os.getenv(_MODEL_ENV, ""),
        _DEFAULT_MODELS_ROOT / "MOSS-Transcribe-Diarize-GGUF/moss-transcribe-q5_k.gguf",
    ])
    if required and not path:
        raise RuntimeError(
            "找不到 MOSS GGUF 模型，请在音频流程设置中配置模型路径"
        )
    return path


def _subprocess_options() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _notify_progress(
    callback: MossProgressCallback | None,
    payload: dict[str, Any],
) -> None:
    if callback is None:
        return
    try:
        callback(payload)
    except Exception:
        logger.debug("MOSS progress callback failed", exc_info=True)


def _text_key(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").lower(), flags=re.UNICODE)


class MossCppASRService:
    """One-pass local transcription and diarization through the C++ CLI."""

    def release(self) -> None:
        # The CLI is launched per request and owns all model memory.
        return None

    def get_pyannote_pipeline(self) -> None:
        return None

    def get_last_diarization(self) -> tuple[None, None]:
        return None, None

    @staticmethod
    def _probe_duration(audio_path: str) -> float:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ]
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                **_subprocess_options(),
            )
        except FileNotFoundError as exc:
            raise RuntimeError("找不到 ffprobe，请将 FFmpeg 加入 PATH") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            raise RuntimeError(f"MOSS 音频时长探测失败: {detail[-800:]}") from exc
        try:
            duration = float(completed.stdout.strip())
        except ValueError as exc:
            raise RuntimeError("MOSS 音频时长探测返回了无效结果") from exc
        if duration <= 0:
            raise RuntimeError("MOSS 输入音频时长必须大于 0")
        return duration

    @staticmethod
    def _prepare_wav_chunk(
        audio_path: str,
        temp_dir: Path,
        *,
        chunk_index: int,
        start_sec: float,
        duration_sec: float,
    ) -> str:
        source = Path(audio_path)
        if not source.is_file():
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")

        wav_path = temp_dir / f"moss-input-{chunk_index:04d}.wav"
        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0.0, start_sec):.3f}",
            "-i",
            str(source),
            "-t",
            f"{max(0.001, duration_sec):.3f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(wav_path),
        ]
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                **_subprocess_options(),
            )
        except FileNotFoundError as exc:
            raise RuntimeError("找不到 ffmpeg，请将 FFmpeg 加入 PATH") from exc
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
            raise RuntimeError(f"MOSS 输入音频转换失败: {detail[-800:]}") from exc
        return str(wav_path)

    @staticmethod
    def _chunk_windows(
        duration_sec: float,
        chunk_duration_sec: float,
        chunk_overlap_sec: float,
    ) -> list[tuple[float, float]]:
        chunk_duration = max(60.0, float(chunk_duration_sec))
        overlap = max(0.0, min(float(chunk_overlap_sec), chunk_duration / 2))
        if duration_sec <= chunk_duration:
            return [(0.0, duration_sec)]

        stride = chunk_duration - overlap
        windows: list[tuple[float, float]] = []
        start = 0.0
        while start < duration_sec:
            length = min(chunk_duration, duration_sec - start)
            windows.append((start, length))
            if start + length >= duration_sec:
                break
            start += stride
        return windows

    @staticmethod
    def _run_cli(
        *,
        binary: str,
        model: str,
        wav_path: str,
        max_new_tokens: int,
        timeout_sec: float,
        env: dict[str, str],
        chunk_index: int,
        chunk_count: int,
    ) -> list[dict[str, Any]]:
        command = [
            binary,
            "transcribe",
            model,
            wav_path,
            "--max-new",
            str(max(1, int(max_new_tokens))),
            "--format",
            "json",
        ]
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(1.0, float(timeout_sec)),
                env=env,
                **_subprocess_options(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"MOSS 第 {chunk_index}/{chunk_count} 段推理超时"
            ) from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            raise RuntimeError(
                f"MOSS 第 {chunk_index}/{chunk_count} 段推理失败: {detail[-1200:]}"
            ) from exc

        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            detail = completed.stdout.strip()[-1200:]
            raise RuntimeError(
                f"MOSS 第 {chunk_index}/{chunk_count} 段返回了无效 JSON: {detail}"
            ) from exc
        if not isinstance(payload, list):
            raise RuntimeError(
                f"MOSS 第 {chunk_index}/{chunk_count} 段 JSON 输出应为说话人片段列表"
            )
        return [item for item in payload if isinstance(item, dict)]

    @staticmethod
    def _normalize_chunk_segments(
        payload: list[dict[str, Any]],
        *,
        offset_sec: float,
    ) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        for item in payload:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            segments.append({
                "start": offset_sec + float(item.get("start") or 0.0),
                "end": offset_sec + float(item.get("end") or 0.0),
                "text": text,
                "speaker": _normalize_speaker_label(item.get("speaker")),
            })
        return segments

    @staticmethod
    def _speaker_index(label: str) -> int | None:
        match = re.fullmatch(r"SPEAKER_(\d+)", label)
        return int(match.group(1)) if match else None

    @classmethod
    def _stitch_speakers(
        cls,
        chunk_segments: list[dict[str, Any]],
        previous_segments: list[dict[str, Any]],
        *,
        overlap_start: float,
        overlap_end: float,
        previous_mapping: dict[str, str],
    ) -> dict[str, str]:
        local_labels = list(dict.fromkeys(
            str(segment["speaker"])
            for segment in chunk_segments
            if segment.get("speaker")
        ))
        if not local_labels:
            return {}

        existing_labels = {
            str(segment["speaker"])
            for segment in previous_segments
            if segment.get("speaker")
        }
        if not previous_segments:
            return {label: label for label in local_labels}

        scores: dict[tuple[str, str], float] = {}
        current_overlap = [
            segment
            for segment in chunk_segments
            if segment["end"] > overlap_start and segment["start"] < overlap_end
        ]
        previous_overlap = [
            segment
            for segment in previous_segments
            if segment["end"] > overlap_start and segment["start"] < overlap_end
        ]
        for current in current_overlap:
            local = current.get("speaker")
            if not local:
                continue
            for previous in previous_overlap:
                global_label = previous.get("speaker")
                if not global_label:
                    continue
                overlap = min(current["end"], previous["end"]) - max(
                    current["start"],
                    previous["start"],
                )
                if overlap <= 0:
                    continue
                current_text = _text_key(current.get("text"))
                previous_text = _text_key(previous.get("text"))
                text_factor = 1.0
                if current_text and previous_text:
                    if current_text == previous_text:
                        text_factor = 4.0
                    elif current_text in previous_text or previous_text in current_text:
                        text_factor = 2.0
                key = (str(local), str(global_label))
                scores[key] = scores.get(key, 0.0) + overlap * text_factor

        mapping: dict[str, str] = {}
        used_global: set[str] = set()
        for (local, global_label), _score in sorted(
            scores.items(),
            key=lambda item: item[1],
            reverse=True,
        ):
            if local in mapping or global_label in used_global:
                continue
            mapping[local] = global_label
            used_global.add(global_label)

        for local in local_labels:
            hinted = previous_mapping.get(local)
            if local not in mapping and hinted in existing_labels and hinted not in used_global:
                mapping[local] = hinted
                used_global.add(hinted)
        for local in local_labels:
            if local not in mapping and local in existing_labels and local not in used_global:
                mapping[local] = local
                used_global.add(local)

        next_index = 0
        existing_indices = [
            index
            for label in existing_labels | used_global
            if (index := cls._speaker_index(label)) is not None
        ]
        if existing_indices:
            next_index = max(existing_indices) + 1
        for local in local_labels:
            if local in mapping:
                continue
            while f"SPEAKER_{next_index:02d}" in existing_labels | used_global:
                next_index += 1
            global_label = f"SPEAKER_{next_index:02d}"
            mapping[local] = global_label
            used_global.add(global_label)
            next_index += 1
        return mapping

    def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        diarize: bool = True,
        num_speakers: int | None = None,
        *,
        binary_path: str = "",
        model_path: str = "",
        device: str = "auto",
        threads: int = 8,
        max_new_tokens: int = 8192,
        chunk_duration_sec: float = 1200.0,
        chunk_overlap_sec: float = 60.0,
        timeout_sec: float = 14400.0,
        progress_callback: MossProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Run MOSS and return its speaker-attributed segment list."""
        binary = resolve_moss_cpp_binary(binary_path)
        model = resolve_moss_cpp_model(model_path)
        env = os.environ.copy()
        if device and device != "auto":
            env["MTD_DEVICE"] = device
        else:
            env.pop("MTD_DEVICE", None)
        env["MTD_THREADS"] = str(max(1, int(threads)))
        binary_dir = Path(binary).parent
        dependency_dirs = [
            binary_dir,
            binary_dir.parent / "bin/Release",
            binary_dir / "bin",
            binary_dir / "bin/Release",
        ]
        env["PATH"] = os.pathsep.join(
            [str(path) for path in dependency_dirs if path.is_dir()] + [env.get("PATH", "")]
        )

        duration_sec = self._probe_duration(audio_path)
        windows = self._chunk_windows(
            duration_sec,
            chunk_duration_sec,
            chunk_overlap_sec,
        )
        chunk_count = len(windows)
        effective_overlap = max(
            0.0,
            min(float(chunk_overlap_sec), max(60.0, float(chunk_duration_sec)) / 2),
        )
        _notify_progress(progress_callback, {
            "phase": "starting",
            "current_chunk": 0,
            "completed_chunks": 0,
            "total_chunks": chunk_count,
            "progress": 0.0,
            "message": f"准备 MOSS 转录：共 {chunk_count} 段",
        })

        deadline = time.monotonic() + max(1.0, float(timeout_sec))
        segments: list[dict[str, Any]] = []
        previous_mapping: dict[str, str] = {}
        with tempfile.TemporaryDirectory(prefix="mpp-moss-") as temp_name:
            temp_dir = Path(temp_name)
            for chunk_index, (start_sec, length_sec) in enumerate(windows, 1):
                remaining_timeout = deadline - time.monotonic()
                if remaining_timeout <= 0:
                    raise RuntimeError(f"MOSS 推理超过 {timeout_sec:g} 秒超时限制")
                _notify_progress(progress_callback, {
                    "phase": "transcribing",
                    "current_chunk": chunk_index,
                    "completed_chunks": chunk_index - 1,
                    "total_chunks": chunk_count,
                    "progress": (chunk_index - 1) / chunk_count,
                    "message": f"MOSS 转录：第 {chunk_index}/{chunk_count} 段",
                })
                wav_path = self._prepare_wav_chunk(
                    audio_path,
                    temp_dir,
                    chunk_index=chunk_index,
                    start_sec=start_sec,
                    duration_sec=length_sec,
                )
                try:
                    payload = self._run_cli(
                        binary=binary,
                        model=model,
                        wav_path=wav_path,
                        max_new_tokens=max_new_tokens,
                        timeout_sec=remaining_timeout,
                        env=env,
                        chunk_index=chunk_index,
                        chunk_count=chunk_count,
                    )
                finally:
                    Path(wav_path).unlink(missing_ok=True)

                chunk_segments = self._normalize_chunk_segments(
                    payload,
                    offset_sec=start_sec,
                )
                overlap_end = start_sec + effective_overlap if chunk_index > 1 else 0.0
                mapping = self._stitch_speakers(
                    chunk_segments,
                    segments,
                    overlap_start=start_sec,
                    overlap_end=overlap_end,
                    previous_mapping=previous_mapping,
                )
                for segment in chunk_segments:
                    if segment.get("speaker") in mapping:
                        segment["speaker"] = mapping[str(segment["speaker"])]
                previous_mapping = mapping

                if chunk_index > 1:
                    chunk_segments = [
                        segment
                        for segment in chunk_segments
                        if (segment["start"] + segment["end"]) / 2 >= overlap_end
                    ]
                segments.extend(chunk_segments)
                segments.sort(key=lambda segment: (segment["start"], segment["end"]))

                logger.info(
                    "MOSS chunk completed: %d/%d start=%.1fs duration=%.1fs segments=%d",
                    chunk_index,
                    chunk_count,
                    start_sec,
                    length_sec,
                    len(chunk_segments),
                )
                _notify_progress(progress_callback, {
                    "phase": "transcribing",
                    "current_chunk": chunk_index,
                    "completed_chunks": chunk_index,
                    "total_chunks": chunk_count,
                    "progress": chunk_index / chunk_count,
                    "message": f"MOSS 转录完成：第 {chunk_index}/{chunk_count} 段",
                })

        speakers = sorted({s["speaker"] for s in segments if s.get("speaker")})
        if num_speakers is not None and len(speakers) != int(num_speakers):
            logger.info(
                "MOSS 自动检测到 %d 位说话人；任务参数 num_speakers=%d 作为结果校验信息记录",
                len(speakers),
                int(num_speakers),
            )
        return {
            "language": language or "unknown",
            "segments": segments,
            "speakers": speakers,
            "speaker_count": len(speakers),
            "diarization": "moss",
            "requested_num_speakers": num_speakers,
            "chunked": chunk_count > 1,
            "chunk_count": chunk_count,
            "duration_sec": duration_sec,
        }

    def to_segments(self, result: dict[str, Any]) -> list[TranscriptSegment]:
        return [
            TranscriptSegment(
                start=float(segment.get("start") or 0.0),
                end=float(segment.get("end") or 0.0),
                text=str(segment.get("text") or "").strip(),
                speaker=_normalize_speaker_label(segment.get("speaker")),
            )
            for segment in result.get("segments", [])
            if str(segment.get("text") or "").strip()
        ]

    def to_srt(self, segments: list[TranscriptSegment]) -> str:
        rows: list[str] = []
        for index, segment in enumerate(segments, 1):
            label = f"[{segment.speaker}] " if segment.speaker else ""
            rows.append(
                f"{index}\n{self._format_time(segment.start)} --> "
                f"{self._format_time(segment.end)}\n{label}{segment.text}\n"
            )
        return "\n".join(rows)

    @staticmethod
    def _format_time(seconds: float) -> str:
        total_milliseconds = max(0, int(round(seconds * 1000)))
        hours, remainder = divmod(total_milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        whole_seconds, milliseconds = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


_service: MossCppASRService | None = None


def get_moss_cpp_service() -> MossCppASRService:
    global _service
    if _service is None:
        _service = MossCppASRService()
    return _service


def release_moss_cpp_service() -> None:
    if _service is not None:
        _service.release()
