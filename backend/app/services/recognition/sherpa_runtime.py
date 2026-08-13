"""Single-model sherpa-onnx runtime owner."""

from __future__ import annotations

import gc
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from app.services.recognition.sherpa_catalog import SherpaModelSpec

logger = logging.getLogger(__name__)
_dll_directory_handles: list[Any] = []


def _prepare_windows_cuda_dlls() -> None:
    """Expose the CUDA and cuDNN DLLs bundled with PyTorch to ONNX Runtime."""
    if os.name != "nt" or _dll_directory_handles:
        return
    try:
        import torch
    except ImportError:
        return
    torch_lib = Path(torch.__file__).resolve().parent / "lib"
    if torch_lib.is_dir():
        _dll_directory_handles.append(os.add_dll_directory(str(torch_lib)))


@dataclass(frozen=True)
class SherpaRuntimeOptions:
    device: str = "auto"
    num_threads: int = 4
    debug: bool = False
    language: str = ""
    hotwords: tuple[str, ...] = ()
    enable_segment_timestamps: bool = True


@dataclass(frozen=True)
class SherpaRuntimeInfo:
    model_id: str
    family: str
    provider: str
    version: str


class SherpaRuntime:
    """Own at most one native recognizer and release it on model switches."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._recognizer: Any | None = None
        self._key: tuple[Any, ...] | None = None
        self._info: SherpaRuntimeInfo | None = None

    @property
    def info(self) -> SherpaRuntimeInfo | None:
        return self._info

    def get(
        self,
        spec: SherpaModelSpec,
        options: SherpaRuntimeOptions,
    ) -> tuple[Any, SherpaRuntimeInfo]:
        with self._lock:
            providers = self._provider_candidates(options.device)
            model_files = tuple(
                sorted(
                    (
                        name,
                        str(path),
                        path.stat().st_size,
                        path.stat().st_mtime_ns,
                    )
                    for name, path in spec.files.items()
                )
            )
            manifest = spec.directory / "manifest.json"
            key_base = (
                spec.id,
                model_files,
                manifest.stat().st_mtime_ns,
                max(1, int(options.num_threads)),
                bool(options.debug),
                options.language,
                options.hotwords,
                bool(options.enable_segment_timestamps),
            )
            if self._recognizer is not None and self._key == (*key_base, self._info.provider):
                return self._recognizer, self._info

            self.release()
            errors: list[str] = []
            for provider in providers:
                try:
                    recognizer = self._create(spec, options, provider)
                except Exception as exc:
                    errors.append(f"{provider}: {exc}")
                    if options.device != "auto":
                        raise RuntimeError(
                            f"Failed to load sherpa model '{spec.id}' with {provider}: {exc}"
                        ) from exc
                    logger.warning(
                        "Sherpa model %s failed with provider %s; trying next provider: %s",
                        spec.id,
                        provider,
                        exc,
                    )
                    continue

                import sherpa_onnx

                self._recognizer = recognizer
                self._info = SherpaRuntimeInfo(
                    model_id=spec.id,
                    family=spec.family,
                    provider=provider,
                    version=str(sherpa_onnx.__version__),
                )
                self._key = (*key_base, provider)
                logger.info(
                    "Loaded sherpa model %s (%s) with provider=%s, threads=%d",
                    spec.id,
                    spec.family,
                    provider,
                    options.num_threads,
                )
                return recognizer, self._info

            raise RuntimeError(
                f"Failed to load sherpa model '{spec.id}': {'; '.join(errors)}"
            )

    def release(self) -> None:
        with self._lock:
            self._recognizer = None
            self._key = None
            self._info = None
            gc.collect()

    @staticmethod
    def _provider_candidates(device: str) -> tuple[str, ...]:
        normalized = str(device or "auto").strip().lower()
        if normalized == "auto":
            try:
                import sherpa_onnx

                if "+cuda" in str(sherpa_onnx.__version__).lower():
                    return ("cuda", "cpu")
            except ImportError:
                pass
            return ("cpu",)
        if normalized not in {"cpu", "cuda"}:
            raise ValueError("sherpa_device must be one of: auto, cuda, cpu")
        return (normalized,)

    @staticmethod
    def _create(
        spec: SherpaModelSpec,
        options: SherpaRuntimeOptions,
        provider: str,
    ) -> Any:
        if provider == "cuda":
            _prepare_windows_cuda_dlls()
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise RuntimeError(
                "sherpa-onnx is not installed; install the local-asr dependency group"
            ) from exc

        common = {
            "num_threads": max(1, int(options.num_threads)),
            "debug": bool(options.debug),
            "provider": provider,
        }
        if spec.family == "qwen3_asr":
            return sherpa_onnx.OfflineRecognizer.from_qwen3_asr(
                conv_frontend=spec.file("conv_frontend"),
                encoder=spec.file("encoder"),
                decoder=spec.file("decoder"),
                tokenizer=spec.file("tokenizer"),
                hotwords=",".join(word for word in options.hotwords if word.strip()),
                max_total_len=int(spec.defaults.get("max_total_len", 2048)),
                max_new_tokens=int(spec.defaults.get("max_new_tokens", 1024)),
                **common,
            )
        if spec.family == "sense_voice":
            language = options.language if options.language in spec.languages else "auto"
            return sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=spec.file("model"),
                tokens=spec.file("tokens"),
                language=language,
                use_itn=bool(spec.defaults.get("use_itn", True)),
                **common,
            )
        if spec.family == "paraformer":
            return sherpa_onnx.OfflineRecognizer.from_paraformer(
                paraformer=spec.file("model"),
                tokens=spec.file("tokens"),
                **common,
            )
        if spec.family == "whisper":
            language = "" if options.language in {"", "auto"} else options.language
            return sherpa_onnx.OfflineRecognizer.from_whisper(
                encoder=spec.file("encoder"),
                decoder=spec.file("decoder"),
                tokens=spec.file("tokens"),
                language=language,
                task=str(spec.defaults.get("task", "transcribe")),
                enable_token_timestamps=bool(
                    spec.defaults.get("enable_token_timestamps", False)
                ),
                enable_segment_timestamps=bool(options.enable_segment_timestamps),
                **common,
            )
        raise ValueError(f"Unsupported sherpa model family: {spec.family}")


_runtime = SherpaRuntime()


def get_sherpa_runtime() -> SherpaRuntime:
    return _runtime


def release_sherpa_runtime() -> None:
    _runtime.release()
