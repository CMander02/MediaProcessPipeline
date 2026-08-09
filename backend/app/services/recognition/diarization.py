"""Provider-independent full-audio speaker diarization."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.core.network import runtime_proxy_url
from app.core.settings import get_runtime_settings

logger = logging.getLogger(__name__)


class DiarizationService:
    """Own the Pyannote pipeline and map global speaker turns to ASR segments."""

    def __init__(self) -> None:
        self._pipeline: Any | None = None
        self._pipeline_key: tuple[str, str, str, str, int] | None = None
        self._last_diarize_df: Any | None = None
        self._last_audio_path: str | None = None

    def release(self) -> None:
        self._pipeline = None
        self._pipeline_key = None
        self._last_diarize_df = None
        self._last_audio_path = None

    def get_pyannote_pipeline(self) -> Any | None:
        return self._pipeline

    def get_last_diarization(self) -> tuple[Any | None, str | None]:
        return self._last_diarize_df, self._last_audio_path

    @staticmethod
    def _normalize_proxy_url(raw: str) -> str:
        proxy = raw.strip()
        if proxy and "://" not in proxy:
            proxy = f"http://{proxy}"
        return proxy

    @staticmethod
    def _redact_proxy_url(raw: str) -> str:
        try:
            parsed = urlsplit(raw)
            host = parsed.hostname or ""
            port = f":{parsed.port}" if parsed.port else ""
            return urlunsplit((parsed.scheme, f"{host}{port}", "", "", ""))
        except Exception:
            return "<proxy>"

    def _configure_huggingface_proxy(self) -> None:
        proxy = runtime_proxy_url("hf_proxy")
        if proxy == "":
            logger.info("Hugging Face proxy disabled by hf_proxy setting")
            return
        if not proxy:
            return
        proxy = self._normalize_proxy_url(proxy)
        for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            os.environ[key] = proxy
        logger.info("Using Hugging Face proxy: %s", self._redact_proxy_url(proxy))

    @staticmethod
    def _config_file(model_path: str) -> Path:
        path = Path(model_path).expanduser()
        config_file = path / "config.yaml" if path.is_dir() else path
        if not config_file.is_file():
            raise FileNotFoundError(f"Pyannote config.yaml not found: {config_file}")
        return config_file

    @staticmethod
    def _checkpoint_file(model_path: str, dependency_name: str) -> Path:
        """Resolve a local model directory to the checkpoint Pyannote expects."""
        path = Path(model_path).expanduser()
        checkpoint = path / "pytorch_model.bin" if path.is_dir() else path
        if not checkpoint.is_file():
            raise FileNotFoundError(
                f"Pyannote {dependency_name} checkpoint not found: {checkpoint}"
            )
        return checkpoint.resolve()

    def _prepare_model_config(self, model_path: str) -> str:
        """Point a local Pyannote pipeline config at its local sub-models."""
        import yaml

        rt = get_runtime_settings()
        config_file = self._config_file(model_path)
        data = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
        pipeline = data.get("pipeline") or {}
        params = pipeline.get("params") or {}

        replacements = {
            "segmentation": str(getattr(rt, "pyannote_segmentation_path", "") or ""),
            "embedding": str(getattr(rt, "pyannote_embedding_path", "") or ""),
        }
        changed: dict[str, str] = {}
        for name, configured_path in replacements.items():
            if not configured_path:
                continue
            params[name] = str(self._checkpoint_file(configured_path, name))
            changed[name] = params[name]

        if not changed:
            return str(config_file.resolve())

        pipeline["params"] = params
        data["pipeline"] = pipeline
        rendered = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        digest = hashlib.sha1(
            (str(config_file.resolve()) + "\n" + rendered).encode("utf-8")
        ).hexdigest()[:12]
        cache_dir = Path(str(rt.data_root)) / ".cache" / "pyannote"
        cache_dir.mkdir(parents=True, exist_ok=True)
        resolved_config = cache_dir / f"{config_file.stem}-{digest}.yaml"
        resolved_config.write_text(rendered, encoding="utf-8")
        logger.info(
            "Resolved local Pyannote dependencies: %s",
            ", ".join(f"{name}={path}" for name, path in changed.items()),
        )
        return str(resolved_config)

    @staticmethod
    def _patch_torch_load(torch: Any) -> None:
        """Allow trusted local Lightning checkpoints with PyTorch 2.6+."""
        if getattr(torch.load, "_mpp_weights_only_patched", False):
            return
        original_load = torch.load

        def patched_load(*args: Any, **kwargs: Any) -> Any:
            kwargs["weights_only"] = False
            return original_load(*args, **kwargs)

        patched_load._mpp_weights_only_patched = True  # type: ignore[attr-defined]
        torch.load = patched_load

    def _ensure_pipeline(self) -> Any:
        import torch
        from pyannote.audio import Pipeline

        rt = get_runtime_settings()
        model_path = str(rt.pyannote_model_path or "")
        if not model_path:
            raise RuntimeError("Pyannote diarization is enabled but pyannote_model_path is empty")
        device = str(getattr(rt, "qwen3_device", "cuda") or "cuda")
        batch_size = max(1, int(getattr(rt, "diarization_batch_size", 16) or 16))
        key = (
            model_path,
            str(getattr(rt, "pyannote_segmentation_path", "") or ""),
            str(getattr(rt, "pyannote_embedding_path", "") or ""),
            device,
            batch_size,
        )
        if self._pipeline is not None and self._pipeline_key == key:
            return self._pipeline

        if device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("Pyannote device is CUDA but torch.cuda.is_available() is false")

        self._configure_huggingface_proxy()
        config_path = self._prepare_model_config(model_path)
        self._patch_torch_load(torch)
        token = str(getattr(rt, "hf_token", "") or "")
        logger.info("Loading Pyannote diarization pipeline: %s", config_path)
        if token:
            try:
                pipeline = Pipeline.from_pretrained(config_path, use_auth_token=token)
            except TypeError:
                pipeline = Pipeline.from_pretrained(config_path, token=token)
        else:
            pipeline = Pipeline.from_pretrained(config_path)
        pipeline = pipeline.to(torch.device(device))

        for attr in ("segmentation_batch_size", "embedding_batch_size"):
            if hasattr(pipeline, attr):
                setattr(pipeline, attr, batch_size)
        for attr in ("_segmentation", "_embedding"):
            inference = getattr(pipeline, attr, None)
            if inference is not None and hasattr(inference, "batch_size"):
                inference.batch_size = batch_size

        self._pipeline = pipeline
        self._pipeline_key = key
        logger.info("Pyannote pipeline ready: device=%s batch_size=%d", device, batch_size)
        return pipeline

    @staticmethod
    def _load_audio(audio_path: Path) -> dict[str, Any]:
        import numpy as np
        import soundfile as sf
        import torch
        import torchaudio

        audio, sample_rate = sf.read(str(audio_path), dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = np.mean(audio, axis=1)
        if sample_rate != 16000:
            waveform = torch.from_numpy(np.asarray(audio, dtype=np.float32))
            audio = torchaudio.functional.resample(waveform, sample_rate, 16000).numpy()
            sample_rate = 16000
        waveform = torch.from_numpy(np.asarray(audio, dtype=np.float32)[None, :])
        return {"waveform": waveform, "sample_rate": sample_rate}

    @staticmethod
    def _annotation_to_turns(output: Any) -> list[dict[str, Any]]:
        annotation = getattr(output, "exclusive_speaker_diarization", None)
        if annotation is None:
            annotation = getattr(output, "speaker_diarization", None)
        if annotation is None:
            annotation = output

        turns: list[dict[str, Any]] = []
        if hasattr(annotation, "itertracks"):
            for segment, _, speaker in annotation.itertracks(yield_label=True):
                turns.append(
                    {
                        "start": round(float(segment.start), 3),
                        "end": round(float(segment.end), 3),
                        "speaker": str(speaker),
                    }
                )
        else:
            for item in annotation:
                if not isinstance(item, (list, tuple)) or len(item) < 2:
                    continue
                segment, speaker = item[0], item[-1]
                turns.append(
                    {
                        "start": round(float(segment.start), 3),
                        "end": round(float(segment.end), 3),
                        "speaker": str(speaker),
                    }
                )
        return sorted(turns, key=lambda item: (item["start"], item["end"], item["speaker"]))

    def _cache_signature(
        self,
        audio_path: Path,
        *,
        num_speakers: int | None,
        min_speakers: int | None,
        max_speakers: int | None,
    ) -> dict[str, Any]:
        rt = get_runtime_settings()
        stat = audio_path.stat()
        return {
            "version": 1,
            "audio_path": str(audio_path.resolve()),
            "audio_size": stat.st_size,
            "audio_mtime_ns": stat.st_mtime_ns,
            "model_path": str(rt.pyannote_model_path or ""),
            "segmentation_path": str(rt.pyannote_segmentation_path or ""),
            "embedding_path": str(rt.pyannote_embedding_path or ""),
            "num_speakers": num_speakers,
            "min_speakers": min_speakers,
            "max_speakers": max_speakers,
        }

    @staticmethod
    def _load_cache(
        cache_path: Path | None,
        signature: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        if cache_path is None or not cache_path.is_file():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if payload.get("signature") != signature:
                return None
            turns = payload.get("turns")
            if not isinstance(turns, list):
                return None
            return [dict(item) for item in turns if isinstance(item, dict)]
        except (OSError, ValueError, TypeError):
            logger.warning("Diarization cache could not be read: %s", cache_path, exc_info=True)
            return None

    @staticmethod
    def _write_cache(
        cache_path: Path | None,
        signature: dict[str, Any],
        turns: list[dict[str, Any]],
    ) -> None:
        if cache_path is None:
            return
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = cache_path.with_suffix(f"{cache_path.suffix}.tmp")
        temp_path.write_text(
            json.dumps({"signature": signature, "turns": turns}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(cache_path)

    @staticmethod
    def _split_text_by_weights(text: str, weights: list[float]) -> list[str]:
        if len(weights) <= 1 or len(text) <= 1:
            return [text]
        total = sum(max(0.0, weight) for weight in weights) or float(len(weights))
        boundaries = [0]
        cumulative = 0.0
        for weight in weights[:-1]:
            cumulative += max(0.0, weight)
            target = round(len(text) * cumulative / total)
            boundaries.append(max(boundaries[-1], min(len(text), target)))
        boundaries.append(len(text))
        return [text[boundaries[index]:boundaries[index + 1]].strip() for index in range(len(weights))]

    @staticmethod
    def assign_speakers(
        segments: list[dict[str, Any]],
        turns: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        import numpy as np

        if not turns:
            return [dict(segment) for segment in segments]
        starts = np.asarray([float(turn["start"]) for turn in turns], dtype=float)
        ends = np.asarray([float(turn["end"]) for turn in turns], dtype=float)
        speakers = [str(turn["speaker"]) for turn in turns]

        assigned = 0
        result: list[dict[str, Any]] = []
        for source_segment in segments:
            segment = dict(source_segment)
            start = float(segment.get("start") or 0.0)
            end = float(segment.get("end") or 0.0)
            if end <= start:
                result.append(segment)
                continue
            overlaps = np.minimum(end, ends) - np.maximum(start, starts)
            indexes = [int(index) for index in np.flatnonzero(overlaps > 0.05)]
            if not indexes:
                result.append(segment)
                continue
            if len({speakers[index] for index in indexes}) == 1:
                segment["speaker"] = speakers[indexes[0]]
                assigned += 1
                result.append(segment)
                continue

            intervals = [
                (
                    max(start, float(starts[index])),
                    min(end, float(ends[index])),
                    speakers[index],
                )
                for index in indexes
            ]
            intervals.sort(key=lambda item: (item[0], item[1]))
            text_parts = DiarizationService._split_text_by_weights(
                str(segment.get("text") or ""),
                [item[1] - item[0] for item in intervals],
            )
            for interval, text_part in zip(intervals, text_parts, strict=True):
                if not text_part:
                    continue
                result.append(
                    {
                        **segment,
                        "start": round(interval[0], 3),
                        "end": round(interval[1], 3),
                        "speaker": interval[2],
                        "text": text_part,
                    }
                )
                assigned += 1
        logger.info(
            "Mapped global diarization to ASR segments: assigned=%d total=%d turns=%d",
            assigned,
            len(result),
            len(turns),
        )
        return result

    def apply(
        self,
        audio_path: str | Path,
        segments: list[dict[str, Any]],
        *,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        cache_path: str | Path | None = None,
    ) -> dict[str, Any]:
        import pandas as pd
        import torch

        audio_file = Path(audio_path)
        if not audio_file.is_file():
            raise FileNotFoundError(f"Diarization audio file not found: {audio_file}")
        pipeline = self._ensure_pipeline()
        resolved_cache = Path(cache_path) if cache_path else None
        signature = self._cache_signature(
            audio_file,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )
        turns = self._load_cache(resolved_cache, signature)
        cache_hit = turns is not None
        if turns is None:
            kwargs: dict[str, int] = {}
            if num_speakers is not None:
                kwargs["num_speakers"] = int(num_speakers)
            else:
                if min_speakers is not None:
                    kwargs["min_speakers"] = int(min_speakers)
                if max_speakers is not None:
                    kwargs["max_speakers"] = int(max_speakers)
            logger.info(
                "Running full-audio Pyannote diarization: audio=%s constraints=%s",
                audio_file,
                kwargs or "auto",
            )
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            output = pipeline(self._load_audio(audio_file), **kwargs)
            turns = self._annotation_to_turns(output)
            self._write_cache(resolved_cache, signature, turns)
        else:
            logger.info("Loaded diarization cache: %s (%d turns)", resolved_cache, len(turns))

        diarize_df = pd.DataFrame(turns, columns=["start", "end", "speaker"])
        self._last_diarize_df = diarize_df
        self._last_audio_path = str(audio_file)
        mapped = self.assign_speakers(segments, turns)
        speakers = sorted({str(turn["speaker"]) for turn in turns})
        return {
            "segments": mapped,
            "turns": turns,
            "speakers": speakers,
            "speaker_count": len(speakers),
            "diarization": "pyannote",
            "cache_hit": cache_hit,
        }


_service: DiarizationService | None = None


def get_diarization_service() -> DiarizationService:
    global _service
    if _service is None:
        _service = DiarizationService()
    return _service


def release_diarization_service() -> None:
    if _service is not None:
        _service.release()
