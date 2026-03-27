"""Runtime settings management - core module.

Provides RuntimeSettings model and get_runtime_settings() singleton.
This module is imported by all services; the API route layer is a thin wrapper.
"""

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Settings file path - stored in project root data directory
# __file__ = backend/app/core/settings.py
# parent x4 = project root
SETTINGS_FILE = Path(__file__).parent.parent.parent.parent / "data" / "settings.json"


class RuntimeSettings(BaseModel):
    """Settings that can be updated at runtime from frontend."""

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

    # ASR Backend Selection
    asr_backend: str = "qwen3"  # "qwen3" | "whisperx"

    # Qwen3-ASR Settings
    qwen3_asr_model_path: str = ""  # Local path, empty = use HuggingFace
    qwen3_aligner_model_path: str = ""  # ForcedAligner path for timestamps
    qwen3_enable_timestamps: bool = True
    qwen3_batch_size: int = 32
    qwen3_max_new_tokens: int = 4096
    qwen3_device: str = "cuda"

    # WhisperX (backup)
    whisper_model: str = "large-v3-turbo"
    whisper_model_path: str = ""
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"
    whisper_batch_size: int = 16
    enable_alignment: bool = True

    # Speaker Diarization (shared by both backends)
    enable_diarization: bool = True
    hf_token: str = ""
    pyannote_model_path: str = ""
    pyannote_segmentation_path: str = ""
    alignment_model_zh: str = ""
    alignment_model_en: str = ""
    diarization_batch_size: int = 16

    # Platform Subtitles
    prefer_platform_subtitles: bool = True  # Use platform subtitles when available
    subtitle_languages: str = "zh,en"  # Comma-separated language priority

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

    # Concurrency
    max_download_concurrency: int = 2  # max parallel downloads (I/O bound, set 1-4)

    # Bilibili
    bilibili_sessdata: str = ""
    bilibili_bili_jct: str = ""
    bilibili_dede_user_id: str = ""

    # Paths
    data_root: str = "D:/Video/MediaProcessPipeline"


# Global runtime settings storage
_runtime_settings: RuntimeSettings | None = None


def _load_settings_from_file() -> RuntimeSettings:
    """Load settings from JSON file."""
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            logger.info(f"Loaded settings from {SETTINGS_FILE}")
            return RuntimeSettings(**data)
        except Exception as e:
            logger.warning(f"Failed to load settings file: {e}")
    return RuntimeSettings()


def _save_settings_to_file(settings: RuntimeSettings) -> None:
    """Save settings to JSON file."""
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(
            json.dumps(settings.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"Saved settings to {SETTINGS_FILE}")
    except Exception as e:
        logger.warning(f"Failed to save settings file: {e}")


def get_runtime_settings() -> RuntimeSettings:
    """Get current runtime settings (singleton)."""
    global _runtime_settings
    if _runtime_settings is None:
        _runtime_settings = _load_settings_from_file()
    return _runtime_settings


def update_runtime_settings(new_settings: RuntimeSettings) -> RuntimeSettings:
    """Replace all runtime settings and persist."""
    global _runtime_settings
    _runtime_settings = new_settings
    _save_settings_to_file(_runtime_settings)
    return _runtime_settings


def patch_runtime_settings(updates: dict[str, Any]) -> RuntimeSettings:
    """Partially update runtime settings and persist."""
    global _runtime_settings
    if _runtime_settings is None:
        _runtime_settings = _load_settings_from_file()
    current = _runtime_settings.model_dump()
    current.update(updates)
    _runtime_settings = RuntimeSettings(**current)
    _save_settings_to_file(_runtime_settings)
    return _runtime_settings
