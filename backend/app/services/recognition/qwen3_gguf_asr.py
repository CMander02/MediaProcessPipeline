"""Qwen3-ASR GGUF provider served by llama.cpp."""

from __future__ import annotations

import base64
import logging
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from app.core.settings import get_runtime_settings
from app.models import TranscriptSegment
from app.services.recognition.chunking import ASRChunker

logger = logging.getLogger(__name__)

_DEFAULT_ALIAS = "Qwen3-ASR-1.7B"
_DEFAULT_HF_REPO = "ggml-org/Qwen3-ASR-1.7B-GGUF:Q8_0"


class LlamaCppRuntime:
    """Owns a single llama.cpp ASR server process."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[Any] | None = None
        self._base_url = ""
        self._signature: tuple[Any, ...] | None = None
        self._timer: threading.Timer | None = None
        self._lock = threading.RLock()

    @property
    def base_url(self) -> str:
        return self._base_url

    def ensure(self, config: dict[str, Any]) -> str:
        signature = self._signature_for(config)
        with self._lock:
            if self._process and self._process.poll() is None and self._signature == signature:
                self._schedule_stop(float(config.get("keepalive_sec") or 0))
                return self._base_url

            self.stop()
            binary_args = self._binary_args(str(config.get("binary_path") or ""))
            port = self._free_port()
            args = [
                *binary_args,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                *self._model_args(config),
                "-c",
                str(int(config.get("ctx") or 4096)),
                "-np",
                "1",
                *self._device_args(config),
                "--no-cache-prompt",
                "--cache-ram",
                "0",
                "--cache-type-k",
                "q8_0",
                "--cache-type-v",
                "q8_0",
                "-a",
                str(config.get("alias") or _DEFAULT_ALIAS),
            ]
            logger.info("Starting llama.cpp ASR server: %s", self._redact_args(args))
            self._process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._base_url = f"http://127.0.0.1:{port}"
            self._signature = signature
            try:
                self._wait_until_ready(float(config.get("timeout_sec") or 300))
                self._schedule_stop(float(config.get("keepalive_sec") or 0))
            except Exception:
                self.stop()
                raise
            return self._base_url

    def stop(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            process = self._process
            self._process = None
            self._base_url = ""
            self._signature = None
            if process and process.poll() is None:
                logger.info("Stopping llama.cpp ASR server")
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()

    def _schedule_stop(self, keepalive_sec: float) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if keepalive_sec <= 0:
            return
        self._timer = threading.Timer(keepalive_sec, self.stop)
        self._timer.daemon = True
        self._timer.start()

    @staticmethod
    def _signature_for(config: dict[str, Any]) -> tuple[Any, ...]:
        return (
            config.get("binary_path"),
            config.get("model_path"),
            config.get("mmproj_path"),
            config.get("hf_repo"),
            config.get("device"),
            config.get("ctx"),
            config.get("n_gpu_layers"),
            config.get("alias"),
        )

    @staticmethod
    def _binary_args(binary_path: str) -> list[str]:
        binary = binary_path.strip() or (
            shutil.which("llama-server")
            or shutil.which("llama-server.exe")
            or shutil.which("llama")
            or shutil.which("llama.exe")
        )
        if not binary:
            raise RuntimeError(
                "llama.cpp binary not found; install llama.cpp or set llama_cpp_binary_path"
            )
        stem = Path(binary).stem.lower()
        if stem == "llama-server":
            return [binary]
        return [binary, "serve"]

    @staticmethod
    def _model_args(config: dict[str, Any]) -> list[str]:
        model_path = str(config.get("model_path") or "").strip()
        mmproj_path = str(config.get("mmproj_path") or "").strip()
        if model_path and mmproj_path:
            return ["--model", model_path, "--mmproj", mmproj_path]
        if model_path or mmproj_path:
            raise RuntimeError("qwen3_gguf_model_path and qwen3_gguf_mmproj_path must be set together")
        return ["-hf", str(config.get("hf_repo") or _DEFAULT_HF_REPO)]

    def _device_args(self, config: dict[str, Any]) -> list[str]:
        device = str(config.get("device") or "auto").strip().lower()
        if device == "auto":
            device = "cuda" if self._has_nvidia_gpu() else "cpu"
        if device == "cuda":
            return ["-fa", "on", "-ngl", str(int(config.get("n_gpu_layers") or 99))]
        return ["-ngl", "0"]

    @staticmethod
    def _has_nvidia_gpu() -> bool:
        try:
            result = subprocess.run(
                ["nvidia-smi", "-L"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
            return result.returncode == 0 and "GPU" in result.stdout
        except Exception:
            return False

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _wait_until_ready(self, timeout_sec: float) -> None:
        deadline = time.monotonic() + timeout_sec
        last_error = ""
        while time.monotonic() < deadline:
            if self._process and self._process.poll() is not None:
                raise RuntimeError("llama.cpp ASR server exited during startup")
            try:
                response = httpx.get(f"{self._base_url}/health", timeout=2)
                if response.status_code < 500:
                    return
            except Exception as exc:
                last_error = str(exc)
            try:
                response = httpx.get(f"{self._base_url}/v1/models", timeout=2)
                if response.status_code < 500:
                    return
            except Exception as exc:
                last_error = str(exc)
            time.sleep(0.5)
        raise RuntimeError(f"llama.cpp ASR server did not become ready: {last_error}")

    @staticmethod
    def _redact_args(args: list[str]) -> str:
        return " ".join(args)


class Qwen3GGUFASRService:
    """ASR provider that sends audio chunks to a local llama.cpp server."""

    def __init__(self) -> None:
        self._runtime = LlamaCppRuntime()
        self._state_lock = threading.RLock()
        self._busy_count = 0
        self._pending_release = False

    def release(self) -> None:
        with self._state_lock:
            if self._busy_count > 0:
                self._pending_release = True
                logger.info("Qwen3 GGUF ASR release deferred until active transcription finishes")
                return
        self._runtime.stop()

    def get_pyannote_pipeline(self):
        return None

    def get_last_diarization(self):
        return None, None

    def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        diarize: bool = True,  # noqa: ARG002
        num_speakers: int | None = None,  # noqa: ARG002
        chunk_strategy: str | None = None,
        hotwords: list[str] | None = None,
    ) -> dict[str, Any]:
        self._begin_transcribe()
        try:
            return self._transcribe_impl(
                audio_path,
                language=language,
                chunk_strategy=chunk_strategy,
                hotwords=hotwords,
            )
        finally:
            self._end_transcribe()

    def _begin_transcribe(self) -> None:
        with self._state_lock:
            self._busy_count += 1

    def _end_transcribe(self) -> None:
        should_release = False
        with self._state_lock:
            self._busy_count = max(0, self._busy_count - 1)
            if self._busy_count == 0 and self._pending_release:
                self._pending_release = False
                should_release = True
        if should_release:
            self._runtime.stop()

    def _transcribe_impl(
        self,
        audio_path: str,
        *,
        language: str | None,
        chunk_strategy: str | None,
        hotwords: list[str] | None,
    ) -> dict[str, Any]:
        rt = get_runtime_settings()
        from app.core.model_router import resolve_asr_binding

        binding = resolve_asr_binding(
            rt,
            task_options={"asr_provider": "qwen3_gguf"},
            language=language,
        )
        if not binding.configured:
            raise RuntimeError(
                f"Qwen3 GGUF ASR binding is unavailable: {binding.reason or 'incomplete configuration'}"
            )

        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise FileNotFoundError(f"File not found: {audio_path}")

        kwargs = binding.request_kwargs
        base_url = self._runtime.ensure(kwargs)
        strategy = chunk_strategy or binding.chunk_strategy or str(kwargs.get("chunk_strategy") or "silero_onnx")
        max_chunk = float(kwargs.get("max_chunk_sec") or 30.0)
        timeout = float(kwargs.get("timeout_sec") or 300.0)
        chunker = ASRChunker(
            silero_onnx_model_path=str(kwargs.get("silero_onnx_model_path") or "")
        )
        chunks = chunker.chunks(audio_file, strategy=strategy, max_duration=max_chunk, allow_fallback=True)

        logger.info(
            "Qwen3 GGUF ASR: %s (chunks=%s, strategy=%s, model=%s)",
            audio_path,
            len(chunks),
            strategy,
            binding.model,
        )

        segments: list[dict[str, Any]] = []
        with httpx.Client(timeout=timeout) as client:
            for index, chunk in enumerate(chunks):
                if chunk.end - chunk.start < 0.1:
                    continue
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp_path = Path(tmp.name)
                try:
                    chunker.export_wav(audio_file, chunk, tmp_path)
                    text = self._post_chunk(
                        client,
                        f"{base_url}/v1/chat/completions",
                        str(kwargs.get("alias") or _DEFAULT_ALIAS),
                        tmp_path,
                        language=language,
                        hotwords=hotwords,
                    )
                    if text:
                        segments.extend(self._split_chunk_text(chunk.start, chunk.end, text))
                    logger.info(
                        "  gguf chunk %s/%s [%.1fs-%.1fs]: %s chars",
                        index + 1,
                        len(chunks),
                        chunk.start,
                        chunk.end,
                        len(text),
                    )
                finally:
                    tmp_path.unlink(missing_ok=True)

        return {
            "language": language or "unknown",
            "segments": segments,
            "provider": "qwen3_gguf",
        }

    def _post_chunk(
        self,
        client: httpx.Client,
        url: str,
        model: str,
        wav_path: Path,
        *,
        language: str | None = None,
        hotwords: list[str] | None = None,
    ) -> str:
        audio_b64 = base64.b64encode(wav_path.read_bytes()).decode("ascii")
        prompt = "Transcribe this audio. Return only the transcription text."
        if language:
            prompt += f" Language hint: {language}."
        if hotwords:
            words = ", ".join(word.strip() for word in hotwords if str(word).strip())
            if words:
                prompt += f" Preserve these terms exactly when they are spoken: {words}."
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": audio_b64, "format": "wav"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "stream": False,
        }
        response = client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        return self._clean_text(self._extract_text(data))

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(message, dict):
                content = message.get("content", "")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts = []
                    for item in content:
                        if isinstance(item, dict):
                            parts.append(str(item.get("text") or item.get("content") or ""))
                    return "".join(parts)
            text = choices[0].get("text") if isinstance(choices[0], dict) else ""
            if text:
                return str(text)
        if isinstance(data.get("text"), str):
            return data["text"]
        return ""

    @staticmethod
    def _clean_text(text: str) -> str:
        cleaned = text.strip()
        cleaned = re.sub(r"(?i)^language\s+[a-z_-]+\s*", "", cleaned)
        cleaned = cleaned.replace("<asr_text>", "")
        cleaned = re.sub(r"<\|[^>]+?\|>", "", cleaned)
        cleaned = re.sub(r"^```(?:text)?|```$", "", cleaned.strip(), flags=re.IGNORECASE | re.MULTILINE)
        return cleaned.strip()

    @staticmethod
    def _split_chunk_text(start: float, end: float, text: str) -> list[dict[str, Any]]:
        pieces = Qwen3GGUFASRService._split_text_pieces(text)
        if not pieces:
            return []
        if len(pieces) == 1:
            return [{"start": round(start, 3), "end": round(end, 3), "text": pieces[0]}]

        total_weight = sum(max(1, len(piece)) for piece in pieces)
        duration = max(0.1, end - start)
        cursor = start
        segments: list[dict[str, Any]] = []
        for index, piece in enumerate(pieces):
            if index == len(pieces) - 1:
                piece_end = end
            else:
                piece_duration = duration * max(1, len(piece)) / total_weight
                piece_end = min(end, cursor + piece_duration)
            if piece_end - cursor < 0.05:
                piece_end = min(end, cursor + 0.05)
            segments.append(
                {
                    "start": round(cursor, 3),
                    "end": round(piece_end, 3),
                    "text": piece,
                }
            )
            cursor = piece_end
        return segments

    @staticmethod
    def _split_text_pieces(text: str, *, max_chars: int = 42) -> list[str]:
        normalized = re.sub(r"\s+", " ", text.strip())
        if not normalized:
            return []

        primary = re.split(r"(?<=[。！？!?；;])\s*", normalized)
        pieces: list[str] = []
        for part in primary:
            part = part.strip()
            if not part:
                continue
            if len(part) <= max_chars:
                pieces.append(part)
                continue
            pieces.extend(Qwen3GGUFASRService._split_long_piece(part, max_chars=max_chars))
        return pieces

    @staticmethod
    def _split_long_piece(text: str, *, max_chars: int) -> list[str]:
        segments = [item.strip() for item in re.split(r"(?<=[，,、])\s*", text) if item.strip()]
        if len(segments) <= 1:
            return Qwen3GGUFASRService._split_even_text(text, max_chars=max_chars)

        pieces: list[str] = []
        current = ""
        for segment in segments:
            candidate = current + segment if current else segment
            if current and len(candidate) > max_chars:
                pieces.append(current)
                current = segment
            else:
                current = candidate
        if current:
            pieces.append(current)

        final: list[str] = []
        for piece in pieces:
            if len(piece) > max_chars * 1.5:
                final.extend(Qwen3GGUFASRService._split_even_text(piece, max_chars=max_chars))
            else:
                final.append(piece)
        return final

    @staticmethod
    def _split_even_text(text: str, *, max_chars: int) -> list[str]:
        return [
            text[index:index + max_chars].strip()
            for index in range(0, len(text), max_chars)
            if text[index:index + max_chars].strip()
        ]

    def to_segments(self, result: dict[str, Any]) -> list[TranscriptSegment]:
        return [
            TranscriptSegment(
                start=s.get("start", 0.0),
                end=s.get("end", 0.0),
                text=s.get("text", "").strip(),
                speaker=s.get("speaker"),
            )
            for s in result.get("segments", [])
            if str(s.get("text", "")).strip()
        ]

    def to_srt(self, segments: list[TranscriptSegment]) -> str:
        lines = []
        for i, seg in enumerate(segments, 1):
            start = self._fmt_time(seg.start)
            end = self._fmt_time(seg.end)
            text = f"[{seg.speaker}] {seg.text}" if seg.speaker else seg.text
            lines.append(f"{i}\n{start} --> {end}\n{text}\n")
        return "\n".join(lines)

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


_service: Qwen3GGUFASRService | None = None


def get_qwen3_gguf_service() -> Qwen3GGUFASRService:
    global _service
    if _service is None:
        _service = Qwen3GGUFASRService()
    return _service


def release_qwen3_gguf_service() -> None:
    if _service is not None:
        _service.release()
