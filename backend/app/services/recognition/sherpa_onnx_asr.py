"""Unified local ASR service backed by sherpa-onnx."""

from __future__ import annotations

import logging
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from app.core.settings import get_runtime_settings
from app.models import TranscriptSegment
from app.services.recognition.alignment import get_qwen_forced_aligner
from app.services.recognition.chunking import ASRChunker, AudioChunk
from app.services.recognition.sherpa_catalog import resolve_model, resolve_model_root
from app.services.recognition.sherpa_runtime import (
    SherpaRuntimeOptions,
    get_sherpa_runtime,
)

logger = logging.getLogger(__name__)

_SENTENCE_END = re.compile(r"[。！？!?；;]$")


def _clean_text(text: Any) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    cleaned = re.sub(
        r"^language\s+[a-z]+\s*<asr_text>\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    if cleaned.startswith("<") and len(cleaned) < 64:
        return ""
    return cleaned


def _normalize_language(value: Any) -> str:
    language = _clean_text(value)
    tag = re.fullmatch(r"<\|([^|]+)\|>", language)
    return tag.group(1).lower() if tag else language.lower()


def _append_token(current: str, token: str, *, token_stream: bool = False) -> str:
    token = str(token or "")
    if not token:
        return current
    if not current:
        return token.strip()
    if token_stream:
        return f"{current}{token}".strip()
    if token.startswith(" ") or token[:1] in "，。！？；：,.!?;:'\")]}％%":
        return f"{current}{token}".strip()
    if current[-1:].isascii() and token[:1].isascii():
        return f"{current} {token}".strip()
    return f"{current}{token}".strip()


def _merge_timed_units(
    units: list[dict[str, Any]],
    *,
    offset: float = 0.0,
    max_segment_sec: float = 12.0,
    token_stream: bool = False,
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    current_text = ""
    current_start: float | None = None
    current_end = 0.0
    for unit in units:
        text = str(unit.get("text") or "")
        if not text.strip():
            continue
        start = float(unit.get("start", 0.0)) + offset
        end = max(start, float(unit.get("end", start)) + offset)
        if current_start is None:
            current_start = start
        current_text = _append_token(
            current_text,
            text,
            token_stream=token_stream,
        )
        current_end = end
        if _SENTENCE_END.search(text.strip()) or current_end - current_start >= max_segment_sec:
            segments.append(
                {
                    "start": round(current_start, 3),
                    "end": round(current_end, 3),
                    "text": _clean_text(current_text),
                }
            )
            current_text = ""
            current_start = None
    if current_start is not None and current_text.strip():
        segments.append(
            {
                "start": round(current_start, 3),
                "end": round(current_end, 3),
                "text": _clean_text(current_text),
            }
        )
    return segments


class SherpaOnnxASRService:
    def release(self) -> None:
        get_sherpa_runtime().release()
        get_qwen_forced_aligner().release()

    def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        diarize: bool = True,
        num_speakers: int | None = None,
        *,
        chunk_strategy: str | None = None,
        hotwords: list[str] | None = None,
        runtime_config: dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        del diarize, num_speakers
        audio_file = Path(audio_path).resolve()
        if not audio_file.is_file():
            raise FileNotFoundError(f"Audio file not found: {audio_file}")

        rt = get_runtime_settings()
        config = dict(runtime_config or {})
        model_id = str(config.get("model_id") or rt.sherpa_model_id)
        model_root = str(config.get("model_root") or rt.sherpa_model_root)
        device = str(config.get("device") or rt.sherpa_device)
        threads = int(config.get("num_threads") or rt.sherpa_num_threads)
        debug = bool(config.get("debug", rt.sherpa_debug))
        strategy = str(chunk_strategy or config.get("chunk_strategy") or rt.sherpa_chunk_strategy)
        configured_chunk_sec = float(
            config.get("max_chunk_sec") or rt.sherpa_max_chunk_sec
        )
        timestamp_mode = str(config.get("timestamp_mode") or rt.asr_timestamp_mode).lower()
        vad_path = str(config.get("vad_model_path") or rt.sherpa_vad_model_path)
        aligner_path = str(config.get("aligner_model_path") or rt.qwen3_aligner_model_path)
        language_hint = str(language or config.get("language") or "auto")
        hotword_values = tuple(str(word).strip() for word in (hotwords or []) if str(word).strip())

        if timestamp_mode not in {"auto", "native", "vad", "qwen_forced"}:
            raise ValueError(
                "asr_timestamp_mode must be one of: auto, native, vad, qwen_forced"
            )
        spec = resolve_model(model_id, model_root)
        max_chunk_sec = min(
            configured_chunk_sec,
            float(spec.defaults.get("max_chunk_sec", configured_chunk_sec)),
        )
        if not vad_path:
            bundled_vad = resolve_model_root(model_root) / "silero_vad.onnx"
            if bundled_vad.is_file():
                vad_path = str(bundled_vad)
        runtime_options = SherpaRuntimeOptions(
            device=device,
            num_threads=threads,
            debug=debug,
            language=language_hint,
            hotwords=hotword_values if spec.supports_hotwords else (),
            enable_segment_timestamps=timestamp_mode in {"auto", "native"},
        )
        recognizer, runtime_info = get_sherpa_runtime().get(spec, runtime_options)

        chunker = ASRChunker(silero_onnx_model_path=vad_path)
        if strategy in {"vad", "auto", "sherpa_vad"}:
            if not vad_path:
                raise FileNotFoundError(
                    "sherpa_vad_model_path is empty; configure a Silero ONNX VAD model"
                )
            chunks = chunker.sherpa_vad_chunks(
                audio_file,
                model_path=vad_path,
                max_duration=max_chunk_sec,
            )
        elif strategy in {"fixed", "ffmpeg"}:
            chunks = chunker.fixed_chunks(audio_file, max_chunk_sec)
        else:
            raise ValueError("sherpa_chunk_strategy must be one of: vad, fixed")

        started = time.monotonic()
        segments: list[dict[str, Any]] = []
        timestamp_sources: list[str] = []
        detected_language = "unknown"
        emotions: list[str] = []
        events: list[str] = []
        failed_chunks: list[dict[str, Any]] = []

        for index, chunk in enumerate(chunks):
            if progress_callback:
                progress_callback(
                    {
                        "phase": "transcribing",
                        "progress": index / max(1, len(chunks)),
                        "message": f"正在转写 {index + 1}/{len(chunks)} 段",
                    }
                )
            try:
                chunk_result, source, chunk_segments = self._decode_chunk(
                    recognizer=recognizer,
                    chunker=chunker,
                    audio_file=audio_file,
                    chunk=chunk,
                    language=language_hint,
                    timestamp_mode=timestamp_mode,
                    aligner_path=aligner_path,
                    aligner_device=device,
                )
                segments.extend(chunk_segments)
                timestamp_sources.append(source)
                result_language = _normalize_language(
                    getattr(chunk_result, "lang", "")
                    or getattr(chunk_result, "language", "")
                    or ""
                )
                if result_language:
                    detected_language = result_language
                emotion = _clean_text(getattr(chunk_result, "emotion", ""))
                event = _clean_text(getattr(chunk_result, "event", ""))
                if emotion and emotion not in emotions:
                    emotions.append(emotion)
                if event and event not in events:
                    events.append(event)
            except Exception as exc:
                logger.exception(
                    "Sherpa ASR chunk %d/%d failed for %s",
                    index + 1,
                    len(chunks),
                    audio_file,
                )
                failed_chunks.append(
                    {
                        "index": index,
                        "start": chunk.start,
                        "end": chunk.end,
                        "error": str(exc),
                    }
                )

        if not segments and failed_chunks:
            raise RuntimeError(
                f"All {len(chunks)} sherpa ASR chunks failed: {failed_chunks[0]['error']}"
            )
        if timestamp_mode == "native" and any(source == "vad" for source in timestamp_sources):
            raise RuntimeError(
                f"Sherpa model '{model_id}' returned no native timestamps for one or more chunks"
            )

        elapsed = time.monotonic() - started
        audio_duration = chunker.probe_duration(audio_file)
        timestamp_source = (
            timestamp_sources[0]
            if timestamp_sources and len(set(timestamp_sources)) == 1
            else "mixed"
        )
        if progress_callback:
            progress_callback(
                {
                    "phase": "transcribing",
                    "progress": 1.0,
                    "message": f"转写完成：{len(segments)} 个字幕段",
                }
            )
        return {
            "language": detected_language if detected_language != "unknown" else language_hint,
            "segments": segments,
            "model": model_id,
            "runtime_provider": runtime_info.provider,
            "runtime_version": runtime_info.version,
            "timestamp_source": timestamp_source,
            "metadata": {
                "model_family": spec.family,
                "chunk_strategy": strategy,
                "chunk_count": len(chunks),
                "failed_chunks": failed_chunks,
                "elapsed_sec": round(elapsed, 3),
                "audio_duration_sec": round(audio_duration, 3),
                "rtf": round(elapsed / audio_duration, 4) if audio_duration > 0 else None,
                "hotwords": list(hotword_values),
                "hotwords_applied": bool(hotword_values and spec.supports_hotwords),
                "emotion": emotions,
                "event": events,
            },
        }

    def _decode_chunk(
        self,
        *,
        recognizer: Any,
        chunker: ASRChunker,
        audio_file: Path,
        chunk: AudioChunk,
        language: str,
        timestamp_mode: str,
        aligner_path: str,
        aligner_device: str,
    ) -> tuple[Any, str, list[dict[str, Any]]]:
        import soundfile as sf

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            wav_path = Path(handle.name)
        try:
            chunker.export_wav(audio_file, chunk, wav_path)
            samples, sample_rate = sf.read(
                str(wav_path),
                dtype="float32",
                always_2d=True,
            )
            mono = samples[:, 0].copy()
            stream = recognizer.create_stream()
            if language not in {"", "auto", "unknown"}:
                try:
                    if stream.has_option("language"):
                        stream.set_option("language", language)
                except Exception:
                    logger.debug("Sherpa stream does not expose a language option", exc_info=True)
            stream.accept_waveform(sample_rate, mono)
            recognizer.decode_stream(stream)
            result = stream.result
            text = _clean_text(getattr(result, "text", ""))

            if timestamp_mode == "qwen_forced":
                if not aligner_path:
                    raise FileNotFoundError(
                        "qwen3_aligner_model_path is empty while qwen_forced "
                        "timestamps are selected"
                    )
                result_language = _normalize_language(
                    getattr(result, "lang", "")
                    or getattr(result, "language", "")
                    or language
                )
                units = get_qwen_forced_aligner().align(
                    wav_path,
                    text,
                    result_language,
                    model_path=aligner_path,
                    device=aligner_device,
                )
                return result, "qwen_forced", _merge_timed_units(units, offset=chunk.start)

            native = self._native_segments(result, chunk.start)
            if native and timestamp_mode in {"auto", "native"}:
                return result, "native_segment", native

            token_segments = self._token_segments(result, chunk.start)
            if token_segments and timestamp_mode in {"auto", "native"}:
                return result, "native_token", token_segments

            if text:
                return (
                    result,
                    "vad",
                    [
                        {
                            "start": round(chunk.start, 3),
                            "end": round(chunk.end, 3),
                            "text": text,
                        }
                    ],
                )
            return result, "vad", []
        finally:
            wav_path.unlink(missing_ok=True)

    @staticmethod
    def _native_segments(result: Any, offset: float) -> list[dict[str, Any]]:
        starts = list(getattr(result, "segment_timestamps", []) or [])
        durations = list(getattr(result, "segment_durations", []) or [])
        texts = list(getattr(result, "segment_texts", []) or [])
        if not starts or len(starts) != len(durations) or len(starts) != len(texts):
            return []
        segments: list[dict[str, Any]] = []
        for index, (start, duration, text) in enumerate(zip(starts, durations, texts)):
            cleaned = _clean_text(text)
            if not cleaned:
                continue
            relative_start = float(start)
            relative_end = relative_start + float(duration)
            if relative_end <= relative_start:
                next_start = next(
                    (
                        float(candidate)
                        for candidate in starts[index + 1 :]
                        if float(candidate) > relative_start
                    ),
                    relative_start + 0.01,
                )
                relative_end = max(relative_start + 0.01, next_start)
            segments.append(
                {
                    "start": round(offset + relative_start, 3),
                    "end": round(offset + relative_end, 3),
                    "text": cleaned,
                }
            )
        return segments

    @staticmethod
    def _token_segments(result: Any, offset: float) -> list[dict[str, Any]]:
        tokens = list(getattr(result, "tokens", []) or [])
        starts = list(getattr(result, "timestamps", []) or [])
        durations = list(getattr(result, "durations", []) or [])
        if not tokens or len(tokens) != len(starts):
            return []
        if len(durations) != len(tokens):
            durations = [0.0 for _ in tokens]
        units = [
            {
                "text": token,
                "start": float(start),
                "end": float(start) + max(0.01, float(duration)),
            }
            for token, start, duration in zip(tokens, starts, durations)
        ]
        return _merge_timed_units(units, offset=offset, token_stream=True)

    def to_segments(self, result: dict[str, Any]) -> list[TranscriptSegment]:
        return [
            TranscriptSegment(
                start=float(segment.get("start", 0.0)),
                end=float(segment.get("end", 0.0)),
                text=_clean_text(segment.get("text", "")),
                speaker=segment.get("speaker"),
                confidence=segment.get("confidence"),
            )
            for segment in result.get("segments", [])
            if _clean_text(segment.get("text", ""))
        ]

    def to_srt(self, segments: list[TranscriptSegment]) -> str:
        lines: list[str] = []
        for index, segment in enumerate(segments, 1):
            text = f"[{segment.speaker}] {segment.text}" if segment.speaker else segment.text
            lines.append(
                f"{index}\n{self._format_time(segment.start)} --> "
                f"{self._format_time(segment.end)}\n{text}\n"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_time(seconds: float) -> str:
        total_ms = max(0, round(float(seconds) * 1000))
        hours, remainder = divmod(total_ms, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, milliseconds = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


_service: SherpaOnnxASRService | None = None


def get_sherpa_onnx_service() -> SherpaOnnxASRService:
    global _service
    if _service is None:
        _service = SherpaOnnxASRService()
    return _service


def release_sherpa_onnx_service() -> None:
    global _service
    if _service is not None:
        _service.release()
        _service = None
