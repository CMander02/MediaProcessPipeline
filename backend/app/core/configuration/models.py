"""Validated runtime configuration fields."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    cli_path: str = ""
    timeout_sec: int = 600
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

    # Audio processing flow
    #   asr  — selected ASR provider, optionally followed by pyannote
    #   moss — MOSS-Transcribe-Diarize produces text, timestamps and speakers
    audio_processing_flow: str = "asr"

    # ASR
    asr_provider: str = "sherpa_onnx"

    # Unified local ASR through sherpa-onnx
    sherpa_model_id: str = "sensevoice-small-int8"
    sherpa_model_root: str = ""
    sherpa_device: str = "auto"  # auto | cuda | cpu
    sherpa_num_threads: int = 4
    sherpa_chunk_strategy: str = "vad"  # vad | fixed
    sherpa_max_chunk_sec: float = 30.0
    sherpa_vad_model_path: str = ""
    sherpa_debug: bool = False
    asr_timestamp_mode: str = "auto"  # auto | native | vad | qwen_forced
    # Optional Safetensors model used as a postprocessor for any sherpa transcript.
    qwen3_aligner_model_path: str = ""

    # Shared llama.cpp binary remains available to local LLM and VLM services.
    llama_cpp_binary_path: str = ""

    # MOSS-Transcribe-Diarize through moss-transcribe.cpp
    moss_cpp_binary_path: str = ""
    moss_cpp_model_path: str = ""
    moss_cpp_device: str = "auto"  # auto | cuda | cpu
    moss_cpp_threads: int = 8
    moss_cpp_max_new_tokens: int = 8192
    # Long recordings are split before MOSS inference so decoder KV cache stays bounded.
    moss_cpp_chunk_duration_sec: float = 1200.0
    moss_cpp_chunk_overlap_sec: float = 60.0
    moss_cpp_timeout_sec: float = 14400.0

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
    enable_voiceprint: bool = False
    voiceprint_match_threshold: float = 0.75  # >= → auto-merge into existing person
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

    # Local LLM / VLM (Transformers directory or llama.cpp GGUF)
    local_llm_engine: str = "transformers"  # "transformers" | "llama_cpp"
    local_llm_name: str = "Local LLM"
    local_llm_model_path: str = ""  # HF model directory or GGUF file
    local_llm_mmproj_path: str = ""  # llama.cpp multimodal projector
    local_llm_device: str = "cuda"  # "cuda" | "cpu" | "auto"
    local_llm_dtype: str = "bfloat16"  # "bfloat16" | "float16" | "float32" | "auto"
    local_llm_max_new_tokens: int = 4096  # Cap per generate() call
    # Kept for backward compat with older settings.json; unused by the transformers backend
    local_llm_n_gpu_layers: int = -1
    local_llm_n_ctx: int = 16384
    local_llm_n_batch: int = 512
    local_llm_timeout_sec: float = 300.0
    local_llm_keepalive_sec: float = 600.0
    local_llm_concurrency: int = 2
    # "" = follow llm_provider, or local/anthropic/openai/custom
    polish_provider: str = "local"
    llm_polish_concurrency: int = 4

    # Concurrency
    max_download_concurrency: int = 2  # max parallel downloads (I/O bound, set 1-4)
    # When False, GPU steps only start after all active downloads finish (serial mode).
    # Reduces peak VRAM by preventing download+GPU overlap.
    # Recommended False for machines with ≤16 GB VRAM.
    pipeline_overlap: bool = True
    # Shared outbound HTTP proxy for platform APIs and asset downloads.
    # Empty = use environment/system proxy. "direct"/"none" disables proxy use.
    network_proxy: str = ""

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
    # Upgrade yt-dlp before the daemon finishes startup when PyPI has a newer build.
    ytdlp_auto_update: bool = False

    # Bilibili
    bilibili_sessdata: str = ""
    bilibili_bili_jct: str = ""
    bilibili_dede_user_id: str = ""
    bilibili_preferred_quality: int = 64  # qn: 16=360P 32=480P 64=720P 80=1080P
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
    # Playwright storage_state JSON saved from an interactive browser login.
    # Empty = data/auth/xiaohongshu_storage_state.json under data_root.
    xiaohongshu_storage_state_path: str = ""

    # X Articles expose their complete body only to a logged-in browser session.
    # Empty = data/auth/twitter_storage_state.json under data_root.
    twitter_storage_state_path: str = ""

    # Zhihu
    # Headless Chromium can be blocked on answer pages; the fallback uses a real
    # browser window. "background" starts it minimized, "foreground" leaves it visible.
    zhihu_browser_mode: str = "background"

    # Generic web page scraping. The pipeline tries Defuddle CLI first and uses
    # Jina Reader when local extraction fails.
    defuddle_enabled: bool = True
    playwright_enabled: bool = True
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
    vlm_concurrency: int = 1
    vlm_timeout_sec: float = 180.0

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
    allow_remote_filesystem: bool = False

    # Remote archive synchronization. A desktop daemon uploads each completed
    # local archive once; the remote daemon accepts it into its own data_root.
    remote_sync_enabled: bool = False
    remote_server_url: str = ""
    remote_api_token: str = ""
    remote_worker_id: str = ""
    remote_worker_name: str = ""
    remote_sync_interval_sec: float = 15.0
    remote_sync_upload_results: bool = True
    remote_sync_download_results: bool = True
    remote_sync_include_media: bool = False

    # Paths
    data_root: str = "D:/Video/MediaProcessPipeline"
    media_retention_policy: Literal["all", "playback", "text"] = "all"

    @field_validator("remote_sync_interval_sec")
    @classmethod
    def _validate_remote_sync_interval(cls, value: float) -> float:
        if value < 5:
            raise ValueError("remote_sync_interval_sec must be at least 5 seconds")
        return value

    @field_validator("asr_provider")
    @classmethod
    def _validate_asr_provider(cls, value: str) -> str:
        provider = value.strip().lower()
        if provider in {"qwen3", "qwen3_gguf"}:
            return "sherpa_onnx"
        if provider not in {"sherpa_onnx", "siliconflow"}:
            raise ValueError("asr_provider must be one of: sherpa_onnx, siliconflow")
        return provider

    @field_validator("audio_processing_flow")
    @classmethod
    def _validate_audio_processing_flow(cls, value: str) -> str:
        flow = value.strip().lower()
        if flow not in {"asr", "moss"}:
            raise ValueError("audio_processing_flow must be one of: asr, moss")
        return flow

    @field_validator("moss_cpp_device")
    @classmethod
    def _validate_moss_cpp_device(cls, value: str) -> str:
        device = value.strip().lower()
        if device not in {"auto", "cuda", "cpu"}:
            raise ValueError("moss_cpp_device must be one of: auto, cuda, cpu")
        return device

    @field_validator("moss_cpp_max_new_tokens")
    @classmethod
    def _validate_moss_cpp_max_new_tokens(cls, value: int) -> int:
        if value < 256:
            raise ValueError("moss_cpp_max_new_tokens must be at least 256")
        return value

    @field_validator("moss_cpp_chunk_duration_sec")
    @classmethod
    def _validate_moss_cpp_chunk_duration(cls, value: float) -> float:
        if value < 60:
            raise ValueError("moss_cpp_chunk_duration_sec must be at least 60 seconds")
        return value

    @field_validator("moss_cpp_chunk_overlap_sec")
    @classmethod
    def _validate_moss_cpp_chunk_overlap(cls, value: float) -> float:
        if value < 0:
            raise ValueError("moss_cpp_chunk_overlap_sec must be non-negative")
        return value

    @field_validator("sherpa_device")
    @classmethod
    def _validate_sherpa_device(cls, value: str) -> str:
        device = value.strip().lower()
        if device not in {"auto", "cuda", "cpu"}:
            raise ValueError("sherpa_device must be one of: auto, cuda, cpu")
        return device

    @field_validator("uvr_device")
    @classmethod
    def _validate_uvr_device(cls, value: str) -> str:
        device = value.strip().lower()
        if device not in {"cuda", "cpu"}:
            raise ValueError("uvr_device must be one of: cuda, cpu")
        return device

    @field_validator("sherpa_chunk_strategy")
    @classmethod
    def _validate_sherpa_chunk_strategy(cls, value: str) -> str:
        strategy = value.strip().lower()
        aliases = {"ffmpeg": "fixed", "silero_onnx": "vad", "silero_torch": "vad"}
        strategy = aliases.get(strategy, strategy)
        if strategy not in {"vad", "fixed"}:
            raise ValueError("sherpa_chunk_strategy must be one of: vad, fixed")
        return strategy

    @field_validator("sherpa_num_threads")
    @classmethod
    def _validate_sherpa_num_threads(cls, value: int) -> int:
        if value < 1:
            raise ValueError("sherpa_num_threads must be at least 1")
        return value

    @field_validator("sherpa_max_chunk_sec")
    @classmethod
    def _validate_sherpa_max_chunk_sec(cls, value: float) -> float:
        if value < 1:
            raise ValueError("sherpa_max_chunk_sec must be at least 1 second")
        return value

    @field_validator("asr_timestamp_mode")
    @classmethod
    def _validate_asr_timestamp_mode(cls, value: str) -> str:
        mode = value.strip().lower()
        if mode not in {"auto", "native", "vad", "qwen_forced"}:
            raise ValueError("asr_timestamp_mode must be one of: auto, native, vad, qwen_forced")
        return mode

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
