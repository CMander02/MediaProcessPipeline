"""Read-only, isolated, and credential-safe runtime diagnostics.

Binary probes execute only caller-supplied trusted paths. Python capability
probes run in isolated helper processes with fixed programs, bounded timeouts,
and a minimal environment. Reports contain fixed enums, parsed versions,
counts, and exit codes; raw command output and exception messages are never
returned.
"""

from __future__ import annotations

import json
import math
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from app.services.runtime_profiles import BINARY_ALLOWLIST, PROBE_ALLOWLIST

DIAGNOSTIC_SCHEMA_VERSION = 1
DEFAULT_COMMAND_TIMEOUT_SECONDS = 10.0
MAX_COMMAND_TIMEOUT_SECONDS = 30.0
DEFAULT_TOTAL_TIMEOUT_SECONDS = 60.0
MAX_TOTAL_TIMEOUT_SECONDS = 300.0

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_VERSION_LENGTH = 64
_MAX_VERSION_DIGIT_SEGMENT = 12
_MAX_SPECIFIER_LENGTH = 256
_VERSION_SEGMENT = r"[0-9]{1,12}"
_STRICT_VERSION = re.compile(
    rf"^{_VERSION_SEGMENT}(?:\.{_VERSION_SEGMENT}){{1,3}}"
    rf"(?:(?:a|b|rc){_VERSION_SEGMENT})?"
    rf"(?:\.post{_VERSION_SEGMENT})?"
    rf"(?:\.dev{_VERSION_SEGMENT})?"
    r"(?:\+cu[0-9]{2,4})?$"
)
_MAX_HELPER_OUTPUT_BYTES = 8192
_MAX_COMPONENT_MANIFEST_BYTES = 8 * 1024 * 1024
_MINIMAL_ENV_PASSTHROUGH = (
    "COMSPEC",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "WINDIR",
)
_KNOWN_HELPER_ERROR_CODES = frozenset(
    {
        "browser-launch-failed",
        "codec-check-failed",
        "component-integrity-failed",
        "cuda-smoke-failed",
        "hash-mismatch",
        "import-failed",
        "invalid-registry-entry",
        "invalid-version-output",
        "internal-error",
        "nonzero-exit",
        "onnx-smoke-failed",
        "output-limit-exceeded",
        "path-escape",
        "process-start-failed",
        "unsupported-probe",
    }
)
_KNOWN_ONNX_FLAVORS = frozenset({"cpu", "cuda"})
_KNOWN_ONNX_DISTRIBUTIONS = frozenset({"onnxruntime", "onnxruntime-gpu"})


class DiagnosticError(RuntimeError):
    """Base diagnostic error."""


class DiagnosticSelectionError(DiagnosticError):
    """A diagnostic request contains unsafe or unsupported input."""


class DiagnosticStatus(str, Enum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    UNTRUSTED = "untrusted"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class DiagnosticCapability:
    id: str
    display_name: str
    category: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {
            "category": self.category,
            "description": self.description,
            "displayName": self.display_name,
            "id": self.id,
        }


_DetailValue = str | int | bool | tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    probe_id: str
    status: DiagnosticStatus
    summary: str
    details: tuple[tuple[str, _DetailValue], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "details": {
                key: list(value) if isinstance(value, tuple) else value
                for key, value in self.details
            },
            "probeId": self.probe_id,
            "status": self.status.value,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    schema: int
    requested_probes: tuple[str, ...]
    results: tuple[DiagnosticResult, ...]

    @property
    def healthy(self) -> bool:
        return bool(self.results) and all(
            result.status in {DiagnosticStatus.AVAILABLE, DiagnosticStatus.UNKNOWN}
            for result in self.results
        )

    @property
    def verified(self) -> bool:
        return bool(self.results) and all(
            result.status is DiagnosticStatus.AVAILABLE for result in self.results
        )

    @property
    def overall_status(self) -> str:
        if not self.results:
            return "not-run"
        if any(
            result.status
            in {
                DiagnosticStatus.DEGRADED,
                DiagnosticStatus.ERROR,
                DiagnosticStatus.TIMEOUT,
                DiagnosticStatus.UNAVAILABLE,
                DiagnosticStatus.UNTRUSTED,
            }
            for result in self.results
        ):
            return "attention"
        if any(result.status is DiagnosticStatus.UNKNOWN for result in self.results):
            return "unknown"
        return "available"

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "overallStatus": self.overall_status,
            "requestedProbes": list(self.requested_probes),
            "results": [result.to_dict() for result in self.results],
            "schema": self.schema,
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class DiagnosticPaths:
    config_file: Path
    data_dir: Path
    cache_dir: Path
    log_dir: Path
    configuration_state: str = "loaded"

    def __post_init__(self) -> None:
        for field_name in ("config_file", "data_dir", "cache_dir", "log_dir"):
            if not isinstance(getattr(self, field_name), Path):
                raise DiagnosticSelectionError(
                    f"DiagnosticPaths.{field_name} must be a pathlib.Path"
                )
            if not getattr(self, field_name).is_absolute():
                raise DiagnosticSelectionError(
                    f"DiagnosticPaths.{field_name} must be absolute"
                )
        if self.configuration_state not in {"loaded", "fallback"}:
            raise DiagnosticSelectionError(
                "DiagnosticPaths.configuration_state is invalid"
            )

    @classmethod
    def from_runtime(cls) -> DiagnosticPaths:
        from app.core.paths import resolve_runtime_paths
        from app.core.settings import get_runtime_settings

        runtime_paths = resolve_runtime_paths()
        try:
            settings = get_runtime_settings()
            data_dir = Path(
                os.path.abspath(os.path.normpath(os.fspath(settings.data_root)))
            )
            configuration_state = "loaded"
        except Exception:
            data_dir = runtime_paths.default_data_root
            configuration_state = "fallback"
        return cls(
            config_file=runtime_paths.config_file,
            data_dir=data_dir,
            cache_dir=runtime_paths.cache_dir,
            log_dir=runtime_paths.log_dir,
            configuration_state=configuration_state,
        )


@dataclass(frozen=True, slots=True)
class TrustedBinary:
    """A binary path authenticated by the launcher or runtime manifest."""

    id: str
    path: Path
    sha256: str
    version_specifier: str = ""

    def __post_init__(self) -> None:
        if self.id not in BINARY_ALLOWLIST:
            raise DiagnosticSelectionError(f"Unsupported binary ID: {self.id!r}")
        if not isinstance(self.path, Path):
            raise DiagnosticSelectionError(
                f"Trusted binary {self.id!r} path must be a pathlib.Path"
            )
        if not self.path.is_absolute():
            raise DiagnosticSelectionError(
                f"Trusted binary {self.id!r} must use an absolute path"
            )
        if not isinstance(self.sha256, str) or _SHA256.fullmatch(self.sha256) is None:
            raise DiagnosticSelectionError(
                f"Trusted binary {self.id!r} has an invalid SHA-256"
            )
        if not isinstance(self.version_specifier, str):
            raise DiagnosticSelectionError(
                f"Trusted binary {self.id!r} version specifier must be a string"
            )
        if (
            len(self.version_specifier) > _MAX_SPECIFIER_LENGTH
            or any(
                len(segment) > _MAX_VERSION_DIGIT_SEGMENT
                for segment in re.findall(r"[0-9]+", self.version_specifier)
            )
        ):
            raise DiagnosticSelectionError(
                f"Trusted binary {self.id!r} version specifier exceeds limits"
            )
        try:
            SpecifierSet(self.version_specifier)
        except InvalidSpecifier as exc:
            raise DiagnosticSelectionError(
                f"Trusted binary {self.id!r} has an invalid version specifier"
            ) from exc


@dataclass(frozen=True, slots=True)
class TrustedBinaryRegistry:
    manifest_root: Path | None = None
    manifest_sha256: str | None = None
    manifest_signature_verified: bool = False
    binaries: tuple[TrustedBinary, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.manifest_signature_verified, bool):
            raise DiagnosticSelectionError(
                "Trusted binary manifest verification state must be boolean"
            )
        if not isinstance(self.binaries, tuple) or any(
            not isinstance(binary, TrustedBinary) for binary in self.binaries
        ):
            raise DiagnosticSelectionError(
                "Trusted binary registry must contain TrustedBinary entries"
            )
        identifiers = [binary.id for binary in self.binaries]
        if len(identifiers) != len(set(identifiers)):
            raise DiagnosticSelectionError("Trusted binary registry contains duplicates")
        if self.manifest_root is not None:
            if (
                not isinstance(self.manifest_root, Path)
                or not self.manifest_root.is_absolute()
            ):
                raise DiagnosticSelectionError(
                    "Trusted manifest root must be an absolute pathlib.Path"
                )
            root = Path(os.path.normpath(os.fspath(self.manifest_root)))
            for binary in self.binaries:
                try:
                    Path(
                        os.path.normpath(os.fspath(binary.path))
                    ).relative_to(root)
                except ValueError as exc:
                    raise DiagnosticSelectionError(
                        f"Trusted binary {binary.id!r} escapes the manifest root"
                    ) from exc
        if self.manifest_sha256 is not None and (
            not isinstance(self.manifest_sha256, str)
            or _SHA256.fullmatch(self.manifest_sha256) is None
        ):
            raise DiagnosticSelectionError("Trusted manifest SHA-256 is invalid")
        if self.manifest_signature_verified and (
            self.manifest_root is None or self.manifest_sha256 is None
        ):
            raise DiagnosticSelectionError(
                "Verified manifest registry requires a root and manifest SHA-256"
            )

    @property
    def ready(self) -> bool:
        return (
            self.manifest_signature_verified
            and self.manifest_root is not None
            and self.manifest_sha256 is not None
        )

    def get(self, binary_id: str) -> TrustedBinary | None:
        for binary in self.binaries:
            if binary.id == binary_id:
                return binary
        return None


@dataclass(frozen=True, slots=True)
class TrustedComponentRegistry:
    """Signed component-tree manifest supplied by the installed launcher."""

    manifest_root: Path | None = None
    manifest_path: Path | None = None
    manifest_sha256: str | None = None
    manifest_signature_verified: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.manifest_signature_verified, bool):
            raise DiagnosticSelectionError(
                "Trusted component manifest verification state must be boolean"
            )
        for field_name in ("manifest_root", "manifest_path"):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, Path) or not value.is_absolute()
            ):
                raise DiagnosticSelectionError(
                    f"Trusted component {field_name} must be an absolute pathlib.Path"
                )
        if self.manifest_sha256 is not None and (
            not isinstance(self.manifest_sha256, str)
            or _SHA256.fullmatch(self.manifest_sha256) is None
        ):
            raise DiagnosticSelectionError(
                "Trusted component manifest SHA-256 is invalid"
            )
        if self.manifest_root is not None and self.manifest_path is not None:
            try:
                relative = self.manifest_path.relative_to(self.manifest_root)
            except ValueError as exc:
                raise DiagnosticSelectionError(
                    "Trusted component manifest escapes its signed root"
                ) from exc
            if ".." in relative.parts:
                raise DiagnosticSelectionError(
                    "Trusted component manifest escapes its signed root"
                )
        if self.manifest_signature_verified and (
            self.manifest_root is None
            or self.manifest_path is None
            or self.manifest_sha256 is None
        ):
            raise DiagnosticSelectionError(
                "Verified component registry requires root, manifest, and SHA-256"
            )

    @property
    def ready(self) -> bool:
        return (
            self.manifest_signature_verified
            and self.manifest_root is not None
            and self.manifest_path is not None
            and self.manifest_sha256 is not None
        )


