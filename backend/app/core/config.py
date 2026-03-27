from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProviderConfig(BaseModel):
    """单个 LLM Provider 配置"""
    name: str  # 显示名称
    api_base: str = ""  # API Base URL (留空使用官方默认)
    api_key: str = ""
    model: str = ""  # 默认模型
    enabled: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # API Settings
    api_title: str = "Media Process Pipeline"
    api_version: str = "0.1.0"
    debug: bool = False

    # CORS
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Data paths - simplified flat structure
    data_root: Path = Path("D:/Video/MediaProcessPipeline")  # All task outputs go here

    # =========================================================================
    # LLM Configuration (使用 LiteLLM 统一处理)
    # =========================================================================
    # 当前使用的 provider (对应 llm_providers 的 key)
    llm_provider: str = "anthropic"

    # 内置 provider 配置 (可通过环境变量覆盖)
    anthropic_api_key: str = ""
    anthropic_api_base: str = ""  # 留空使用官方
    anthropic_model: str = "claude-sonnet-4-20250514"

    openai_api_key: str = ""
    openai_api_base: str = ""  # 留空使用官方
    openai_model: str = "gpt-4o"

    # 自定义 OpenAI Compatible provider
    custom_api_key: str = ""
    custom_api_base: str = ""  # 例如: http://localhost:11434/v1
    custom_model: str = ""  # 例如: llama3, qwen2
    custom_name: str = "Custom"  # 显示名称

    # LiteLLM 通用设置
    temperature: float = 0.1

    # =========================================================================
    # WhisperX settings
    # =========================================================================
    hf_token: str = ""
    whisper_model: str = "large-v3-turbo"
    whisper_model_path: str = ""  # 本地 Whisper 模型路径
    faster_whisper_model_path: str = ""  # 本地 faster-whisper 模型路径
    compute_type: str = "float16"
    device: str = "cuda"

    # Pyannote/Diarization settings
    pyannote_model_path: str = ""
    pyannote_segmentation_path: str = ""

    # wav2vec2 alignment model paths (per language)
    alignment_model_zh: str = ""
    alignment_model_en: str = ""

    # =========================================================================
    # UVR settings
    # =========================================================================
    uvr_model: str = "UVR-MDX-NET-Inst_HQ_3"
    uvr_model_dir: str = ""
    uvr_mdx_inst_hq3_path: str = ""
    uvr_hp_uvr_path: str = ""
    uvr_denoise_lite_path: str = ""
    uvr_kim_vocal_2_path: str = ""
    uvr_deecho_dereverb_path: str = ""
    uvr_htdemucs_path: str = ""

    # =========================================================================
    # Other settings
    # =========================================================================
    bilibili_sessdata: str = ""

    def get_llm_config(self, provider: str | None = None) -> dict:
        """获取指定 provider 的 LLM 配置（OpenAI-compatible 格式）。"""
        provider = provider or self.llm_provider

        if provider == "anthropic":
            config = {
                "model": self.anthropic_model,
                "api_key": self.anthropic_api_key,
                "base_url": self.anthropic_api_base or "https://api.anthropic.com/v1",
            }
        elif provider == "openai":
            config = {
                "model": self.openai_model,
                "api_key": self.openai_api_key,
            }
            if self.openai_api_base:
                config["base_url"] = self.openai_api_base
        elif provider == "custom":
            config = {
                "model": self.custom_model,
                "api_key": self.custom_api_key or "not-needed",
                "base_url": self.custom_api_base,
            }
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

        config["temperature"] = self.temperature
        return config


@lru_cache
def get_settings() -> Settings:
    return Settings()
