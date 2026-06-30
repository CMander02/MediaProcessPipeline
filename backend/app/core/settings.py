"""Runtime settings management - core module.

Provides RuntimeSettings model and get_runtime_settings() singleton.
This module is imported by all services; the API route layer is a thin wrapper.
"""

import json
import logging
import re
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


class ProviderBalanceConfig(BaseModel):
    """Optional balance endpoint metadata for a provider."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    endpoint_path: str = ""
    method: str = "GET"


class ProviderModelConfig(BaseModel):
    """Model inventory entry owned by one provider endpoint."""

    model_config = ConfigDict(extra="allow")

    id: str = ""
    model_id: str = ""
    display_name: str = ""
    enabled: bool = True
    model_type: str = "llm"
    capabilities: list[str] = Field(default_factory=list)
    endpoint_path: str = "/chat/completions"
    default_params: dict[str, Any] = Field(default_factory=dict)


class ProviderConfig(BaseModel):
    """Configurable API provider endpoint and its model inventory."""

    model_config = ConfigDict(extra="allow")

    id: str = ""
    name: str = ""
    provider_type: str = "openai_compatible"
    enabled: bool = True
    api_base: str = ""
    api_key: str = ""
    api_mode: str = "chat_completions"
    headers: dict[str, Any] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    balance: ProviderBalanceConfig = Field(default_factory=ProviderBalanceConfig)
    models: list[ProviderModelConfig] = Field(default_factory=list)


class RuntimeModelBinding(BaseModel):
    """Active model selection for one pipeline purpose."""

    model_config = ConfigDict(extra="allow")

    provider_id: str = ""
    model_id: str = ""
    capability: str = "llm"


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

    # Generic web page scraping. The pipeline tries Defuddle CLI first and uses
    # Jina Reader when local extraction fails.
    jina_reader_enabled: bool = True
    jina_reader_api_base: str = "https://r.jina.ai"
    jina_reader_api_key: str = ""
    jina_reader_bypass_cache: bool = False
    web_scrape_timeout_sec: float = 30.0

    # Per-platform configs (JSON string: {platform_id: {quality, prefer_subtitle, ...}})
    platform_configs: str = "{}"

    # VLM (image understanding) — OpenAI-Compatible API
    vlm_api_base: str = ""
    vlm_api_key: str = ""
    vlm_model: str = "Qwen/Qwen3.5-4B"
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
    providers: list[ProviderConfig] = Field(default_factory=list)
    deleted_provider_ids: list[str] = Field(default_factory=list)
    runtime_model_bindings: dict[str, RuntimeModelBinding] = Field(default_factory=dict)
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

_MODEL_TYPE_CAPABILITIES: dict[str, list[str]] = {
    "llm": ["chat"],
    "vlm": ["chat", "vision"],
    "embedding": ["embedding"],
    "rerank": ["rerank"],
    "asr": ["asr"],
}

_MODEL_TYPE_ENDPOINT_PATHS: dict[str, str] = {
    "llm": "/chat/completions",
    "vlm": "/chat/completions",
    "embedding": "/embeddings",
    "rerank": "/rerank",
    "asr": "/audio/transcriptions",
}

_SILICONFLOW_ASR_DEFAULT_PARAMS: dict[str, Any] = {
    "request_format": "multipart",
    "file_field": "file",
    "model_field": "model",
    "include_language": False,
    "max_file_mb": 50,
    "max_duration_sec": 3600,
}

_SILICONFLOW_RERANK_DEFAULT_PARAMS: dict[str, Any] = {
    "request_format": "json",
    "query_field": "query",
    "documents_field": "documents",
    "return_documents": False,
    "max_chunks_per_doc": 1024,
}

_PROVIDER_MODEL_TYPE_CAPABILITIES: dict[str, list[str]] = {
    "llm": ["llm", "chat", "json"],
    "vlm": ["vlm", "chat", "vision", "json"],
    "embedding": ["embedding"],
    "rerank": ["rerank"],
    "asr": ["asr"],
}

_PROVIDER_CONNECTION_ALIASES: dict[str, str] = {
    "siliconflow-asr": "siliconflow",
    "vision-default": "custom-vision-default",
    "embedding-default": "custom-embedding-default",
}

_PROVIDER_FLAT_KEYS: dict[str, dict[str, str]] = {
    "anthropic": {
        "api_base": "anthropic_api_base",
        "api_key": "anthropic_api_key",
        "model": "anthropic_model",
    },
    "openai": {
        "api_base": "openai_api_base",
        "api_key": "openai_api_key",
        "model": "openai_model",
    },
    "deepseek": {
        "api_base": "deepseek_api_base",
        "api_key": "deepseek_api_key",
    },
    "siliconflow": {
        "api_base": "siliconflow_api_base",
        "api_key": "siliconflow_api_key",
        "model": "siliconflow_asr_model",
    },
    "custom-vision-default": {
        "api_base": "vlm_api_base",
        "api_key": "vlm_api_key",
        "model": "vlm_model",
    },
    "custom-embedding-default": {
        "api_base": "kb_embedding_api_base",
        "api_key": "kb_embedding_api_key",
        "model": "kb_embedding_model",
    },
}

_MASKED_SECRET_PATTERN = re.compile(r"^\*{3,}\.{3}.{0,4}$")


def _looks_masked_secret(value: Any) -> bool:
    return isinstance(value, str) and bool(_MASKED_SECRET_PATTERN.match(value))

_RUNTIME_BINDING_SPECS: dict[str, tuple[str, str, str]] = {
    "polish": ("deepseek", "deepseek_polish_model", "llm"),
    "subtitle_polish": ("deepseek", "deepseek_polish_model", "llm"),
    "subtitle_refine": ("deepseek", "deepseek_polish_model", "llm"),
    "analyze": ("deepseek", "deepseek_analyze_model", "llm"),
    "summary": ("deepseek", "deepseek_summary_model", "llm"),
    "mindmap": ("deepseek", "deepseek_mindmap_model", "llm"),
    "asr": ("qwen3", "", "asr"),
    "vision": ("custom-vision-default", "vlm_model", "vlm"),
    "embedding": ("custom-embedding-default", "kb_embedding_model", "embedding"),
}

_MODEL_FIELD_SPECS: dict[str, tuple[str, str, list[str]]] = {
    "anthropic_model": ("anthropic", "llm", ["chat", "json"]),
    "openai_model": ("openai", "llm", ["chat", "vision", "json"]),
    "deepseek_analyze_model": ("deepseek", "llm", ["chat", "reasoning", "json"]),
    "deepseek_polish_model": ("deepseek", "llm", ["chat", "reasoning", "json"]),
    "deepseek_summary_model": ("deepseek", "llm", ["chat", "reasoning", "json"]),
    "deepseek_mindmap_model": ("deepseek", "llm", ["chat", "reasoning", "json"]),
    "siliconflow_asr_model": ("siliconflow-asr", "asr", ["asr"]),
    "vlm_model": ("vision-default", "vlm", ["chat", "vision", "json"]),
    "kb_embedding_model": ("embedding-default", "embedding", ["embedding"]),
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


def _normalize_model_type(model_type: Any, capabilities: list[str] | None = None) -> str:
    normalized = _str_value(model_type).strip().lower()
    if normalized in _MODEL_TYPE_CAPABILITIES:
        return normalized

    capability_set = {capability.strip().lower() for capability in capabilities or []}
    if "asr" in capability_set:
        return "asr"
    if "rerank" in capability_set:
        return "rerank"
    if "embedding" in capability_set:
        return "embedding"
    if "vision" in capability_set:
        return "vlm"
    return "llm"


def _normalize_model_capabilities(model_type: str, capabilities: list[str] | None) -> list[str]:
    cleaned = [
        capability.strip().lower()
        for capability in capabilities or []
        if capability and capability.strip()
    ]
    if cleaned:
        return list(dict.fromkeys(cleaned))
    return list(_MODEL_TYPE_CAPABILITIES[model_type])


def _model_endpoint_path(model_type: str) -> str:
    return _MODEL_TYPE_ENDPOINT_PATHS.get(model_type, "/chat/completions")


def _model_default_params(provider_id: str, model_type: str, default_params: dict[str, Any] | None) -> dict[str, Any]:
    params = default_params if isinstance(default_params, dict) else {}
    if provider_id == "siliconflow" and model_type == "asr":
        return {**_SILICONFLOW_ASR_DEFAULT_PARAMS, **params}
    if provider_id == "siliconflow" and model_type == "rerank":
        return {**_SILICONFLOW_RERANK_DEFAULT_PARAMS, **params}
    return params


def _provider_model_capabilities(model_type: str, capabilities: list[str] | None) -> list[str]:
    cleaned = [
        capability.strip().lower()
        for capability in capabilities or []
        if capability and capability.strip()
    ]
    defaults = _PROVIDER_MODEL_TYPE_CAPABILITIES.get(model_type, ["llm", "chat", "json"])
    return list(dict.fromkeys([*defaults, *cleaned]))


def _provider_model_record(
    provider_id: str,
    model_id: Any,
    *,
    model_type: str = "llm",
    capabilities: list[str] | None = None,
    display_name: Any = "",
    enabled: Any = True,
    endpoint_path: Any = "",
    default_params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    model = _str_value(model_id).strip()
    if not model:
        return None
    normalized_type = _normalize_model_type(model_type, capabilities)
    return {
        "id": f"{provider_id}:{model}",
        "model_id": model,
        "display_name": _str_value(display_name).strip() or model,
        "enabled": bool(enabled),
        "model_type": normalized_type,
        "capabilities": _provider_model_capabilities(normalized_type, capabilities),
        "endpoint_path": _str_value(endpoint_path).strip() or _model_endpoint_path(normalized_type),
        "default_params": _model_default_params(provider_id, normalized_type, default_params),
    }


def _provider_record(
    *,
    provider_id: str,
    name: str,
    provider_type: str,
    api_base: Any = "",
    api_key: Any = "",
    enabled: Any = True,
    api_mode: str = "chat_completions",
    headers: dict[str, Any] | None = None,
    extra_body: dict[str, Any] | None = None,
    balance: dict[str, Any] | None = None,
    models: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": provider_id,
        "name": name,
        "provider_type": provider_type,
        "enabled": bool(enabled),
        "api_base": _str_value(api_base),
        "api_key": _str_value(api_key),
        "api_mode": api_mode,
        "headers": headers if isinstance(headers, dict) else {},
        "extra_body": extra_body if isinstance(extra_body, dict) else {},
        "balance": balance if isinstance(balance, dict) else {"enabled": False, "endpoint_path": "", "method": "GET"},
        "models": models or [],
    }


def _normalize_provider_id(value: Any) -> str:
    return _str_value(value).strip().lower().replace(" ", "-")


def _custom_provider_id(profile_id: Any) -> str:
    slug = _normalize_provider_id(profile_id) or "default"
    if slug.startswith("custom-"):
        return slug
    return f"custom-{slug}"


def _canonical_provider_id(provider_id: Any) -> str:
    normalized = _normalize_provider_id(provider_id)
    return _PROVIDER_CONNECTION_ALIASES.get(normalized, normalized)


def _normalize_provider_model_array(
    provider_id: str,
    models: list[Any],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in models:
        if isinstance(item, BaseModel):
            data = item.model_dump()
        elif isinstance(item, dict):
            data = dict(item)
        else:
            continue

        model_id = _str_value(data.get("model_id") or data.get("id") or data.get("display_name")).strip()
        if not model_id or model_id in seen:
            continue
        raw_capabilities = data.get("capabilities")
        capabilities = [str(value) for value in raw_capabilities] if isinstance(raw_capabilities, list) else []
        model_type = _normalize_model_type(data.get("model_type"), capabilities)
        record = _provider_model_record(
            provider_id,
            model_id,
            model_type=model_type,
            capabilities=capabilities,
            display_name=data.get("display_name"),
            enabled=data.get("enabled", True),
            endpoint_path=data.get("endpoint_path"),
            default_params=data.get("default_params") if isinstance(data.get("default_params"), dict) else {},
        )
        if record is None:
            continue
        seen.add(model_id)
        normalized.append(record)
    return normalized


def _merge_provider_model(
    models: list[dict[str, Any]],
    record: dict[str, Any] | None,
) -> None:
    if record is None:
        return
    for index, model in enumerate(models):
        if model.get("model_id") == record["model_id"]:
            models[index] = {
                **record,
                **model,
                "capabilities": _provider_model_capabilities(
                    _normalize_model_type(model.get("model_type", record["model_type"])),
                    [str(value) for value in model.get("capabilities", [])]
                    if isinstance(model.get("capabilities"), list)
                    else record["capabilities"],
                ),
            }
            return
    models.append(record)


def _provider_index(providers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(provider.get("id")): provider
        for provider in providers
        if isinstance(provider, dict) and provider.get("id")
    }


def _deleted_provider_ids(data: dict[str, Any]) -> set[str]:
    raw_ids = data.get("deleted_provider_ids")
    if not isinstance(raw_ids, list):
        return set()
    return {
        _canonical_provider_id(provider_id)
        for provider_id in raw_ids
        if _canonical_provider_id(provider_id)
    }


def _upsert_provider(
    providers: list[dict[str, Any]],
    record: dict[str, Any],
) -> dict[str, Any]:
    existing = _provider_index(providers).get(record["id"])
    if existing is None:
        providers.append(record)
        return record

    existing.update(
        {
            "name": existing.get("name") or record["name"],
            "provider_type": existing.get("provider_type") or record["provider_type"],
            "api_base": existing.get("api_base", record["api_base"]),
            "api_key": record["api_key"]
            if _looks_masked_secret(existing.get("api_key")) and not _looks_masked_secret(record.get("api_key"))
            else existing.get("api_key", record["api_key"]),
            "api_mode": existing.get("api_mode") or record["api_mode"],
            "enabled": bool(existing.get("enabled", record["enabled"])),
            "headers": existing.get("headers") if isinstance(existing.get("headers"), dict) else record["headers"],
            "extra_body": existing.get("extra_body") if isinstance(existing.get("extra_body"), dict) else record["extra_body"],
            "balance": existing.get("balance") if isinstance(existing.get("balance"), dict) else record["balance"],
        }
    )
    models = existing.setdefault("models", [])
    if not isinstance(models, list):
        models = []
        existing["models"] = models
    for model in record.get("models", []):
        _merge_provider_model(models, model)
    return existing


def _service_model_record(
    connection_id: str,
    model_id: Any,
    capabilities: list[str],
    *,
    model_type: str = "llm",
    default_params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    model = _str_value(model_id).strip()
    if not model:
        return None
    normalized_type = _normalize_model_type(model_type, capabilities)
    normalized_capabilities = _normalize_model_capabilities(normalized_type, capabilities)
    return {
        "id": _model_record_id(connection_id, model),
        "connection_id": connection_id,
        "model_id": model,
        "display_name": model,
        "model_type": normalized_type,
        "capabilities": normalized_capabilities,
        "endpoint_path": _model_endpoint_path(normalized_type),
        "enabled": True,
        "default_params": default_params or {},
    }


def _default_service_models(data: dict[str, Any]) -> list[dict[str, Any]]:
    models: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(field: str, default_params: dict[str, Any] | None = None) -> None:
        connection_id, model_type, capabilities = _MODEL_FIELD_SPECS[field]
        record = _service_model_record(
            connection_id,
            data.get(field),
            capabilities,
            model_type=model_type,
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


def _default_provider_records(data: dict[str, Any]) -> list[dict[str, Any]]:
    providers: list[dict[str, Any]] = []

    deepseek_models: list[dict[str, Any]] = []
    for field in (
        "deepseek_analyze_model",
        "deepseek_polish_model",
        "deepseek_summary_model",
        "deepseek_mindmap_model",
    ):
        _merge_provider_model(
            deepseek_models,
            _provider_model_record(
                "deepseek",
                data.get(field),
                model_type="llm",
                capabilities=["reasoning", "json"],
            ),
        )
    providers.append(
        _provider_record(
            provider_id="deepseek",
            name="DeepSeek",
            provider_type="deepseek",
            api_base=data.get("deepseek_api_base"),
            api_key=data.get("deepseek_api_key"),
            extra_body={"thinking": {"type": "disabled"}},
            models=deepseek_models,
        )
    )

    siliconflow_models: list[dict[str, Any]] = []
    for model in data.get("service_models", []):
        if not isinstance(model, dict):
            continue
        if _canonical_provider_id(model.get("connection_id")) != "siliconflow":
            continue
        raw_capabilities = model.get("capabilities")
        capabilities = [str(value) for value in raw_capabilities] if isinstance(raw_capabilities, list) else []
        _merge_provider_model(
            siliconflow_models,
            _provider_model_record(
                "siliconflow",
                model.get("model_id"),
                model_type=_normalize_model_type(model.get("model_type"), capabilities),
                capabilities=capabilities,
                display_name=model.get("display_name"),
                enabled=model.get("enabled", True),
                endpoint_path=model.get("endpoint_path"),
                default_params=model.get("default_params") if isinstance(model.get("default_params"), dict) else {},
            ),
        )
    _merge_provider_model(
        siliconflow_models,
        _provider_model_record(
            "siliconflow",
            data.get("siliconflow_asr_model"),
            model_type="asr",
            capabilities=["asr"],
        ),
    )
    _merge_provider_model(
        siliconflow_models,
        _provider_model_record(
            "siliconflow",
            "TeleAI/TeleSpeechASR",
            model_type="asr",
            capabilities=["asr"],
        ),
    )
    _merge_provider_model(
        siliconflow_models,
        _provider_model_record(
            "siliconflow",
            "BAAI/bge-reranker-v2-m3",
            model_type="rerank",
            capabilities=["rerank"],
        ),
    )
    if _str_value(data.get("vlm_api_base")).strip() == _str_value(data.get("siliconflow_api_base")).strip():
        _merge_provider_model(
            siliconflow_models,
            _provider_model_record(
                "siliconflow",
                data.get("vlm_model"),
                model_type="vlm",
                capabilities=["vision", "json"],
            ),
        )
    if _str_value(data.get("kb_embedding_api_base")).strip() == _str_value(data.get("siliconflow_api_base")).strip():
        _merge_provider_model(
            siliconflow_models,
            _provider_model_record(
                "siliconflow",
                data.get("kb_embedding_model"),
                model_type="embedding",
                capabilities=["embedding"],
                default_params={"dim": data.get("kb_embedding_dim", 1024)},
            ),
        )
    providers.append(
        _provider_record(
            provider_id="siliconflow",
            name="SiliconFlow",
            provider_type="siliconflow",
            api_base=data.get("siliconflow_api_base"),
            api_key=data.get("siliconflow_api_key"),
            balance={"enabled": True, "endpoint_path": "/user/info", "method": "GET"},
            models=siliconflow_models,
        )
    )

    openai_model = _provider_model_record(
        "openai",
        data.get("openai_model"),
        model_type="llm",
        capabilities=["vision", "json"],
    )
    providers.append(
        _provider_record(
            provider_id="openai",
            name="OpenAI",
            provider_type="openai_compatible",
            api_base=data.get("openai_api_base"),
            api_key=data.get("openai_api_key"),
            models=[openai_model] if openai_model else [],
        )
    )

    anthropic_model = _provider_model_record(
        "anthropic",
        data.get("anthropic_model"),
        model_type="llm",
        capabilities=["reasoning", "json"],
    )
    providers.append(
        _provider_record(
            provider_id="anthropic",
            name="Anthropic",
            provider_type="anthropic",
            api_base=data.get("anthropic_api_base"),
            api_key=data.get("anthropic_api_key"),
            models=[anthropic_model] if anthropic_model else [],
        )
    )

    for profile in _coerce_custom_profiles(data.get("custom_llm_profiles")):
        provider_id = _custom_provider_id(profile["id"])
        model = _provider_model_record(
            provider_id,
            profile.get("model"),
            model_type="llm",
            capabilities=["json"],
        )
        providers.append(
            _provider_record(
                provider_id=provider_id,
                name=profile.get("name") or "Custom",
                provider_type="openai_compatible",
                api_base=profile.get("api_base"),
                api_key=profile.get("api_key"),
                models=[model] if model else [],
            )
        )

    if data.get("vlm_api_base"):
        model = _provider_model_record(
            "custom-vision-default",
            data.get("vlm_model"),
            model_type="vlm",
            capabilities=["vision", "json"],
        )
        providers.append(
            _provider_record(
                provider_id="custom-vision-default",
                name="Vision API",
                provider_type="openai_compatible",
                api_base=data.get("vlm_api_base"),
                api_key=data.get("vlm_api_key"),
                models=[model] if model else [],
            )
        )

    if data.get("kb_embedding_api_base"):
        model = _provider_model_record(
            "custom-embedding-default",
            data.get("kb_embedding_model"),
            model_type="embedding",
            capabilities=["embedding"],
            default_params={"dim": data.get("kb_embedding_dim", 1024)},
        )
        providers.append(
            _provider_record(
                provider_id="custom-embedding-default",
                name="Knowledge Base Embedding",
                provider_type="openai_compatible",
                api_base=data.get("kb_embedding_api_base"),
                api_key=data.get("kb_embedding_api_key"),
                models=[model] if model else [],
            )
        )

    return providers


def _provider_from_service_connection(
    data: dict[str, Any],
    connection: dict[str, Any],
    service_models: list[dict[str, Any]],
) -> dict[str, Any] | None:
    raw_id = _str_value(connection.get("id")).strip()
    provider_id = _canonical_provider_id(raw_id)
    if not provider_id:
        return None

    provider_type = _str_value(connection.get("endpoint_type") or connection.get("provider") or "openai_compatible")
    if provider_id == "deepseek":
        provider_type = "deepseek"
    elif provider_id == "siliconflow":
        provider_type = "siliconflow"

    models: list[dict[str, Any]] = []
    for model in service_models:
        if not isinstance(model, dict):
            continue
        if _canonical_provider_id(model.get("connection_id")) != provider_id:
            continue
        raw_capabilities = model.get("capabilities")
        capabilities = [str(value) for value in raw_capabilities] if isinstance(raw_capabilities, list) else []
        _merge_provider_model(
            models,
            _provider_model_record(
                provider_id,
                model.get("model_id"),
                model_type=_normalize_model_type(model.get("model_type"), capabilities),
                capabilities=capabilities,
                display_name=model.get("display_name"),
                enabled=model.get("enabled", True),
                endpoint_path=model.get("endpoint_path"),
                default_params=model.get("default_params") if isinstance(model.get("default_params"), dict) else {},
            ),
        )

    return _provider_record(
        provider_id=provider_id,
        name=_str_value(connection.get("name") or provider_id),
        provider_type=provider_type,
        api_base=connection.get("api_base"),
        api_key=connection.get("api_key"),
        enabled=connection.get("enabled", True),
        headers=connection.get("headers") if isinstance(connection.get("headers"), dict) else {},
        models=models,
        balance={"enabled": True, "endpoint_path": "/user/info", "method": "GET"}
        if provider_id == "siliconflow"
        else None,
    )


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


def _normalize_service_model_array(models: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in models:
        if isinstance(item, BaseModel):
            data = item.model_dump()
        elif isinstance(item, dict):
            data = dict(item)
        else:
            continue

        connection_id = _str_value(data.get("connection_id")).strip()
        model_id = _str_value(data.get("model_id") or data.get("display_name")).strip()
        if not connection_id or not model_id:
            continue

        raw_capabilities = data.get("capabilities")
        capabilities = raw_capabilities if isinstance(raw_capabilities, list) else []
        model_type = _normalize_model_type(data.get("model_type"), [str(item) for item in capabilities])
        data["id"] = _str_value(data.get("id") or _model_record_id(connection_id, model_id))
        data["connection_id"] = connection_id
        data["model_id"] = model_id
        data["display_name"] = _str_value(data.get("display_name") or model_id)
        data["model_type"] = model_type
        data["capabilities"] = _normalize_model_capabilities(
            model_type,
            [str(item) for item in capabilities],
        )
        data["endpoint_path"] = _str_value(data.get("endpoint_path") or _model_endpoint_path(model_type))
        data["enabled"] = bool(data.get("enabled", True))
        if not isinstance(data.get("default_params"), dict):
            data["default_params"] = {}
        normalized.append(data)
    return normalized


def _normalize_provider_array(providers: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(providers):
        if isinstance(item, BaseModel):
            data = item.model_dump()
        elif isinstance(item, dict):
            data = dict(item)
        else:
            continue

        provider_id = _canonical_provider_id(data.get("id") or f"provider-{index + 1}")
        if not provider_id:
            continue
        if provider_id in seen:
            provider_id = f"{provider_id}-{index + 1}"
        seen.add(provider_id)

        provider_type = _str_value(data.get("provider_type") or data.get("endpoint_type") or "openai_compatible")
        if provider_id == "deepseek":
            provider_type = "deepseek"
        elif provider_id == "siliconflow":
            provider_type = "siliconflow"
        balance = data.get("balance") if isinstance(data.get("balance"), dict) else {}
        if provider_id == "siliconflow":
            balance = {"enabled": True, "endpoint_path": "/user/info", "method": "GET", **balance}

        normalized.append(
            _provider_record(
                provider_id=provider_id,
                name=_str_value(data.get("name") or provider_id),
                provider_type=provider_type,
                enabled=data.get("enabled", True),
                api_base=data.get("api_base"),
                api_key=data.get("api_key"),
                api_mode=_str_value(data.get("api_mode") or "chat_completions"),
                headers=data.get("headers") if isinstance(data.get("headers"), dict) else {},
                extra_body=data.get("extra_body") if isinstance(data.get("extra_body"), dict) else {},
                balance=balance,
                models=_normalize_provider_model_array(
                    provider_id,
                    data.get("models") if isinstance(data.get("models"), list) else [],
                ),
            )
        )
    return normalized


def _ensure_providers(data: dict[str, Any]) -> list[dict[str, Any]]:
    deleted = _deleted_provider_ids(data)
    providers = data.get("providers")
    if isinstance(providers, list) and providers:
        normalized = [
            provider
            for provider in _normalize_provider_array(providers)
            if _canonical_provider_id(provider.get("id")) not in deleted
        ]
    else:
        normalized = []

    for record in _default_provider_records(data):
        if _canonical_provider_id(record.get("id")) in deleted:
            continue
        _upsert_provider(normalized, record)

    service_connections = data.get("service_connections")
    service_models = data.get("service_models")
    if isinstance(service_connections, list) and isinstance(service_models, list):
        normalized_service_models = _normalize_service_model_array(service_models)
        for connection in service_connections:
            if not isinstance(connection, dict):
                continue
            record = _provider_from_service_connection(data, connection, normalized_service_models)
            if record is not None:
                if _canonical_provider_id(record.get("id")) in deleted:
                    continue
                _upsert_provider(normalized, record)

    data["providers"] = normalized
    data["deleted_provider_ids"] = sorted(deleted)
    return normalized


def _find_provider(data: dict[str, Any], provider_id: Any) -> dict[str, Any] | None:
    canonical_id = _canonical_provider_id(provider_id)
    for provider in _ensure_providers(data):
        if provider.get("id") == canonical_id:
            return provider
    return None


def _find_provider_model(
    provider: dict[str, Any] | None,
    model_id: Any = "",
    capability: str = "",
) -> dict[str, Any] | None:
    if provider is None:
        return None
    models = provider.get("models")
    if not isinstance(models, list):
        return None
    requested_model = _str_value(model_id).strip()
    requested_capability = _str_value(capability).strip().lower()
    for model in models:
        if not isinstance(model, dict):
            continue
        if requested_model and _str_value(model.get("model_id")) != requested_model:
            continue
        if requested_capability:
            caps = {str(value).strip().lower() for value in model.get("capabilities", [])}
            model_type = _str_value(model.get("model_type")).strip().lower()
            if requested_capability not in caps and requested_capability != model_type:
                continue
        return model
    return None


def _provider_model_to_service_model(
    provider_id: str,
    model: dict[str, Any],
) -> dict[str, Any] | None:
    model_type = _normalize_model_type(model.get("model_type"), [str(value) for value in model.get("capabilities", [])])
    service_connection_id = "siliconflow-asr" if provider_id == "siliconflow" else provider_id
    if provider_id == "custom-vision-default":
        service_connection_id = "vision-default"
    elif provider_id == "custom-embedding-default":
        service_connection_id = "embedding-default"
    return _service_model_record(
        service_connection_id,
        model.get("model_id"),
        _normalize_model_capabilities(model_type, [
            capability
            for capability in [str(value) for value in model.get("capabilities", [])]
            if capability not in {"llm", "vlm"}
        ]),
        model_type=model_type,
        default_params=model.get("default_params") if isinstance(model.get("default_params"), dict) else {},
    )


def _sync_service_registry_from_providers(data: dict[str, Any]) -> None:
    providers = _ensure_providers(data)
    existing_connections = data.get("service_connections")
    preserve_connections = isinstance(existing_connections, list) and bool(existing_connections)
    if not preserve_connections:
        data["service_connections"] = _default_service_connections(data)

    existing_models = data.get("service_models")
    if not isinstance(existing_models, list) or not existing_models:
        service_models: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for provider in providers:
            if not isinstance(provider, dict):
                continue
            provider_id = str(provider.get("id"))
            for model in provider.get("models", []):
                if not isinstance(model, dict):
                    continue
                record = _provider_model_to_service_model(provider_id, model)
                if record is None:
                    continue
                key = (record["connection_id"], record["model_id"])
                if key in seen:
                    continue
                seen.add(key)
                service_models.append(record)
        data["service_models"] = service_models or _default_service_models(data)


def _sync_provider_flat_fields(data: dict[str, Any]) -> None:
    providers = _ensure_providers(data)
    by_id = _provider_index(providers)
    for provider_id, fields in _PROVIDER_FLAT_KEYS.items():
        provider = by_id.get(provider_id)
        if provider is None:
            continue
        if fields.get("api_base"):
            data[fields["api_base"]] = provider.get("api_base", "")
        if fields.get("api_key"):
            provider_api_key = provider.get("api_key", "")
            if _looks_masked_secret(provider_api_key):
                existing_api_key = data.get(fields["api_key"], "")
                if not _looks_masked_secret(existing_api_key):
                    provider["api_key"] = existing_api_key
            else:
                data[fields["api_key"]] = provider_api_key
        model_field = fields.get("model")
        if model_field:
            capability = "asr" if provider_id == "siliconflow" else ""
            if provider_id == "custom-vision-default":
                capability = "vision"
            elif provider_id == "custom-embedding-default":
                capability = "embedding"
            model = _find_provider_model(provider, capability=capability) or _find_provider_model(provider)
            if model is not None:
                data[model_field] = model.get("model_id", data.get(model_field, ""))


def _binding_record(provider_id: Any, model_id: Any, capability: str) -> dict[str, str]:
    return {
        "provider_id": _str_value(provider_id).strip(),
        "model_id": _str_value(model_id).strip(),
        "capability": capability,
    }


def _parse_binding_value(value: Any) -> tuple[str, str]:
    text = _str_value(value).strip()
    if ":" not in text:
        return "", text
    provider_id, model_id = text.split(":", 1)
    return _canonical_provider_id(provider_id), model_id.strip()


def _default_runtime_model_bindings(data: dict[str, Any]) -> dict[str, dict[str, str]]:
    bindings: dict[str, dict[str, str]] = {}
    llm_provider = _canonical_provider_id(data.get("llm_provider") or "deepseek")
    if llm_provider == "custom":
        llm_provider = _custom_provider_id(data.get("custom_active_profile_id") or "default")
    polish_provider = _canonical_provider_id(data.get("polish_provider") or llm_provider)
    if polish_provider == "custom":
        polish_provider = llm_provider if llm_provider.startswith("custom-") else _custom_provider_id(data.get("custom_active_profile_id") or "default")

    for key, (fallback_provider, model_field, capability) in _RUNTIME_BINDING_SPECS.items():
        provider_id = fallback_provider
        if capability == "llm":
            provider_id = polish_provider if key in {"polish", "subtitle_polish", "subtitle_refine"} else llm_provider
        if key == "asr":
            provider_id = _canonical_provider_id(data.get("asr_provider") or fallback_provider)

        model_id = _str_value(data.get(model_field)).strip() if model_field else ""
        if provider_id == "qwen3":
            model_id = _str_value(data.get("qwen3_asr_model_path") or "Qwen/Qwen3-ASR").strip()
        elif provider_id == "siliconflow" and key == "asr":
            model_id = _str_value(data.get("siliconflow_asr_model")).strip()
        elif provider_id.startswith("custom-") and capability == "llm":
            profile_id = provider_id.removeprefix("custom-")
            profile = next(
                (item for item in _coerce_custom_profiles(data.get("custom_llm_profiles")) if _normalize_provider_id(item["id"]) == profile_id),
                None,
            )
            if profile:
                model_id = profile.get("model", "")
        bindings[key] = _binding_record(provider_id, model_id, capability)

    purpose_aliases = {
        "purpose_subtitle_polish_model": "subtitle_polish",
        "purpose_subtitle_refine_model": "subtitle_refine",
        "purpose_analyze_model": "analyze",
        "purpose_summary_model": "summary",
        "purpose_mindmap_model": "mindmap",
        "purpose_asr_model": "asr",
        "purpose_vision_model": "vision",
        "purpose_embedding_model": "embedding",
    }
    for flat_key, binding_key in purpose_aliases.items():
        provider_id, model_id = _parse_binding_value(data.get(flat_key))
        if provider_id or model_id:
            capability = bindings.get(binding_key, {}).get("capability", "llm")
            bindings[binding_key] = _binding_record(provider_id, model_id, capability)
    return bindings


def _normalize_runtime_model_bindings(data: dict[str, Any]) -> None:
    current = data.get("runtime_model_bindings")
    normalized = _default_runtime_model_bindings(data)
    if isinstance(current, dict):
        for key, item in current.items():
            if isinstance(item, BaseModel):
                value = item.model_dump()
            elif isinstance(item, dict):
                value = item
            else:
                continue
            spec = _RUNTIME_BINDING_SPECS.get(key)
            capability = _str_value(value.get("capability") or (spec[2] if spec else "llm"))
            normalized[key] = _binding_record(
                value.get("provider_id"),
                value.get("model_id"),
                capability,
            )
    data["runtime_model_bindings"] = normalized


def _sync_flat_from_runtime_model_bindings(data: dict[str, Any]) -> None:
    bindings = data.get("runtime_model_bindings")
    if not isinstance(bindings, dict):
        return

    def binding(key: str) -> dict[str, Any]:
        item = bindings.get(key)
        return item if isinstance(item, dict) else {}

    stage_fields = {
        "analyze": "deepseek_analyze_model",
        "polish": "deepseek_polish_model",
        "summary": "deepseek_summary_model",
        "mindmap": "deepseek_mindmap_model",
    }
    for key, field in stage_fields.items():
        item = binding(key)
        if item.get("provider_id") == "deepseek" and item.get("model_id"):
            data[field] = item["model_id"]

    polish = binding("polish") or binding("subtitle_polish")
    polish_provider = _canonical_provider_id(polish.get("provider_id"))
    if polish_provider:
        data["polish_provider"] = "custom" if polish_provider.startswith("custom-") else polish_provider

    summary = binding("summary")
    llm_provider = _canonical_provider_id(summary.get("provider_id")) or _canonical_provider_id(data.get("llm_provider"))
    if llm_provider and llm_provider not in {"qwen3", "siliconflow"}:
        data["llm_provider"] = "custom" if llm_provider.startswith("custom-") else llm_provider
        if llm_provider.startswith("custom-"):
            data["custom_active_profile_id"] = llm_provider.removeprefix("custom-")

    asr = binding("asr")
    if asr.get("provider_id") in {"qwen3", "siliconflow"}:
        data["asr_provider"] = asr["provider_id"]
        if asr["provider_id"] == "siliconflow" and asr.get("model_id"):
            data["siliconflow_asr_model"] = asr["model_id"]

    vision = binding("vision")
    provider = _find_provider(data, vision.get("provider_id"))
    model = _find_provider_model(provider, vision.get("model_id"), "vision")
    if provider and model:
        data["vlm_api_base"] = provider.get("api_base", "")
        data["vlm_api_key"] = provider.get("api_key", "")
        data["vlm_model"] = model.get("model_id", "")

    embedding = binding("embedding")
    provider = _find_provider(data, embedding.get("provider_id"))
    model = _find_provider_model(provider, embedding.get("model_id"), "embedding")
    if provider and model:
        data["kb_embedding_api_base"] = provider.get("api_base", "")
        data["kb_embedding_api_key"] = provider.get("api_key", "")
        data["kb_embedding_model"] = model.get("model_id", "")


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
        {"dim": data.get("kb_embedding_dim", 1024)}
        if field == "kb_embedding_model"
        else None
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
