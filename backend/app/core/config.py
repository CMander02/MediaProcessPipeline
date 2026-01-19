from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Data paths
    data_inbox: Path = Path("../data/inbox")
    data_processing: Path = Path("../data/processing")
    data_outputs: Path = Path("../data/outputs")
    data_archive: Path = Path("../data/archive")

    # AI Provider (for analysis)
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    default_model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096

    # WhisperX settings
    hf_token: str = ""
    whisper_model: str = "large-v3-turbo"
    whisper_model_path: str = ""  # 本地 Whisper 模型路径（用于 whisperx）
    faster_whisper_model_path: str = ""  # 本地 faster-whisper 模型路径
    compute_type: str = "float16"
    device: str = "cuda"

    # Pyannote/Diarization settings
    pyannote_model_path: str = ""  # 本地 pyannote 模型路径
    pyannote_segmentation_path: str = ""  # 本地 segmentation 模型路径

    # wav2vec2 alignment model paths (per language)
    alignment_model_zh: str = ""  # 中文对齐模型路径
    alignment_model_en: str = ""  # 英文对齐模型路径

    # UVR settings
    uvr_model: str = "UVR-MDX-NET-Inst_HQ_3"  # 默认使用本地已有模型
    uvr_model_dir: str = ""  # UVR 模型目录路径
    # 具体 UVR 模型文件路径（可选，优先于 uvr_model_dir）
    uvr_mdx_inst_hq3_path: str = ""  # UVR-MDX-NET-Inst_HQ_3.onnx
    uvr_hp_uvr_path: str = ""  # 1_HP-UVR.pth
    uvr_denoise_lite_path: str = ""  # UVR-DeNoise-Lite.pth
    uvr_kim_vocal_2_path: str = ""  # Kim_Vocal_2.onnx
    uvr_deecho_dereverb_path: str = ""  # UVR-DeEcho-DeReverb.pth
    uvr_htdemucs_path: str = ""  # htdemucs 模型路径

    # Bilibili settings
    bilibili_sessdata: str = ""  # Optional: for higher quality downloads (login cookie)

    # Obsidian (optional)
    obsidian_vault_path: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
