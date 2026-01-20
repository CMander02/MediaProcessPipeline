"""Runtime settings management routes."""

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/settings", tags=["settings"])
logger = logging.getLogger(__name__)

# Settings file path - stored in project root data directory
# __file__ = backend/app/api/routes/settings.py
# parent x5 = project root
SETTINGS_FILE = Path(__file__).parent.parent.parent.parent.parent / "data" / "settings.json"


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

    # WhisperX
    whisper_model: str = "large-v3-turbo"
    whisper_model_path: str = ""
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"
    whisper_batch_size: int = 16  # Reduce for long audio or low VRAM
    enable_diarization: bool = True
    hf_token: str = ""
    pyannote_model_path: str = ""
    pyannote_segmentation_path: str = ""
    alignment_model_zh: str = ""
    alignment_model_en: str = ""
    diarization_batch_size: int = 16  # Reduce for long audio or low VRAM

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

    # Paths - simplified flat structure
    data_root: str = "../data"  # All task outputs go to project root data/{task_id}/
    obsidian_vault_path: str = ""


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
            encoding="utf-8"
        )
        logger.info(f"Saved settings to {SETTINGS_FILE}")
    except Exception as e:
        logger.warning(f"Failed to save settings file: {e}")


def get_runtime_settings() -> RuntimeSettings:
    """Get current runtime settings."""
    global _runtime_settings
    if _runtime_settings is None:
        _runtime_settings = _load_settings_from_file()
    return _runtime_settings


@router.get("", response_model=RuntimeSettings)
async def get_settings():
    """Get current runtime settings."""
    return get_runtime_settings()


@router.put("", response_model=RuntimeSettings)
async def update_settings(new_settings: RuntimeSettings):
    """Update runtime settings and persist to file."""
    global _runtime_settings
    _runtime_settings = new_settings
    _save_settings_to_file(_runtime_settings)
    return _runtime_settings


@router.patch("")
async def patch_settings(updates: dict[str, Any]):
    """Partially update runtime settings and persist to file."""
    global _runtime_settings
    if _runtime_settings is None:
        _runtime_settings = _load_settings_from_file()

    # Update only provided fields
    current = _runtime_settings.model_dump()
    current.update(updates)
    _runtime_settings = RuntimeSettings(**current)
    _save_settings_to_file(_runtime_settings)
    return _runtime_settings
