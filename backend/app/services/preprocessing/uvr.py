"""UVR5 vocal separation service."""

import logging
import os
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.api.routes.settings import get_runtime_settings

logger = logging.getLogger(__name__)

# 默认本地 UVR 安装路径
DEFAULT_UVR_PATHS = [
    # Windows
    Path(os.path.expanduser("~")) / "AppData/Local/Programs/Ultimate Vocal Remover/models",
    # Linux (if installed via pip or custom)
    Path(os.path.expanduser("~")) / ".cache/audio-separator-models",
    # Fallback
    Path("/tmp/audio-separator-models"),
]


def find_local_uvr_model_dir() -> Path | None:
    """查找本地 UVR 模型目录."""
    for path in DEFAULT_UVR_PATHS:
        if path.exists() and (path / "MDX_Net_Models").exists():
            logger.info(f"Found local UVR models at: {path}")
            return path
    return None


class UVRService:
    def __init__(self):
        self._separator = None
        self._current_model: str | None = None
        self._current_model_dir: str | None = None

    def _get_model_path(self, model_name: str) -> str | None:
        """Get specific model path from runtime settings."""
        rt = get_runtime_settings()
        model_paths = {
            "UVR-MDX-NET-Inst_HQ_3": rt.uvr_mdx_inst_hq3_path,
            "1_HP-UVR": rt.uvr_hp_uvr_path,
            "UVR-DeNoise-Lite": rt.uvr_denoise_lite_path,
            "Kim_Vocal_2": rt.uvr_kim_vocal_2_path,
            "UVR-DeEcho-DeReverb": rt.uvr_deecho_dereverb_path,
            "htdemucs": rt.uvr_htdemucs_path,
        }
        return model_paths.get(model_name, "")

    def _ensure_init(self):
        """Initialize or reinitialize separator with current settings."""
        rt = get_runtime_settings()
        model_name = rt.uvr_model
        model_dir = rt.uvr_model_dir

        # Check if we need to reinitialize (settings changed)
        if (
            self._separator is not None
            and self._current_model == model_name
            and self._current_model_dir == model_dir
        ):
            return

        try:
            from audio_separator.separator import Separator

            # Determine model directory
            if model_dir:
                base_model_dir = Path(model_dir)
            else:
                base_model_dir = find_local_uvr_model_dir()

            logger.info(f"Loading UVR model: {model_name}")

            # Check for specific model path first
            specific_path = self._get_model_path(model_name)
            if specific_path:
                # If specific path is set, use it
                full_path = Path(specific_path)
                if not full_path.is_absolute() and base_model_dir:
                    full_path = base_model_dir / specific_path
                if full_path.exists():
                    logger.info(f"Using specific model path: {full_path}")
                    self._separator = Separator(output_format="wav")
                    self._separator.load_model(str(full_path))
                    self._current_model = model_name
                    self._current_model_dir = model_dir
                    return

            # Try to find model in standard directory structure
            if base_model_dir:
                logger.info(f"Using UVR model directory: {base_model_dir}")

                # Search for model file in common subdirectories
                search_dirs = [
                    base_model_dir / "MDX_Net_Models",
                    base_model_dir / "VR_Models",
                    base_model_dir / "Demucs_Models",
                    base_model_dir,
                ]

                model_file = None
                for search_dir in search_dirs:
                    if not search_dir.exists():
                        continue
                    # Try exact name with extensions
                    for ext in [".onnx", ".pth", ""]:
                        candidate = search_dir / f"{model_name}{ext}"
                        if candidate.exists():
                            model_file = candidate
                            break
                    if model_file:
                        break

                if model_file:
                    logger.info(f"Found model file: {model_file}")
                    self._separator = Separator(output_format="wav")
                    self._separator.load_model(str(model_file))
                else:
                    # Let audio-separator try to find/download it
                    self._separator = Separator(
                        output_format="wav",
                        model_file_dir=str(base_model_dir / "MDX_Net_Models"),
                    )
                    self._separator.load_model(model_name)
            else:
                # Use default download directory
                self._separator = Separator(output_format="wav")
                self._separator.load_model(model_name)

            self._current_model = model_name
            self._current_model_dir = model_dir

        except ImportError:
            logger.warning("audio-separator not installed - mock mode")

    def separate(self, audio_path: str, output_dir: Path | None = None) -> dict[str, Any]:
        self._ensure_init()

        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise FileNotFoundError(f"File not found: {audio_path}")

        settings = get_settings()
        output_dir = output_dir or settings.data_processing.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        if self._separator is None:
            logger.warning("Mock mode - no separation performed")
            return {
                "input_path": audio_path,
                "vocals_path": audio_path,  # Return original in mock mode
                "model_used": "mock",
            }

        logger.info(f"Separating vocals: {audio_path}")
        output_files = self._separator.separate(str(audio_file))

        vocals_path = None
        for f in output_files:
            if "vocals" in Path(f).stem.lower():
                vocals_path = f
                break

        rt = get_runtime_settings()
        return {
            "input_path": audio_path,
            "vocals_path": vocals_path,
            "model_used": rt.uvr_model,
        }


_service: UVRService | None = None


def get_uvr_service() -> UVRService:
    global _service
    if _service is None:
        _service = UVRService()
    return _service


async def separate_vocals(audio_path: str) -> dict[str, Any]:
    return get_uvr_service().separate(audio_path)
