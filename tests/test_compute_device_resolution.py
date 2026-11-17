"""Verify host snapshots, provider constraints and service device boundaries."""

import sys
from types import SimpleNamespace
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.compute_device import ComputeCapabilities, resolve_compute_device
from app.core.settings import RuntimeSettings

COMPONENTS = ("uvr", "sherpa_onnx", "llama_cpp", "moss_cpp", "torch", "transformers")
LINUX_CPU = ComputeCapabilities()
WINDOWS_CUDA = ComputeCapabilities(
    cuda_available=True, torch_cuda_available=True, sherpa_cuda_available=True,
    onnx_providers=("CPUExecutionProvider", "CUDAExecutionProvider"),
)
MAC_METAL = ComputeCapabilities(mps_available=True, metal_available=True)


@pytest.mark.parametrize("component", COMPONENTS)
def test_linux_cpu_auto_is_safe_and_explicit_gpu_is_rejected(component):
    assert resolve_compute_device("auto", component, LINUX_CPU) == "cpu"
    assert resolve_compute_device("cpu", component, WINDOWS_CUDA) == "cpu"
    for setting in ("gpu", "cuda"):
        with pytest.raises(ValueError, match=f"{component}: requested device"):
            resolve_compute_device(setting, component, LINUX_CPU)


@pytest.mark.parametrize("component", COMPONENTS)
def test_windows_cuda_snapshot(component):
    for setting in ("auto", "gpu", "cuda"):
        assert resolve_compute_device(setting, component, WINDOWS_CUDA) == "cuda"


@pytest.mark.parametrize("component,expected", [
    ("llama_cpp", "metal"), ("moss_cpp", "metal"), ("torch", "mps"),
    ("transformers", "mps"), ("sherpa_onnx", "cpu"), ("uvr", "cpu"),
])
def test_apple_silicon_snapshot(component, expected):
    assert resolve_compute_device("auto", component, MAC_METAL) == expected
    if expected != "cpu":
        assert resolve_compute_device("gpu", component, MAC_METAL) == expected
        assert resolve_compute_device(expected, component, MAC_METAL) == expected


@pytest.mark.parametrize("component", ("sherpa_onnx", "uvr", "torch", "transformers"))
def test_hardware_cuda_requires_component_runtime(component):
    host = ComputeCapabilities(cuda_available=True)
    assert resolve_compute_device("auto", component, host) == "cpu"
    with pytest.raises(ValueError, match="required GPU runtime/provider"):
        resolve_compute_device("cuda", component, host)


def test_uvr_coreml_requires_mps_and_provider():
    host = ComputeCapabilities(mps_available=True, onnx_providers=("CoreMLExecutionProvider",))
    assert resolve_compute_device("gpu", "uvr", host) == "mps"


@pytest.mark.parametrize("field", (
    "uvr_device", "local_llm_device", "pyannote_device", "aligner_device",
    "llama_asr_device", "moss_cpp_device", "sherpa_device",
))
def test_runtime_settings_normalize_unified_and_legacy_values(field):
    assert getattr(RuntimeSettings(), field) == "auto"
    for value in ("auto", "gpu", "cpu", "cuda", "mps", "metal"):
        assert getattr(RuntimeSettings(**{field: f" {value.upper()} "}), field) == value
    with pytest.raises(ValueError):
        RuntimeSettings(**{field: "invalid"})


@pytest.mark.asyncio
async def test_capabilities_exposes_component_accelerators(monkeypatch):
    from app.api.routes import auth

    monkeypatch.setattr(auth, "detect_compute_capabilities", lambda: MAC_METAL)
    app = FastAPI()
    app.include_router(auth.router, prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        response = await client.get("/api/capabilities")
    assert response.status_code == 200
    capabilities = response.json()["compute_capabilities"]
    assert capabilities["recommended_gpu_backend"] == "metal"
    assert capabilities["component_gpu_backends"]["llama_cpp"] == "metal"
    assert capabilities["component_gpu_backends"]["torch"] == "mps"
    assert capabilities["component_gpu_backends"]["sherpa_onnx"] is None


def test_forced_aligner_uses_mps_independently_of_llama_metal(monkeypatch, tmp_path):
    from app.services.recognition import alignment

    loaded = []
    aligner = object()
    monkeypatch.setattr(alignment, "detect_compute_capabilities", lambda: MAC_METAL)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        float32="float32", bfloat16="bfloat16", cuda=SimpleNamespace(is_available=lambda: False),
    ))
    monkeypatch.setitem(sys.modules, "qwen_asr", SimpleNamespace(
        Qwen3ForcedAligner=SimpleNamespace(from_pretrained=lambda *args, **kwargs: loaded.append(kwargs) or aligner),
    ))
    runtime = alignment.QwenForcedAlignerRuntime()
    assert runtime._get(tmp_path, "auto") is aligner
    assert loaded == [{"dtype": "float32", "device_map": "mps"}]
    assert runtime._get(tmp_path, "gpu") is aligner
    assert len(loaded) == 1