_CAPABILITIES = tuple(
    DiagnosticCapability(*values)
    for values in (
        (
            "accelerate",
            "Accelerate",
            "python-module",
            "Imports Accelerate in a bounded isolated helper.",
        ),
        (
            "audio-separator",
            "Audio Separator",
            "python-module",
            "Imports Audio Separator in a bounded isolated helper.",
        ),
        (
            "chromium",
            "Chromium",
            "browser",
            "Launches bundled Playwright Chromium in a bounded isolated helper.",
        ),
        (
            "configuration",
            "Runtime configuration",
            "configuration",
            "Reports whether effective settings loaded or safe fallback paths are active.",
        ),
        (
            "cuda",
            "CUDA",
            "accelerator",
            "Runs a minimal PyTorch CUDA tensor smoke test in an isolated helper.",
        ),
        (
            "disk",
            "Disk capacity",
            "filesystem",
            "Reads capacity without creating files.",
        ),
        (
            "fastapi",
            "FastAPI",
            "python-module",
            "Imports FastAPI in a bounded isolated helper.",
        ),
        (
            "ffmpeg",
            "FFmpeg",
            "binary",
            "Checks a caller-authenticated FFmpeg binary.",
        ),
        (
            "ffprobe",
            "FFprobe",
            "binary",
            "Checks a caller-authenticated FFprobe binary.",
        ),
        (
            "onnx-cpu-provider",
            "ONNX CPU provider",
            "accelerator",
            "Runs a tiny ONNX graph using the CPU-only ONNX Runtime distribution.",
        ),
        (
            "onnx-cuda-provider",
            "ONNX CUDA provider",
            "accelerator",
            "Runs a tiny ONNX graph using CUDAExecutionProvider.",
        ),
        (
            "onnx-provider",
            "ONNX provider",
            "accelerator",
            "Runs a tiny ONNX graph using an installed execution provider.",
        ),
        (
            "openai",
            "OpenAI SDK",
            "python-module",
            "Imports the OpenAI SDK in a bounded isolated helper.",
        ),
        (
            "pillow",
            "Pillow codecs",
            "python-module",
            "Checks Pillow JPEG, PNG, and WebP support in an isolated helper.",
        ),
        (
            "playwright",
            "Playwright API",
            "python-module",
            "Imports the Playwright API in a bounded isolated helper.",
        ),
        (
            "pyannote",
            "Pyannote Audio",
            "python-module",
            "Imports Pyannote Audio in a bounded isolated helper.",
        ),
        (
            "python",
            "Python",
            "runtime",
            "Reports the active Python implementation and version.",
        ),
        (
            "qwen-asr",
            "Qwen ASR",
            "python-module",
            "Imports Qwen ASR in a bounded isolated helper.",
        ),
        (
            "safetensors",
            "Safetensors",
            "python-module",
            "Imports Safetensors in a bounded isolated helper.",
        ),
        (
            "torchaudio",
            "TorchAudio",
            "python-module",
            "Imports TorchAudio in a bounded isolated helper.",
        ),
        (
            "transformers",
            "Transformers",
            "python-module",
            "Imports Transformers in a bounded isolated helper.",
        ),
        (
            "uv",
            "uv",
            "binary",
            "Checks the launcher-authenticated bundled uv binary.",
        ),
        (
            "writable-paths",
            "Writable path hints",
            "filesystem",
            "Reads path types and permission hints without writing sentinels.",
        ),
    )
)


