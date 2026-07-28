"""Strict runtime profile and dependency contracts.

The catalog is repository-owned data. It cannot supply executables or command
arguments. Every executable plan requires a concrete target and resolves only
to actions defined in this module.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from packaging.markers import InvalidMarker, Marker
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

PROFILE_SCHEMA_VERSION = 1

SUPPORTED_OPERATING_SYSTEMS = frozenset({"windows", "linux", "macos"})
SUPPORTED_ARCHITECTURES = frozenset({"x86_64", "aarch64"})
SUPPORTED_GPU_MODES = frozenset({"none", "nvidia"})

RUNTIME_EXTRA_ALLOWLIST = frozenset(
    {
        "asr-api-vad",
        "hf-local-inference",
        "local-asr",
        "local-llm",
        "local-models",
        "uvr",
    }
)
MODEL_VERIFICATION_EXTRAS = frozenset(
    {
        "asr-api-vad",
        "hf-local-inference",
        "local-asr",
        "local-llm",
        "local-models",
        "uvr",
    }
)
COMPONENT_ALLOWLIST = frozenset({"playwright-chromium", "remote-api"})
BINARY_ALLOWLIST = frozenset({"ffmpeg", "ffprobe", "uv"})
PROBE_ALLOWLIST = frozenset(
    {
        "accelerate",
        "audio-separator",
        "chromium",
        "configuration",
        "cuda",
        "disk",
        "fastapi",
        "ffmpeg",
        "ffprobe",
        "onnx-cuda-provider",
        "onnx-cpu-provider",
        "onnx-provider",
        "openai",
        "pillow",
        "playwright",
        "pyannote",
        "python",
        "qwen-asr",
        "safetensors",
        "torchaudio",
        "transformers",
        "uv",
        "writable-paths",
    }
)

_ROOT_FIELDS = frozenset({"schema", "profiles"})
_PROFILE_FIELDS = frozenset(
    {
        "id",
        "displayName",
        "targets",
        "uvExtras",
        "components",
        "verificationPending",
        "mutuallyExclusive",
        "estimatedDownloadBytes",
        "estimatedInstalledBytes",
        "requiredBinaries",
        "requiredModels",
        "probes",
    }
)
_TARGET_FIELDS = frozenset({"os", "arch", "gpu"})
_LOCK_REQUIREMENT_FIELDS = frozenset(
    {
        "name",
        "extra",
        "extras",
        "specifier",
        "marker",
        "index",
        "url",
        "git",
        "path",
    }
)
_SOURCE_KINDS = frozenset({"index", "url", "git", "path"})
_TRUSTED_DEPENDENCY_HOSTS = frozenset(
    {
        "download.pytorch.org",
        "download-r2.pytorch.org",
        "files.pythonhosted.org",
        "pypi.org",
    }
)
_DEFAULT_INDEX_URL = "https://pypi.org/simple"
_DIRECT_URL_HASH = re.compile(r"^sha256=([0-9a-fA-F]{64})$")
_LOCK_SHA256 = re.compile(r"^sha256:([0-9a-fA-F]{64})$")
_MAX_VERSION_LENGTH = 64
_MAX_VERSION_DIGIT_SEGMENT = 12
_MAX_SPECIFIER_LENGTH = 256
_MAX_MARKER_LENGTH = 1024
_SUPPORTED_PYTHON_VERSIONS = ("3.11", "3.12")
_PACKAGE_PROBES = {
    "accelerate": "accelerate",
    "audio-separator": "audio-separator",
    "fastapi": "fastapi",
    "onnxruntime": "onnx-cpu-provider",
    "onnxruntime-gpu": "onnx-cuda-provider",
    "openai": "openai",
    "pillow": "pillow",
    "playwright": "playwright",
    "pyannote-audio": "pyannote",
    "qwen-asr": "qwen-asr",
    "safetensors": "safetensors",
    "torch": "cuda",
    "torchaudio": "torchaudio",
    "transformers": "transformers",
}
_PROFILE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MODEL_ID = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$"
)
_MAX_MANIFEST_BYTES = 1024 * 1024


class RuntimeProfileError(RuntimeError):
    """Base error for runtime profile operations."""


class RuntimeProfileValidationError(RuntimeProfileError):
    """The profile or dependency contract is invalid."""


class RuntimeProfileConflictError(RuntimeProfileError):
    """A profile selection is incompatible with its target."""


@dataclass(frozen=True, slots=True)
class DependencyRequirement:
    """Canonical PEP 508 and uv source contract for one direct dependency."""

    name: str
    extras: tuple[str, ...]
    specifier: str
    marker: str
    source_kind: str
    source_value: str

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "extras": list(self.extras),
            "marker": self.marker,
            "name": self.name,
            "sourceKind": self.source_kind,
            "sourceValue": self.source_value,
            "specifier": self.specifier,
        }


@dataclass(frozen=True, slots=True)
class LockedDependencyBinding:
    """One direct requirement bound to its concrete locked package source."""

    name: str
    extras: tuple[str, ...]
    specifier: str
    marker: str
    expected_source_kind: str
    expected_source_value: str
    locked_version: str
    locked_markers: tuple[str, ...]
    locked_source_kind: str
    locked_source_value: str

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "expectedSourceKind": self.expected_source_kind,
            "expectedSourceValue": self.expected_source_value,
            "extras": list(self.extras),
            "lockedSourceKind": self.locked_source_kind,
            "lockedSourceValue": self.locked_source_value,
            "lockedVersion": self.locked_version,
            "lockedMarkers": list(self.locked_markers),
            "marker": self.marker,
            "name": self.name,
            "specifier": self.specifier,
        }


@dataclass(frozen=True, slots=True)
class LockedProbeBinding:
    """One probed distribution bound to an exact uv.lock package record."""

    name: str
    probe_id: str
    locked_version: str
    locked_markers: tuple[str, ...]

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "lockedMarkers": list(self.locked_markers),
            "lockedVersion": self.locked_version,
            "name": self.name,
            "probeId": self.probe_id,
        }


@dataclass(frozen=True, slots=True)
class RuntimeTarget:
    """Concrete machine target required for every executable plan."""

    operating_system: str
    architecture: str
    gpu_mode: str

    def __post_init__(self) -> None:
        if self.operating_system not in SUPPORTED_OPERATING_SYSTEMS:
            raise RuntimeProfileValidationError(
                f"Unsupported operating system: {self.operating_system!r}"
            )
        if self.architecture not in SUPPORTED_ARCHITECTURES:
            raise RuntimeProfileValidationError(
                f"Unsupported architecture: {self.architecture!r}"
            )
        if self.gpu_mode not in SUPPORTED_GPU_MODES:
            raise RuntimeProfileValidationError(
                f"Unsupported GPU mode: {self.gpu_mode!r}"
            )

    def canonical_dict(self) -> dict[str, str]:
        return {
            "arch": self.architecture,
            "gpu": self.gpu_mode,
            "os": self.operating_system,
        }


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    id: str
    display_name: str
    targets: tuple[RuntimeTarget, ...]
    uv_extras: tuple[str, ...]
    components: tuple[str, ...]
    verification_pending: bool
    mutually_exclusive: tuple[str, ...]
    estimated_download_bytes: int
    estimated_installed_bytes: int
    required_binaries: tuple[str, ...]
    required_models: tuple[str, ...]
    probes: tuple[str, ...]

    def supports(self, target: RuntimeTarget) -> bool:
        return target in self.targets

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "components": list(self.components),
            "displayName": self.display_name,
            "estimatedDownloadBytes": self.estimated_download_bytes,
            "estimatedInstalledBytes": self.estimated_installed_bytes,
            "id": self.id,
            "mutuallyExclusive": list(self.mutually_exclusive),
            "probes": list(self.probes),
            "requiredBinaries": list(self.required_binaries),
            "requiredModels": list(self.required_models),
            "targets": [target.canonical_dict() for target in self.targets],
            "uvExtras": list(self.uv_extras),
            "verificationPending": self.verification_pending,
        }


@dataclass(frozen=True, slots=True)
class RuntimeProfileCatalog:
    schema: int
    profiles: tuple[RuntimeProfile, ...]
    dependency_extras: tuple[tuple[str, tuple[str, ...]], ...]
    dependency_requirements: tuple[DependencyRequirement, ...]
    dependency_bindings: tuple[LockedDependencyBinding, ...]
    locked_probe_bindings: tuple[LockedProbeBinding, ...]
    digest: str

    def get(self, profile_id: str) -> RuntimeProfile:
        for profile in self.profiles:
            if profile.id == profile_id:
                return profile
        raise RuntimeProfileValidationError(f"Unknown runtime profile: {profile_id!r}")

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "dependencyExtras": {
                name: list(requirements) for name, requirements in self.dependency_extras
            },
            "dependencyRequirements": [
                requirement.canonical_dict()
                for requirement in self.dependency_requirements
            ],
            "dependencyBindings": [
                binding.canonical_dict()
                for binding in self.dependency_bindings
            ],
            "lockedProbeBindings": [
                binding.canonical_dict()
                for binding in self.locked_probe_bindings
            ],
            "profiles": [profile.canonical_dict() for profile in self.profiles],
            "schema": self.schema,
        }


@dataclass(frozen=True, slots=True)
class RuntimeAction:
    id: str
    argv: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolvedRuntimePlan:
    target: RuntimeTarget
    profile_ids: tuple[str, ...]
    uv_extras: tuple[str, ...]
    components: tuple[str, ...]
    component_actions: tuple[RuntimeAction, ...]
    required_binaries: tuple[str, ...]
    required_models: tuple[str, ...]
    probes: tuple[str, ...]
    version_expectations: tuple[tuple[str, str], ...]
    estimated_download_bytes: int
    estimated_installed_bytes: int
    verification_pending_profiles: tuple[str, ...]
    verification_pending: bool
    ready: bool

    @property
    def uv_arguments(self) -> tuple[str, ...]:
        arguments = ["sync", "--frozen", "--no-dev"]
        for extra in self.uv_extras:
            arguments.extend(("--extra", extra))
        return tuple(arguments)

    @property
    def uv_dry_run_arguments(self) -> tuple[str, ...]:
        arguments = [
            *self.uv_arguments,
            "--dry-run",
            "--offline",
            "--python-platform",
            _UV_PLATFORM_BY_TARGET[
                (
                    self.target.operating_system,
                    self.target.architecture,
                )
            ],
        ]
        return tuple(arguments)


_COMPONENT_ACTIONS: dict[str, RuntimeAction | None] = {
    "remote-api": None,
    "playwright-chromium": RuntimeAction(
        id="install-playwright-chromium",
        argv=(
            "run",
            "--frozen",
            "--no-sync",
            "playwright",
            "install",
            "chromium",
        ),
    ),
}

_UV_PLATFORM_BY_TARGET = {
    ("windows", "x86_64"): "x86_64-pc-windows-msvc",
    ("linux", "x86_64"): "x86_64-unknown-linux-gnu",
    ("linux", "aarch64"): "aarch64-unknown-linux-gnu",
    ("macos", "aarch64"): "aarch64-apple-darwin",
}


def declared_runtime_targets(profile: RuntimeProfile) -> tuple[RuntimeTarget, ...]:
    """Enumerate every explicit target declared by a profile."""

    return profile.targets


def all_runtime_targets() -> tuple[RuntimeTarget, ...]:
    return tuple(
        RuntimeTarget(operating_system, architecture, gpu_mode)
        for operating_system, architecture, gpu_mode in product(
            sorted(SUPPORTED_OPERATING_SYSTEMS),
            sorted(SUPPORTED_ARCHITECTURES),
            sorted(SUPPORTED_GPU_MODES),
        )
    )


def _marker_environment(
    *,
    target: RuntimeTarget,
    python_version: str,
    extra: str,
) -> dict[str, str]:
    parsed_python = Version(python_version)
    python_minor = f"{parsed_python.major}.{parsed_python.minor}"
    python_full = (
        f"{parsed_python.major}.{parsed_python.minor}.{parsed_python.micro}"
    )
    system_values = {
        "windows": ("nt", "win32", "Windows"),
        "linux": ("posix", "linux", "Linux"),
        "macos": ("posix", "darwin", "Darwin"),
    }
    machine_values = {
        ("windows", "x86_64"): "AMD64",
        ("windows", "aarch64"): "ARM64",
        ("linux", "x86_64"): "x86_64",
        ("linux", "aarch64"): "aarch64",
        ("macos", "x86_64"): "x86_64",
        ("macos", "aarch64"): "arm64",
    }
    os_name, sys_platform, platform_system = system_values[
        target.operating_system
    ]
    return {
        "extra": extra,
        "implementation_name": "cpython",
        "implementation_version": python_full,
        "os_name": os_name,
        "platform_machine": machine_values[
            (target.operating_system, target.architecture)
        ],
        "platform_python_implementation": "CPython",
        "platform_release": "",
        "platform_system": platform_system,
        "platform_version": "",
        "python_full_version": python_full,
        "python_version": python_minor,
        "sys_platform": sys_platform,
    }


def _marker_applies(
    marker: str,
    *,
    target: RuntimeTarget,
    python_version: str,
    extra: str,
) -> bool:
    return not marker or Marker(marker).evaluate(
        _marker_environment(
            target=target,
            python_version=python_version,
            extra=extra,
        )
    )


def _runtime_python_version(value: str | None) -> str:
    if value is None:
        value = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if not isinstance(value, str):
        raise RuntimeProfileValidationError("python_version must be a string")
    try:
        parsed = Version(value)
    except InvalidVersion as exc:
        raise RuntimeProfileValidationError("python_version is invalid") from exc
    minor = f"{parsed.major}.{parsed.minor}"
    if minor not in _SUPPORTED_PYTHON_VERSIONS:
        raise RuntimeProfileValidationError(
            f"Unsupported runtime Python version: {value!r}"
        )
    return str(parsed)


def _locked_probe_binding_applies(
    binding: LockedProbeBinding,
    *,
    target: RuntimeTarget,
    python_version: str,
    selected_extras: tuple[str, ...],
) -> bool:
    if not binding.locked_markers:
        return True
    extras = tuple(dict.fromkeys(("", *selected_extras)))
    return any(
        _marker_applies(
            marker,
            target=target,
            python_version=python_version,
            extra=extra,
        )
        for marker in binding.locked_markers
        for extra in extras
    )


def _exact_probe_expectations(
    catalog: RuntimeProfileCatalog,
    *,
    probes: tuple[str, ...],
    selected_extras: tuple[str, ...],
    target: RuntimeTarget,
    python_version: str,
) -> tuple[tuple[str, str], ...]:
    expectations: list[tuple[str, str]] = []
    mapped_probes = frozenset(_PACKAGE_PROBES.values())
    for probe_id in sorted(set(probes).intersection(mapped_probes)):
        applicable = tuple(
            binding
            for binding in catalog.locked_probe_bindings
            if binding.probe_id == probe_id
            and _locked_probe_binding_applies(
                binding,
                target=target,
                python_version=python_version,
                selected_extras=selected_extras,
            )
        )
        versions = {binding.locked_version for binding in applicable}
        if not applicable:
            raise RuntimeProfileValidationError(
                f"uv.lock has no package record for probe {probe_id!r} on "
                f"{target.operating_system}/{target.architecture} with Python "
                f"{python_version}"
            )
        if len(versions) != 1:
            raise RuntimeProfileValidationError(
                f"uv.lock has ambiguous package versions for probe {probe_id!r} on "
                f"{target.operating_system}/{target.architecture} with Python "
                f"{python_version}"
            )
        expectations.append((probe_id, f"=={next(iter(versions))}"))
    return tuple(expectations)


def _normalize_url(
    value: str,
    *,
    label: str,
    require_sha256: bool = False,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 32 for character in value)
    ):
        raise RuntimeProfileValidationError(f"{label} is not a canonical URL")
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise RuntimeProfileValidationError(f"{label} must use an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise RuntimeProfileValidationError(f"{label} must not contain credentials")
    hostname = (parsed.hostname or "").lower()
    if hostname not in _TRUSTED_DEPENDENCY_HOSTS:
        raise RuntimeProfileValidationError(
            f"{label} host is outside the production dependency allowlist"
        )
    try:
        explicit_port = parsed.port
    except ValueError as exc:
        raise RuntimeProfileValidationError(f"{label} contains an invalid port") from exc
    if explicit_port is not None:
        raise RuntimeProfileValidationError(f"{label} must not contain an explicit port")
    if parsed.query:
        raise RuntimeProfileValidationError(f"{label} must not contain a query")
    fragment = parsed.fragment
    if require_sha256:
        match = _DIRECT_URL_HASH.fullmatch(fragment)
        if match is None:
            raise RuntimeProfileValidationError(
                f"{label} must include one SHA-256 URL fragment"
            )
        fragment = f"sha256={match.group(1).lower()}"
    elif fragment:
        raise RuntimeProfileValidationError(f"{label} must not contain a URL fragment")
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            parsed.query,
            fragment,
        )
    )


def _canonical_marker(value: str, *, label: str) -> str:
    if not value:
        return ""
    if (
        len(value) > _MAX_MARKER_LENGTH
        or any(
            len(segment) > _MAX_VERSION_DIGIT_SEGMENT
            for segment in re.findall(r"[0-9]+", value)
        )
    ):
        raise RuntimeProfileValidationError(f"{label} exceeds marker limits")
    try:
        return str(Marker(value))
    except InvalidMarker as exc:
        raise RuntimeProfileValidationError(f"{label} contains an invalid marker") from exc


def _canonical_specifier(value: str, *, label: str) -> str:
    if (
        len(value) > _MAX_SPECIFIER_LENGTH
        or any(
            len(segment) > _MAX_VERSION_DIGIT_SEGMENT
            for segment in re.findall(r"[0-9]+", value)
        )
    ):
        raise RuntimeProfileValidationError(f"{label} exceeds version limits")
    try:
        return str(SpecifierSet(value))
    except InvalidSpecifier as exc:
        raise RuntimeProfileValidationError(
            f"{label} contains an invalid version specifier"
        ) from exc


def _canonical_version(value: Any, *, label: str) -> Version:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_VERSION_LENGTH
        or any(
            len(segment) > _MAX_VERSION_DIGIT_SEGMENT
            for segment in re.findall(r"[0-9]+", value)
        )
    ):
        raise RuntimeProfileValidationError(f"{label} has an invalid bounded version")
    try:
        parsed = Version(value)
    except InvalidVersion as exc:
        raise RuntimeProfileValidationError(
            f"{label} has an invalid version"
        ) from exc
    if str(parsed) != value:
        raise RuntimeProfileValidationError(
            f"{label} version is not canonical"
        )
    return parsed


def _index_urls(pyproject: dict[str, Any]) -> dict[str, str]:
    tool = pyproject.get("tool")
    uv = tool.get("uv") if isinstance(tool, dict) else None
    indexes = uv.get("index", []) if isinstance(uv, dict) else []
    if not isinstance(indexes, list):
        raise RuntimeProfileValidationError("tool.uv.index must be an array")
    values: dict[str, str] = {}
    for index, item in enumerate(indexes):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not isinstance(item.get("url"), str)
        ):
            raise RuntimeProfileValidationError(
                f"tool.uv.index[{index}] must contain string name and url"
            )
        name = item["name"]
        if name in values:
            raise RuntimeProfileValidationError(f"Duplicate uv index name: {name!r}")
        values[name] = _normalize_url(item["url"], label=f"uv index {name!r}")
    return values


def _uv_sources(pyproject: dict[str, Any]) -> dict[str, Any]:
    tool = pyproject.get("tool")
    uv = tool.get("uv") if isinstance(tool, dict) else None
    sources = uv.get("sources", {}) if isinstance(uv, dict) else {}
    if not isinstance(sources, dict):
        raise RuntimeProfileValidationError("tool.uv.sources must be a table")
    normalized: dict[str, Any] = {}
    for name, source in sources.items():
        if not isinstance(name, str):
            raise RuntimeProfileValidationError("tool.uv.sources keys must be strings")
        key = canonicalize_name(name)
        if key in normalized:
            raise RuntimeProfileValidationError(f"Duplicate uv source: {key!r}")
        normalized[key] = source
    return normalized


def _expected_source(
    requirement: Requirement,
    *,
    sources: dict[str, Any],
    indexes: dict[str, str],
) -> tuple[str, str]:
    name = canonicalize_name(requirement.name)
    configured = sources.get(name)
    if requirement.url is not None and configured is not None:
        raise RuntimeProfileValidationError(
            f"Dependency {name!r} declares both a direct URL and tool.uv source"
        )
    if requirement.url is not None:
        return "url", _normalize_url(
            requirement.url,
            label=f"direct URL for {name!r}",
            require_sha256=True,
        )
    if configured is None:
        return "", ""
    if not isinstance(configured, dict):
        raise RuntimeProfileValidationError(
            f"tool.uv.sources.{name} must be one source table"
        )
    source_keys = sorted(set(configured).intersection(_SOURCE_KINDS))
    unknown = sorted(set(configured) - _SOURCE_KINDS)
    if unknown or len(source_keys) != 1:
        raise RuntimeProfileValidationError(
            f"tool.uv.sources.{name} must contain exactly one supported source"
        )
    kind = source_keys[0]
    value = configured[kind]
    if not isinstance(value, str) or not value:
        raise RuntimeProfileValidationError(
            f"tool.uv.sources.{name}.{kind} must be a non-empty string"
        )
    if kind == "index":
        if value not in indexes:
            raise RuntimeProfileValidationError(
                f"tool.uv.sources.{name} references unknown index {value!r}"
            )
        return kind, indexes[value]
    if kind in {"url", "git"}:
        if kind == "git":
            raise RuntimeProfileValidationError(
                "Git dependencies are outside the production runtime trust boundary"
            )
        return kind, _normalize_url(
            value,
            label=f"{kind} source for {name!r}",
            require_sha256=True,
        )
    raise RuntimeProfileValidationError(
        "Local path dependencies are outside the production runtime trust boundary"
    )


def _expected_requirement(
    raw: str,
    *,
    extra_name: str | None,
    sources: dict[str, Any],
    indexes: dict[str, str],
) -> DependencyRequirement:
    if not isinstance(raw, str) or len(raw) > 4096:
        raise RuntimeProfileValidationError("Dependency requirement exceeds limits")
    try:
        requirement = Requirement(raw)
    except InvalidRequirement as exc:
        raise RuntimeProfileValidationError(
            f"Unsupported dependency requirement: {raw!r}"
        ) from exc
    marker = str(requirement.marker) if requirement.marker is not None else ""
    if extra_name is not None:
        extra_marker = f'extra == "{extra_name}"'
        marker = (
            _canonical_marker(
                f"({marker}) and {extra_marker}",
                label=f"marker for extra {extra_name!r}",
            )
            if marker
            else _canonical_marker(
                extra_marker,
                label=f"marker for extra {extra_name!r}",
            )
        )
    source_kind, source_value = _expected_source(
        requirement,
        sources=sources,
        indexes=indexes,
    )
    return DependencyRequirement(
        name=canonicalize_name(requirement.name),
        extras=tuple(sorted(canonicalize_name(extra) for extra in requirement.extras)),
        specifier=_canonical_specifier(
            str(requirement.specifier),
            label=f"specifier for {requirement.name!r}",
        ),
        marker=_canonical_marker(
            marker,
            label=f"marker for {requirement.name!r}",
        ),
        source_kind=source_kind,
        source_value=source_value,
    )


def _locked_requirement(value: Any, *, index: int) -> DependencyRequirement:
    if not isinstance(value, dict):
        raise RuntimeProfileValidationError(
            f"uv.lock metadata.requires-dist[{index}] must be a table"
        )
    unknown = sorted(set(value) - _LOCK_REQUIREMENT_FIELDS)
    if unknown:
        raise RuntimeProfileValidationError(
            "uv.lock metadata requirement contains unknown fields: "
            + ", ".join(unknown)
        )
    name = value.get("name")
    if not isinstance(name, str) or not name:
        raise RuntimeProfileValidationError(
            f"uv.lock metadata.requires-dist[{index}] is missing a name"
        )
    extras_value = value.get("extras", value.get("extra", []))
    if isinstance(extras_value, str):
        extras_value = [extras_value]
    if not isinstance(extras_value, list) or any(
        not isinstance(extra, str) for extra in extras_value
    ):
        raise RuntimeProfileValidationError(
            f"uv.lock metadata requirement {name!r} has invalid extras"
        )
    specifier_value = value.get("specifier", "")
    marker_value = value.get("marker", "")
    if not isinstance(specifier_value, str) or not isinstance(marker_value, str):
        raise RuntimeProfileValidationError(
            f"uv.lock metadata requirement {name!r} has invalid specifier or marker"
        )
    source_keys = sorted(set(value).intersection(_SOURCE_KINDS))
    if len(source_keys) > 1:
        raise RuntimeProfileValidationError(
            f"uv.lock metadata requirement {name!r} has multiple sources"
        )
    source_kind = source_keys[0] if source_keys else ""
    source_value = value[source_kind] if source_kind else ""
    if not isinstance(source_value, str):
        raise RuntimeProfileValidationError(
            f"uv.lock metadata requirement {name!r} has an invalid source"
        )
    if source_kind in {"git", "path"}:
        raise RuntimeProfileValidationError(
            f"uv.lock {source_kind} source is outside the production trust boundary"
        )
    if source_kind in {"index", "url"}:
        source_value = _normalize_url(
            source_value,
            label=f"uv.lock {source_kind} for {name!r}",
            require_sha256=source_kind == "url",
        )
    return DependencyRequirement(
        name=canonicalize_name(name),
        extras=tuple(sorted(canonicalize_name(extra) for extra in extras_value)),
        specifier=_canonical_specifier(
            specifier_value,
            label=f"uv.lock specifier for {name!r}",
        ),
        marker=_canonical_marker(
            marker_value,
            label=f"uv.lock marker for {name!r}",
        ),
        source_kind=source_kind,
        source_value=source_value,
    )


def _read_toml(path: Path, *, label: str) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeProfileValidationError(f"Cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeProfileValidationError(f"{label} must contain a TOML table")
    return value


def _validate_lock_artifact(value: Any, *, package_name: str, label: str) -> None:
    if not isinstance(value, dict):
        raise RuntimeProfileValidationError(
            f"uv.lock {package_name!r} {label} artifact is invalid"
        )
    url = value.get("url")
    digest = value.get("hash")
    if not isinstance(url, str) or not isinstance(digest, str):
        raise RuntimeProfileValidationError(
            f"uv.lock {package_name!r} {label} artifact is unpinned"
        )
    if (
        _normalize_url(
            url,
            label=f"uv.lock {package_name!r} {label} artifact",
        )
        != url
    ):
        raise RuntimeProfileValidationError(
            f"uv.lock {package_name!r} {label} artifact URL is not canonical"
        )
    if _LOCK_SHA256.fullmatch(digest) is None:
        raise RuntimeProfileValidationError(
            f"uv.lock {package_name!r} {label} artifact has no valid SHA-256"
        )


@dataclass(frozen=True, slots=True)
class _LockedPackageRecord:
    version: Version
    source_kind: str
    source_value: str
    resolution_markers: tuple[str, ...]


def _validate_lock_package_sources(
    packages: list[Any],
    *,
    project_name: str,
) -> dict[str, tuple[_LockedPackageRecord, ...]]:
    canonical_project = canonicalize_name(project_name)
    records: dict[str, list[_LockedPackageRecord]] = {}
    for index, package in enumerate(packages):
        if not isinstance(package, dict) or not isinstance(package.get("name"), str):
            raise RuntimeProfileValidationError(
                f"uv.lock package[{index}] is invalid"
            )
        package_name = canonicalize_name(package["name"])
        source = package.get("source")
        if not isinstance(source, dict) or len(source) != 1:
            raise RuntimeProfileValidationError(
                f"uv.lock package {package_name!r} has an invalid source"
            )
        if package_name == canonical_project and source == {"virtual": "."}:
            continue
        version = _canonical_version(
            package.get("version"),
            label=f"uv.lock package {package_name!r}",
        )
        raw_markers = package.get("resolution-markers", [])
        if not isinstance(raw_markers, list) or any(
            not isinstance(marker, str) for marker in raw_markers
        ):
            raise RuntimeProfileValidationError(
                f"uv.lock package {package_name!r} resolution markers are invalid"
            )
        resolution_markers = tuple(
            sorted(
                _canonical_marker(
                    marker,
                    label=f"uv.lock package {package_name!r} resolution marker",
                )
                for marker in raw_markers
            )
        )
        if len(resolution_markers) != len(set(resolution_markers)):
            raise RuntimeProfileValidationError(
                f"uv.lock package {package_name!r} resolution markers are duplicated"
            )
        source_kind = next(iter(source))
        source_value = source[source_kind]
        if source_kind == "url" and isinstance(source_value, str):
            normalized_url = _normalize_url(
                source_value,
                label=f"uv.lock direct URL for {package_name!r}",
                require_sha256=True,
            )
            if normalized_url != source_value:
                raise RuntimeProfileValidationError(
                    f"uv.lock direct URL for {package_name!r} is not canonical"
                )
            records.setdefault(package_name, []).append(
                _LockedPackageRecord(
                    version=version,
                    source_kind="url",
                    source_value=normalized_url,
                    resolution_markers=resolution_markers,
                )
            )
            continue
        if source_kind != "registry" or not isinstance(source_value, str):
            raise RuntimeProfileValidationError(
                f"uv.lock package {package_name!r} source is outside "
                "the production trust boundary"
            )
        normalized_registry = _normalize_url(
            source_value,
            label=f"uv.lock registry for {package_name!r}",
        )
        if normalized_registry != source_value:
            raise RuntimeProfileValidationError(
                f"uv.lock registry for {package_name!r} is not canonical"
            )
        if "sdist" in package:
            _validate_lock_artifact(
                package["sdist"],
                package_name=package_name,
                label="sdist",
            )
        wheels = package.get("wheels", [])
        if not isinstance(wheels, list):
            raise RuntimeProfileValidationError(
                f"uv.lock package {package_name!r} wheels are invalid"
            )
        for wheel_index, wheel in enumerate(wheels):
            _validate_lock_artifact(
                wheel,
                package_name=package_name,
                label=f"wheel[{wheel_index}]",
            )
        if "sdist" not in package and not wheels:
            raise RuntimeProfileValidationError(
                f"uv.lock package {package_name!r} has no pinned artifacts"
            )
        records.setdefault(package_name, []).append(
            _LockedPackageRecord(
                version=version,
                source_kind="index",
                source_value=normalized_registry,
                resolution_markers=resolution_markers,
            )
        )
    return {
        name: tuple(
            sorted(
                values,
                key=lambda record: (
                    record.version,
                    record.source_kind,
                    record.source_value,
                    record.resolution_markers,
                ),
            )
        )
        for name, values in records.items()
    }


def _normalize_lock_direct(value: Any, *, group: str) -> str:
    if not isinstance(value, dict) or not isinstance(value.get("name"), str):
        raise RuntimeProfileValidationError(
            f"uv.lock {group} contains an invalid dependency entry"
        )
    name = canonicalize_name(value["name"])
    extras_value = value.get("extra", value.get("extras", []))
    if not isinstance(extras_value, list) or any(
        not isinstance(extra, str) for extra in extras_value
    ):
        raise RuntimeProfileValidationError(
            f"uv.lock {group} contains invalid package extras"
        )
    extras = sorted(canonicalize_name(extra) for extra in extras_value)
    return f"{name}[{','.join(extras)}]" if extras else name


def _requirement_direct_key(requirement: DependencyRequirement) -> str:
    suffix = (
        f"[{','.join(requirement.extras)}]" if requirement.extras else ""
    )
    return f"{requirement.name}{suffix}"


def _dependency_contract(
    pyproject_path: Path,
    lock_path: Path,
) -> tuple[
    tuple[tuple[str, tuple[str, ...]], ...],
    tuple[DependencyRequirement, ...],
    tuple[LockedDependencyBinding, ...],
    tuple[LockedProbeBinding, ...],
]:
    pyproject = _read_toml(pyproject_path, label="pyproject.toml")
    lock = _read_toml(lock_path, label="uv.lock")
    project = pyproject.get("project")
    if not isinstance(project, dict):
        raise RuntimeProfileValidationError("pyproject.toml is missing [project]")
    name = project.get("name")
    version = project.get("version")
    base_values = project.get("dependencies")
    optional = project.get("optional-dependencies")
    if (
        not isinstance(name, str)
        or not isinstance(version, str)
        or not isinstance(base_values, list)
        or not isinstance(optional, dict)
    ):
        raise RuntimeProfileValidationError(
            "pyproject.toml project dependency contract is invalid"
        )
    if any(not isinstance(value, str) for value in base_values):
        raise RuntimeProfileValidationError("project.dependencies must be a string array")

    indexes = _index_urls(pyproject)
    sources = _uv_sources(pyproject)
    expected_base = tuple(
        _expected_requirement(
            raw,
            extra_name=None,
            sources=sources,
            indexes=indexes,
        )
        for raw in base_values
    )
    expected_by_extra: dict[str, tuple[DependencyRequirement, ...]] = {}
    for raw_extra, values in optional.items():
        if not isinstance(raw_extra, str) or not isinstance(values, list):
            raise RuntimeProfileValidationError(
                "project.optional-dependencies must contain string arrays"
            )
        if any(not isinstance(value, str) for value in values):
            raise RuntimeProfileValidationError(
                f"Extra {raw_extra!r} contains a non-string requirement"
            )
        extra = canonicalize_name(raw_extra)
        if extra in expected_by_extra:
            raise RuntimeProfileValidationError(f"Duplicate normalized extra: {extra!r}")
        expected_by_extra[extra] = tuple(
            _expected_requirement(
                raw,
                extra_name=extra,
                sources=sources,
                indexes=indexes,
            )
            for raw in values
        )

    missing_runtime_extras = sorted(RUNTIME_EXTRA_ALLOWLIST - set(expected_by_extra))
    if missing_runtime_extras:
        raise RuntimeProfileValidationError(
            "pyproject.toml is missing runtime extras: " + ", ".join(missing_runtime_extras)
        )

    packages = lock.get("package")
    if not isinstance(packages, list):
        raise RuntimeProfileValidationError("uv.lock is missing package records")
    roots = [
        package
        for package in packages
        if isinstance(package, dict)
        and canonicalize_name(str(package.get("name", "")))
        == canonicalize_name(name)
        and isinstance(package.get("source"), dict)
        and package["source"].get("virtual") == "."
    ]
    if len(roots) != 1:
        raise RuntimeProfileValidationError(
            "uv.lock must contain exactly one virtual root project record"
        )
    root = roots[0]
    if root.get("version") != version:
        raise RuntimeProfileValidationError(
            "pyproject.toml and uv.lock project versions differ"
        )
    locked_records = _validate_lock_package_sources(packages, project_name=name)

    lock_base_values = root.get("dependencies")
    if not isinstance(lock_base_values, list):
        raise RuntimeProfileValidationError(
            "uv.lock root project is missing direct dependencies"
        )
    expected_base_keys = Counter(
        _requirement_direct_key(requirement) for requirement in expected_base
    )
    actual_base_keys = Counter(
        _normalize_lock_direct(value, group="root dependencies")
        for value in lock_base_values
    )
    if expected_base_keys != actual_base_keys:
        raise RuntimeProfileValidationError(
            "pyproject.toml and uv.lock root direct dependencies differ"
        )

    locked_optional = root.get("optional-dependencies")
    if not isinstance(locked_optional, dict):
        raise RuntimeProfileValidationError(
            "uv.lock root project is missing optional dependencies"
        )
    dependency_extras: dict[str, tuple[str, ...]] = {}
    for raw_extra, values in locked_optional.items():
        if not isinstance(raw_extra, str) or not isinstance(values, list):
            raise RuntimeProfileValidationError(
                "uv.lock optional dependency entries must be arrays"
            )
        extra = canonicalize_name(raw_extra)
        dependency_extras[extra] = tuple(
            sorted(
                _normalize_lock_direct(
                    value,
                    group=f"extra {extra!r}",
                )
                for value in values
            )
        )
    expected_extra_keys = {
        extra: tuple(
            sorted(_requirement_direct_key(requirement) for requirement in requirements)
        )
        for extra, requirements in expected_by_extra.items()
    }
    if dependency_extras != expected_extra_keys:
        differing = sorted(
            extra
            for extra in set(dependency_extras) | set(expected_extra_keys)
            if dependency_extras.get(extra) != expected_extra_keys.get(extra)
        )
        raise RuntimeProfileValidationError(
            "pyproject.toml and uv.lock optional dependencies differ: "
            + ", ".join(differing)
        )

    metadata = root.get("metadata")
    if not isinstance(metadata, dict):
        raise RuntimeProfileValidationError("uv.lock root metadata is missing")
    provides = metadata.get("provides-extras")
    if not isinstance(provides, list) or any(
        not isinstance(extra, str) for extra in provides
    ):
        raise RuntimeProfileValidationError(
            "uv.lock root metadata.provides-extras is invalid"
        )
    if {canonicalize_name(extra) for extra in provides} != set(expected_by_extra):
        raise RuntimeProfileValidationError(
            "uv.lock provides-extras does not match pyproject.toml"
        )
    metadata_values = metadata.get("requires-dist")
    if not isinstance(metadata_values, list):
        raise RuntimeProfileValidationError(
            "uv.lock root metadata.requires-dist is missing"
        )
    actual_requirements = tuple(
        _locked_requirement(value, index=index)
        for index, value in enumerate(metadata_values)
    )
    expected_requirements = expected_base + tuple(
        requirement
        for extra in sorted(expected_by_extra)
        for requirement in expected_by_extra[extra]
    )
    if Counter(actual_requirements) != Counter(expected_requirements):
        expected_counter = Counter(expected_requirements)
        actual_counter = Counter(actual_requirements)
        missing = sorted(
            {
                requirement.name
                for requirement in (expected_counter - actual_counter).elements()
            }
        )
        unexpected = sorted(
            {
                requirement.name
                for requirement in (actual_counter - expected_counter).elements()
            }
        )
        raise RuntimeProfileValidationError(
            "pyproject.toml and uv.lock metadata.requires-dist differ"
            f" (missing={missing}, unexpected={unexpected})"
        )

    expected_sources: dict[str, set[tuple[str, str]]] = {}
    for requirement in expected_requirements:
        expected_sources.setdefault(requirement.name, set()).add(
            (
                requirement.source_kind or "index",
                requirement.source_value or _DEFAULT_INDEX_URL,
            )
        )
    for package_name, allowed_sources in expected_sources.items():
        records = locked_records.get(package_name, ())
        if not records:
            raise RuntimeProfileValidationError(
                f"uv.lock does not resolve direct dependency {package_name!r}"
            )
        if any(
            (record.source_kind, record.source_value) not in allowed_sources
            for record in records
        ):
            raise RuntimeProfileValidationError(
                "uv.lock direct dependency source does not match its declaration "
                f"for {package_name!r}"
            )
    bindings: list[LockedDependencyBinding] = []
    for requirement in expected_requirements:
        expected_source_kind = requirement.source_kind or "index"
        expected_source_value = requirement.source_value or _DEFAULT_INDEX_URL
        specifier = SpecifierSet(requirement.specifier)
        matching_records = tuple(
            record
            for record in locked_records.get(requirement.name, ())
            if record.source_kind == expected_source_kind
            and record.source_value == expected_source_value
            and (not requirement.specifier or record.version in specifier)
        )
        if not matching_records:
            raise RuntimeProfileValidationError(
                "uv.lock package/version/source does not satisfy direct dependency "
                f"{requirement.name!r} with marker {requirement.marker!r}"
            )
        for record in matching_records:
            bindings.append(
                LockedDependencyBinding(
                    name=requirement.name,
                    extras=requirement.extras,
                    specifier=requirement.specifier,
                    marker=requirement.marker,
                    expected_source_kind=expected_source_kind,
                    expected_source_value=expected_source_value,
                    locked_version=str(record.version),
                    locked_markers=record.resolution_markers,
                    locked_source_kind=record.source_kind,
                    locked_source_value=record.source_value,
                )
            )
    ordered_requirements = tuple(
        sorted(
            expected_requirements,
            key=lambda item: (
                item.name,
                item.extras,
                item.marker,
                item.specifier,
                item.source_kind,
                item.source_value,
            ),
        )
    )
    ordered_bindings = tuple(
        sorted(
            bindings,
            key=lambda item: (
                item.name,
                item.extras,
                item.marker,
                item.specifier,
                item.expected_source_kind,
                item.expected_source_value,
                item.locked_version,
                item.locked_markers,
                item.locked_source_kind,
                item.locked_source_value,
            ),
        )
    )
    locked_probe_bindings = tuple(
        sorted(
            (
                LockedProbeBinding(
                    name=package_name,
                    probe_id=_PACKAGE_PROBES[package_name],
                    locked_version=str(record.version),
                    locked_markers=record.resolution_markers,
                )
                for package_name in sorted(_PACKAGE_PROBES)
                for record in locked_records.get(package_name, ())
            ),
            key=lambda item: (
                item.probe_id,
                item.name,
                item.locked_version,
                item.locked_markers,
            ),
        )
    )
    return (
        tuple(sorted(dependency_extras.items())),
        ordered_requirements,
        ordered_bindings,
        locked_probe_bindings,
    )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise RuntimeProfileValidationError(f"Duplicate JSON field: {key!r}")
        value[key] = item
    return value


def _invalid_json_constant(constant: str) -> None:
    raise RuntimeProfileValidationError(f"Invalid JSON numeric constant: {constant}")


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise RuntimeProfileValidationError("Runtime profile manifest is too large")
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_invalid_json_constant,
        )
    except RuntimeProfileValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeProfileValidationError(
            f"Cannot read runtime profile manifest: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeProfileValidationError("Runtime profile manifest must be an object")
    return value


def _require_fields(
    value: dict[str, Any],
    *,
    expected: frozenset[str],
    label: str,
) -> None:
    unknown = sorted(set(value) - expected)
    missing = sorted(expected - set(value))
    if unknown:
        raise RuntimeProfileValidationError(
            f"{label} contains unknown fields: {', '.join(unknown)}"
        )
    if missing:
        raise RuntimeProfileValidationError(
            f"{label} is missing fields: {', '.join(missing)}"
        )


def _string_list(
    value: Any,
    *,
    label: str,
    allowed: frozenset[str] | None = None,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RuntimeProfileValidationError(f"{label} must be a string array")
    if not allow_empty and not value:
        raise RuntimeProfileValidationError(f"{label} must contain at least one value")
    if len(value) != len(set(value)):
        raise RuntimeProfileValidationError(f"{label} contains duplicate values")
    if allowed is not None:
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise RuntimeProfileValidationError(
                f"{label} contains unsupported values: {', '.join(unknown)}"
            )
    return tuple(sorted(value))


def _targets_from_json(value: Any, *, label: str) -> tuple[RuntimeTarget, ...]:
    if not isinstance(value, list) or not value:
        raise RuntimeProfileValidationError(
            f"{label} must contain explicit target objects"
        )
    targets: list[RuntimeTarget] = []
    for index, raw_target in enumerate(value):
        target_label = f"{label}[{index}]"
        if not isinstance(raw_target, dict):
            raise RuntimeProfileValidationError(
                f"{target_label} must be an object"
            )
        _require_fields(
            raw_target,
            expected=_TARGET_FIELDS,
            label=target_label,
        )
        if any(not isinstance(raw_target[field], str) for field in _TARGET_FIELDS):
            raise RuntimeProfileValidationError(
                f"{target_label} fields must be strings"
            )
        target = RuntimeTarget(
            raw_target["os"],
            raw_target["arch"],
            raw_target["gpu"],
        )
        if (
            target.operating_system,
            target.architecture,
        ) not in _UV_PLATFORM_BY_TARGET:
            raise RuntimeProfileValidationError(
                f"{target_label} is outside the installable release target set"
            )
        targets.append(target)
    ordered = tuple(
        sorted(
            targets,
            key=lambda target: (
                target.operating_system,
                target.architecture,
                target.gpu_mode,
            ),
        )
    )
    if len(ordered) != len(set(ordered)):
        raise RuntimeProfileValidationError(f"{label} contains duplicate targets")
    return ordered


def _profile_from_json(value: Any, *, index: int) -> RuntimeProfile:
    if not isinstance(value, dict):
        raise RuntimeProfileValidationError(f"profiles[{index}] must be an object")
    label = f"profiles[{index}]"
    _require_fields(value, expected=_PROFILE_FIELDS, label=label)
    profile_id = value["id"]
    display_name = value["displayName"]
    if not isinstance(profile_id, str) or _PROFILE_ID.fullmatch(profile_id) is None:
        raise RuntimeProfileValidationError(f"{label}.id is invalid")
    if (
        not isinstance(display_name, str)
        or not display_name.strip()
        or len(display_name) > 100
        or any(ord(character) < 32 for character in display_name)
    ):
        raise RuntimeProfileValidationError(f"{label}.displayName is invalid")
    if not isinstance(value["verificationPending"], bool):
        raise RuntimeProfileValidationError(
            f"{label}.verificationPending must be boolean"
        )
    numeric: dict[str, int] = {}
    for field in ("estimatedDownloadBytes", "estimatedInstalledBytes"):
        item = value[field]
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise RuntimeProfileValidationError(
                f"{label}.{field} must be a non-negative integer"
            )
        numeric[field] = item
    models = _string_list(value["requiredModels"], label=f"{label}.requiredModels")
    for model in models:
        if (
            _MODEL_ID.fullmatch(model) is None
            or ".." in model
            or "\\" in model
            or ":" in model
        ):
            raise RuntimeProfileValidationError(
                f"{label}.requiredModels contains an unsafe model identifier"
            )
    return RuntimeProfile(
        id=profile_id,
        display_name=display_name.strip(),
        targets=_targets_from_json(value["targets"], label=f"{label}.targets"),
        uv_extras=_string_list(
            value["uvExtras"],
            label=f"{label}.uvExtras",
            allowed=RUNTIME_EXTRA_ALLOWLIST,
        ),
        components=_string_list(
            value["components"],
            label=f"{label}.components",
            allowed=COMPONENT_ALLOWLIST,
        ),
        verification_pending=value["verificationPending"],
        mutually_exclusive=_string_list(
            value["mutuallyExclusive"],
            label=f"{label}.mutuallyExclusive",
        ),
        estimated_download_bytes=numeric["estimatedDownloadBytes"],
        estimated_installed_bytes=numeric["estimatedInstalledBytes"],
        required_binaries=_string_list(
            value["requiredBinaries"],
            label=f"{label}.requiredBinaries",
            allowed=BINARY_ALLOWLIST,
        ),
        required_models=models,
        probes=_string_list(
            value["probes"],
            label=f"{label}.probes",
            allowed=PROBE_ALLOWLIST,
            allow_empty=False,
        ),
    )


def _onnx_flavors(
    extras: tuple[str, ...],
    dependency_extras: dict[str, tuple[str, ...]],
) -> frozenset[str]:
    flavors: set[str] = set()
    for extra in extras:
        requirements = dependency_extras[extra]
        if "onnxruntime" in requirements:
            flavors.add("cpu")
        if "onnxruntime-gpu" in requirements or "audio-separator[gpu]" in requirements:
            flavors.add("gpu")
    return frozenset(flavors)


def _validate_profiles(
    profiles: tuple[RuntimeProfile, ...],
    dependency_extras: tuple[tuple[str, tuple[str, ...]], ...],
) -> None:
    by_id = {profile.id: profile for profile in profiles}
    if len(by_id) != len(profiles):
        raise RuntimeProfileValidationError("Runtime profile IDs must be unique")
    dependencies = dict(dependency_extras)
    for profile_index, profile in enumerate(profiles):
        if _profile_from_json(
            profile.canonical_dict(),
            index=profile_index,
        ) != profile:
            raise RuntimeProfileValidationError(
                f"Profile {profile.id!r} is not in canonical validated form"
            )
        if profile.id in profile.mutually_exclusive:
            raise RuntimeProfileValidationError(
                f"Profile {profile.id!r} cannot exclude itself"
            )
        for other_id in profile.mutually_exclusive:
            other = by_id.get(other_id)
            if other is None:
                raise RuntimeProfileValidationError(
                    f"Profile {profile.id!r} excludes unknown profile {other_id!r}"
                )
            if profile.id not in other.mutually_exclusive:
                raise RuntimeProfileValidationError(
                    f"Profile exclusions must be symmetric: {profile.id!r}, {other_id!r}"
                )
        flavors = _onnx_flavors(profile.uv_extras, dependencies)
        if flavors == {"cpu", "gpu"}:
            raise RuntimeProfileValidationError(
                f"Profile {profile.id!r} mixes CPU and GPU ONNX runtimes"
            )
        needs_model_verification = bool(
            set(profile.uv_extras).intersection(MODEL_VERIFICATION_EXTRAS)
            or profile.required_models
        )
        if needs_model_verification and not profile.verification_pending:
            raise RuntimeProfileValidationError(
                f"Profile {profile.id!r} must remain verificationPending until B4"
            )
        if (
            "playwright-chromium" in profile.components
            and not profile.verification_pending
        ):
            raise RuntimeProfileValidationError(
                f"Profile {profile.id!r} must remain verificationPending "
                "until its Defuddle sidecar contract is complete"
            )

    for index, profile in enumerate(profiles):
        first = _onnx_flavors(profile.uv_extras, dependencies)
        for other in profiles[index + 1 :]:
            second = _onnx_flavors(other.uv_extras, dependencies)
            conflict = (
                ("cpu" in first and "gpu" in second)
                or ("gpu" in first and "cpu" in second)
            )
            if conflict and other.id not in profile.mutually_exclusive:
                raise RuntimeProfileValidationError(
                    "CPU/GPU ONNX profile conflict must be declared: "
                    f"{profile.id!r}, {other.id!r}"
                )


def validate_runtime_profile_catalog(catalog: RuntimeProfileCatalog) -> None:
    """Perform static catalog validation without generating an executable plan."""

    if not isinstance(catalog, RuntimeProfileCatalog):
        raise RuntimeProfileValidationError(
            "Runtime profile catalog has an invalid type"
        )
    if (
        isinstance(catalog.schema, bool)
        or catalog.schema != PROFILE_SCHEMA_VERSION
        or not isinstance(catalog.profiles, tuple)
        or not catalog.profiles
        or any(
            not isinstance(profile, RuntimeProfile)
            for profile in catalog.profiles
        )
        or not isinstance(catalog.dependency_extras, tuple)
        or not isinstance(catalog.dependency_requirements, tuple)
        or not isinstance(catalog.dependency_bindings, tuple)
        or not isinstance(catalog.locked_probe_bindings, tuple)
        or any(
            not isinstance(requirement, DependencyRequirement)
            for requirement in catalog.dependency_requirements
        )
        or any(
            not isinstance(binding, LockedDependencyBinding)
            for binding in catalog.dependency_bindings
        )
        or any(
            not isinstance(binding, LockedProbeBinding)
            for binding in catalog.locked_probe_bindings
        )
        or not isinstance(catalog.digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", catalog.digest) is None
    ):
        raise RuntimeProfileValidationError("Runtime profile catalog is incomplete")
    if tuple(sorted(catalog.profiles, key=lambda profile: profile.id)) != catalog.profiles:
        raise RuntimeProfileValidationError("Runtime profile catalog ordering is unstable")
    if any(
        not isinstance(item, tuple)
        or len(item) != 2
        or not isinstance(item[0], str)
        or not isinstance(item[1], tuple)
        for item in catalog.dependency_extras
    ):
        raise RuntimeProfileValidationError(
            "Runtime profile dependency extras have an invalid structure"
        )
    extra_names = {name for name, _requirements in catalog.dependency_extras}
    if not RUNTIME_EXTRA_ALLOWLIST.issubset(extra_names):
        raise RuntimeProfileValidationError(
            "Runtime profile catalog dependency extras are incomplete"
        )
    if (
        catalog.dependency_extras
        != tuple(sorted(catalog.dependency_extras, key=lambda item: item[0]))
        or len(extra_names) != len(catalog.dependency_extras)
    ):
        raise RuntimeProfileValidationError(
            "Runtime profile dependency extras are not ordered and unique"
        )
    for extra_name, requirements in catalog.dependency_extras:
        if extra_name != canonicalize_name(extra_name):
            raise RuntimeProfileValidationError(
                "Runtime profile dependency extra name is not canonical"
            )
        normalized_requirements: list[str] = []
        if not isinstance(requirements, tuple):
            raise RuntimeProfileValidationError(
                f"Runtime profile dependency extra {extra_name!r} is not canonical"
            )
        for raw_requirement in requirements:
            if not isinstance(raw_requirement, str):
                raise RuntimeProfileValidationError(
                    f"Runtime profile dependency extra {extra_name!r} is not canonical"
                )
            try:
                parsed_requirement = Requirement(raw_requirement)
            except InvalidRequirement as exc:
                raise RuntimeProfileValidationError(
                    f"Runtime profile dependency extra {extra_name!r} is invalid"
                ) from exc
            if (
                parsed_requirement.marker is not None
                or parsed_requirement.url is not None
                or parsed_requirement.specifier
            ):
                raise RuntimeProfileValidationError(
                    f"Runtime profile dependency extra {extra_name!r} is not canonical"
                )
            parsed_extras = tuple(
                sorted(canonicalize_name(value) for value in parsed_requirement.extras)
            )
            normalized_requirements.append(
                _requirement_direct_key(
                    DependencyRequirement(
                        name=canonicalize_name(parsed_requirement.name),
                        extras=parsed_extras,
                        specifier="",
                        marker="",
                        source_kind="",
                        source_value="",
                    )
                )
            )
        if requirements != tuple(sorted(set(normalized_requirements))):
            raise RuntimeProfileValidationError(
                f"Runtime profile dependency extra {extra_name!r} is not canonical"
            )
    for requirement in catalog.dependency_requirements:
        if (
            not isinstance(requirement.name, str)
            or not requirement.name
            or requirement.name != canonicalize_name(requirement.name)
        ):
            raise RuntimeProfileValidationError(
                "Runtime profile dependency name is not canonical"
            )
        try:
            parsed_name = Requirement(requirement.name)
        except InvalidRequirement as exc:
            raise RuntimeProfileValidationError(
                "Runtime profile dependency name is invalid"
            ) from exc
        if (
            parsed_name.url is not None
            or parsed_name.marker is not None
            or parsed_name.specifier
            or parsed_name.extras
        ):
            raise RuntimeProfileValidationError(
                "Runtime profile dependency name is invalid"
            )
        if (
            not isinstance(requirement.extras, tuple)
            or any(not isinstance(extra, str) for extra in requirement.extras)
            or requirement.extras
            != tuple(
                sorted(
                    {
                        canonicalize_name(extra)
                        for extra in requirement.extras
                    }
                )
            )
        ):
            raise RuntimeProfileValidationError(
                "Runtime profile dependency extras are not canonical"
            )
        if (
            not isinstance(requirement.specifier, str)
            or not isinstance(requirement.marker, str)
            or not isinstance(requirement.source_kind, str)
            or not isinstance(requirement.source_value, str)
        ):
            raise RuntimeProfileValidationError(
                "Runtime profile dependency fields have invalid types"
            )
        canonical_specifier = _canonical_specifier(
            requirement.specifier,
            label=f"catalog requirement {requirement.name!r}",
        )
        if canonical_specifier != requirement.specifier:
            raise RuntimeProfileValidationError(
                "Runtime profile dependency specifier is not canonical"
            )
        canonical_marker = _canonical_marker(
            requirement.marker,
            label=f"catalog requirement {requirement.name!r}",
        )
        if canonical_marker != requirement.marker:
            raise RuntimeProfileValidationError(
                "Runtime profile dependency marker is not canonical"
            )
        if requirement.source_kind not in {"", "index", "url"}:
            raise RuntimeProfileValidationError(
                "Runtime profile dependency source is outside the trust boundary"
            )
        if requirement.source_kind == "":
            if requirement.source_value:
                raise RuntimeProfileValidationError(
                    "Runtime profile dependency source value has no source kind"
                )
        else:
            normalized_source = _normalize_url(
                requirement.source_value,
                label=f"catalog source for {requirement.name!r}",
                require_sha256=requirement.source_kind == "url",
            )
            if normalized_source != requirement.source_value:
                raise RuntimeProfileValidationError(
                    "Runtime profile dependency source is not canonical"
                )
    def requirement_order(item: DependencyRequirement) -> tuple[Any, ...]:
        return (
            item.name,
            item.extras,
            item.marker,
            item.specifier,
            item.source_kind,
            item.source_value,
        )

    if (
        len(set(catalog.dependency_requirements))
        != len(catalog.dependency_requirements)
        or catalog.dependency_requirements
        != tuple(sorted(catalog.dependency_requirements, key=requirement_order))
    ):
        raise RuntimeProfileValidationError(
            "Runtime profile dependencies are not ordered and unique"
        )
    requirements_by_key = {
        (
            requirement.name,
            requirement.extras,
            requirement.specifier,
            requirement.marker,
        ): requirement
        for requirement in catalog.dependency_requirements
    }
    binding_keys: set[tuple[Any, ...]] = set()
    bound_requirement_keys: set[tuple[Any, ...]] = set()
    for binding in catalog.dependency_bindings:
        if (
            not isinstance(binding.name, str)
            or not isinstance(binding.extras, tuple)
            or any(not isinstance(extra, str) for extra in binding.extras)
            or not isinstance(binding.specifier, str)
            or not isinstance(binding.marker, str)
            or not isinstance(binding.expected_source_kind, str)
            or not isinstance(binding.expected_source_value, str)
            or not isinstance(binding.locked_version, str)
            or not isinstance(binding.locked_markers, tuple)
            or any(
                not isinstance(marker, str)
                for marker in binding.locked_markers
            )
            or not isinstance(binding.locked_source_kind, str)
            or not isinstance(binding.locked_source_value, str)
        ):
            raise RuntimeProfileValidationError(
                "Locked dependency binding fields have invalid types"
            )
        requirement_key = (
            binding.name,
            binding.extras,
            binding.specifier,
            binding.marker,
        )
        requirement = requirements_by_key.get(requirement_key)
        if requirement is None:
            raise RuntimeProfileValidationError(
                "Locked dependency binding has no direct requirement"
            )
        expected_source_kind = requirement.source_kind or "index"
        expected_source_value = requirement.source_value or _DEFAULT_INDEX_URL
        if (
            binding.expected_source_kind != expected_source_kind
            or binding.expected_source_value != expected_source_value
            or binding.locked_source_kind != expected_source_kind
            or binding.locked_source_value != expected_source_value
        ):
            raise RuntimeProfileValidationError(
                "Locked dependency binding source is inconsistent"
            )
        locked_version = _canonical_version(
            binding.locked_version,
            label=f"locked binding {binding.name!r}",
        )
        if binding.specifier and locked_version not in SpecifierSet(binding.specifier):
            raise RuntimeProfileValidationError(
                "Locked dependency binding version is incompatible"
            )
        if (
            any(
                _canonical_marker(
                    marker,
                    label=f"locked binding marker for {binding.name!r}",
                )
                != marker
                for marker in binding.locked_markers
            )
            or binding.locked_markers
            != tuple(sorted(set(binding.locked_markers)))
        ):
            raise RuntimeProfileValidationError(
                "Locked dependency binding markers are invalid"
            )
        binding_key = (
            *requirement_key,
            binding.expected_source_kind,
            binding.expected_source_value,
            binding.locked_version,
            binding.locked_markers,
            binding.locked_source_kind,
            binding.locked_source_value,
        )
        if binding_key in binding_keys:
            raise RuntimeProfileValidationError(
                "Locked dependency bindings contain duplicates"
            )
        binding_keys.add(binding_key)
        bound_requirement_keys.add(requirement_key)
    if bound_requirement_keys != set(requirements_by_key):
        raise RuntimeProfileValidationError(
            "Locked dependency bindings are incomplete"
        )
    if catalog.dependency_bindings != tuple(
        sorted(
            catalog.dependency_bindings,
            key=lambda binding: (
                binding.name,
                binding.extras,
                binding.marker,
                binding.specifier,
                binding.expected_source_kind,
                binding.expected_source_value,
                binding.locked_version,
                binding.locked_markers,
                binding.locked_source_kind,
                binding.locked_source_value,
            ),
        )
    ):
        raise RuntimeProfileValidationError(
            "Locked dependency bindings are not ordered"
        )
    locked_probe_keys: set[tuple[Any, ...]] = set()
    for binding in catalog.locked_probe_bindings:
        if (
            not isinstance(binding.name, str)
            or binding.name != canonicalize_name(binding.name)
            or not isinstance(binding.probe_id, str)
            or _PACKAGE_PROBES.get(binding.name) != binding.probe_id
            or not isinstance(binding.locked_version, str)
            or not isinstance(binding.locked_markers, tuple)
            or any(not isinstance(marker, str) for marker in binding.locked_markers)
        ):
            raise RuntimeProfileValidationError(
                "Locked probe binding fields are invalid"
            )
        _canonical_version(
            binding.locked_version,
            label=f"locked probe binding {binding.name!r}",
        )
        if (
            binding.locked_markers
            != tuple(sorted(set(binding.locked_markers)))
            or any(
                _canonical_marker(
                    marker,
                    label=f"locked probe marker for {binding.name!r}",
                )
                != marker
                for marker in binding.locked_markers
            )
        ):
            raise RuntimeProfileValidationError(
                "Locked probe binding markers are invalid"
            )
        key = (
            binding.probe_id,
            binding.name,
            binding.locked_version,
            binding.locked_markers,
        )
        if key in locked_probe_keys:
            raise RuntimeProfileValidationError(
                "Locked probe bindings contain duplicates"
            )
        locked_probe_keys.add(key)
    if catalog.locked_probe_bindings != tuple(
        sorted(
            catalog.locked_probe_bindings,
            key=lambda binding: (
                binding.probe_id,
                binding.name,
                binding.locked_version,
                binding.locked_markers,
            ),
        )
    ):
        raise RuntimeProfileValidationError(
            "Locked probe bindings are not ordered"
        )
    _validate_profiles(catalog.profiles, catalog.dependency_extras)
    bindings_by_requirement: dict[
        tuple[str, tuple[str, ...], str, str],
        list[LockedDependencyBinding],
    ] = {}
    for binding in catalog.dependency_bindings:
        bindings_by_requirement.setdefault(
            (
                binding.name,
                binding.extras,
                binding.specifier,
                binding.marker,
            ),
            [],
        ).append(binding)
    for profile in catalog.profiles:
        marker_extras = ("", *profile.uv_extras)
        for target in profile.targets:
            for python_version in _SUPPORTED_PYTHON_VERSIONS:
                _exact_probe_expectations(
                    catalog,
                    probes=profile.probes,
                    selected_extras=profile.uv_extras,
                    target=target,
                    python_version=f"{python_version}.0",
                )
                for requirement in catalog.dependency_requirements:
                    requirement_key = (
                        requirement.name,
                        requirement.extras,
                        requirement.specifier,
                        requirement.marker,
                    )
                    for extra in marker_extras:
                        if not _marker_applies(
                            requirement.marker,
                            target=target,
                            python_version=python_version,
                            extra=extra,
                        ):
                            continue
                        if any(
                            not binding.locked_markers
                            or any(
                                _marker_applies(
                                    locked_marker,
                                    target=target,
                                    python_version=python_version,
                                    extra=extra,
                                )
                                for locked_marker in binding.locked_markers
                            )
                            for binding in bindings_by_requirement[requirement_key]
                        ):
                            continue
                        raise RuntimeProfileValidationError(
                            "Locked dependency resolution markers do not cover "
                            f"profile {profile.id!r} target "
                            f"{target.operating_system}/"
                            f"{target.architecture}/{target.gpu_mode} "
                            f"on Python {python_version}: "
                            f"{requirement.name!r}"
                        )
    canonical = catalog.canonical_dict()
    expected_digest = hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    if catalog.digest != expected_digest:
        raise RuntimeProfileValidationError("Runtime profile catalog digest is invalid")


def dependency_requirement_applies_to_target(
    requirement: DependencyRequirement,
    *,
    selected_extras: tuple[str, ...],
    target: RuntimeTarget,
) -> bool:
    """Evaluate a dependency marker using the requested release target.

    Runtime releases support Python 3.11 and 3.12, so a dependency is retained
    when its marker applies to either supported interpreter on the target.
    """

    extras = selected_extras or ("",)
    for python_version in _SUPPORTED_PYTHON_VERSIONS:
        for extra in extras:
            if _marker_applies(
                requirement.marker,
                target=target,
                python_version=python_version,
                extra=extra,
            ):
                return True
    return False


def _repository_paths(
    *,
    project_root: str | Path | None,
    manifest_path: str | Path | None,
    pyproject_path: str | Path | None,
    lock_path: str | Path | None,
) -> tuple[Path, Path, Path]:
    root = (
        Path(project_root).resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[3]
    )
    expected_paths = (
        (manifest_path, root / "backend" / "resources" / "runtime-profiles.json", "manifest"),
        (pyproject_path, root / "pyproject.toml", "pyproject"),
        (lock_path, root / "uv.lock", "lock"),
    )
    resolved: list[Path] = []
    for supplied, expected, label in expected_paths:
        candidate = Path(supplied) if supplied is not None else expected
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.absolute() != expected.absolute():
            raise RuntimeProfileValidationError(f"Unsupported {label} path: {candidate}")
        real = expected.resolve()
        try:
            real.relative_to(root)
        except ValueError as exc:
            raise RuntimeProfileValidationError(
                f"{label.capitalize()} path escapes the project root"
            ) from exc
        if not real.is_file():
            raise RuntimeProfileValidationError(
                f"Required {label} file is unavailable: {real}"
            )
        resolved.append(real)
    return resolved[0], resolved[1], resolved[2]


def load_runtime_profile_catalog(
    *,
    project_root: str | Path | None = None,
    manifest_path: str | Path | None = None,
    pyproject_path: str | Path | None = None,
    lock_path: str | Path | None = None,
) -> RuntimeProfileCatalog:
    manifest, pyproject, lock = _repository_paths(
        project_root=project_root,
        manifest_path=manifest_path,
        pyproject_path=pyproject_path,
        lock_path=lock_path,
    )
    (
        dependency_extras,
        dependency_requirements,
        dependency_bindings,
        locked_probe_bindings,
    ) = _dependency_contract(pyproject, lock)
    payload = _read_manifest(manifest)
    _require_fields(payload, expected=_ROOT_FIELDS, label="manifest")
    if (
        isinstance(payload["schema"], bool)
        or not isinstance(payload["schema"], int)
        or payload["schema"] != PROFILE_SCHEMA_VERSION
    ):
        raise RuntimeProfileValidationError(
            f"Unsupported runtime profile schema: {payload['schema']!r}"
        )
    values = payload["profiles"]
    if not isinstance(values, list) or not values:
        raise RuntimeProfileValidationError("manifest.profiles must be a non-empty array")
    profiles = tuple(
        sorted(
            (
                _profile_from_json(value, index=index)
                for index, value in enumerate(values)
            ),
            key=lambda profile: profile.id,
        )
    )
    _validate_profiles(profiles, dependency_extras)
    canonical = {
        "dependencyExtras": {
            name: list(requirements) for name, requirements in dependency_extras
        },
        "dependencyRequirements": [
            requirement.canonical_dict() for requirement in dependency_requirements
        ],
        "dependencyBindings": [
            binding.canonical_dict() for binding in dependency_bindings
        ],
        "lockedProbeBindings": [
            binding.canonical_dict() for binding in locked_probe_bindings
        ],
        "profiles": [profile.canonical_dict() for profile in profiles],
        "schema": PROFILE_SCHEMA_VERSION,
    }
    digest = hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    catalog = RuntimeProfileCatalog(
        schema=PROFILE_SCHEMA_VERSION,
        profiles=profiles,
        dependency_extras=dependency_extras,
        dependency_requirements=dependency_requirements,
        dependency_bindings=dependency_bindings,
        locked_probe_bindings=locked_probe_bindings,
        digest=digest,
    )
    validate_runtime_profile_catalog(catalog)
    return catalog


def resolve_runtime_profiles(
    catalog: RuntimeProfileCatalog,
    profile_ids: list[str] | tuple[str, ...],
    *,
    target: RuntimeTarget,
    python_version: str | None = None,
) -> ResolvedRuntimePlan:
    """Create a deterministic plan for an explicit, compatible target."""

    validate_runtime_profile_catalog(catalog)
    if not isinstance(target, RuntimeTarget):
        raise RuntimeProfileValidationError("target must be a RuntimeTarget")
    if isinstance(profile_ids, (str, bytes)) or not isinstance(profile_ids, (list, tuple)):
        raise RuntimeProfileValidationError("profile_ids must be a string array")
    if not profile_ids:
        raise RuntimeProfileValidationError("profile_ids must not be empty")
    if any(not isinstance(profile_id, str) for profile_id in profile_ids):
        raise RuntimeProfileValidationError("profile_ids must be a string array")
    if len(profile_ids) != len(set(profile_ids)):
        raise RuntimeProfileValidationError("profile_ids contains duplicate values")

    selected = tuple(catalog.get(profile_id) for profile_id in sorted(profile_ids))
    selected_ids = {profile.id for profile in selected}
    for profile in selected:
        conflicts = sorted(selected_ids.intersection(profile.mutually_exclusive))
        if conflicts:
            raise RuntimeProfileConflictError(
                f"Profile {profile.id!r} conflicts with {', '.join(conflicts)}"
            )
        if not profile.supports(target):
            raise RuntimeProfileConflictError(
                f"Profile {profile.id!r} does not support target "
                f"{target.operating_system}/{target.architecture}/{target.gpu_mode}"
            )

    extras = tuple(sorted({extra for profile in selected for extra in profile.uv_extras}))
    if _onnx_flavors(extras, dict(catalog.dependency_extras)) == {"cpu", "gpu"}:
        raise RuntimeProfileConflictError(
            "Selected profiles mix CPU and GPU ONNX runtimes"
        )
    components = tuple(
        sorted({component for profile in selected for component in profile.components})
    )
    actions = tuple(
        action
        for component in components
        if (action := _COMPONENT_ACTIONS[component]) is not None
    )
    pending_profiles = tuple(
        profile.id for profile in selected if profile.verification_pending
    )
    selected_probes = tuple(
        sorted({item for profile in selected for item in profile.probes})
    )
    version_expectations = _exact_probe_expectations(
        catalog,
        probes=selected_probes,
        selected_extras=extras,
        target=target,
        python_version=_runtime_python_version(python_version),
    )
    return ResolvedRuntimePlan(
        target=target,
        profile_ids=tuple(profile.id for profile in selected),
        uv_extras=extras,
        components=components,
        component_actions=actions,
        required_binaries=tuple(
            sorted({item for profile in selected for item in profile.required_binaries})
        ),
        required_models=tuple(
            sorted({item for profile in selected for item in profile.required_models})
        ),
        probes=selected_probes,
        version_expectations=version_expectations,
        estimated_download_bytes=sum(
            profile.estimated_download_bytes for profile in selected
        ),
        estimated_installed_bytes=sum(
            profile.estimated_installed_bytes for profile in selected
        ),
        verification_pending_profiles=pending_profiles,
        verification_pending=bool(pending_profiles),
        ready=not pending_profiles,
    )


__all__ = [
    "BINARY_ALLOWLIST",
    "COMPONENT_ALLOWLIST",
    "MODEL_VERIFICATION_EXTRAS",
    "PROFILE_SCHEMA_VERSION",
    "PROBE_ALLOWLIST",
    "RUNTIME_EXTRA_ALLOWLIST",
    "DependencyRequirement",
    "LockedDependencyBinding",
    "LockedProbeBinding",
    "ResolvedRuntimePlan",
    "RuntimeAction",
    "RuntimeProfile",
    "RuntimeProfileCatalog",
    "RuntimeProfileConflictError",
    "RuntimeProfileError",
    "RuntimeProfileValidationError",
    "RuntimeTarget",
    "all_runtime_targets",
    "declared_runtime_targets",
    "dependency_requirement_applies_to_target",
    "load_runtime_profile_catalog",
    "resolve_runtime_profiles",
    "validate_runtime_profile_catalog",
]