def test_sherpa_auto_cpu_without_cuda_provider(monkeypatch):
    from app.services.recognition import sherpa_runtime

    monkeypatch.setattr(sherpa_runtime, "detect_compute_capabilities", lambda: ComputeCapabilities(cuda_available=True))
    assert sherpa_runtime.SherpaRuntime._provider_candidates("auto") == ("cpu",)
    with pytest.raises(ValueError, match="sherpa_onnx"):
        sherpa_runtime.SherpaRuntime._provider_candidates("gpu")


def test_uvr_torch_model_auto_uses_cuda_without_onnx_gpu_provider(monkeypatch):
    from app.services.preprocessing import uvr

    monkeypatch.setattr(uvr, "detect_compute_capabilities", lambda: ComputeCapabilities(
        cuda_available=True, torch_cuda_available=True,
    ))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        device=lambda value: value, cuda=SimpleNamespace(is_available=lambda: True),
    ))
    monkeypatch.setattr(uvr, "_available_onnx_providers", lambda: ["CPUExecutionProvider"])
    service = uvr.UVRService()
    torch_separator, onnx_separator = SimpleNamespace(), SimpleNamespace()
    assert service._configure_separator_device(torch_separator, "auto", onnx_model=False) == "CUDA"
    assert torch_separator.torch_device == "cuda"
    assert service._configure_separator_device(onnx_separator, "auto", onnx_model=True) == "CPUExecutionProvider"
    assert onnx_separator.torch_device == "cpu"


def test_llama_explicit_cpu_does_not_probe_gpu_runtimes(monkeypatch):
    from app.services.analysis import local_llm_runtime

    def unexpected_probe():
        pytest.fail("Explicit CPU inference must not probe GPU runtimes")

    monkeypatch.setattr(local_llm_runtime, "detect_compute_capabilities", unexpected_probe)
    assert local_llm_runtime.LocalLlamaCppRuntime._device_args({"device": "cpu"}) == ["-ngl", "0"]


@pytest.mark.parametrize("system,machine,nvidia,mps", [
    ("Linux", "x86_64", False, False),
    ("Windows", "AMD64", True, False),
    ("Darwin", "arm64", False, True),
])
def test_probe_separates_hardware_and_runtime_support(monkeypatch, system, machine, nvidia, mps):
    from app.core import compute_device

    imports = []
    modules = {
        "torch": SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: False),
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)),
        ),
        "sherpa_onnx": SimpleNamespace(__version__="1.13.4+cuda"),
        "onnxruntime": SimpleNamespace(get_available_providers=lambda: ["CPUExecutionProvider"]),
        "psutil": SimpleNamespace(virtual_memory=lambda: SimpleNamespace(total=4 * 1024**3)),
    }

    def import_module(name):
        imports.append(name)
        return modules[name]

    def run(args, **kwargs):
        if args[0] == "nvidia-smi":
            return SimpleNamespace(returncode=0 if nvidia else 1, stdout="GPU 0" if nvidia else "")
        return SimpleNamespace(returncode=0, stdout='["CPUExecutionProvider"]')

    monkeypatch.setattr(compute_device, "importlib", SimpleNamespace(import_module=import_module))
    monkeypatch.setattr(compute_device, "sys", SimpleNamespace(
        platform="win32" if system == "Windows" else "darwin" if system == "Darwin" else "linux",
        executable=sys.executable,
    ))
    monkeypatch.setattr(compute_device, "platform", SimpleNamespace(system=lambda: system, machine=lambda: machine))
    monkeypatch.setattr(compute_device.subprocess, "run", run)
    snapshot = compute_device.detect_compute_capabilities()
    assert snapshot.cuda_available is nvidia
    assert snapshot.torch_cuda_available is False
    assert snapshot.mps_available is mps
    assert snapshot.metal_available is (system == "Darwin")
    assert snapshot.memory_bytes == 4 * 1024**3
    assert resolve_compute_device("auto", "sherpa_onnx", snapshot) == ("cuda" if nvidia else "cpu")
    assert ("onnxruntime" in imports) is (system != "Windows")


@pytest.mark.parametrize("snapshot,expected", [(LINUX_CPU, "cpu"), (MAC_METAL, "mps")])
def test_pyannote_auto_uses_torch_host_capabilities(monkeypatch, tmp_path, snapshot, expected):
    from app.services.recognition import diarization

    config = tmp_path / "config.yaml"
    config.write_text("pipeline: {}", encoding="utf-8")
    loaded = []
    pipeline = SimpleNamespace(to=lambda value: loaded.append(value) or pipeline)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(device=lambda value: value))
    monkeypatch.setitem(sys.modules, "pyannote.audio", SimpleNamespace(
        Pipeline=SimpleNamespace(from_pretrained=lambda value: pipeline),
    ))
    monkeypatch.setattr(diarization, "detect_compute_capabilities", lambda: snapshot)
    monkeypatch.setattr(diarization, "get_runtime_settings", lambda: RuntimeSettings(
        pyannote_device="", sherpa_device="auto", pyannote_model_path=str(config),
    ))
    runtime = diarization.DiarizationService()
    monkeypatch.setattr(runtime, "_prepare_model_config", lambda value: value)
    monkeypatch.setattr(runtime, "_patch_torch_load", lambda value: None)
    monkeypatch.setattr(runtime, "_configure_huggingface_proxy", lambda: None)
    assert runtime._ensure_pipeline() is pipeline
    assert loaded == [expected]
