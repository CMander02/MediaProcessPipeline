"""Runtime settings management - core module.

Provides RuntimeSettings model and get_runtime_settings() singleton.
This module is imported by all services; the API route layer is a thin wrapper.
"""

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.logging_setup import log_event

logger = logging.getLogger(__name__)

# Settings file path - stored in project root
# __file__ = backend/app/core/settings.py
# parent x4 = project root
SETTINGS_FILE = Path(__file__).parent.parent.parent.parent / "config.json"


class CustomLLMProfile(BaseModel):
    """OpenAI-compatible custom LLM endpoint profile."""

    id: str = "default"
    name: str = "Custom"
    api_base: str = ""
    model: str = ""
    api_key: str = ""


class RuntimeSettings(BaseModel):
    """Settings that can be updated at runtime from frontend."""

    model_config = ConfigDict(extra="allow")

    # LLM
    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""
    anthropic_api_base: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    openai_api_key: str = ""
    openai_api_base: str = ""
    openai_model: str = "gpt-4o"
    custom_api_key: str = ""
    custom_api_base: str = ""
    custom_model: str = ""
    custom_name: str = "Custom"
    custom_llm_profiles: list[CustomLLMProfile] = Field(default_factory=list)
    custom_active_profile_id: str = "default"

    # DeepSeek (native v4 API with thinking control)
    # Shared credentials — per-stage model/thinking/effort below.
    deepseek_api_key: str = ""
    deepseek_api_base: str = "https://api.deepseek.com"

    # Per-stage config. thinking: "disabled" | "enabled". effort: "" | "high" | "max".
    # Analyze (Phase 1 metadata extraction) — cheap + fast
    deepseek_analyze_model: str = "deepseek-v4-flash"
    deepseek_analyze_thinking: str = "disabled"
    deepseek_analyze_effort: str = ""
    # Polish (subtitle rewrite, bulk work) — cheap + fast
    deepseek_polish_model: str = "deepseek-v4-flash"
    deepseek_polish_thinking: str = "disabled"
    deepseek_polish_effort: str = ""
    # Summary / README — quality priority
    deepseek_summary_model: str = "deepseek-v4-pro"
    deepseek_summary_thinking: str = "enabled"
    deepseek_summary_effort: str = "max"
    # Mindmap (map + reduce) — cheap + fast
    deepseek_mindmap_model: str = "deepseek-v4-flash"
    deepseek_mindmap_thinking: str = "disabled"
    deepseek_mindmap_effort: str = ""

    # Output artifacts
    # mindmap.md is a concise display map; detail.md keeps the former deep outline.
    generate_video_detail: bool = True

    # ASR
    asr_provider: str = "qwen3"  # Supported: qwen3 (local), siliconflow (OpenAI-compatible API)

    # Qwen3-ASR Settings
    qwen3_asr_model_path: str = ""  # Local path, empty = use HuggingFace
    qwen3_aligner_model_path: str = ""  # ForcedAligner path for timestamps
    qwen3_enable_timestamps: bool = True
    qwen3_batch_size: int = 32
    qwen3_max_new_tokens: int = 4096
    qwen3_device: str = "cuda"

    # SiliconFlow ASR (OpenAI-compatible /audio/transcriptions)
    # ffmpeg chunking keeps API-only installs free of torch/torchaudio.
    # Set to "vad" or "auto" if local torch deps are installed and VAD chunking
    # is preferred.
    siliconflow_api_base: str = "https://api.siliconflow.cn/v1"
    siliconflow_api_key: str = ""
    siliconflow_asr_model: str = "FunAudioLLM/SenseVoiceSmall"
    siliconflow_asr_language: str = ""  # "" = auto; e.g. "zh", "en"
    siliconflow_asr_max_chunk_sec: float = 30.0
    siliconflow_asr_timeout_sec: float = 120.0
    siliconflow_asr_chunk_strategy: str = "ffmpeg"  # ffmpeg | vad | auto

    # Speaker Diarization
    enable_diarization: bool = True
    hf_token: str = ""
    # Optional proxy for Hugging Face Hub requests made by pyannote/model loaders.
    # Empty = use process/env proxy or Windows user proxy when available.
    # "direct"/"none" disables proxy env setup for this loader.
    hf_proxy: str = ""
    pyannote_model_path: str = ""
    pyannote_segmentation_path: str = ""
    pyannote_embedding_path: str = ""
    diarization_batch_size: int = 16

    # Voiceprint (speaker embedding) library
    enable_voiceprint: bool = True
    voiceprint_match_threshold: float = 0.75      # >= → auto-merge into existing person
    # [suggest, match) -> suggest but create new; < suggest -> new person
    voiceprint_suggest_threshold: float = 0.60

    # Platform Subtitles
    prefer_platform_subtitles: bool = True  # Use platform subtitles when available
    subtitle_languages: str = "zh,en"  # Comma-separated language priority
    force_asr: bool = False  # Force ASR even when platform subtitles are available

    # UVR
    uvr_model: str = "UVR-MDX-NET-Inst_HQ_3"
    uvr_device: str = "cuda"
    uvr_model_dir: str = ""
    uvr_mdx_inst_hq3_path: str = ""
    uvr_hp_uvr_path: str = ""
    uvr_denoise_lite_path: str = ""
    uvr_kim_vocal_2_path: str = ""
    uvr_deecho_dereverb_path: str = ""
    uvr_htdemucs_path: str = ""
    # audio-separator chunking guard for long files; 0 disables chunking.
    uvr_chunk_duration_sec: float = 300.0

    # Local LLM (transformers + safetensors)
    local_llm_model_path: str = ""          # Path to HuggingFace model directory
    local_llm_device: str = "cuda"          # "cuda" | "cpu" | "auto"
    local_llm_dtype: str = "bfloat16"       # "bfloat16" | "float16" | "float32" | "auto"
    local_llm_max_new_tokens: int = 4096    # Cap per generate() call
    # Kept for backward compat with older settings.json; unused by the transformers backend
    local_llm_n_gpu_layers: int = -1
    local_llm_n_ctx: int = 16384
    local_llm_n_batch: int = 512
    # "" = follow llm_provider, or local/anthropic/openai/custom
    polish_provider: str = "local"
    llm_polish_concurrency: int = 4

    # Concurrency
    max_download_concurrency: int = 2  # max parallel downloads (I/O bound, set 1-4)
    # When False, GPU steps only start after all active downloads finish (serial mode).
    # Reduces peak VRAM by preventing download+GPU overlap.
    # Recommended False for machines with ≤16 GB VRAM.
    pipeline_overlap: bool = True

    # YouTube (yt-dlp)
    # Path to a Netscape-format cookies.txt exported from a logged-in browser.
    # Takes precedence over youtube_cookies_browser when both are set.
    youtube_cookies_file: str = ""
    # Browser name to read cookies from directly (yt-dlp --cookies-from-browser).
    # One of: "", "chrome", "firefox", "edge", "brave", "opera", "vivaldi", "safari".
    # Chrome locks its cookie DB while running — prefer firefox/edge or close Chrome.
    youtube_cookies_browser: str = ""
    # Optional proxy for yt-dlp YouTube requests. Empty = auto-detect process/env
    # or Windows user proxy; "direct"/"none" disables proxy use explicitly.
    youtube_proxy: str = ""

    # Bilibili
    bilibili_sessdata: str = ""
    bilibili_bili_jct: str = ""
    bilibili_dede_user_id: str = ""
    bilibili_preferred_quality: int = 64   # qn: 16=360P 32=480P 64=720P 80=1080P
    # Bilibili subtitles use native WBI API, not yt-dlp
    bilibili_subtitle_engine: str = "native_wbi"
    bilibili_subtitle_strict_validation: bool = True
    bilibili_subtitle_min_coverage: float = 0.60
    bilibili_subtitle_allow_legacy_fallback: bool = False

    # YouTube download quality (for DASH-based YouTube downloader parity)
    youtube_preferred_quality: str = "1080p"  # "720p" | "1080p" | "best"

    # Xiaohongshu
    # Optional raw Cookie header copied from a logged-in browser. Public notes
    # often work without it, but some notes require a browser session.
    xiaohongshu_cookie: str = ""

    # Zhihu
    # Headless Chromium can be blocked on answer pages; the fallback uses a real
    # browser window. "background" starts it minimized, "foreground" leaves it visible.
    zhihu_browser_mode: str = "background"

    # Per-platform configs (JSON string: {platform_id: {quality, prefer_subtitle, ...}})
    platform_configs: str = "{}"

    # VLM (image understanding) — OpenAI-Compatible API
    vlm_api_base: str = ""
    vlm_api_key: str = ""
    vlm_model: str = "qwen2.5-vl-7b-instruct"
    vlm_max_tokens: int = 1024
    vlm_concurrency: int = 3

    # Knowledge base — sqlite-vec vector search over subtitles + summaries
    kb_enabled: bool = True
    kb_embedding_api_base: str = ""
    kb_embedding_api_key: str = ""
    kb_embedding_model: str = "qwen3-embedding-0.6b"
    kb_embedding_dim: int = 1024
    kb_chunk_size_chars: int = 400
    kb_chunk_overlap_chars: int = 50

    # Document-style service registry. The flat fields above remain the
    # compatibility surface used by existing services.
    service_connections: list[dict[str, Any]] = Field(default_factory=list)
    service_models: list[dict[str, Any]] = Field(default_factory=list)
    flow_profiles: list[dict[str, Any]] = Field(default_factory=list)
    active_flow_defaults: dict[str, Any] = Field(default_factory=dict)

    # Security
    api_token: str = ""  # Bearer token for API auth; empty = auth disabled

    # Paths
    data_root: str = "D:/Video/MediaProcessPipeline"

    @field_validator("asr_provider")
    @classmethod
    def _validate_asr_provider(cls, value: str) -> str:
        provider = value.strip().lower()
        if provider not in {"qwen3", "siliconflow"}:
            raise ValueError("asr_provider must be one of: qwen3, siliconflow")
        return provider

    @field_validator("siliconflow_asr_chunk_strategy")
    @classmethod
    def _validate_siliconflow_asr_chunk_strategy(cls, value: str) -> str:
        strategy = value.strip().lower()
        if strategy not in {"ffmpeg", "vad", "auto"}:
            raise ValueError("siliconflow_asr_chunk_strategy must be one of: ffmpeg, vad, auto")
        return strategy

    @field_validator("uvr_chunk_duration_sec")
    @classmethod
    def _validate_uvr_chunk_duration_sec(cls, value: float) -> float:
        if value < 0:
            raise ValueError("uvr_chunk_duration_sec must be greater than or equal to 0")
        return value

    @field_validator("bilibili_subtitle_engine")
    @classmethod
    def _validate_bilibili_subtitle_engine(cls, value: str) -> str:
        engine = value.strip().lower()
        if engine != "native_wbi":
            raise ValueError("bilibili_subtitle_engine must be 'native_wbi'")
        return engine

    @field_validator("bilibili_subtitle_min_coverage")
    @classmethod
    def _validate_bilibili_subtitle_min_coverage(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("bilibili_subtitle_min_coverage must be between 0 and 1")
        return value


# Global runtime settings storage
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
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(
            json.dumps(settings.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log_event(logger, logging.INFO, "settings.saved", path=SETTINGS_FILE)
    except Exception as e:
        log_event(logger, logging.WARNING, "settings.save_failed", path=SETTINGS_FILE, error=e)


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


def _str_value(value: Any) -> str:
    return "" if value is None else str(value)


def _coerce_custom_profiles(raw: Any) -> list[dict[str, str]]:
    """Coerce persisted/custom profile data into stable dicts."""
    profiles: list[dict[str, str]] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        raw = []

    for index, item in enumerate(raw):
        if isinstance(item, BaseModel):
            data = item.model_dump()
        elif isinstance(item, dict):
            data = item
        else:
            data = {}

        profile_id = _str_value(data.get("id") or f"custom-{index + 1}").strip()
        if not profile_id:
            profile_id = f"custom-{index + 1}"
        if profile_id in seen:
            profile_id = f"{profile_id}-{index + 1}"
        seen.add(profile_id)

        profiles.append(
            {
                "id": profile_id,
                "name": _str_value(data.get("name") or data.get("custom_name") or "Custom"),
                "api_base": _str_value(data.get("api_base") or data.get("custom_api_base")),
                "model": _str_value(data.get("model") or data.get("custom_model")),
                "api_key": _str_value(data.get("api_key") or data.get("custom_api_key")),
            }
        )
    return profiles


def _legacy_custom_profile(data: dict[str, Any]) -> dict[str, str]:
    return {
        "id": _str_value(data.get("custom_active_profile_id") or "default"),
        "name": _str_value(data.get("custom_name") or "Custom"),
        "api_base": _str_value(data.get("custom_api_base")),
        "model": _str_value(data.get("custom_model")),
        "api_key": _str_value(data.get("custom_api_key")),
    }


def _normalize_custom_profile_state(data: dict[str, Any], *, prefer_profiles: bool) -> None:
    """Keep multi-profile config and legacy custom_* fields in sync.

    The service still reads the legacy custom_* fields for active calls. The
    profile list is the durable multi-config representation, while the legacy
    fields mirror the active profile for older code paths and CLI commands.
    """
    profiles = _coerce_custom_profiles(data.get("custom_llm_profiles"))
    if not profiles:
        profiles = [_legacy_custom_profile(data)]

    active_id = _str_value(data.get("custom_active_profile_id") or profiles[0]["id"])
    active = next((profile for profile in profiles if profile["id"] == active_id), profiles[0])
    active_id = active["id"]

    if not prefer_profiles:
        active["name"] = _str_value(data.get("custom_name") or active["name"] or "Custom")
        active["api_base"] = _str_value(data.get("custom_api_base") or active["api_base"])
        active["model"] = _str_value(data.get("custom_model") or active["model"])
        active["api_key"] = _str_value(data.get("custom_api_key") or active["api_key"])

    data["custom_llm_profiles"] = profiles
    data["custom_active_profile_id"] = active_id
    data["custom_name"] = active["name"]
    data["custom_api_base"] = active["api_base"]
    data["custom_model"] = active["model"]
    data["custom_api_key"] = active["api_key"]


_CONNECTION_FIELD_FLAT_KEYS: dict[str, dict[str, str]] = {
    "anthropic": {
        "api_base": "anthropic_api_base",
        "api_key": "anthropic_api_key",
    },
    "openai": {
        "api_base": "openai_api_base",
        "api_key": "openai_api_key",
    },
    "deepseek": {
        "api_base": "deepseek_api_base",
        "api_key": "deepseek_api_key",
    },
    "siliconflow-asr": {
        "api_base": "siliconflow_api_base",
        "api_key": "siliconflow_api_key",
    },
    "vision-default": {
        "api_base": "vlm_api_base",
        "api_key": "vlm_api_key",
    },
    "embedding-default": {
        "api_base": "kb_embedding_api_base",
        "api_key": "kb_embedding_api_key",
    },
}

_FLAT_CONNECTION_FIELDS = {
    flat_key: (connection_id, field)
    for connection_id, fields in _CONNECTION_FIELD_FLAT_KEYS.items()
    for field, flat_key in fields.items()
}

_MODEL_FIELD_SPECS: dict[str, tuple[str, list[str]]] = {
    "anthropic_model": ("anthropic", ["chat", "json"]),
    "openai_model": ("openai", ["chat", "vision", "json"]),
    "deepseek_analyze_model": ("deepseek", ["chat", "reasoning", "json"]),
    "deepseek_polish_model": ("deepseek", ["chat", "reasoning", "json"]),
    "deepseek_summary_model": ("deepseek", ["chat", "reasoning", "json"]),
    "deepseek_mindmap_model": ("deepseek", ["chat", "reasoning", "json"]),
    "siliconflow_asr_model": ("siliconflow-asr", ["asr"]),
    "vlm_model": ("vision-default", ["chat", "vision", "json"]),
    "kb_embedding_model": ("embedding-default", ["embedding"]),
}


def _service_connection_record(
    *,
    connection_id: str,
    name: str,
    service_scope: str,
    provider: str,
    endpoint_type: str,
    api_base: Any,
    api_key: Any,
) -> dict[str, Any]:
    return {
        "id": connection_id,
        "name": name,
        "service_scope": service_scope,
        "provider": provider,
        "endpoint_type": endpoint_type,
        "api_base": _str_value(api_base),
        "api_key": _str_value(api_key),
        "headers": {},
        "enabled": True,
        "timeout_sec": 120.0,
        "max_concurrency": 4,
        "status": "unknown",
        "last_checked_at": "",
    }


def _default_service_connections(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _service_connection_record(
            connection_id="anthropic",
            name="Anthropic",
            service_scope="api",
            provider="anthropic",
            endpoint_type="anthropic",
            api_base=data.get("anthropic_api_base"),
            api_key=data.get("anthropic_api_key"),
        ),
        _service_connection_record(
            connection_id="openai",
            name="OpenAI",
            service_scope="api",
            provider="openai",
            endpoint_type="openai_compatible",
            api_base=data.get("openai_api_base"),
            api_key=data.get("openai_api_key"),
        ),
        _service_connection_record(
            connection_id="deepseek",
            name="DeepSeek",
            service_scope="api",
            provider="deepseek",
            endpoint_type="deepseek_native",
            api_base=data.get("deepseek_api_base"),
            api_key=data.get("deepseek_api_key"),
        ),
        _service_connection_record(
            connection_id="siliconflow-asr",
            name="SiliconFlow ASR",
            service_scope="api",
            provider="siliconflow",
            endpoint_type="audio_transcription",
            api_base=data.get("siliconflow_api_base"),
            api_key=data.get("siliconflow_api_key"),
        ),
        _service_connection_record(
            connection_id="vision-default",
            name="Vision API",
            service_scope="api",
            provider="custom_openai",
            endpoint_type="openai_compatible",
            api_base=data.get("vlm_api_base"),
            api_key=data.get("vlm_api_key"),
        ),
        _service_connection_record(
            connection_id="embedding-default",
            name="Knowledge Base Embedding",
            service_scope="api",
            provider="custom_openai",
            endpoint_type="openai_compatible",
            api_base=data.get("kb_embedding_api_base"),
            api_key=data.get("kb_embedding_api_key"),
        ),
    ]


def _default_service_connection_by_id(
    data: dict[str, Any],
    connection_id: str,
) -> dict[str, Any] | None:
    return next(
        (
            connection
            for connection in _default_service_connections(data)
            if connection["id"] == connection_id
        ),
        None,
    )


def _generic_service_connection(connection_id: str) -> dict[str, Any]:
    return _service_connection_record(
        connection_id=connection_id,
        name=connection_id,
        service_scope="api",
        provider=connection_id,
        endpoint_type="openai_compatible",
        api_base="",
        api_key="",
    )


def _model_record_id(connection_id: str, model_id: str) -> str:
    slug = model_id.strip().lower().replace("/", "-").replace(":", "-")
    return f"{connection_id}:{slug}"


def _service_model_record(
    connection_id: str,
    model_id: Any,
    capabilities: list[str],
    *,
    default_params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    model = _str_value(model_id).strip()
    if not model:
        return None
    return {
        "id": _model_record_id(connection_id, model),
        "connection_id": connection_id,
        "model_id": model,
        "display_name": model,
        "capabilities": capabilities,
        "enabled": True,
        "default_params": default_params or {},
    }


def _default_service_models(data: dict[str, Any]) -> list[dict[str, Any]]:
    models: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(field: str, default_params: dict[str, Any] | None = None) -> None:
        connection_id, capabilities = _MODEL_FIELD_SPECS[field]
        record = _service_model_record(
            connection_id,
            data.get(field),
            capabilities,
            default_params=default_params,
        )
        if record is None:
            return
        key = (record["connection_id"], record["model_id"])
        if key in seen:
            return
        seen.add(key)
        models.append(record)

    add("anthropic_model")
    add("openai_model")
    add("deepseek_analyze_model")
    add("deepseek_polish_model")
    add("deepseek_summary_model")
    add("deepseek_mindmap_model")
    add("siliconflow_asr_model")
    add("vlm_model")
    add("kb_embedding_model", {"dim": data.get("kb_embedding_dim", 1024)})
    return models


def _ensure_service_connections(data: dict[str, Any]) -> list[dict[str, Any]]:
    connections = data.get("service_connections")
    if not isinstance(connections, list) or not connections:
        connections = _default_service_connections(data)
        data["service_connections"] = connections
    return connections


def _ensure_service_models(data: dict[str, Any]) -> list[dict[str, Any]]:
    models = data.get("service_models")
    if not isinstance(models, list) or not models:
        models = _default_service_models(data)
        data["service_models"] = models
    return models


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
    connection_id, capabilities = spec
    default_params = (
        {"dim": data.get("kb_embedding_dim", 1024)}
        if field == "kb_embedding_model"
        else None
    )
    record = _service_model_record(
        connection_id,
        data.get(field),
        capabilities,
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
            item.setdefault("capabilities", record["capabilities"])
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
    _ensure_service_connections(data)
    _ensure_service_models(data)
    if not isinstance(data.get("flow_profiles"), list):
        data["flow_profiles"] = []
    if not isinstance(data.get("active_flow_defaults"), dict):
        data["active_flow_defaults"] = {}

    if sync_flat_keys:
        _sync_service_connections_from_flat(data, sync_flat_keys)
        _sync_service_models_from_flat(data, sync_flat_keys)


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


def update_runtime_settings(new_settings: RuntimeSettings) -> RuntimeSettings:
    """Replace all runtime settings and persist."""
    global _runtime_settings
    data = new_settings.model_dump()
    _normalize_custom_profile_state(data, prefer_profiles=True)
    _normalize_settings_document_state(data)
    candidate = RuntimeSettings(**data)
    _validate_data_root(candidate.data_root)
    _runtime_settings = candidate
    _save_settings_to_file(_runtime_settings)
    return _runtime_settings


def patch_runtime_settings(updates: dict[str, Any]) -> RuntimeSettings:
    """Partially update runtime settings and persist."""
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
    _runtime_settings = candidate
    _save_settings_to_file(_runtime_settings)
    return _runtime_settings


def replace_runtime_settings_for_process(settings: RuntimeSettings) -> RuntimeSettings:
    """Replace settings in memory only; used by one-shot CLI flows."""
    global _runtime_settings
    data = settings.model_dump()
    _normalize_custom_profile_state(data, prefer_profiles=True)
    _normalize_settings_document_state(data)
    _runtime_settings = RuntimeSettings(**data)
    return _runtime_settings