_HELPER_PROGRAM = r"""
import base64
import contextlib
import hashlib
import importlib
import importlib.metadata
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

MODULES = {
    "accelerate": ("accelerate", "accelerate"),
    "audio-separator": ("audio_separator", "audio-separator"),
    "fastapi": ("fastapi", "fastapi"),
    "openai": ("openai", "openai"),
    "playwright": ("playwright.sync_api", "playwright"),
    "pyannote": ("pyannote.audio", "pyannote-audio"),
    "qwen-asr": ("qwen_asr", "qwen-asr"),
    "safetensors": ("safetensors", "safetensors"),
    "torchaudio": ("torchaudio", "torchaudio"),
    "transformers": ("transformers", "transformers"),
}
MODEL = base64.b64decode(
    "CAgSA21wcDpRChkKBWlucHV0EgZvdXRwdXQiCElkZW50aXR5EgltcHBfcHJvYmVaEwoFaW5wdXQSCgoICAESBAoCCAFiFAoGb3V0cHV0EgoKCAgBEgQKAggBQgQKABAN"
)
IMAGE_FIXTURES = {
    "JPEG": "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDxGiiitjI//9k=",
    "PNG": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGPgEpEDAABoAD1UCKP3AAAAAElFTkSuQmCC",
    "WEBP": "UklGRi4AAABXRUJQVlA4ICIAAABwAQCdASoBAAEAAUAmJZQCdAFAAAD+/DeBV/fU6D4r4AAA",
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_COMPONENT_MANIFEST_BYTES = 8388608
MAX_COMPONENT_FILES = 20000
MAX_COMPONENT_FILE_BYTES = 8589934592
MAX_COMPONENT_TREE_BYTES = 17179869184
MAX_BINARY_OUTPUT_BYTES = 8192
BINARY_VERSION_ARGUMENTS = {
    "ffmpeg": "-version",
    "ffprobe": "-version",
    "uv": "--version",
}
VERSION_SEGMENT = r"[0-9]{1,12}"
BINARY_VERSION_PATTERNS = {
    "ffmpeg": re.compile(
        rf"(?m)^ffmpeg version ({VERSION_SEGMENT}(?:\.{VERSION_SEGMENT}){{1,3}})(?:[-+\s]|$)"
    ),
    "ffprobe": re.compile(
        rf"(?m)^ffprobe version ({VERSION_SEGMENT}(?:\.{VERSION_SEGMENT}){{1,3}})(?:[-+\s]|$)"
    ),
    "uv": re.compile(
        rf"(?m)^uv ({VERSION_SEGMENT}\.{VERSION_SEGMENT}\.{VERSION_SEGMENT}"
        rf"(?:-(?:alpha|beta|rc)\.?{VERSION_SEGMENT})?)(?:\s|$)"
    ),
}

class ComponentIntegrityError(Exception):
    pass

class BinaryProbeError(Exception):
    def __init__(self, code):
        self.code = code

class BoundedSink(io.TextIOBase):
    def write(self, value):
        return len(value)

def dist_version(name):
    try:
        return importlib.metadata.version(name)
    except Exception:
        return ""

def module_probe(probe):
    module_name, distribution = MODULES[probe]
    importlib.import_module(module_name)
    return {"status": "available", "version": dist_version(distribution)}

def pillow_probe():
    pillow = importlib.import_module("PIL")
    features = importlib.import_module("PIL.features")
    image = importlib.import_module("PIL.Image")
    formats = {str(value).upper() for value in image.registered_extensions().values()}
    decoded = {}
    for format_name, encoded in IMAGE_FIXTURES.items():
        with image.open(io.BytesIO(base64.b64decode(encoded))) as value:
            value.load()
            decoded[format_name] = value.size == (1, 1)
    jpeg = bool(features.check_codec("jpg")) and "JPEG" in formats and decoded["JPEG"]
    png = "PNG" in formats and decoded["PNG"]
    webp = bool(features.check_module("webp")) and "WEBP" in formats and decoded["WEBP"]
    return {
        "status": "available" if jpeg and png and webp else "degraded",
        "version": dist_version("pillow") or getattr(pillow, "__version__", ""),
        "jpeg": jpeg,
        "png": png,
        "webp": webp,
    }

def cuda_probe():
    torch = importlib.import_module("torch")
    if not bool(torch.cuda.is_available()):
        return {"status": "unavailable", "errorCode": "cuda-smoke-failed"}
    value = torch.ones(1, device="cuda")
    valid = float(value.cpu().item()) == 1.0
    if not valid:
        return {"status": "unavailable", "errorCode": "cuda-smoke-failed"}
    cuda_version = str(getattr(torch.version, "cuda", "") or "")
    return {
        "status": "available",
        "version": dist_version("torch"),
        "cudaVersion": cuda_version,
        "deviceCount": int(torch.cuda.device_count()),
    }

def onnx_probe(mode):
    numpy = importlib.import_module("numpy")
    runtime = importlib.import_module("onnxruntime")
    cpu_version = dist_version("onnxruntime")
    gpu_version = dist_version("onnxruntime-gpu")
    if mode == "cpu":
        if not cpu_version or gpu_version:
            return {"status": "unavailable", "errorCode": "onnx-smoke-failed"}
        distribution = "onnxruntime"
        version = cpu_version
        provider = "CPUExecutionProvider"
    elif mode == "cuda":
        if not gpu_version or cpu_version:
            return {"status": "unavailable", "errorCode": "onnx-smoke-failed"}
        distribution = "onnxruntime-gpu"
        version = gpu_version
        provider = "CUDAExecutionProvider"
    else:
        if gpu_version and not cpu_version:
            distribution = "onnxruntime-gpu"
            version = gpu_version
            provider = "CUDAExecutionProvider"
        elif cpu_version and not gpu_version:
            distribution = "onnxruntime"
            version = cpu_version
            provider = "CPUExecutionProvider"
        else:
            return {"status": "unavailable", "errorCode": "onnx-smoke-failed"}
    available = set(runtime.get_available_providers())
    if provider not in available:
        return {"status": "unavailable", "errorCode": "onnx-smoke-failed"}
    session = runtime.InferenceSession(MODEL, providers=[provider])
    session.disable_fallback()
    active = set(session.get_providers())
    if provider not in active:
        return {"status": "unavailable", "errorCode": "onnx-smoke-failed"}
    output = session.run(None, {"input": numpy.array([1.0], dtype=numpy.float32)})
    if not output or float(output[0][0]) != 1.0:
        return {"status": "unavailable", "errorCode": "onnx-smoke-failed"}
    return {
        "status": "available",
        "distribution": distribution,
        "version": version,
        "providerFlavor": "cuda" if provider == "CUDAExecutionProvider" else "cpu",
    }

def strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ComponentIntegrityError()
        value[key] = item
    return value

def safe_relative_path(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or "\\" in value
        or ":" in value
    ):
        raise ComponentIntegrityError()
    path = pathlib.PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ComponentIntegrityError()
    return path

def hash_file(path, expected_size):
    digest = hashlib.sha256()
    read_size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1048576)
            if not chunk:
                break
            read_size += len(chunk)
            if read_size > expected_size:
                raise ComponentIntegrityError()
            digest.update(chunk)
    if read_size != expected_size:
        raise ComponentIntegrityError()
    return digest.hexdigest()

def trusted_binary_probe(arguments):
    if len(arguments) != 4:
        raise BinaryProbeError("invalid-registry-entry")
    binary_id, path_text, root_text, expected_hash = arguments
    if (
        binary_id not in BINARY_VERSION_ARGUMENTS
        or SHA256.fullmatch(expected_hash) is None
    ):
        raise BinaryProbeError("invalid-registry-entry")
    try:
        root = pathlib.Path(root_text).resolve(strict=True)
        executable = pathlib.Path(path_text).resolve(strict=True)
        executable.relative_to(root)
    except ValueError as exc:
        raise BinaryProbeError("path-escape") from exc
    except (OSError, RuntimeError) as exc:
        raise BinaryProbeError("invalid-registry-entry") from exc
    if not root.is_dir() or not executable.is_file():
        raise BinaryProbeError("invalid-registry-entry")
    try:
        size = executable.stat().st_size
        actual_hash = hash_file(executable, size)
    except (OSError, RuntimeError) as exc:
        raise BinaryProbeError("invalid-registry-entry") from exc
    if actual_hash != expected_hash:
        raise BinaryProbeError("hash-mismatch")
    try:
        completed = subprocess.run(
            [str(executable), BINARY_VERSION_ARGUMENTS[binary_id]],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=executable.parent,
            shell=False,
            check=False,
        )
    except OSError as exc:
        raise BinaryProbeError("process-start-failed") from exc
    output = completed.stdout if isinstance(completed.stdout, bytes) else b""
    if len(output) > MAX_BINARY_OUTPUT_BYTES:
        raise BinaryProbeError("output-limit-exceeded")
    if completed.returncode != 0:
        return {
            "status": "error",
            "errorCode": "nonzero-exit",
            "exitCode": int(completed.returncode),
        }
    text = output.decode("utf-8", errors="replace")
    match = BINARY_VERSION_PATTERNS[binary_id].search(text[:4096])
    if match is None:
        raise BinaryProbeError("invalid-version-output")
    return {"status": "available", "version": match.group(1)}

def verified_component_executable(root_text, manifest_text, expected_manifest_hash):
    if SHA256.fullmatch(expected_manifest_hash) is None:
        raise ComponentIntegrityError()
    root = pathlib.Path(root_text).resolve(strict=True)
    manifest_path = pathlib.Path(manifest_text).resolve(strict=True)
    try:
        manifest_path.relative_to(root)
    except ValueError as exc:
        raise ComponentIntegrityError() from exc
    if (
        not root.is_dir()
        or not manifest_path.is_file()
        or manifest_path.stat().st_size > MAX_COMPONENT_MANIFEST_BYTES
    ):
        raise ComponentIntegrityError()
    manifest_bytes = manifest_path.read_bytes()
    if (
        len(manifest_bytes) > MAX_COMPONENT_MANIFEST_BYTES
        or hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_hash
    ):
        raise ComponentIntegrityError()
    try:
        payload = json.loads(
            manifest_bytes.decode("utf-8"),
            object_pairs_hook=strict_object,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ComponentIntegrityError() from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"components", "schema"}
        or payload["schema"] != 1
        or isinstance(payload["schema"], bool)
        or not isinstance(payload["components"], list)
        or not 1 <= len(payload["components"]) <= 16
    ):
        raise ComponentIntegrityError()
    matches = [
        item
        for item in payload["components"]
        if isinstance(item, dict) and item.get("id") == "playwright-chromium"
    ]
    if len(matches) != 1:
        raise ComponentIntegrityError()
    component = matches[0]
    if set(component) != {
        "executable",
        "files",
        "id",
        "root",
        "treeSha256",
    }:
        raise ComponentIntegrityError()
    root_relative = safe_relative_path(component["root"])
    executable_relative = safe_relative_path(component["executable"])
    tree_hash = component["treeSha256"]
    files = component["files"]
    if (
        not isinstance(tree_hash, str)
        or SHA256.fullmatch(tree_hash) is None
        or not isinstance(files, list)
        or not 1 <= len(files) <= MAX_COMPONENT_FILES
    ):
        raise ComponentIntegrityError()
    normalized_files = []
    expected_files = {}
    total_size = 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size"}:
            raise ComponentIntegrityError()
        relative = safe_relative_path(item["path"])
        relative_text = relative.as_posix()
        digest = item["sha256"]
        size = item["size"]
        if (
            relative_text in expected_files
            or not isinstance(digest, str)
            or SHA256.fullmatch(digest) is None
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or size > MAX_COMPONENT_FILE_BYTES
        ):
            raise ComponentIntegrityError()
        total_size += size
        if total_size > MAX_COMPONENT_TREE_BYTES:
            raise ComponentIntegrityError()
        expected_files[relative_text] = (digest, size)
        normalized_files.append(
            {"path": relative_text, "sha256": digest, "size": size}
        )
    if normalized_files != sorted(normalized_files, key=lambda item: item["path"]):
        raise ComponentIntegrityError()
    canonical_tree = json.dumps(
        normalized_files,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if hashlib.sha256(canonical_tree).hexdigest() != tree_hash:
        raise ComponentIntegrityError()
    component_root = root.joinpath(*root_relative.parts).resolve(strict=True)
    try:
        component_root.relative_to(root)
    except ValueError as exc:
        raise ComponentIntegrityError() from exc
    if not component_root.is_dir():
        raise ComponentIntegrityError()
    actual_files = {}
    for directory, directories, filenames in os.walk(
        component_root,
        topdown=True,
        followlinks=False,
    ):
        directory_path = pathlib.Path(directory)
        for child in tuple(directories):
            if (directory_path / child).is_symlink():
                raise ComponentIntegrityError()
        for filename in filenames:
            file_path = directory_path / filename
            if file_path.is_symlink() or not file_path.is_file():
                raise ComponentIntegrityError()
            relative_text = file_path.relative_to(component_root).as_posix()
            actual_files[relative_text] = file_path
            if len(actual_files) > MAX_COMPONENT_FILES:
                raise ComponentIntegrityError()
    if set(actual_files) != set(expected_files):
        raise ComponentIntegrityError()
    for relative_text, file_path in actual_files.items():
        expected_hash, expected_size = expected_files[relative_text]
        if (
            file_path.stat().st_size != expected_size
            or hash_file(file_path, expected_size) != expected_hash
        ):
            raise ComponentIntegrityError()
    executable_text = executable_relative.as_posix()
    if executable_text not in actual_files:
        raise ComponentIntegrityError()
    return str(actual_files[executable_text])

def chromium_probe(component_arguments):
    if len(component_arguments) != 3:
        raise ComponentIntegrityError()
    executable = verified_component_executable(*component_arguments)
    sync_api = importlib.import_module("playwright.sync_api")
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=executable,
            headless=True,
        )
        browser.close()
    return {"status": "available", "version": dist_version("playwright")}

def nearest_existing(path):
    candidate = path
    while True:
        if candidate.exists():
            return candidate
        if candidate.parent == candidate:
            return None
        candidate = candidate.parent

def permission_hint(path, expected_file):
    try:
        if path.exists():
            if expected_file and not path.is_file():
                return "type-mismatch"
            if not expected_file and not path.is_dir():
                return "type-mismatch"
            target = path
        else:
            target = nearest_existing(path.parent if expected_file else path)
        if target is None:
            return "parent-unavailable"
        if not path.exists() and not target.is_dir():
            return "parent-type-mismatch"
        return "permission-hint" if os.access(target, os.W_OK) else "permission-denied"
    except (OSError, RuntimeError):
        return "inspection-failed"

def filesystem_paths(arguments):
    if len(arguments) != 4:
        raise ValueError()
    paths = [pathlib.Path(value) for value in arguments]
    if any(not path.is_absolute() for path in paths):
        raise ValueError()
    config, data, cache, logs = paths
    return config, data, cache, logs

def writable_paths_probe(arguments):
    config, data, cache, logs = filesystem_paths(arguments)
    states = {
        "cache": permission_hint(cache, False),
        "config": permission_hint(config, True),
        "data": permission_hint(data, False),
        "logs": permission_hint(logs, False),
    }
    return {
        "status": (
            "unknown"
            if all(value == "permission-hint" for value in states.values())
            else "degraded"
        ),
        **states,
    }

def disk_probe(arguments):
    config, data, cache, logs = filesystem_paths(arguments)
    targets = {
        "cache": cache,
        "config": config.parent,
        "data": data,
        "logs": logs,
    }
    result = {"status": "available"}
    unavailable = []
    for label, target in sorted(targets.items()):
        existing = nearest_existing(target)
        if existing is None:
            unavailable.append(label)
            continue
        try:
            usage = shutil.disk_usage(existing)
        except (OSError, ValueError):
            unavailable.append(label)
            continue
        result[label + "FreeBytes"] = int(usage.free)
        result[label + "TotalBytes"] = int(usage.total)
    if unavailable:
        result["status"] = "degraded"
        result["unavailableScopes"] = unavailable
    return result

def dispatch(probe, arguments):
    if probe == "__trusted-binary":
        return trusted_binary_probe(arguments)
    if probe in MODULES:
        return module_probe(probe)
    if probe == "pillow":
        return pillow_probe()
    if probe == "cuda":
        return cuda_probe()
    if probe == "onnx-provider":
        return onnx_probe("auto")
    if probe == "onnx-cpu-provider":
        return onnx_probe("cpu")
    if probe == "onnx-cuda-provider":
        return onnx_probe("cuda")
    if probe == "chromium":
        return chromium_probe(arguments)
    if probe == "writable-paths":
        return writable_paths_probe(arguments)
    if probe == "disk":
        return disk_probe(arguments)
    return {"status": "error", "errorCode": "unsupported-probe"}

probe = sys.argv[1] if len(sys.argv) >= 2 else ""
try:
    with contextlib.redirect_stdout(BoundedSink()), contextlib.redirect_stderr(BoundedSink()):
        result = dispatch(probe, sys.argv[2:])
except (ImportError, ModuleNotFoundError):
    result = {"status": "unavailable", "errorCode": "import-failed"}
except ComponentIntegrityError:
    result = {"status": "unavailable", "errorCode": "component-integrity-failed"}
except BinaryProbeError as exc:
    result = {"status": "unavailable", "errorCode": exc.code}
except Exception:
    if probe == "chromium":
        code = "browser-launch-failed"
    elif probe == "cuda":
        code = "cuda-smoke-failed"
    elif probe.startswith("onnx-"):
        code = "onnx-smoke-failed"
    elif probe == "pillow":
        code = "codec-check-failed"
    else:
        code = "internal-error"
    result = {"status": "unavailable", "errorCode": code}
sys.stdout.write(json.dumps(result, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
"""

