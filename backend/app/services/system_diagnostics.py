"""Stable public diagnostics document shared by the CLI and HTTP API."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.services.runtime_diagnostics import (
    DIAGNOSTIC_SCHEMA_VERSION,
    DiagnosticReport,
    DiagnosticResult,
    DiagnosticStatus,
    list_diagnostic_capabilities,
    run_runtime_diagnostics,
)
from app.version import APP_VERSION

SYSTEM_DIAGNOSTICS_SCHEMA = "mpp.system-diagnostics"
SYSTEM_DIAGNOSTICS_SCHEMA_VERSION = 1
SYSTEM_DIAGNOSTICS_PROBES = (
    "configuration",
    "disk",
    "fastapi",
    "ffmpeg",
    "ffprobe",
    "openai",
    "pillow",
    "python",
    "uv",
    "writable-paths",
)
SYSTEM_DIAGNOSTICS_PER_PROBE_TIMEOUT_SECONDS = 5.0
SYSTEM_DIAGNOSTICS_TOTAL_TIMEOUT_SECONDS = 15.0
SYSTEM_DIAGNOSTICS_MAX_COMPONENTS = len(SYSTEM_DIAGNOSTICS_PROBES)
SYSTEM_DIAGNOSTICS_MAX_DETAIL_FIELDS = 16
SYSTEM_DIAGNOSTICS_MAX_DETAIL_ITEMS = 16
SYSTEM_DIAGNOSTICS_MAX_STRING_BYTES = 256

_MAX_SOURCE_STRING_BYTES = 8192
_MAX_DETAIL_KEY_LENGTH = 64
_REDACTED = "[REDACTED]"
_OUTPUT_REJECTED = "[OUTPUT_REJECTED]"
_DETAIL_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|bili[_-]?jct|cookie|csrf|"
    r"password|refresh[_-]?token|secret|sessdata|token)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(?:"
    r"authorization\s*:\s*bearer\s+\S+"
    r"|(?:api[_-]?key|bili[_-]?jct|cookie|csrf|password|"
    r"refresh[_-]?token|secret|sessdata|token)"
    r"\s*[\"']?\s*[:=]\s*[\"']?[^\s\"',;}{]{4,}"
    r"|(?:sk-|gh[oprsu]_|github_pat_|hf_)[A-Za-z0-9_-]{8,}"
    r"|https?://[^/\s:@]+:[^/\s@]+@"
    r")",
    re.IGNORECASE,
)


def _bounded_text(value: str) -> str:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) > _MAX_SOURCE_STRING_BYTES:
        return _OUTPUT_REJECTED
    normalized = "".join(
        character if character >= " " and character != "\x7f" else " "
        for character in encoded.decode("utf-8", errors="replace")
    )
    if _SENSITIVE_VALUE.search(normalized):
        return _REDACTED
    normalized_bytes = normalized.encode("utf-8")
    if len(normalized_bytes) <= SYSTEM_DIAGNOSTICS_MAX_STRING_BYTES:
        return normalized
    suffix = b"..."
    prefix = normalized_bytes[
        : SYSTEM_DIAGNOSTICS_MAX_STRING_BYTES - len(suffix)
    ].decode("utf-8", errors="ignore")
    return prefix + suffix.decode("ascii")


def _safe_detail_value(value: Any) -> str | int | bool | list[str]:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return _bounded_text(value)
    if isinstance(value, tuple):
        return [
            _bounded_text(item) if isinstance(item, str) else _OUTPUT_REJECTED
            for item in value[:SYSTEM_DIAGNOSTICS_MAX_DETAIL_ITEMS]
        ]
    return _OUTPUT_REJECTED


def _safe_details(result: DiagnosticResult) -> dict[str, str | int | bool | list[str]]:
    details: dict[str, str | int | bool | list[str]] = {}
    for raw_key, raw_value in result.details[:SYSTEM_DIAGNOSTICS_MAX_DETAIL_FIELDS]:
        if not isinstance(raw_key, str) or len(raw_key) > _MAX_DETAIL_KEY_LENGTH:
            continue
        if _DETAIL_KEY.fullmatch(raw_key) is None:
            continue
        details[raw_key] = (
            _REDACTED
            if _SENSITIVE_KEY.search(raw_key)
            else _safe_detail_value(raw_value)
        )
    return details


def _component_document(
    result: DiagnosticResult,
    *,
    display_name: str,
    category: str,
) -> dict[str, Any]:
    status = (
        result.status.value
        if isinstance(result.status, DiagnosticStatus)
        else DiagnosticStatus.ERROR.value
    )
    summary = (
        _bounded_text(result.summary)
        if isinstance(result.summary, str)
        else _OUTPUT_REJECTED
    )
    return {
        "category": _bounded_text(category),
        "details": _safe_details(result),
        "displayName": _bounded_text(display_name),
        "id": result.probe_id,
        "status": status,
        "summary": summary,
    }


def _component_summary(components: list[dict[str, Any]]) -> tuple[str, bool, bool]:
    statuses = tuple(component["status"] for component in components)
    if not statuses:
        return "not-run", False, False
    attention_statuses = {
        DiagnosticStatus.DEGRADED.value,
        DiagnosticStatus.ERROR.value,
        DiagnosticStatus.TIMEOUT.value,
        DiagnosticStatus.UNAVAILABLE.value,
        DiagnosticStatus.UNTRUSTED.value,
    }
    verified = all(status == DiagnosticStatus.AVAILABLE.value for status in statuses)
    healthy = all(
        status
        in {
            DiagnosticStatus.AVAILABLE.value,
            DiagnosticStatus.UNKNOWN.value,
        }
        for status in statuses
    )
    if any(status in attention_statuses for status in statuses):
        return "attention", healthy, verified
    if any(status == DiagnosticStatus.UNKNOWN.value for status in statuses):
        return "unknown", healthy, verified
    return "available", healthy, verified


def build_system_diagnostics_document(
    report: DiagnosticReport,
    *,
    app_version: str = APP_VERSION,
) -> dict[str, Any]:
    """Build the bounded, deterministic diagnostics contract exposed publicly."""

    if not isinstance(report, DiagnosticReport):
        raise TypeError("report must be a DiagnosticReport")

    capabilities = {
        capability.id: capability for capability in list_diagnostic_capabilities()
    }
    allowed_probes = frozenset(SYSTEM_DIAGNOSTICS_PROBES)
    requested_probes = tuple(
        probe_id
        for probe_id in report.requested_probes
        if isinstance(probe_id, str) and probe_id in allowed_probes
    )[:SYSTEM_DIAGNOSTICS_MAX_COMPONENTS]

    components: list[dict[str, Any]] = []
    seen: set[str] = set()
    for result in report.results:
        if len(components) >= SYSTEM_DIAGNOSTICS_MAX_COMPONENTS:
            break
        if not isinstance(result, DiagnosticResult):
            continue
        if result.probe_id not in allowed_probes or result.probe_id in seen:
            continue
        capability = capabilities[result.probe_id]
        components.append(
            _component_document(
                result,
                display_name=capability.display_name,
                category=capability.category,
            )
        )
        seen.add(result.probe_id)

    status, healthy, verified = _component_summary(components)
    payload: dict[str, Any] = {
        "appVersion": _bounded_text(app_version),
        "components": components,
        "diagnosticSchemaVersion": DIAGNOSTIC_SCHEMA_VERSION,
        "healthy": healthy,
        "limits": {
            "componentLimit": SYSTEM_DIAGNOSTICS_MAX_COMPONENTS,
            "detailFieldLimit": SYSTEM_DIAGNOSTICS_MAX_DETAIL_FIELDS,
            "detailItemLimit": SYSTEM_DIAGNOSTICS_MAX_DETAIL_ITEMS,
            "perProbeTimeoutSeconds": SYSTEM_DIAGNOSTICS_PER_PROBE_TIMEOUT_SECONDS,
            "stringByteLimit": SYSTEM_DIAGNOSTICS_MAX_STRING_BYTES,
            "totalTimeoutSeconds": SYSTEM_DIAGNOSTICS_TOTAL_TIMEOUT_SECONDS,
        },
        "requestedProbes": list(requested_probes),
        "schema": SYSTEM_DIAGNOSTICS_SCHEMA,
        "schemaVersion": SYSTEM_DIAGNOSTICS_SCHEMA_VERSION,
        "status": status,
        "verified": verified,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    payload["reportDigest"] = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
    return payload


def get_system_diagnostics() -> dict[str, Any]:
    """Run the fixed public probe set within a bounded total deadline."""

    report = run_runtime_diagnostics(
        SYSTEM_DIAGNOSTICS_PROBES,
        timeout_seconds=SYSTEM_DIAGNOSTICS_PER_PROBE_TIMEOUT_SECONDS,
        total_timeout_seconds=SYSTEM_DIAGNOSTICS_TOTAL_TIMEOUT_SECONDS,
    )
    return build_system_diagnostics_document(report)


__all__ = [
    "SYSTEM_DIAGNOSTICS_MAX_COMPONENTS",
    "SYSTEM_DIAGNOSTICS_MAX_DETAIL_FIELDS",
    "SYSTEM_DIAGNOSTICS_MAX_DETAIL_ITEMS",
    "SYSTEM_DIAGNOSTICS_MAX_STRING_BYTES",
    "SYSTEM_DIAGNOSTICS_PER_PROBE_TIMEOUT_SECONDS",
    "SYSTEM_DIAGNOSTICS_PROBES",
    "SYSTEM_DIAGNOSTICS_SCHEMA",
    "SYSTEM_DIAGNOSTICS_SCHEMA_VERSION",
    "SYSTEM_DIAGNOSTICS_TOTAL_TIMEOUT_SECONDS",
    "build_system_diagnostics_document",
    "get_system_diagnostics",
]
