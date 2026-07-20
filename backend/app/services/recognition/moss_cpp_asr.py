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
from pathlib import Path
from typing import Any

from app.models import TranscriptSegment

logger = logging.getLogger(__name__)

_DEFAULT_MODELS_ROOT = Path("C:/zychen/AIGC/Models")
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_BUNDLED_ENGINE_DIR = _PROJECT_ROOT / "backend/tools/moss-transcribe"
_BINARY_ENV = "MOSS_TRANSCRIBE_CPP_BINARY"
_MODEL_ENV = "MOSS_TRANSCRIBE_GGUF"


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
    def _prepare_wav(audio_path: str, temp_dir: Path) -> str:
        source = Path(audio_path)
        if not source.is_file():
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")

        wav_path = temp_dir / "moss-input.wav"
        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
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
        max_new_tokens: int = 32768,
        timeout_sec: float = 3600.0,
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

        with tempfile.TemporaryDirectory(prefix="mpp-moss-") as temp_name:
            wav_path = self._prepare_wav(audio_path, Path(temp_name))
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
                raise RuntimeError(f"MOSS 推理超过 {timeout_sec:g} 秒超时限制") from exc
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or "").strip()
                raise RuntimeError(f"MOSS 推理失败: {detail[-1200:]}") from exc

        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            detail = completed.stdout.strip()[-1200:]
            raise RuntimeError(f"MOSS 返回了无效 JSON: {detail}") from exc
        if not isinstance(payload, list):
            raise RuntimeError("MOSS JSON 输出应为说话人片段列表")

        segments: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            speaker = _normalize_speaker_label(item.get("speaker"))
            segments.append({
                "start": float(item.get("start") or 0.0),
                "end": float(item.get("end") or 0.0),
                "text": text,
                "speaker": speaker,
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
