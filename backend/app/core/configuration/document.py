"""Configuration document migration and dotted update paths."""

from typing import Any

from app.core.configuration.bindings import (
    _binding_record,
    _normalize_runtime_model_bindings,
    _sync_flat_from_runtime_model_bindings,
)
from app.core.configuration.constants import (
    _CONNECTION_FIELD_FLAT_KEYS,
    _FLAT_CONNECTION_FIELDS,
    _MODEL_FIELD_SPECS,
)
from app.core.configuration.profiles import _str_value
from app.core.configuration.registry import (
    _canonical_provider_id,
    _default_service_connection_by_id,
    _ensure_providers,
    _ensure_service_connections,
    _ensure_service_models,
    _generic_service_connection,
    _normalize_provider_array,
    _normalize_service_model_array,
    _service_model_record,
    _sync_provider_flat_fields,
    _sync_service_registry_from_providers,
)


def _get_service_connection(
    data: dict[str, Any],
    connection_id: str,
) -> dict[str, Any]:
    connections = _ensure_service_connections(data)
    for connection in connections:
        if isinstance(connection, dict) and connection.get("id") == connection_id:
            return connection

    connection = _default_service_connection_by_id(data, connection_id)
    if connection is None:
        connection = _generic_service_connection(connection_id)
    connections.append(connection)
    return connection


def _set_service_connection_field(
    data: dict[str, Any],
    connection_id: str,
    field: str,
    value: Any,
) -> None:
    connection = _get_service_connection(data, connection_id)
    connection[field] = value


def _sync_service_connections_from_flat(
    data: dict[str, Any],
    touched_flat_keys: set[str],
) -> None:
    for flat_key in touched_flat_keys:
        mirror = _FLAT_CONNECTION_FIELDS.get(flat_key)
        if mirror is None:
            continue
        connection_id, field = mirror
        _set_service_connection_field(data, connection_id, field, data.get(flat_key))


def _sync_flat_from_service_connection_field(
    data: dict[str, Any],
    connection_id: str,
    field: str,
    value: Any,
) -> str | None:
    flat_key = _CONNECTION_FIELD_FLAT_KEYS.get(connection_id, {}).get(field)
    if flat_key is None:
        return None
    data[flat_key] = value
    return flat_key


def _ensure_service_model_for_field(data: dict[str, Any], field: str) -> None:
    spec = _MODEL_FIELD_SPECS.get(field)
    if spec is None:
        return
    connection_id, model_type, capabilities = spec
    default_params = (
        {"dim": data.get("kb_embedding_dim", 1024)} if field == "kb_embedding_model" else None
    )
    record = _service_model_record(
        connection_id,
        data.get(field),
        capabilities,
        model_type=model_type,
        default_params=default_params,
    )
    if record is None:
        return

    models = _ensure_service_models(data)
    for item in models:
        if not isinstance(item, dict):
            continue
        if (
            item.get("connection_id") == record["connection_id"]
            and item.get("model_id") == record["model_id"]
        ):
            item.setdefault("display_name", record["display_name"])
            item.setdefault("model_type", record["model_type"])
            item.setdefault("capabilities", record["capabilities"])
            item.setdefault("endpoint_path", record["endpoint_path"])
            item.setdefault("enabled", record["enabled"])
            item.setdefault("default_params", record["default_params"])
            return
    models.append(record)


def _sync_service_models_from_flat(data: dict[str, Any], touched_flat_keys: set[str]) -> None:
    for flat_key in touched_flat_keys:
        _ensure_service_model_for_field(data, flat_key)


