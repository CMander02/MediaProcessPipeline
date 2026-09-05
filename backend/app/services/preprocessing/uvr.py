"""UVR5 vocal separation service."""

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable

from app.core.paths import get_workspace_paths

from app.core.settings import RuntimeSettings, get_runtime_settings

logger = logging.getLogger(__name__)

UVRProgressCallback = Callable[[dict[str, Any]], None]
_CHUNK_START_RE = re.compile(r"Processing chunk\s+(\d+)/(\d+):")
_CHUNK_SPLIT_RE = re.compile(r"Splitting .+ audio into\s+(\d+)\s+chunks")
_CHUNK_MERGE_RE = re.compile(r"Merging\s+(\d+)\s+chunks for stem:")

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


def _available_onnx_providers() -> list[str]:
    try:
        import onnxruntime as ort
    except ImportError:
        return []
    return list(ort.get_available_providers())


def _notify_progress(callback: UVRProgressCallback | None, payload: dict[str, Any]) -> None:
    if callback is None:
        return
    try:
        callback(payload)
    except Exception:
        logger.debug("UVR progress callback failed", exc_info=True)


class _UVRProgressLogHandler(logging.Handler):
    """Translate audio-separator chunk logs into structured progress callbacks."""

    def __init__(self, callback: UVRProgressCallback):
        super().__init__(level=logging.INFO)
        self._callback = callback
        self._total_chunks = 0
        self._thread_id = threading.get_ident()

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self._thread_id:
            return
        message = record.getMessage()

        split_match = _CHUNK_SPLIT_RE.search(message)
        if split_match:
            self._total_chunks = int(split_match.group(1))
            _notify_progress(self._callback, {
                "phase": "splitting",
                "current_chunk": 0,
                "completed_chunks": 0,
                "total_chunks": self._total_chunks,
                "progress": 0.0,
                "message": f"准备分离人声：共 {self._total_chunks} 段",
            })
            return

        chunk_match = _CHUNK_START_RE.search(message)
        if chunk_match:
            current = int(chunk_match.group(1))
            total = int(chunk_match.group(2))
            self._total_chunks = total
            completed = max(0, current - 1)
            _notify_progress(self._callback, {
                "phase": "separating",
                "current_chunk": current,
                "completed_chunks": completed,
                "total_chunks": total,
                "progress": completed / total if total else 0.0,
                "message": f"分离人声：第 {current}/{total} 段",
            })
            return

        merge_match = _CHUNK_MERGE_RE.search(message)
        if merge_match:
            total = self._total_chunks or int(merge_match.group(1))
            _notify_progress(self._callback, {
                "phase": "merging",
                "current_chunk": total,
                "completed_chunks": total,
                "total_chunks": total,
                "progress": 0.99,
                "message": "合并人声分离结果",
            })


