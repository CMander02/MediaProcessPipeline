"""Runtime settings management routes."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/settings", tags=["settings"])


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
    enable_diarization: bool = True
    hf_token: str = ""
    pyannote_model_path: str = ""
    pyannote_segmentation_path: str = ""
    alignment_model_zh: str = ""
    alignment_model_en: str = ""

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

    # Paths
    inbox_path: str = "./data/inbox"
    processing_path: str = "./data/processing"
    outputs_path: str = "./data/outputs"
    archive_path: str = "./data/archive"
    obsidian_vault_path: str = ""


# Global runtime settings storage
_runtime_settings: RuntimeSettings | None = None


def get_runtime_settings() -> RuntimeSettings:
    """Get current runtime settings."""
    global _runtime_settings
    if _runtime_settings is None:
        _runtime_settings = RuntimeSettings()
    return _runtime_settings


@router.get("", response_model=RuntimeSettings)
async def get_settings():
    """Get current runtime settings."""
    return get_runtime_settings()


@router.put("", response_model=RuntimeSettings)
async def update_settings(new_settings: RuntimeSettings):
    """Update runtime settings."""
    global _runtime_settings
    _runtime_settings = new_settings
    return _runtime_settings


@router.patch("")
async def patch_settings(updates: dict[str, Any]):
    """Partially update runtime settings."""
    global _runtime_settings
    if _runtime_settings is None:
        _runtime_settings = RuntimeSettings()

    # Update only provided fields
    current = _runtime_settings.model_dump()
    current.update(updates)
    _runtime_settings = RuntimeSettings(**current)
    return _runtime_settings
