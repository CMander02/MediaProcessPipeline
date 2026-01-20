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

            # Determine base model directory
            if model_dir:
                base_model_dir = Path(model_dir)
            else:
                base_model_dir = find_local_uvr_model_dir()

            logger.info(f"Loading UVR model: {model_name}")

            # Determine which subdirectory contains the model
            # audio-separator expects model_file_dir to be the directory containing the model file
            # AND the mdx_model_data.json / vr_model_data.json config files
            model_file = None
            model_file_dir = None

            if base_model_dir:
                # Search for model file in common subdirectories
                search_dirs = [
                    ("MDX_Net_Models", [".onnx"]),
                    ("VR_Models", [".pth"]),
                    ("Demucs_Models", [".yaml", ".th", ""]),
                ]

                for subdir_name, extensions in search_dirs:
                    subdir = base_model_dir / subdir_name
                    if not subdir.exists():
                        continue
                    for ext in extensions:
                        candidate = subdir / f"{model_name}{ext}"
                        if candidate.exists():
                            model_file = candidate
                            model_file_dir = subdir
                            break
                    if model_file:
                        break

                # Also check base directory
                if not model_file:
                    for ext in [".onnx", ".pth", ""]:
                        candidate = base_model_dir / f"{model_name}{ext}"
                        if candidate.exists():
                            model_file = candidate
                            model_file_dir = base_model_dir
                            break

            if model_file and model_file_dir:
                logger.info(f"Found model file: {model_file}")
                logger.info(f"Using model_file_dir: {model_file_dir}")
                # Create separator with model_file_dir set to the directory containing the model
                # This allows audio-separator to find the mdx_model_data.json config file
                self._separator = Separator(
                    output_format="wav",
                    model_file_dir=str(model_file_dir),
                )
                # Load using filename (not full path) since we set model_file_dir
                self._separator.load_model(model_file.name)
            elif base_model_dir:
                # Fallback: let audio-separator try to find/download it
                logger.info(f"Model not found locally, using base dir: {base_model_dir}")
                self._separator = Separator(
                    output_format="wav",
                    model_file_dir=str(base_model_dir / "MDX_Net_Models"),
                )
                self._separator.load_model(model_name)
            else:
                # Use default download directory
                logger.info("No model directory configured, using default")
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

        if output_dir is None:
            rt = get_runtime_settings()
            output_dir = Path(rt.data_root).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        if self._separator is None:
            logger.warning("Mock mode - no separation performed")
            return {
                "input_path": audio_path,
                "vocals_path": audio_path,  # Return original in mock mode
                "model_used": "mock",
            }

        # Set output directory for this separation (must be absolute path)
        output_dir_abs = output_dir.resolve()
        self._separator.output_dir = str(output_dir_abs)

        logger.info(f"Separating vocals: {audio_path} -> {output_dir_abs}")
        output_files = self._separator.separate(str(audio_file))

        vocals_path = None
        for f in output_files:
            file_path = Path(f)
            stem_lower = file_path.stem.lower()
            if "vocals" in stem_lower:
                vocals_path = f
            elif "instrumental" in stem_lower:
                # Delete instrumental/background music file - not needed
                try:
                    file_path.unlink()
                    logger.info(f"Deleted instrumental file: {f}")
                except Exception as e:
                    logger.warning(f"Failed to delete instrumental file {f}: {e}")

        rt = get_runtime_settings()
        return {
            "input_path": audio_path,
            "vocals_path": vocals_path,
            "output_dir": str(output_dir),
            "model_used": rt.uvr_model,
        }


_service: UVRService | None = None


def get_uvr_service() -> UVRService:
    global _service
    if _service is None:
        _service = UVRService()
    return _service


async def separate_vocals(audio_path: str, output_dir: Path | None = None) -> dict[str, Any]:
    return get_uvr_service().separate(audio_path, output_dir=output_dir)
