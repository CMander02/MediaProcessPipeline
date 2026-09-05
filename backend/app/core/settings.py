"""Runtime settings singleton, persistence and public compatibility exports."""

import json
import logging
import threading
from pathlib import Path
from typing import Any

from app.core.configuration.bindings import _binding_record as _binding_record
from app.core.configuration.bindings import (
    _default_runtime_model_bindings as _default_runtime_model_bindings,
)
from app.core.configuration.bindings import (
    _normalize_runtime_model_bindings as _normalize_runtime_model_bindings,
)
from app.core.configuration.bindings import _parse_binding_value as _parse_binding_value
from app.core.configuration.bindings import (
    _sync_flat_from_runtime_model_bindings as _sync_flat_from_runtime_model_bindings,
)
from app.core.configuration.constants import (
    _CONNECTION_FIELD_FLAT_KEYS as _CONNECTION_FIELD_FLAT_KEYS,
)
from app.core.configuration.constants import _FLAT_CONNECTION_FIELDS as _FLAT_CONNECTION_FIELDS
from app.core.configuration.constants import _MASKED_SECRET_PATTERN as _MASKED_SECRET_PATTERN
from app.core.configuration.constants import _MODEL_FIELD_SPECS as _MODEL_FIELD_SPECS
from app.core.configuration.constants import _MODEL_TYPE_CAPABILITIES as _MODEL_TYPE_CAPABILITIES
from app.core.configuration.constants import (
    _MODEL_TYPE_ENDPOINT_PATHS as _MODEL_TYPE_ENDPOINT_PATHS,
)
from app.core.configuration.constants import (
    _PROVIDER_CONNECTION_ALIASES as _PROVIDER_CONNECTION_ALIASES,
)
from app.core.configuration.constants import _PROVIDER_FLAT_KEYS as _PROVIDER_FLAT_KEYS
from app.core.configuration.constants import (
    _PROVIDER_MODEL_TYPE_CAPABILITIES as _PROVIDER_MODEL_TYPE_CAPABILITIES,
)
from app.core.configuration.constants import _RUNTIME_BINDING_SPECS as _RUNTIME_BINDING_SPECS
from app.core.configuration.constants import (
    _SILICONFLOW_ASR_DEFAULT_PARAMS as _SILICONFLOW_ASR_DEFAULT_PARAMS,
)
from app.core.configuration.constants import (
    _SILICONFLOW_RERANK_DEFAULT_PARAMS as _SILICONFLOW_RERANK_DEFAULT_PARAMS,
)
from app.core.configuration.document import _apply_dot_path_updates as _apply_dot_path_updates
from app.core.configuration.document import (
    _ensure_service_model_for_field as _ensure_service_model_for_field,
)
from app.core.configuration.document import _get_service_connection as _get_service_connection
from app.core.configuration.document import (
    _normalize_settings_document_state as _normalize_settings_document_state,
)
from app.core.configuration.document import (
    _set_service_connection_field as _set_service_connection_field,
)
from app.core.configuration.document import (
    _sync_flat_from_service_connection_field as _sync_flat_from_service_connection_field,
)
from app.core.configuration.document import (
    _sync_service_connections_from_flat as _sync_service_connections_from_flat,
)
from app.core.configuration.document import (
    _sync_service_models_from_flat as _sync_service_models_from_flat,
)
from app.core.configuration.models import CustomLLMProfile as CustomLLMProfile
from app.core.configuration.models import ProviderBalanceConfig as ProviderBalanceConfig
from app.core.configuration.models import ProviderConfig as ProviderConfig
from app.core.configuration.models import ProviderModelConfig as ProviderModelConfig
from app.core.configuration.models import RuntimeModelBinding as RuntimeModelBinding
from app.core.configuration.models import RuntimeSettings as RuntimeSettings
from app.core.configuration.profiles import _coerce_custom_profiles as _coerce_custom_profiles
from app.core.configuration.profiles import _legacy_custom_profile as _legacy_custom_profile
from app.core.configuration.profiles import (
    _normalize_custom_profile_state as _normalize_custom_profile_state,
)
from app.core.configuration.profiles import _positive_int as _positive_int
from app.core.configuration.profiles import _str_value as _str_value
from app.core.configuration.registry import _canonical_provider_id as _canonical_provider_id
from app.core.configuration.registry import _custom_provider_id as _custom_provider_id
from app.core.configuration.registry import _default_provider_records as _default_provider_records
from app.core.configuration.registry import (
    _default_service_connection_by_id as _default_service_connection_by_id,
)
from app.core.configuration.registry import (
    _default_service_connections as _default_service_connections,
)
from app.core.configuration.registry import _default_service_models as _default_service_models
from app.core.configuration.registry import _deleted_provider_ids as _deleted_provider_ids
from app.core.configuration.registry import _ensure_providers as _ensure_providers
from app.core.configuration.registry import (
    _ensure_service_connections as _ensure_service_connections,
)
from app.core.configuration.registry import _ensure_service_models as _ensure_service_models
from app.core.configuration.registry import _find_provider as _find_provider
from app.core.configuration.registry import _find_provider_model as _find_provider_model
from app.core.configuration.registry import (
    _generic_service_connection as _generic_service_connection,
)
from app.core.configuration.registry import _looks_masked_secret as _looks_masked_secret
from app.core.configuration.registry import _merge_provider_model as _merge_provider_model
from app.core.configuration.registry import _model_default_params as _model_default_params
from app.core.configuration.registry import _model_endpoint_path as _model_endpoint_path
from app.core.configuration.registry import _model_record_id as _model_record_id
from app.core.configuration.registry import (
    _normalize_model_capabilities as _normalize_model_capabilities,
)
from app.core.configuration.registry import _normalize_model_type as _normalize_model_type
from app.core.configuration.registry import _normalize_provider_array as _normalize_provider_array
from app.core.configuration.registry import _normalize_provider_id as _normalize_provider_id
from app.core.configuration.registry import (
    _normalize_provider_model_array as _normalize_provider_model_array,
)
from app.core.configuration.registry import (
    _normalize_service_model_array as _normalize_service_model_array,
)
from app.core.configuration.registry import (
    _provider_from_service_connection as _provider_from_service_connection,
)
from app.core.configuration.registry import _provider_index as _provider_index
from app.core.configuration.registry import (
    _provider_model_capabilities as _provider_model_capabilities,
)
from app.core.configuration.registry import _provider_model_record as _provider_model_record
from app.core.configuration.registry import (
    _provider_model_to_service_model as _provider_model_to_service_model,
)
from app.core.configuration.registry import _provider_record as _provider_record
from app.core.configuration.registry import _service_connection_record as _service_connection_record
from app.core.configuration.registry import _service_model_record as _service_model_record
from app.core.configuration.registry import _sync_provider_flat_fields as _sync_provider_flat_fields
from app.core.configuration.registry import (
    _sync_service_registry_from_providers as _sync_service_registry_from_providers,
)
from app.core.configuration.registry import _upsert_provider as _upsert_provider
from app.core.logging_setup import log_event
from app.core.paths import CONFIG_FILE

