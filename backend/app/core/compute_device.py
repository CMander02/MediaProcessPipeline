"""Host accelerator discovery and component-specific device resolution."""

from __future__ import annotations

import importlib
import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Literal

Component = Literal["sherpa_onnx", "uvr", "llama_cpp", "moss_cpp", "torch", "transformers"]
_COMPONENT_BACKENDS: dict[Component, tuple[str, ...]] = {
    "sherpa_onnx": ("cuda",),
    "uvr": ("cuda", "mps"),
    "llama_cpp": ("cuda", "metal"),
    "moss_cpp": ("cuda", "metal"),
    "torch": ("cuda", "mps"),
    "transformers": ("cuda", "mps"),
}


@dataclass(frozen=True)
class ComputeCapabilities:
    cuda_available: bool = False
    mps_available: bool = False
    metal_available: bool = False
    torch_cuda_available: bool = False
    sherpa_cuda_available: bool = False
    onnx_providers: tuple[str, ...] = ()
    cpu_count: int = 1
    memory_bytes: int = 0

    def gpu_backends(self, component: Component) -> tuple[str, ...]:
        availability = {
            "cuda": self.cuda_available,
            "mps": self.mps_available,
            "metal": self.metal_available,
        }
        if component in {"torch", "transformers", "uvr"}:
            availability["cuda"] = self.torch_cuda_available
        if component == "sherpa_onnx":
            availability["cuda"] = self.cuda_available and self.sherpa_cuda_available
        if component == "uvr":
            availability["cuda"] &= "CUDAExecutionProvider" in self.onnx_providers
            availability["mps"] &= "CoreMLExecutionProvider" in self.onnx_providers
        return tuple(backend for backend in _COMPONENT_BACKENDS[component] if availability[backend])

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "recommended_gpu_backend": (
                "cuda" if self.cuda_available else "metal" if self.metal_available
                else "mps" if self.mps_available else None
            ),
            "component_gpu_backends": {
                component: next(iter(self.gpu_backends(component)), None) for component in _COMPONENT_BACKENDS
            },
        }


def resolve_compute_device(setting: str, component: Component, capabilities: ComputeCapabilities) -> str:
    """Resolve a validated setting against an immutable host snapshot."""
    available = capabilities.gpu_backends(component)
    if setting == "cpu":
        return "cpu"
    if setting in {"auto", "gpu"}:
        if available:
            return available[0]
        if setting == "auto":
            return "cpu"
    elif setting in available:
        return setting
    raise ValueError(
        f"{component}: requested device '{setting}' is unavailable. "
        f"Available devices: {', '.join(('cpu', *available))}. "
        "Select auto or cpu, or install the required GPU runtime/provider."
    )


def detect_compute_capabilities() -> ComputeCapabilities:
    """Probe optional runtimes without making them installation requirements."""
    torch_cuda = mps = False
    try:
        torch = importlib.import_module("torch")
        torch_cuda = bool(torch.cuda.is_available())
        mps_backend = getattr(getattr(torch, "backends", None), "mps", None)
        mps = bool(mps_backend and mps_backend.is_available())
    except (ImportError, OSError, RuntimeError):
        pass
    cuda = torch_cuda
    if not cuda:
        try:
            result = subprocess.run(
                ["nvidia-smi", "-L"], capture_output=True, text=True, timeout=3, check=False,
            )
            cuda = result.returncode == 0 and "GPU" in result.stdout
        except (OSError, subprocess.TimeoutExpired):
            pass
    sherpa_cuda = False
    try:
        sherpa_cuda = "+cuda" in str(importlib.import_module("sherpa_onnx").__version__).lower()
    except (ImportError, OSError):
        pass
    providers: tuple[str, ...] = ()
    try:
        if sys.platform == "win32":
            probe = subprocess.run(
                [sys.executable, "-c", "import json, onnxruntime; print(json.dumps(onnxruntime.get_available_providers()))"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if probe.returncode == 0:
                providers = tuple(json.loads(probe.stdout))
        else:
            providers = tuple(importlib.import_module("onnxruntime").get_available_providers())
    except (ImportError, OSError, RuntimeError, subprocess.TimeoutExpired, ValueError):
        pass
    memory = 0
    try:
        memory = int(importlib.import_module("psutil").virtual_memory().total)
    except (ImportError, OSError):
        pass
    return ComputeCapabilities(
        cuda_available=cuda,
        torch_cuda_available=torch_cuda,
        mps_available=mps,
        metal_available=platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"},
        sherpa_cuda_available=sherpa_cuda,
        onnx_providers=providers,
        cpu_count=os.cpu_count() or 1,
        memory_bytes=memory,
    )