_HELPER_PROBES = PROBE_ALLOWLIST - {
    "configuration",
    "ffmpeg",
    "ffprobe",
    "python",
    "uv",
}


@dataclass(slots=True)
class _ProbeContext:
    paths: DiagnosticPaths
    per_probe_timeout_seconds: float
    timeout_seconds: float
    timeout_scope: str
    deadline: float
    clock: Callable[[], float]
    trusted_binaries: TrustedBinaryRegistry
    trusted_components: TrustedComponentRegistry
    version_expectations: dict[str, SpecifierSet]
    playwright_browsers_path: Path
    which: Callable[[str], str | None]
    command_runner: Callable[..., subprocess.CompletedProcess[str]]


def list_diagnostic_capabilities() -> tuple[DiagnosticCapability, ...]:
    return _CAPABILITIES


def _details(**values: _DetailValue) -> tuple[tuple[str, _DetailValue], ...]:
    return tuple(sorted(values.items()))


def _error_result(probe_id: str, *, code: str = "internal-error") -> DiagnosticResult:
    return DiagnosticResult(
        probe_id,
        DiagnosticStatus.ERROR,
        "Probe could not complete safely.",
        _details(errorCode=code),
    )


def _creation_flags() -> int:
    return int(
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )


def _assign_windows_job(process: subprocess.Popen[bytes]) -> int | None:
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
    ]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    information = _ExtendedLimitInformation()
    information.BasicLimitInformation.LimitFlags = 0x00002000
    if not kernel32.SetInformationJobObject(
        handle,
        9,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise OSError(error, "SetInformationJobObject failed")
    process_handle = wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
    if not kernel32.AssignProcessToJobObject(handle, process_handle):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise OSError(error, "AssignProcessToJobObject failed")
    return int(handle)


def _close_windows_job(handle: int | None) -> None:
    if handle is None or os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle(wintypes.HANDLE(handle))


def _terminate_process_tree(
    process: subprocess.Popen[bytes],
    *,
    windows_job: int | None,
) -> None:
    if os.name == "nt":
        _close_windows_job(windows_job)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


@dataclass(frozen=True, slots=True)
class _ProcessExecution:
    completed: subprocess.CompletedProcess[str]
    output_truncated: bool


def _bounded_process(
    arguments: list[str],
    *,
    timeout: float,
    environment: dict[str, str],
    cwd: Path,
    max_output_bytes: int,
) -> _ProcessExecution:
    process = subprocess.Popen(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        shell=False,
        cwd=cwd,
        creationflags=_creation_flags(),
        start_new_session=os.name != "nt",
    )
    try:
        windows_job = _assign_windows_job(process)
    except OSError:
        process.kill()
        process.wait()
        raise
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"value": False}

    def drain(name: str, stream: Any) -> None:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            remaining = max_output_bytes - len(buffers[name])
            if remaining > 0:
                buffers[name].extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated["value"] = True

    assert process.stdout is not None and process.stderr is not None
    readers = [
        threading.Thread(
            target=drain,
            args=("stdout", process.stdout),
            daemon=True,
        ),
        threading.Thread(
            target=drain,
            args=("stderr", process.stderr),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        return_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        return_code = -1
    finally:
        _terminate_process_tree(process, windows_job=windows_job)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        for reader in readers:
            reader.join(timeout=2)
        process.stdout.close()
        process.stderr.close()
    if timed_out:
        raise subprocess.TimeoutExpired(arguments, timeout)
    return _ProcessExecution(
        completed=subprocess.CompletedProcess(
            arguments,
            return_code,
            buffers["stdout"].decode("utf-8", errors="replace"),
            buffers["stderr"].decode("utf-8", errors="replace"),
        ),
        output_truncated=truncated["value"],
    )


def _run_process(
    context: _ProbeContext,
    arguments: list[str],
    *,
    environment: dict[str, str],
    cwd: Path,
    max_output_bytes: int,
) -> _ProcessExecution:
    if context.command_runner is subprocess.run:
        return _bounded_process(
            arguments,
            timeout=context.timeout_seconds,
            environment=environment,
            cwd=cwd,
            max_output_bytes=max_output_bytes,
        )
    completed = context.command_runner(
        arguments,
        capture_output=True,
        text=True,
        timeout=context.timeout_seconds,
        check=False,
        encoding="utf-8",
        errors="replace",
        env=environment,
        stdin=subprocess.DEVNULL,
        shell=False,
        cwd=cwd,
        creationflags=_creation_flags(),
    )
    stdout = completed.stdout if isinstance(completed.stdout, str) else ""
    stderr = completed.stderr if isinstance(completed.stderr, str) else ""
    stdout_bytes = stdout.encode("utf-8", errors="replace")
    stderr_bytes = stderr.encode("utf-8", errors="replace")
    truncated = (
        len(stdout_bytes) > max_output_bytes
        or len(stderr_bytes) > max_output_bytes
    )
    return _ProcessExecution(
        completed=subprocess.CompletedProcess(
            arguments,
            int(completed.returncode),
            stdout_bytes[:max_output_bytes].decode("utf-8", errors="replace"),
            stderr_bytes[:max_output_bytes].decode("utf-8", errors="replace"),
        ),
        output_truncated=truncated,
    )


def _minimal_environment(
    executable: Path,
    *,
    playwright_browsers_path: Path | None = None,
) -> dict[str, str]:
    environment = {
        "HF_HUB_OFFLINE": "1",
        "OMP_NUM_THREADS": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "TRANSFORMERS_OFFLINE": "1",
    }
    for key in _MINIMAL_ENV_PASSTHROUGH:
        value = os.environ.get(key)
        if value:
            environment[key] = value
    path_entries = [str(executable.parent)]
    system_root = environment.get("SYSTEMROOT") or environment.get("WINDIR")
    if system_root:
        path_entries.append(str(Path(system_root) / "System32"))
    elif os.name != "nt":
        path_entries.extend(("/usr/bin", "/bin"))
    environment["PATH"] = os.pathsep.join(path_entries)
    if playwright_browsers_path is not None:
        environment["PLAYWRIGHT_BROWSERS_PATH"] = str(playwright_browsers_path)
    return environment


def _parsed_version(value: Any) -> str | None:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_VERSION_LENGTH
        or any(
            len(segment) > _MAX_VERSION_DIGIT_SEGMENT
            for segment in re.findall(r"[0-9]+", value)
        )
        or _STRICT_VERSION.fullmatch(value) is None
    ):
        return None
    try:
        return str(Version(value))
    except InvalidVersion:
        return None


def _version_status(
    probe_id: str,
    version: str | None,
    context: _ProbeContext,
) -> tuple[DiagnosticStatus, tuple[tuple[str, _DetailValue], ...]]:
    expectation = context.version_expectations.get(probe_id)
    if version is None:
        return DiagnosticStatus.DEGRADED, _details(versionState="unparsed")
    if expectation is not None and Version(version) not in expectation:
        return DiagnosticStatus.DEGRADED, _details(
            installedVersion=version,
            versionState="incompatible",
        )
    return DiagnosticStatus.AVAILABLE, _details(
        installedVersion=version,
        versionState="compatible" if expectation is not None else "detected",
    )


def _probe_python(context: _ProbeContext) -> DiagnosticResult:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    supported = (3, 11) <= sys.version_info[:2] < (3, 13)
    expectation = context.version_expectations.get("python")
    compatible = expectation is None or Version(version) in expectation
    available = supported and compatible
    return DiagnosticResult(
        "python",
        DiagnosticStatus.AVAILABLE if available else DiagnosticStatus.DEGRADED,
        (
            "Python runtime is supported."
            if available
            else "Python runtime is outside the required range."
        ),
        _details(
            implementation=platform.python_implementation(),
            installedVersion=version,
            versionState=(
                "compatible"
                if available and expectation is not None
                else "detected"
                if supported and expectation is None
                else "incompatible"
            ),
        ),
    )


def _probe_configuration(context: _ProbeContext) -> DiagnosticResult:
    loaded = context.paths.configuration_state == "loaded"
    return DiagnosticResult(
        "configuration",
        DiagnosticStatus.AVAILABLE if loaded else DiagnosticStatus.DEGRADED,
        (
            "Runtime configuration loaded successfully."
            if loaded
            else "Runtime configuration is unavailable; safe fallback paths are active."
        ),
        _details(configurationState="loaded" if loaded else "fallback"),
    )


def _probe_binary(context: _ProbeContext, probe_id: str) -> DiagnosticResult:
    if not context.trusted_binaries.ready:
        return DiagnosticResult(
            probe_id,
            DiagnosticStatus.UNAVAILABLE,
            "Signed runtime manifest trust root is unavailable.",
            _details(errorCode="trust-root-unavailable"),
        )
    trusted = context.trusted_binaries.get(probe_id)
    if trusted is None:
        return DiagnosticResult(
            probe_id,
            DiagnosticStatus.UNAVAILABLE,
            f"Signed manifest has no {probe_id} entry.",
            _details(errorCode="manifest-entry-unavailable"),
        )
    manifest_root = context.trusted_binaries.manifest_root
    assert manifest_root is not None
    executable = Path(sys.executable)
    try:
        execution = _run_process(
            context,
            [
                str(executable),
                "-I",
                "-c",
                _HELPER_PROGRAM,
                "__trusted-binary",
                probe_id,
                str(trusted.path),
                str(manifest_root),
                trusted.sha256,
            ],
            environment=_minimal_environment(executable),
            cwd=executable.parent,
            max_output_bytes=_MAX_HELPER_OUTPUT_BYTES,
        )
    except subprocess.TimeoutExpired:
        return DiagnosticResult(
            probe_id,
            DiagnosticStatus.TIMEOUT,
            f"Trusted {probe_id} version probe timed out.",
            _details(timeoutScope=context.timeout_scope),
        )
    except OSError:
        return _error_result(probe_id, code="process-start-failed")
    if execution.output_truncated:
        return _error_result(probe_id, code="output-limit-exceeded")
    completed = execution.completed
    if completed.returncode != 0:
        return DiagnosticResult(
            probe_id,
            DiagnosticStatus.ERROR,
            f"Trusted {probe_id} version probe failed.",
            _details(errorCode="nonzero-exit", exitCode=int(completed.returncode)),
        )
    raw = completed.stdout if isinstance(completed.stdout, str) else ""
    try:
        payload = _validate_helper_payload(json.loads(raw))
    except json.JSONDecodeError:
        payload = None
    if payload is None:
        return DiagnosticResult(
            probe_id,
            DiagnosticStatus.ERROR,
            f"Trusted {probe_id} returned an invalid diagnostic response.",
            _details(errorCode="invalid-helper-output"),
        )
    if payload["status"] != "available":
        error_code = payload.get("errorCode", "internal-error")
        if error_code in {"hash-mismatch", "path-escape"}:
            status = DiagnosticStatus.UNTRUSTED
        elif (
            payload["status"] == "error"
            or error_code
            in {
                "invalid-version-output",
                "nonzero-exit",
                "output-limit-exceeded",
                "process-start-failed",
            }
        ):
            status = DiagnosticStatus.ERROR
        else:
            status = DiagnosticStatus.UNAVAILABLE
        return DiagnosticResult(
            probe_id,
            status,
            f"Trusted {probe_id} executable check failed.",
            _details(
                errorCode=error_code,
                **(
                    {"exitCode": payload["exitCode"]}
                    if isinstance(payload.get("exitCode"), int)
                    else {}
                ),
            ),
        )
    version = _parsed_version(payload.get("version"))
    if version is None:
        return DiagnosticResult(
            probe_id,
            DiagnosticStatus.ERROR,
            f"Trusted {probe_id} returned an invalid version.",
            _details(errorCode="invalid-version"),
        )
    specifier = (
        SpecifierSet(trusted.version_specifier)
        if trusted.version_specifier
        else context.version_expectations.get(probe_id)
    )
    matches_specifier = specifier is None or Version(version) in specifier
    if not matches_specifier:
        return DiagnosticResult(
            probe_id,
            DiagnosticStatus.DEGRADED,
            f"Trusted {probe_id} version is outside the required range.",
            _details(installedVersion=version, versionState="incompatible"),
        )
    return DiagnosticResult(
        probe_id,
        DiagnosticStatus.AVAILABLE,
        f"Trusted {probe_id} executable is available.",
        _details(
            installedVersion=version,
            versionState="compatible" if specifier is not None else "detected",
        ),
    )


def _validate_helper_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed = {
        "cache",
        "cacheFreeBytes",
        "cacheTotalBytes",
        "config",
        "configFreeBytes",
        "configTotalBytes",
        "cudaVersion",
        "data",
        "dataFreeBytes",
        "dataTotalBytes",
        "deviceCount",
        "distribution",
        "errorCode",
        "exitCode",
        "jpeg",
        "logs",
        "logsFreeBytes",
        "logsTotalBytes",
        "png",
        "providerFlavor",
        "status",
        "unavailableScopes",
        "version",
        "webp",
    }
    if set(value) - allowed:
        return None
    if value.get("status") not in {
        "available",
        "degraded",
        "error",
        "unavailable",
        "unknown",
    }:
        return None
    error_code = value.get("errorCode")
    if error_code is not None and error_code not in _KNOWN_HELPER_ERROR_CODES:
        return None
    for key in ("jpeg", "png", "webp"):
        if key in value and not isinstance(value[key], bool):
            return None
    if "deviceCount" in value and (
        isinstance(value["deviceCount"], bool)
        or not isinstance(value["deviceCount"], int)
        or value["deviceCount"] < 0
    ):
        return None
    if "exitCode" in value and (
        isinstance(value["exitCode"], bool)
        or not isinstance(value["exitCode"], int)
        or not -(2**31) <= value["exitCode"] <= 2**31 - 1
    ):
        return None
    if "providerFlavor" in value and value["providerFlavor"] not in _KNOWN_ONNX_FLAVORS:
        return None
    if "distribution" in value and value["distribution"] not in _KNOWN_ONNX_DISTRIBUTIONS:
        return None
    if "cudaVersion" in value and (
        not isinstance(value["cudaVersion"], str)
        or re.fullmatch(r"[0-9.]{1,20}", value["cudaVersion"]) is None
    ):
        return None
    if "version" in value and value["version"] and _parsed_version(value["version"]) is None:
        return None
    path_states = {
        "inspection-failed",
        "parent-type-mismatch",
        "parent-unavailable",
        "permission-denied",
        "permission-hint",
        "type-mismatch",
    }
    for key in ("cache", "config", "data", "logs"):
        if key in value and value[key] not in path_states:
            return None
    for key in (
        "cacheFreeBytes",
        "cacheTotalBytes",
        "configFreeBytes",
        "configTotalBytes",
        "dataFreeBytes",
        "dataTotalBytes",
        "logsFreeBytes",
        "logsTotalBytes",
    ):
        if key in value and (
            isinstance(value[key], bool)
            or not isinstance(value[key], int)
            or value[key] < 0
            or value[key] > 2**63 - 1
        ):
            return None
    if "unavailableScopes" in value and (
        not isinstance(value["unavailableScopes"], list)
        or any(
            scope not in {"cache", "config", "data", "logs"}
            for scope in value["unavailableScopes"]
        )
        or value["unavailableScopes"]
        != sorted(set(value["unavailableScopes"]))
    ):
        return None
    return value


def _probe_helper(context: _ProbeContext, probe_id: str) -> DiagnosticResult:
    # Preserve the active virtual-environment launcher. On POSIX, resolving the
    # venv's python symlink selects the base interpreter and drops the locked
    # environment's site-packages from isolated (-I) probes.
    executable = Path(sys.executable)
    arguments = [str(executable), "-I", "-c", _HELPER_PROGRAM, probe_id]
    if probe_id == "chromium":
        registry = context.trusted_components
        if not registry.ready:
            return DiagnosticResult(
                probe_id,
                DiagnosticStatus.UNAVAILABLE,
                "Signed Chromium component manifest is unavailable.",
                _details(errorCode="component-trust-root-unavailable"),
            )
        assert (
            registry.manifest_root is not None
            and registry.manifest_path is not None
            and registry.manifest_sha256 is not None
        )
        arguments.extend(
            (
                str(registry.manifest_root),
                str(registry.manifest_path),
                registry.manifest_sha256,
            )
        )
    elif probe_id in {"disk", "writable-paths"}:
        arguments.extend(
            (
                str(context.paths.config_file),
                str(context.paths.data_dir),
                str(context.paths.cache_dir),
                str(context.paths.log_dir),
            )
        )
    try:
        execution = _run_process(
            context,
            arguments,
            environment=_minimal_environment(
                executable,
                playwright_browsers_path=(
                    context.playwright_browsers_path
                    if probe_id in {"chromium", "playwright"}
                    else None
                ),
            ),
            cwd=executable.parent,
            max_output_bytes=_MAX_HELPER_OUTPUT_BYTES,
        )
    except subprocess.TimeoutExpired:
        return DiagnosticResult(
            probe_id,
            DiagnosticStatus.TIMEOUT,
            "Isolated capability probe timed out.",
            _details(timeoutScope=context.timeout_scope),
        )
    except OSError:
        return _error_result(probe_id, code="process-start-failed")
    if execution.output_truncated:
        return _error_result(probe_id, code="output-limit-exceeded")
    completed = execution.completed
    if completed.returncode != 0:
        return DiagnosticResult(
            probe_id,
            DiagnosticStatus.ERROR,
            "Isolated capability probe failed.",
            _details(errorCode="nonzero-exit", exitCode=int(completed.returncode)),
        )
    raw = completed.stdout if isinstance(completed.stdout, str) else ""
    if len(raw.encode("utf-8", errors="ignore")) > _MAX_HELPER_OUTPUT_BYTES:
        return _error_result(probe_id, code="invalid-helper-output")
    try:
        payload = _validate_helper_payload(json.loads(raw))
    except json.JSONDecodeError:
        payload = None
    if payload is None:
        return _error_result(probe_id, code="invalid-helper-output")

    status_value = payload["status"]
    if status_value in {"unavailable", "error"}:
        error_code = payload.get("errorCode", "internal-error")
        component_integrity_failed = (
            probe_id == "chromium"
            and error_code == "component-integrity-failed"
        )
        return DiagnosticResult(
            probe_id,
            (
                DiagnosticStatus.UNTRUSTED
                if component_integrity_failed
                else DiagnosticStatus.UNAVAILABLE
                if status_value == "unavailable"
                else DiagnosticStatus.ERROR
            ),
            "Isolated capability probe is unavailable.",
            _details(errorCode=error_code),
        )
    if probe_id == "writable-paths":
        states = {
            key: payload.get(key, "inspection-failed")
            for key in ("cache", "config", "data", "logs")
        }
        only_hints = all(
            state == "permission-hint" for state in states.values()
        )
        return DiagnosticResult(
            probe_id,
            (
                DiagnosticStatus.UNKNOWN
                if status_value == "unknown" and only_hints
                else DiagnosticStatus.DEGRADED
            ),
            (
                "Read-only permission hints are positive; writability remains unverified."
                if status_value == "unknown" and only_hints
                else "One or more configured path checks need attention."
            ),
            _details(**states),
        )
    if probe_id == "disk":
        detail_map: dict[str, _DetailValue] = {
            key: value
            for key, value in payload.items()
            if key.endswith("Bytes") and isinstance(value, int)
        }
        scopes = payload.get("unavailableScopes")
        if isinstance(scopes, list):
            detail_map["unavailableScopes"] = tuple(scopes)
        return DiagnosticResult(
            probe_id,
            (
                DiagnosticStatus.AVAILABLE
                if status_value == "available"
                else DiagnosticStatus.DEGRADED
            ),
            (
                "Disk capacity is available for configured paths."
                if status_value == "available"
                else "Disk capacity is unavailable for one or more configured paths."
            ),
            tuple(sorted(detail_map.items())),
        )

    version = _parsed_version(payload.get("version"))
    version_status, details = _version_status(probe_id, version, context)
    status = (
        DiagnosticStatus.DEGRADED
        if status_value == "degraded" or version_status is DiagnosticStatus.DEGRADED
        else DiagnosticStatus.AVAILABLE
    )
    detail_map = dict(details)
    if probe_id == "pillow":
        codecs_ready = all(
            payload.get(codec) is True for codec in ("jpeg", "png", "webp")
        )
        detail_map.update(
            {
                "jpeg": payload.get("jpeg", False),
                "png": payload.get("png", False),
                "webp": payload.get("webp", False),
            }
        )
        if not codecs_ready:
            status = DiagnosticStatus.DEGRADED
    elif probe_id == "cuda":
        detail_map.update(
            {
                "cudaVersion": payload.get("cudaVersion", ""),
                "deviceCount": payload.get("deviceCount", 0),
            }
        )
        if (
            not payload.get("cudaVersion")
            or not isinstance(payload.get("deviceCount"), int)
            or payload.get("deviceCount", 0) < 1
        ):
            status = DiagnosticStatus.DEGRADED
    elif probe_id in {
        "onnx-provider",
        "onnx-cpu-provider",
        "onnx-cuda-provider",
    }:
        detail_map["providerFlavor"] = payload.get("providerFlavor", "cpu")
        detail_map["distribution"] = payload.get("distribution", "")
        actual_onnx = (
            detail_map["providerFlavor"],
            detail_map["distribution"],
        )
        valid_onnx_contracts = {
            ("cpu", "onnxruntime"),
            ("cuda", "onnxruntime-gpu"),
        }
        expected_onnx = {
            "onnx-cpu-provider": ("cpu", "onnxruntime"),
            "onnx-cuda-provider": ("cuda", "onnxruntime-gpu"),
        }.get(probe_id)
        if (
            actual_onnx not in valid_onnx_contracts
            or (
                expected_onnx is not None
                and actual_onnx != expected_onnx
            )
        ):
            return DiagnosticResult(
                probe_id,
                DiagnosticStatus.UNAVAILABLE,
                "ONNX execution provider distribution contract failed.",
                _details(errorCode="onnx-smoke-failed"),
            )
    return DiagnosticResult(
        probe_id,
        status,
        (
            "Isolated capability probe is available."
            if status is DiagnosticStatus.AVAILABLE
            else "Isolated capability probe requires attention."
        ),
        tuple(sorted(detail_map.items())),
    )


def _run_probe(probe_id: str, context: _ProbeContext) -> DiagnosticResult:
    if probe_id == "python":
        return _probe_python(context)
    if probe_id == "configuration":
        return _probe_configuration(context)
    if probe_id in BINARY_ALLOWLIST:
        return _probe_binary(context, probe_id)
    if probe_id in _HELPER_PROBES:
        return _probe_helper(context, probe_id)
    raise DiagnosticSelectionError(f"Unknown diagnostic probe: {probe_id!r}")


def _version_expectations(
    values: Mapping[str, str] | None,
) -> dict[str, SpecifierSet]:
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise DiagnosticSelectionError("version_expectations must be a mapping")
    if any(not isinstance(probe_id, str) for probe_id in values):
        raise DiagnosticSelectionError(
            "version_expectations keys must be probe identifiers"
        )
    unknown = sorted(set(values) - PROBE_ALLOWLIST)
    if unknown:
        raise DiagnosticSelectionError(
            "Version expectations contain unknown probes: " + ", ".join(unknown)
        )
    parsed: dict[str, SpecifierSet] = {}
    for probe_id, value in values.items():
        if not isinstance(value, str):
            raise DiagnosticSelectionError(
                f"Version expectation for {probe_id!r} must be a string"
            )
        if (
            len(value) > _MAX_SPECIFIER_LENGTH
            or any(
                len(segment) > _MAX_VERSION_DIGIT_SEGMENT
                for segment in re.findall(r"[0-9]+", value)
            )
        ):
            raise DiagnosticSelectionError(
                f"Version expectation for {probe_id!r} exceeds limits"
            )
        try:
            parsed[probe_id] = SpecifierSet(value)
        except InvalidSpecifier as exc:
            raise DiagnosticSelectionError(
                f"Version expectation for {probe_id!r} is invalid"
            ) from exc
    return parsed


def _playwright_browsers_path(
    paths: DiagnosticPaths,
    supplied: Path | None,
) -> Path:
    def normalized(path: Path) -> Path:
        return Path(os.path.normpath(os.fspath(path)))

    cache_root = normalized(paths.cache_dir)
    expected = normalized(cache_root / "ms-playwright")
    if supplied is not None:
        if not isinstance(supplied, Path) or not supplied.is_absolute():
            raise DiagnosticSelectionError(
                "playwright_browsers_path must be an absolute pathlib.Path"
            )
        candidate = normalized(supplied)
        if candidate != expected:
            raise DiagnosticSelectionError(
                "playwright_browsers_path must be the runtime cache/ms-playwright directory"
            )
        return candidate
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if configured:
        candidate = Path(configured)
        if candidate.is_absolute():
            normalized_candidate = normalized(candidate)
            if normalized_candidate == expected:
                return normalized_candidate
    return expected


def run_runtime_diagnostics(
    probe_ids: Sequence[str] | None = None,
    *,
    paths: DiagnosticPaths | None = None,
    timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    total_timeout_seconds: float = DEFAULT_TOTAL_TIMEOUT_SECONDS,
    trusted_binaries: TrustedBinaryRegistry | None = None,
    trusted_components: TrustedComponentRegistry | None = None,
    version_expectations: Mapping[str, str] | None = None,
    playwright_browsers_path: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    clock: Callable[[], float] = time.monotonic,
) -> DiagnosticReport:
    """Run predefined probes without downloading, installing, or exposing secrets."""

    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
        or timeout_seconds > MAX_COMMAND_TIMEOUT_SECONDS
    ):
        raise DiagnosticSelectionError(
            f"timeout_seconds must be finite and within (0, {MAX_COMMAND_TIMEOUT_SECONDS}]"
        )
    if (
        isinstance(total_timeout_seconds, bool)
        or not isinstance(total_timeout_seconds, (int, float))
        or not math.isfinite(total_timeout_seconds)
        or total_timeout_seconds <= 0
        or total_timeout_seconds > MAX_TOTAL_TIMEOUT_SECONDS
    ):
        raise DiagnosticSelectionError(
            "total_timeout_seconds must be finite and within "
            f"(0, {MAX_TOTAL_TIMEOUT_SECONDS}]"
        )
    if not callable(clock):
        raise DiagnosticSelectionError("clock must be callable")
    if probe_ids is None:
        selected = tuple(capability.id for capability in _CAPABILITIES)
    else:
        if isinstance(probe_ids, (str, bytes)) or not isinstance(probe_ids, Sequence):
            raise DiagnosticSelectionError("probe_ids must be a string array")
        if not probe_ids:
            raise DiagnosticSelectionError("probe_ids must not be empty")
        if any(not isinstance(probe_id, str) for probe_id in probe_ids):
            raise DiagnosticSelectionError("probe_ids must be a string array")
        if len(probe_ids) != len(set(probe_ids)):
            raise DiagnosticSelectionError("probe_ids contains duplicate values")
        unknown = sorted(set(probe_ids) - PROBE_ALLOWLIST)
        if unknown:
            raise DiagnosticSelectionError(
                "Unknown diagnostic probes: " + ", ".join(unknown)
            )
        selected = tuple(sorted(probe_ids))
    try:
        started_at = float(clock())
    except Exception as exc:
        raise DiagnosticSelectionError("clock returned an invalid value") from exc
    if not math.isfinite(started_at):
        raise DiagnosticSelectionError("clock returned an invalid value")
    registry = trusted_binaries or TrustedBinaryRegistry()
    if not isinstance(registry, TrustedBinaryRegistry):
        raise DiagnosticSelectionError(
            "trusted_binaries must be a TrustedBinaryRegistry"
        )
    component_registry = trusted_components or TrustedComponentRegistry()
    if not isinstance(component_registry, TrustedComponentRegistry):
        raise DiagnosticSelectionError(
            "trusted_components must be a TrustedComponentRegistry"
        )
    diagnostic_paths = paths or DiagnosticPaths.from_runtime()
    browser_cache_path = _playwright_browsers_path(
        diagnostic_paths,
        playwright_browsers_path,
    )
    if (
        component_registry.ready
        and component_registry.manifest_root != browser_cache_path
    ):
        raise DiagnosticSelectionError(
            "Trusted component manifest root must be cache/ms-playwright"
        )
    context = _ProbeContext(
        paths=diagnostic_paths,
        per_probe_timeout_seconds=float(timeout_seconds),
        timeout_seconds=float(timeout_seconds),
        timeout_scope="probe",
        deadline=started_at + float(total_timeout_seconds),
        clock=clock,
        trusted_binaries=registry,
        trusted_components=component_registry,
        version_expectations=_version_expectations(version_expectations),
        playwright_browsers_path=browser_cache_path,
        which=which,
        command_runner=command_runner,
    )
    results: list[DiagnosticResult] = []
    for probe_id in selected:
        try:
            remaining = context.deadline - float(context.clock())
        except Exception:
            remaining = -1.0
        if not math.isfinite(remaining) or remaining <= 0:
            results.append(
                DiagnosticResult(
                    probe_id,
                    DiagnosticStatus.TIMEOUT,
                    "Runtime diagnostics total deadline was exhausted.",
                    _details(timeoutScope="total"),
                )
            )
            continue
        context.timeout_seconds = min(
            context.per_probe_timeout_seconds,
            remaining,
        )
        context.timeout_scope = (
            "total"
            if remaining < context.per_probe_timeout_seconds
            else "probe"
        )
        try:
            result = _run_probe(probe_id, context)
        except Exception:
            result = _error_result(probe_id)
        results.append(result)
    return DiagnosticReport(
        schema=DIAGNOSTIC_SCHEMA_VERSION,
        requested_probes=selected,
        results=tuple(results),
    )


if tuple(capability.id for capability in _CAPABILITIES) != tuple(sorted(PROBE_ALLOWLIST)):
    raise RuntimeError("Diagnostic capabilities and runtime profile probes differ")


__all__ = [
    "DEFAULT_COMMAND_TIMEOUT_SECONDS",
    "DEFAULT_TOTAL_TIMEOUT_SECONDS",
    "DIAGNOSTIC_SCHEMA_VERSION",
    "DiagnosticCapability",
    "DiagnosticError",
    "DiagnosticPaths",
    "DiagnosticReport",
    "DiagnosticResult",
    "DiagnosticSelectionError",
    "DiagnosticStatus",
    "TrustedBinary",
    "TrustedBinaryRegistry",
    "TrustedComponentRegistry",
    "list_diagnostic_capabilities",
    "run_runtime_diagnostics",
]