def _normalize_settings_document_state(
    data: dict[str, Any],
    *,
    sync_flat_keys: set[str] | None = None,
) -> None:
    legacy_asr_provider = _str_value(data.get("asr_provider")).strip().lower()
    if legacy_asr_provider in {"qwen3", "qwen3_gguf"}:
        data["asr_provider"] = "sherpa_onnx"
    default_sherpa_model = (
        "qwen3-asr-1.7b-onnx"
        if legacy_asr_provider in {"qwen3", "qwen3_gguf"}
        else "sensevoice-small-int8"
    )
    data.setdefault("sherpa_model_id", default_sherpa_model)
    data.setdefault("sherpa_model_root", "")
    data.setdefault(
        "sherpa_device", data.get("qwen3_gguf_device") or data.get("qwen3_device") or "auto"
    )
    data.setdefault("sherpa_num_threads", 4)
    legacy_chunk_strategy = _str_value(data.get("qwen3_gguf_chunk_strategy")).strip().lower()
    data.setdefault(
        "sherpa_chunk_strategy", "fixed" if legacy_chunk_strategy == "ffmpeg" else "vad"
    )
    data.setdefault("sherpa_max_chunk_sec", 30.0)
    data.setdefault(
        "sherpa_vad_model_path",
        _str_value(data.get("silero_onnx_model_path")),
    )
    data.setdefault("sherpa_debug", False)
    data.setdefault("asr_timestamp_mode", "auto")

    for legacy_key in (
        "qwen3_asr_model_path",
        "qwen3_enable_timestamps",
        "qwen3_batch_size",
        "qwen3_max_new_tokens",
        "qwen3_device",
        "qwen3_gguf_model_path",
        "qwen3_gguf_mmproj_path",
        "qwen3_gguf_hf_repo",
        "qwen3_gguf_device",
        "qwen3_gguf_ctx",
        "qwen3_gguf_n_gpu_layers",
        "qwen3_gguf_timeout_sec",
        "qwen3_gguf_keepalive_sec",
        "qwen3_gguf_chunk_strategy",
        "silero_onnx_model_path",
    ):
        data.pop(legacy_key, None)

    legacy_moss_chunk_config = "moss_cpp_chunk_duration_sec" not in data
    data.setdefault("moss_cpp_chunk_duration_sec", 1200.0)
    data.setdefault("moss_cpp_chunk_overlap_sec", 60.0)
    if legacy_moss_chunk_config and data.get("moss_cpp_max_new_tokens") == 32768:
        data["moss_cpp_max_new_tokens"] = 8192

    _ensure_service_connections(data)
    _ensure_service_models(data)

    if sync_flat_keys:
        if "service_models" in sync_flat_keys and isinstance(data.get("service_models"), list):
            data["service_models"] = _normalize_service_model_array(data["service_models"])
        _sync_service_connections_from_flat(data, sync_flat_keys)
        _sync_service_models_from_flat(data, sync_flat_keys)
        if "providers" in sync_flat_keys and isinstance(data.get("providers"), list):
            data["providers"] = _normalize_provider_array(data["providers"])

    _ensure_providers(data)

    if sync_flat_keys and "providers" in sync_flat_keys:
        _sync_provider_flat_fields(data)
        _sync_service_connections_from_flat(data, set(_FLAT_CONNECTION_FIELDS))

    _normalize_runtime_model_bindings(data)
    if sync_flat_keys and "runtime_model_bindings" not in sync_flat_keys:
        asr_binding_keys = {"asr_provider", "sherpa_model_id", "siliconflow_asr_model"}
        if sync_flat_keys & asr_binding_keys:
            provider_id = _canonical_provider_id(data.get("asr_provider"))
            model_id = (
                data.get("sherpa_model_id")
                if provider_id == "sherpa_onnx"
                else data.get("siliconflow_asr_model")
            )
            data["runtime_model_bindings"]["asr"] = _binding_record(
                provider_id,
                model_id,
                "asr",
            )
    if sync_flat_keys and "runtime_model_bindings" in sync_flat_keys:
        _sync_flat_from_runtime_model_bindings(data)
        _ensure_providers(data)
        _sync_provider_flat_fields(data)
        _sync_flat_from_runtime_model_bindings(data)
        _sync_service_connections_from_flat(data, set(_FLAT_CONNECTION_FIELDS))

    _sync_service_registry_from_providers(data)

    if not isinstance(data.get("flow_profiles"), list):
        data["flow_profiles"] = []
    if not isinstance(data.get("active_flow_defaults"), dict):
        data["active_flow_defaults"] = {}


def _apply_dot_path_updates(
    data: dict[str, Any],
    updates: dict[str, Any],
) -> tuple[dict[str, Any], set[str]]:
    direct_updates: dict[str, Any] = {}
    mirrored_flat_keys: set[str] = set()

    for key, value in updates.items():
        parts = key.split(".", 2)
        if len(parts) == 3 and parts[0] == "service_connections":
            _, connection_id, field = parts
            _set_service_connection_field(data, connection_id, field, value)
            flat_key = _sync_flat_from_service_connection_field(
                data,
                connection_id,
                field,
                value,
            )
            if flat_key:
                mirrored_flat_keys.add(flat_key)
            continue
        direct_updates[key] = value

    return direct_updates, mirrored_flat_keys
