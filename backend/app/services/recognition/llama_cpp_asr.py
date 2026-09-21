"""Qwen3-ASR GGUF with one llama-server and bounded parallel audio requests."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import logging
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any, Callable

import httpx

from app.services.analysis.local_llm_runtime import LocalLlamaCppRuntime
from app.services.recognition.chunking import ASRChunker, AudioChunk
from app.services.recognition.sherpa_onnx_asr import (
    SherpaOnnxASRService, _merge_timed_units, _remove_hotword_echoes,
)

logger = logging.getLogger(__name__)


def validate_llama_asr_config(config: dict[str, Any]) -> str:
    try:
        LocalLlamaCppRuntime._resolve_binary(str(config.get("binary_path") or ""))
        for key in ("model_path", "mmproj_path"):
            if not str(config.get(key) or "").strip() or not Path(config[key]).expanduser().is_file():
                return f"Qwen3-ASR {key} does not exist: {config.get(key) or '(empty)'}"
        mode = config.get("timestamp_mode", "auto")
        if mode not in {"auto", "vad", "qwen_forced"}:
            return "llama.cpp ASR supports auto, vad, or qwen_forced timestamps"
        if mode == "qwen_forced" and (not config.get("aligner_model_path") or not Path(config["aligner_model_path"]).is_dir()):
            return "Qwen3 ForcedAligner model directory is required"
    except (RuntimeError, OSError) as exc:
        return str(exc)
    return ""


def parse_asr_text(raw: str, language: str | None = None) -> tuple[str, str]:
    detected = language or "unknown"
    if "<asr_text>" in raw:
        header, raw = raw.split("<asr_text>", 1)
        match = re.search(r"language\s+([^<]+)", header, re.IGNORECASE)
        if match:
            detected = match.group(1).strip().lower()
    text = re.sub(r"<\|(?:im_end|endoftext)\|>", "", raw).strip()
    return text, detected


class LlamaCppASRService:
    def __init__(self) -> None:
        # Separate ownership from the text/vision runtime: ASR exits before diarization.
        self.runtime = LocalLlamaCppRuntime()

    def release(self) -> None:
        self.runtime.stop()

    to_segments = SherpaOnnxASRService.to_segments
    to_srt = SherpaOnnxASRService.to_srt
    _format_time = staticmethod(SherpaOnnxASRService._format_time)

    def transcribe(
        self, audio_path: str, language: str | None = None, diarize: bool = True,
        num_speakers: int | None = None, *, chunk_strategy: str | None = None,
        hotwords: list[str] | None = None, runtime_config: dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        import numpy as np
        import soundfile as sf

        del diarize, num_speakers
        if runtime_config is None:
            from app.core.model_router import resolve_asr_binding
            from app.core.settings import get_runtime_settings
            runtime_config = resolve_asr_binding(get_runtime_settings(), task_options={"asr_provider": "llama_cpp"}).request_kwargs
        config = dict(runtime_config)
        reason = validate_llama_asr_config(config)
        if reason:
            raise RuntimeError(reason)
        audio_file = Path(audio_path).resolve()
        if not audio_file.is_file():
            raise FileNotFoundError(audio_file)
        started = time.monotonic()
        def progress(done: int, total: int, message: str, phase: str = "transcribing") -> None:
            if progress_callback:
                progress_callback({"phase": phase, "progress": done / max(1, total),
                                   "message": message, "completed_chunks": done, "total_chunks": total})
        progress(0, 1, "准备 llama.cpp 音频分段", "preparing")
        decoded = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(audio_file), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"],
            capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if decoded.returncode:
            raise RuntimeError(f"ffmpeg audio decode failed: {decoded.stderr.decode('utf-8', errors='replace')[:500]}")
        samples = np.frombuffer(decoded.stdout, dtype=np.float32)
        if not len(samples):
            raise RuntimeError("Audio contains no samples")
        duration = len(samples) / 16000
        strategy = chunk_strategy or config.get("chunk_strategy", "vad")
        maximum = min(30.0, max(1.0, float(config.get("max_chunk_sec") or 30)))
        chunker = ASRChunker()
        if strategy in {"vad", "auto", "sherpa_vad"}:
            vad_path = config.get("vad_model_path")
            if not vad_path:
                from app.services.recognition.sherpa_catalog import resolve_model_root
                vad_path = str(resolve_model_root(config.get("model_root", "")) / "silero_vad.onnx")
            chunks = chunker.sherpa_vad_chunks(audio_file, model_path=vad_path, max_duration=maximum, samples=samples)
        elif strategy in {"fixed", "ffmpeg"}:
            chunks = chunker._split_evenly(AudioChunk(0.0, duration), maximum)
        else:
            raise ValueError(f"Unsupported ASR chunk strategy: {strategy}")
        prepared = time.monotonic()
        parallel = max(1, min(16, int(config.get("parallel") or 8)))
        device = str(config.get("device") or "auto")
        if device == "auto":
            device = "cuda" if self.runtime._has_nvidia_gpu() else "cpu"
        runtime_config = {
            **config, "device": device, "ctx": 4096, "parallel": parallel,
            "alias": "Qwen3-ASR", "keepalive_sec": 0, "cache_type": "f16", "cache_ram": 0,
        }
        results: list[tuple[str, str] | None] = [None] * len(chunks)
        def wav_bytes(chunk: AudioChunk) -> bytes:
            out = io.BytesIO()
            sf.write(out, samples[round(chunk.start * 16000):round(chunk.end * 16000)], 16000, format="WAV", subtype="PCM_16")
            return out.getvalue()
        try:
            base_url = self.runtime.ensure(runtime_config)
            infer_started = time.monotonic()
            logger.info("llama.cpp ASR: model=%s chunks=%d parallel=%d device=%s", config["model_path"], len(chunks), parallel, device)
            with httpx.Client(base_url=base_url, timeout=float(config.get("timeout_sec") or 120), trust_env=False) as client:
                def infer(index: int) -> tuple[str, str]:
                    payload: dict[str, Any] = {
                        "model": "Qwen3-ASR",
                        "messages": [
                            {"role": "system", "content": " ".join(hotwords or [])},
                            {"role": "user", "content": [{"type": "input_audio", "input_audio": {
                                "format": "wav", "data": base64.b64encode(wav_bytes(chunks[index])).decode("ascii"),
                            }}]},
                        ],
                        "temperature": 0, "seed": 42,
                        "max_tokens": int(config.get("max_new_tokens") or 512), "cache_prompt": False,
                    }
                    if language and language.lower() not in {"auto", "unknown"}:
                        from app.services.recognition.alignment import _LANGUAGE_NAMES
                        payload["generation_prompt"] = f"language {_LANGUAGE_NAMES.get(language.lower(), language)}<asr_text>"
                    response = client.post("/v1/chat/completions", json=payload)
                    response.raise_for_status()
                    choice = response.json()["choices"][0]
                    if choice.get("finish_reason") == "length":
                        raise RuntimeError(f"ASR chunk {index + 1}/{len(chunks)} reached its output token limit")
                    return parse_asr_text(choice["message"]["content"], language)
                with ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="llama-asr") as pool:
                    futures = {pool.submit(infer, i): i for i in range(len(chunks))}
                    try:
                        for completed, future in enumerate(as_completed(futures), 1):
                            index = futures[future]
                            results[index] = future.result()
                            progress(completed, len(chunks), f"已转写 {completed}/{len(chunks)} 段（{parallel} 路并发）")
                    except Exception:
                        for future in futures:
                            future.cancel()
                        raise
            infer_elapsed = time.monotonic() - infer_started
        finally:
            self.runtime.stop()

        segments = []
        detected = language or "unknown"
        for chunk, result in zip(chunks, results):
            assert result is not None
            text, detected_language = result
            if detected_language not in {"auto", "unknown"}:
                detected = detected_language
            if text:
                segments.append({"start": chunk.start, "end": chunk.end, "text": text})
        segments, echoes_removed = _remove_hotword_echoes(segments, tuple(hotwords or []))
        timestamp_source = "vad" if strategy in {"vad", "auto", "sherpa_vad"} else "fixed"
        if config.get("timestamp_mode") == "qwen_forced":
            from app.services.recognition.alignment import get_qwen_forced_aligner
            from app.core.paths import CONFIG_FILE
            temp_root = CONFIG_FILE.parent / "tmp" / "asr-alignment"
            temp_root.mkdir(parents=True, exist_ok=True)
            aligned = []
            aligner = get_qwen_forced_aligner()
            try:
                with tempfile.TemporaryDirectory(dir=temp_root) as directory:
                    wav = Path(directory) / "chunk.wav"
                    for i, segment in enumerate(segments):
                        progress(i, len(segments), f"正在对齐 {i + 1}/{len(segments)} 段", "aligning")
                        wav.write_bytes(wav_bytes(AudioChunk(segment["start"], segment["end"])))
                        units = aligner.align(wav, segment["text"], detected, model_path=config["aligner_model_path"], device=device)
                        if not units:
                            raise RuntimeError(f"Forced alignment returned no timestamps for segment {i + 1}")
                        aligned.extend(_merge_timed_units(units, offset=segment["start"]))
                segments = aligned
            finally:
                aligner.release()
            timestamp_source = "qwen_forced"
        elapsed = time.monotonic() - started
        progress(len(chunks), len(chunks), f"转写完成：{len(segments)} 个字幕段")
        logger.info("llama.cpp ASR completed: %.2fs total, %.2fs inference, %d segments", elapsed, infer_elapsed, len(segments))
        return {
            "segments": segments, "language": detected, "model": Path(config["model_path"]).stem,
            "runtime_provider": device, "timestamp_source": timestamp_source,
            "metadata": {"engine": "llama_cpp", "chunk_strategy": strategy, "chunk_count": len(chunks),
                         "parallel": parallel, "failed_chunks": [], "elapsed_sec": round(elapsed, 3),
                         "preparation_sec": round(prepared - started, 3), "inference_sec": round(infer_elapsed, 3),
                         "audio_duration_sec": round(duration, 3), "rtf": round(elapsed / duration, 4),
                         "hotwords": hotwords or [], "hotword_echoes_removed": echoes_removed},
        }


_service: LlamaCppASRService | None = None

def get_llama_cpp_asr_service() -> LlamaCppASRService:
    global _service
    if _service is None:
        _service = LlamaCppASRService()
    return _service

def release_llama_cpp_asr_service() -> None:
    global _service
    if _service is not None:
        _service.release()
        _service = None
