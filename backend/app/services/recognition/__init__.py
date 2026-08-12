"""Recognition service entrypoint."""

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable

from app.core.settings import get_runtime_settings
from app.services.recognition.base import ASRService

logger = logging.getLogger(__name__)

SUPPORTED_ASR_PROVIDERS = {"moss_cpp", "sherpa_onnx", "siliconflow"}

__all__ = [
    "SUPPORTED_ASR_PROVIDERS",
    "get_asr_service",
    "get_diarization_service",
    "release_asr_models",
    "transcribe_audio",
]


def get_diarization_service():
    from app.services.recognition.diarization import get_diarization_service as _get_service

    return _get_service()


def get_asr_service(provider: str | None = None) -> ASRService:
    """Get the configured singleton ASR service."""
    if provider in SUPPORTED_ASR_PROVIDERS:
        provider_id = provider
    else:
        from app.core.model_router import resolve_asr_binding

        provider_id = resolve_asr_binding(
            get_runtime_settings(),
            task_options={"asr_provider": provider} if provider else None,
        ).provider
    if provider_id == "sherpa_onnx":
        from app.services.recognition.sherpa_onnx_asr import get_sherpa_onnx_service

        return get_sherpa_onnx_service()
    if provider_id == "siliconflow":
        from app.services.recognition.siliconflow_asr import get_siliconflow_service

        return get_siliconflow_service()
    if provider_id == "moss_cpp":
        from app.services.recognition.moss_cpp_asr import get_moss_cpp_service

        return get_moss_cpp_service()
    supported = ", ".join(sorted(SUPPORTED_ASR_PROVIDERS))
    raise ValueError(f"Unsupported ASR provider '{provider_id}'. Supported providers: {supported}")


def release_asr_models() -> None:
    """Release ASR-owned GPU resources without binding queue.py to a provider."""
    from app.services.recognition.moss_cpp_asr import release_moss_cpp_service
    from app.services.recognition.sherpa_onnx_asr import release_sherpa_onnx_service
    from app.services.recognition.siliconflow_asr import release_siliconflow_service

    release_moss_cpp_service()
    release_sherpa_onnx_service()
    release_siliconflow_service()
    from app.services.recognition.diarization import release_diarization_service

    release_diarization_service()


async def transcribe_audio(
    audio_path: str,
    language: str | None = None,
    output_dir: Path | None = None,
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    provider: str | None = None,
    model: str | None = None,
    diarize: bool = True,
    chunk_strategy: str | None = None,
    timestamp_mode: str | None = None,
    hotwords: list[str] | None = None,
    audio_processing_flow: str | None = None,
    diarization_audio_path: str | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Transcribe audio with the configured ASR provider and optionally write an SRT file."""
    from app.core.model_router import resolve_asr_binding

    options: dict[str, Any] = {}
    if provider:
        options["asr_provider"] = provider
    if model:
        options["asr_model"] = model
    if audio_processing_flow:
        options["audio_processing_flow"] = audio_processing_flow
    if chunk_strategy:
        options["asr_chunk_strategy"] = chunk_strategy
    if timestamp_mode:
        options["asr_timestamp_mode"] = timestamp_mode
    if num_speakers is not None:
        options["num_speakers"] = num_speakers
    if not diarize:
        options["disable_diarization"] = True

    binding = resolve_asr_binding(get_runtime_settings(), task_options=options, language=language)
    if not binding.configured:
        raise RuntimeError(binding.reason or f"ASR provider '{binding.provider}' is not configured")
    provider_id = binding.provider
    service = get_asr_service(provider_id)

    def _run_transcribe() -> dict[str, Any]:
        if provider_id == "sherpa_onnx":
            return service.transcribe(
                audio_path,
                language=binding.language,
                diarize=False,
                num_speakers=binding.num_speakers,
                chunk_strategy=binding.chunk_strategy,
                hotwords=hotwords,
                runtime_config=binding.request_kwargs,
                progress_callback=progress_callback,
            )
        if provider_id == "siliconflow":
            return service.transcribe(
                audio_path,
                language=binding.language,
                diarize=binding.diarize,
                num_speakers=binding.num_speakers,
                chunk_strategy=binding.chunk_strategy,
            )
        if provider_id == "moss_cpp":
            return service.transcribe(
                audio_path,
                language=binding.language,
                diarize=True,
                num_speakers=binding.num_speakers,
                progress_callback=progress_callback,
                **binding.request_kwargs,
            )
        return service.transcribe(
            audio_path,
            language=binding.language,
            diarize=binding.diarize,
            num_speakers=binding.num_speakers,
        )

    result = await asyncio.to_thread(_run_transcribe)
    from app.services.recognition.quality import filter_asr_segments

    raw_segments = result.get("segments")
    if isinstance(raw_segments, list):
        filtered_segments, quality_diagnostics = filter_asr_segments(raw_segments)
        result["segments"] = filtered_segments
        result["quality_diagnostics"] = list(result.get("quality_diagnostics") or []) + quality_diagnostics
    if binding.diarize and provider_id != "moss_cpp":
        raw_segments = result.get("segments")
        if isinstance(raw_segments, list) and raw_segments:
            if progress_callback:
                progress_callback({
                    "phase": "diarizing",
                    "progress": 0.0,
                    "message": "正在执行完整音频说话人分离",
                })
            cache_path = Path(output_dir) / "diarization.json" if output_dir else None
            diarization_result = await asyncio.to_thread(
                get_diarization_service().apply,
                diarization_audio_path or audio_path,
                raw_segments,
                num_speakers=binding.num_speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
                cache_path=cache_path,
            )
            result["segments"] = diarization_result["segments"]
            result["speakers"] = diarization_result["speakers"]
            result["speaker_count"] = diarization_result["speaker_count"]
            result["diarization"] = diarization_result["diarization"]
            result["diarization_cache_hit"] = diarization_result["cache_hit"]
            if progress_callback:
                progress_callback({
                    "phase": "diarizing",
                    "progress": 1.0,
                    "message": (
                        f"说话人分离完成：{diarization_result['speaker_count']} 位说话人"
                    ),
                })
    segments = service.to_segments(result)
    srt_content = service.to_srt(segments)
    detected_language = result.get("language", language or "unknown")
    speakers = result.get("speakers")
    if not isinstance(speakers, list):
        speakers = sorted({segment.speaker for segment in segments if segment.speaker})

    # Save SRT file to output_dir if provided
    srt_path = None
    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        srt_path = output_dir / f"{Path(audio_path).stem}.srt"
        srt_path.write_text(srt_content, encoding="utf-8")
        logger.info(f"Saved SRT to: {srt_path}")

    return {
        "provider": provider_id,
        "audio_processing_flow": "moss" if provider_id == "moss_cpp" else "asr",
        "language": detected_language or language or "unknown",
        "segments": [s.model_dump() if hasattr(s, 'model_dump') else s for s in segments],
        "speakers": speakers,
        "speaker_count": int(result.get("speaker_count", len(speakers))),
        "diarization": result.get("diarization", "pyannote" if binding.diarize else "none"),
        "quality_diagnostics": result.get("quality_diagnostics", []),
        "model": result.get("model", binding.model),
        "runtime_provider": result.get("runtime_provider"),
        "runtime_version": result.get("runtime_version"),
        "timestamp_source": result.get("timestamp_source"),
        "asr_metadata": result.get("metadata", {}),
        "srt": srt_content,
        "srt_path": str(srt_path) if srt_path else None,
    }