class UVRService:
    def __init__(self):
        self._separator = None
        self._current_model: str | None = None
        self._current_model_dir: str | None = None
        self._current_chunk_duration: float | None = None
        self._current_device: str | None = None
        self._execution_provider: str | None = None

    def release(self) -> None:
        """Release the loaded separator so downstream GPU steps can use VRAM."""
        self._separator = None
        self._current_model = None
        self._current_model_dir = None
        self._current_chunk_duration = None
        self._current_device = None
        self._execution_provider = None

    def _configure_separator_device(
        self,
        separator: Any,
        device: str,
        *,
        onnx_model: bool,
    ) -> str:
        import torch

        if device == "cpu":
            separator.torch_device_cpu = torch.device("cpu")
            separator.torch_device = separator.torch_device_cpu
            separator.onnx_execution_provider = ["CPUExecutionProvider"]
            logger.info("UVR device configured: cpu provider=CPUExecutionProvider")
            return "CPUExecutionProvider"

        if not torch.cuda.is_available():
            raise RuntimeError(
                "UVR device is set to CUDA, but PyTorch cannot access CUDA. "
                "Check the CUDA-enabled PyTorch installation and NVIDIA driver."
            )

        providers = _available_onnx_providers()
        if onnx_model and "CUDAExecutionProvider" not in providers:
            raise RuntimeError(
                "UVR device is set to CUDA, but ONNX Runtime has no CUDAExecutionProvider. "
                f"Available providers: {providers or ['none']}. "
                "Install only onnxruntime-gpu for the local-models environment "
                "and restart the daemon."
            )

        separator.torch_device = torch.device("cuda")
        if onnx_model:
            separator.onnx_execution_provider = ["CUDAExecutionProvider"]
            provider = "CUDAExecutionProvider"
        else:
            provider = "CUDA"
        logger.info(f"UVR device configured: cuda provider={provider}")
        return provider

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

    def _ensure_init(self, rt: RuntimeSettings | None = None):
        """Initialize or reinitialize separator with current settings."""
        rt = rt or get_runtime_settings()
        model_name = rt.uvr_model
        model_dir = rt.uvr_model_dir
        device = rt.uvr_device
        chunk_duration = float(rt.uvr_chunk_duration_sec or 0)
        chunk_duration_arg = chunk_duration if chunk_duration > 0 else None

        # Check if we need to reinitialize (settings changed)
        if (
            self._separator is not None
            and self._current_model == model_name
            and self._current_model_dir == model_dir
            and self._current_chunk_duration == chunk_duration_arg
            and self._current_device == device
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
            if chunk_duration_arg:
                logger.info(f"UVR chunked processing enabled: {chunk_duration_arg:.0f}s chunks")
            else:
                logger.info("UVR chunked processing disabled")

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
                # Use output_single_stem="Vocals" to only output vocals (no instrumental)
                self._separator = Separator(
                    output_format="wav",
                    model_file_dir=str(model_file_dir),
                    output_single_stem="Vocals",
                    chunk_duration=chunk_duration_arg,
                )
                self._execution_provider = self._configure_separator_device(
                    self._separator,
                    device,
                    onnx_model=model_file.suffix.lower() == ".onnx",
                )
                # Load using filename (not full path) since we set model_file_dir
                self._separator.load_model(model_file.name)
            elif base_model_dir:
                # Fallback: let audio-separator try to find/download it
                logger.info(f"Model not found locally, using base dir: {base_model_dir}")
                self._separator = Separator(
                    output_format="wav",
                    model_file_dir=str(base_model_dir / "MDX_Net_Models"),
                    output_single_stem="Vocals",
                    chunk_duration=chunk_duration_arg,
                )
                self._execution_provider = self._configure_separator_device(
                    self._separator,
                    device,
                    onnx_model=model_name.startswith("UVR-MDX"),
                )
                self._separator.load_model(model_name)
            else:
                # Use default download directory
                logger.info("No model directory configured, using default")
                self._separator = Separator(
                    output_format="wav",
                    output_single_stem="Vocals",
                    chunk_duration=chunk_duration_arg,
                )
                self._execution_provider = self._configure_separator_device(
                    self._separator,
                    device,
                    onnx_model=model_name.startswith("UVR-MDX"),
                )
                self._separator.load_model(model_name)

            self._current_model = model_name
            self._current_model_dir = model_dir
            self._current_chunk_duration = chunk_duration_arg
            self._current_device = device

        except ImportError as exc:
            if exc.name and exc.name.startswith("audio_separator"):
                logger.warning("audio-separator not installed - mock mode")
                return
            raise

    def separate(
        self,
        audio_path: str,
        output_dir: Path | None = None,
        progress_callback: UVRProgressCallback | None = None,
    ) -> dict[str, Any]:
        if not audio_path:
            raise ValueError("UVR separation requires a non-empty audio path")

        # Read one validated settings snapshot for the whole separation request.
        # Runtime settings updates replace the singleton, so this avoids mixing
        # values from two settings revisions while a model is being initialized.
        rt = get_runtime_settings()
        self._ensure_init(rt)

        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise FileNotFoundError(f"File not found: {audio_path}")

        if output_dir is None:
            output_dir = get_workspace_paths(rt.data_root).temporary("uvr")
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
        _notify_progress(progress_callback, {
            "phase": "starting",
            "current_chunk": 0,
            "completed_chunks": 0,
            "total_chunks": 0,
            "progress": 0.0,
            "message": "准备分离人声",
        })

        progress_handler = None
        separator_logger = getattr(self._separator, "logger", None)
        if progress_callback is not None and isinstance(separator_logger, logging.Logger):
            progress_handler = _UVRProgressLogHandler(progress_callback)
            separator_logger.addHandler(progress_handler)
        try:
            output_files = self._separator.separate(str(audio_file))
        finally:
            if progress_handler is not None:
                separator_logger.removeHandler(progress_handler)

        if not output_files:
            raise RuntimeError(
                f"UVR separation did not produce any output for {audio_path}. "
                "Check the preceding UVR log entries for the underlying failure."
            )

        # With output_single_stem="Vocals", only vocals file is produced
        # Note: audio-separator may return just filename or relative path,
        # so we need to check both output_dir and current working directory
        vocals_path = None
        for f in output_files:
            file_path = Path(f)
            stem_lower = file_path.stem.lower()
            if "vocals" in stem_lower:
                # If path is not absolute, it might be in output_dir or cwd
                if not file_path.is_absolute():
                    # Check if file exists in output_dir
                    expected_path = output_dir_abs / file_path.name
                    if expected_path.exists():
                        vocals_path = str(expected_path)
                    else:
                        # File might be in cwd, move it to output_dir
                        cwd_path = Path.cwd() / file_path.name
                        if cwd_path.exists():
                            import shutil
                            dest_path = output_dir_abs / file_path.name
                            shutil.move(str(cwd_path), str(dest_path))
                            logger.info(f"Moved vocals file from cwd to: {dest_path}")
                            vocals_path = str(dest_path)
                        else:
                            vocals_path = f
                else:
                    vocals_path = f

        if not vocals_path or not Path(vocals_path).exists():
            raise RuntimeError(
                f"UVR separation did not produce a vocals file for {audio_path}. "
                f"Outputs: {output_files}"
            )

        return {
            "input_path": audio_path,
            "vocals_path": vocals_path,
            "output_dir": str(output_dir),
            "model_used": rt.uvr_model,
            "device": self._current_device,
            "execution_provider": self._execution_provider,
        }


_service: UVRService | None = None


def get_uvr_service() -> UVRService:
    global _service
    if _service is None:
        _service = UVRService()
    return _service


def release_uvr_service() -> None:
    if _service is not None:
        _service.release()


async def separate_vocals(
    audio_path: str,
    output_dir: Path | None = None,
    progress_callback: UVRProgressCallback | None = None,
) -> dict[str, Any]:
    import asyncio

    rt = get_runtime_settings()
    if os.name == "nt" and str(rt.uvr_device).lower() == "cuda":
        return await asyncio.to_thread(
            _separate_vocals_in_subprocess,
            audio_path,
            output_dir,
            progress_callback,
        )
    return await asyncio.to_thread(
        get_uvr_service().separate,
        audio_path,
        output_dir=output_dir,
        progress_callback=progress_callback,
    )


def _separate_vocals_in_subprocess(
    audio_path: str,
    output_dir: Path | None,
    progress_callback: UVRProgressCallback | None,
) -> dict[str, Any]:
    """Run Windows GPU UVR in a short process so its ORT DLLs unload before ASR."""
    import json
    import subprocess
    import sys
    import tempfile

    target_dir = (
        Path(output_dir).resolve()
        if output_dir
        else get_workspace_paths().temporary("uvr")
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    _notify_progress(
        progress_callback,
        {
            "phase": "starting",
            "current_chunk": 0,
            "completed_chunks": 0,
            "total_chunks": 0,
            "progress": 0.0,
            "message": "准备在独立 GPU 进程中分离人声",
        },
    )
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
        result_path = Path(handle.name)
    command = [
        sys.executable,
        "-m",
        "app.services.preprocessing.uvr_worker",
        "--audio",
        str(Path(audio_path).resolve()),
        "--output-dir",
        str(target_dir),
        "--result",
        str(result_path),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(Path(__file__).resolve().parents[3]),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout or "").strip()[-4000:]
            raise RuntimeError(
                f"UVR worker exited with code {completed.returncode}: {details}"
            )
        result = json.loads(result_path.read_text(encoding="utf-8"))
        _notify_progress(
            progress_callback,
            {
                "phase": "completed",
                "progress": 1.0,
                "message": "人声分离完成",
            },
        )
        return result
    finally:
        result_path.unlink(missing_ok=True)