logger = logging.getLogger(__name__)

_settings_lock = threading.RLock()

SETTINGS_FILE = CONFIG_FILE

_runtime_settings: RuntimeSettings | None = None


def _load_settings_from_file() -> RuntimeSettings:
    """Load settings from JSON file."""
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            _normalize_custom_profile_state(data, prefer_profiles=True)
            _normalize_settings_document_state(data)
            log_event(logger, logging.INFO, "settings.loaded", path=SETTINGS_FILE)
            return RuntimeSettings(**data)
        except Exception as e:
            log_event(logger, logging.WARNING, "settings.load_failed", path=SETTINGS_FILE, error=e)
    return RuntimeSettings()


def _save_settings_to_file(settings: RuntimeSettings) -> None:
    """Save settings to JSON file."""
    try:
        from app.core.atomic_file import atomic_write_text

        atomic_write_text(
            SETTINGS_FILE, json.dumps(settings.model_dump(), indent=2, ensure_ascii=False)
        )
        log_event(logger, logging.INFO, "settings.saved", path=SETTINGS_FILE)
    except Exception as e:
        log_event(logger, logging.WARNING, "settings.save_failed", path=SETTINGS_FILE, error=e)
        raise


def get_runtime_settings() -> RuntimeSettings:
    """Get current runtime settings (singleton)."""
    global _runtime_settings
    if _runtime_settings is None:
        _runtime_settings = _load_settings_from_file()
    return _runtime_settings


