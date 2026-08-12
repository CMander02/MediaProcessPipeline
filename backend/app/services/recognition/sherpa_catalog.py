"""Model catalog and validation for the unified sherpa-onnx ASR backend."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from packaging.version import Version

DEFAULT_SHERPA_MODEL_ID = "sensevoice-small-int8"
MIN_SHERPA_ONNX_VERSION = "1.13.4"
_checksum_cache: dict[tuple[Any, ...], bool] = {}


@dataclass(frozen=True)
class SherpaModelSpec:
    """Resolved model bundle consumed by :mod:`sherpa_onnx`."""

    id: str
    display_name: str
    family: str
    languages: tuple[str, ...]
    directory: Path
    files: dict[str, Path]
    source: str
    license: str
    size_bytes: int
    verified: bool
    compatible: bool
    defaults: dict[str, Any]
    supports_hotwords: bool = False
    supports_native_timestamps: bool = False
    supports_token_timestamps: bool = False

    def file(self, name: str) -> str:
        return str(self.files[name])

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "family": self.family,
            "languages": list(self.languages),
            "directory": str(self.directory),
            "files": {key: str(value) for key, value in self.files.items()},
            "source": self.source,
            "license": self.license,
            "size_bytes": self.size_bytes,
            "verified": self.verified,
            "compatible": self.compatible,
            "defaults": dict(self.defaults),
            "supports_hotwords": self.supports_hotwords,
            "supports_native_timestamps": self.supports_native_timestamps,
            "supports_token_timestamps": self.supports_token_timestamps,
        }


_BUILTIN_MODELS: dict[str, dict[str, Any]] = {
    "qwen3-asr-1.7b-onnx": {
        "display_name": "Qwen3-ASR 1.7B INT8",
        "family": "qwen3_asr",
        "languages": ("auto", "zh", "en", "yue", "ja", "ko", "de", "fr", "es"),
        "files": {
            "conv_frontend": "conv_frontend.onnx",
            "encoder": "encoder.int8.onnx",
            "decoder": "decoder.int8.onnx",
            "tokenizer": "tokenizer",
        },
        "supports_hotwords": True,
        "defaults": {"max_total_len": 2048, "max_new_tokens": 1024, "max_chunk_sec": 30},
    },
    "sensevoice-small-int8": {
        "display_name": "SenseVoice Small INT8",
        "family": "sense_voice",
        "languages": ("auto", "zh", "en", "yue", "ja", "ko"),
        "files": {"model": "model.int8.onnx", "tokens": "tokens.txt"},
        "supports_token_timestamps": True,
        "defaults": {"use_itn": True, "max_chunk_sec": 30},
    },
    "paraformer-zh-int8": {
        "display_name": "Paraformer Chinese INT8",
        "family": "paraformer",
        "languages": ("zh", "en"),
        "files": {"model": "model.int8.onnx", "tokens": "tokens.txt"},
        "defaults": {"max_chunk_sec": 30},
    },
    "whisper-small-multi-int8": {
        "display_name": "Whisper Small Multilingual INT8",
        "family": "whisper",
        "languages": ("auto", "zh", "en", "yue", "ja", "ko", "de", "fr", "es"),
        "files": {
            "encoder": "small-encoder.int8.onnx",
            "decoder": "small-decoder.int8.onnx",
            "tokens": "small-tokens.txt",
        },
        "supports_native_timestamps": True,
        "defaults": {
            "task": "transcribe",
            "enable_token_timestamps": False,
            "max_chunk_sec": 30,
        },
    },
}


def default_model_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "MediaProcessPipeline" / "models" / "sherpa-onnx"


def resolve_model_root(configured: str | Path = "") -> Path:
    value = str(configured or "").strip()
    return Path(value).expanduser().resolve() if value else default_model_root().resolve()


def model_ids() -> tuple[str, ...]:
    return tuple(_BUILTIN_MODELS)


def model_definition(model_id: str) -> dict[str, Any]:
    try:
        return dict(_BUILTIN_MODELS[model_id])
    except KeyError as exc:
        supported = ", ".join(_BUILTIN_MODELS)
        raise ValueError(
            f"Unknown sherpa model '{model_id}'. Available models: {supported}"
        ) from exc


def _load_manifest(directory: Path) -> dict[str, Any]:
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Sherpa model manifest not found: {manifest_path}")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid sherpa model manifest: {manifest_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Sherpa model manifest must contain an object: {manifest_path}")
    return data


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _verify_checksums(directory: Path, manifest: dict[str, Any]) -> bool:
    checksums = manifest.get("checksums")
    if not isinstance(checksums, dict) or not checksums:
        raise RuntimeError(f"Sherpa model manifest has no checksums: {directory / 'manifest.json'}")
    entries: list[tuple[Path, str]] = []
    for relative, expected in checksums.items():
        path = (directory / str(relative)).resolve()
        if directory.resolve() not in path.parents:
            raise RuntimeError(f"Sherpa checksum path escapes model directory: {relative}")
        if not path.is_file():
            raise FileNotFoundError(f"Sherpa checksum target not found: {path}")
        entries.append((path, str(expected).lower()))
    signature = tuple(
        (str(path), path.stat().st_size, path.stat().st_mtime_ns, expected)
        for path, expected in entries
    )
    if _checksum_cache.get(signature):
        return True
    for path, expected in entries:
        actual = _sha256(path)
        if actual != expected:
            raise RuntimeError(
                f"Sherpa model checksum mismatch: {path}; expected {expected}, got {actual}"
            )
    _checksum_cache[signature] = True
    return True


def _runtime_compatible(manifest: dict[str, Any]) -> bool:
    minimum = str(manifest.get("sherpa_onnx_min_version") or MIN_SHERPA_ONNX_VERSION)
    try:
        installed = Version(version("sherpa-onnx"))
    except PackageNotFoundError:
        return False
    return installed >= Version(minimum)


def resolve_model(model_id: str, model_root: str | Path = "") -> SherpaModelSpec:
    definition = model_definition(model_id)
    directory = resolve_model_root(model_root) / model_id
    if not directory.is_dir():
        raise FileNotFoundError(
            f"Sherpa model '{model_id}' is not installed at {directory}. "
            "Run 'uv run python scripts/install_sherpa_models.py' to install the default model set."
        )

    manifest = _load_manifest(directory)
    manifest_id = str(manifest.get("id") or model_id)
    if manifest_id != model_id:
        raise RuntimeError(
            f"Sherpa model manifest id '{manifest_id}' does not match directory id '{model_id}'"
        )

    declared_files = manifest.get("files")
    file_names = definition["files"]
    if isinstance(declared_files, dict):
        file_names = {**file_names, **{str(k): str(v) for k, v in declared_files.items()}}

    files = {name: (directory / relative).resolve() for name, relative in file_names.items()}
    resolved_directory = directory.resolve()
    escaped = [str(path) for path in files.values() if resolved_directory not in path.parents]
    if escaped:
        raise RuntimeError(
            f"Sherpa model '{model_id}' contains paths outside its directory: {', '.join(escaped)}"
        )
    missing = [str(path) for path in files.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            f"Sherpa model '{model_id}' is incomplete. Missing: {', '.join(missing)}"
        )

    source = str(manifest.get("source") or "").strip()
    model_license = str(manifest.get("license") or "").strip()
    if not source or not model_license:
        raise RuntimeError(
            f"Sherpa model '{model_id}' manifest must declare source and license"
        )
    verified = _verify_checksums(directory, manifest)
    compatible = _runtime_compatible(manifest)
    if not compatible:
        minimum = manifest.get("sherpa_onnx_min_version") or MIN_SHERPA_ONNX_VERSION
        raise RuntimeError(
            f"Sherpa model '{model_id}' requires sherpa-onnx>={minimum}"
        )
    size_bytes = sum(
        item.stat().st_size for item in directory.rglob("*") if item.is_file()
    )

    return SherpaModelSpec(
        id=model_id,
        display_name=str(manifest.get("display_name") or definition["display_name"]),
        family=str(manifest.get("family") or definition["family"]),
        languages=tuple(manifest.get("languages") or definition["languages"]),
        directory=directory.resolve(),
        files=files,
        source=source,
        license=model_license,
        size_bytes=size_bytes,
        verified=verified,
        compatible=compatible,
        defaults=dict(manifest.get("defaults") or definition.get("defaults") or {}),
        supports_hotwords=bool(
            manifest.get("supports_hotwords", definition.get("supports_hotwords", False))
        ),
        supports_native_timestamps=bool(
            manifest.get(
                "supports_native_timestamps",
                definition.get("supports_native_timestamps", False),
            )
        ),
        supports_token_timestamps=bool(
            manifest.get(
                "supports_token_timestamps",
                definition.get("supports_token_timestamps", False),
            )
        ),
    )


def catalog_status(model_root: str | Path = "") -> list[dict[str, Any]]:
    root = resolve_model_root(model_root)
    status: list[dict[str, Any]] = []
    for model_id, definition in _BUILTIN_MODELS.items():
        try:
            spec = resolve_model(model_id, root)
            status.append({**spec.as_dict(), "installed": True, "error": ""})
        except (FileNotFoundError, RuntimeError) as exc:
            status.append(
                {
                    "id": model_id,
                    "display_name": definition["display_name"],
                    "family": definition["family"],
                    "languages": list(definition["languages"]),
                    "directory": str(root / model_id),
                    "installed": (root / model_id).is_dir(),
                    "verified": False,
                    "compatible": False,
                    "error": str(exc),
                }
            )
    return status
