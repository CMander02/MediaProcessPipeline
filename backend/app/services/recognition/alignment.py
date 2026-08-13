"""Optional Qwen3 ForcedAligner post-processing for sherpa transcripts."""

from __future__ import annotations

import gc
import logging
from pathlib import Path
from threading import RLock
from typing import Any

logger = logging.getLogger(__name__)

_LANGUAGE_NAMES = {
    "zh": "Chinese",
    "chinese": "Chinese",
    "en": "English",
    "english": "English",
    "yue": "Cantonese",
    "cantonese": "Cantonese",
    "ja": "Japanese",
    "jp": "Japanese",
    "japanese": "Japanese",
    "ko": "Korean",
    "korean": "Korean",
    "de": "German",
    "german": "German",
    "fr": "French",
    "french": "French",
    "es": "Spanish",
    "spanish": "Spanish",
    "it": "Italian",
    "italian": "Italian",
    "pt": "Portuguese",
    "portuguese": "Portuguese",
    "ru": "Russian",
    "russian": "Russian",
}


class QwenForcedAlignerRuntime:
    def __init__(self) -> None:
        self._lock = RLock()
        self._aligner: Any | None = None
        self._key: tuple[str, str] | None = None

    def align(
        self,
        audio_path: str | Path,
        text: str,
        language: str,
        *,
        model_path: str | Path,
        device: str = "auto",
    ) -> list[dict[str, Any]]:
        if not text.strip():
            return []
        aligner = self._get(model_path, device)
        language_name = _LANGUAGE_NAMES.get(language.strip().lower(), language.strip())
        if not language_name or language_name.lower() in {"auto", "unknown"}:
            language_name = "Chinese"
        results = aligner.align(
            audio=str(Path(audio_path).resolve()),
            text=text,
            language=language_name,
        )
        if not results:
            return []
        return [
            {
                "text": str(item.text),
                "start": float(item.start_time),
                "end": float(item.end_time),
            }
            for item in results[0]
        ]

    def _get(self, model_path: str | Path, device: str) -> Any:
        resolved = Path(model_path).expanduser().resolve()
        if not resolved.is_dir():
            raise FileNotFoundError(f"Qwen3 ForcedAligner model not found: {resolved}")
        normalized_device = str(device or "auto").strip().lower()
        if normalized_device == "auto":
            normalized_device = "cuda" if self._cuda_available() else "cpu"
        key = (str(resolved), normalized_device)
        with self._lock:
            if self._aligner is not None and self._key == key:
                return self._aligner
            self.release()
            try:
                import torch
                from qwen_asr import Qwen3ForcedAligner
            except ImportError as exc:
                raise RuntimeError(
                    "qwen-asr is required when asr_timestamp_mode is qwen_forced"
                ) from exc
            dtype = torch.bfloat16 if normalized_device.startswith("cuda") else torch.float32
            logger.info(
                "Loading Qwen3 ForcedAligner %s on %s",
                resolved,
                normalized_device,
            )
            self._aligner = Qwen3ForcedAligner.from_pretrained(
                str(resolved),
                dtype=dtype,
                device_map=normalized_device,
            )
            self._key = key
            return self._aligner

    def release(self) -> None:
        with self._lock:
            self._aligner = None
            self._key = None
            gc.collect()
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass

    @staticmethod
    def _cuda_available() -> bool:
        try:
            import torch

            return bool(torch.cuda.is_available())
        except ImportError:
            return False


_runtime = QwenForcedAlignerRuntime()


def get_qwen_forced_aligner() -> QwenForcedAlignerRuntime:
    return _runtime


def release_qwen_forced_aligner() -> None:
    _runtime.release()