def _validate_data_root(path_str: str) -> None:
    """Reject obviously dangerous data_root values (e.g. filesystem root)."""
    p = Path(path_str).resolve()
    # Must be at least 2 levels deep (e.g. D:/Something, not D:/ or C:/)
    if len(p.parts) < 3:
        raise ValueError(
            f"data_root is too broad: {p} — must be at least two directory levels deep"
        )


def _persist_candidate(candidate: RuntimeSettings) -> RuntimeSettings:
    global _runtime_settings
    previous = get_runtime_settings()
    old_root = Path(previous.data_root).resolve()
    new_root = Path(candidate.data_root).resolve()
    if old_root == new_root:
        _save_settings_to_file(candidate)
        _runtime_settings = candidate
        return candidate

    from app.core.workspace_lifecycle import (
        relocate_daemon_state,
        reset_workspace_stores,
        workspace_change,
    )

    with workspace_change():
        new_root.mkdir(parents=True, exist_ok=True)
        _save_settings_to_file(candidate)
        try:
            reset_workspace_stores(new_root)
            relocate_daemon_state(old_root, new_root)
        except Exception:
            _save_settings_to_file(previous)
            reset_workspace_stores(old_root)
            raise
        _runtime_settings = candidate
    return candidate


def update_runtime_settings(new_settings: RuntimeSettings) -> RuntimeSettings:
    """Replace all runtime settings and persist."""
    with _settings_lock:
        global _runtime_settings
        data = new_settings.model_dump()
        _normalize_custom_profile_state(data, prefer_profiles=True)
        _normalize_settings_document_state(data)
        candidate = RuntimeSettings(**data)
        _validate_data_root(candidate.data_root)
        return _persist_candidate(candidate)


def patch_runtime_settings(updates: dict[str, Any]) -> RuntimeSettings:
    """Partially update runtime settings and persist."""
    with _settings_lock:
        global _runtime_settings
        if _runtime_settings is None:
            _runtime_settings = _load_settings_from_file()
        current = _runtime_settings.model_dump()
        direct_updates, mirrored_flat_keys = _apply_dot_path_updates(current, updates)
        current.update(direct_updates)
        sync_flat_keys = set(direct_updates) | mirrored_flat_keys
        prefer_profiles = any(
            key in direct_updates for key in ("custom_llm_profiles", "custom_active_profile_id")
        )
        _normalize_custom_profile_state(current, prefer_profiles=prefer_profiles)
        _normalize_settings_document_state(current, sync_flat_keys=sync_flat_keys)
        candidate = RuntimeSettings(**current)
        _validate_data_root(candidate.data_root)
        return _persist_candidate(candidate)


def replace_runtime_settings_for_process(settings: RuntimeSettings) -> RuntimeSettings:
    """Replace settings in memory only; used by one-shot CLI flows."""
    global _runtime_settings
    data = settings.model_dump()
    _normalize_custom_profile_state(data, prefer_profiles=True)
    _normalize_settings_document_state(data)
    _runtime_settings = RuntimeSettings(**data)
    return _runtime_settings
