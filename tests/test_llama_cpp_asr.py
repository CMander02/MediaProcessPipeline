import base64
import io
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.recognition import llama_cpp_asr as module
from app.core.model_router import resolve_asr_binding
from app.core.settings import RuntimeSettings


@pytest.fixture
def config(tmp_path):
    values = {"device": "cuda", "parallel": 2, "chunk_strategy": "fixed", "max_chunk_sec": 1}
    for key in ("model_path", "mmproj_path", "binary_path"):
        path = tmp_path / key
        path.write_bytes(b"test")
        values[key] = str(path)
    return values


@pytest.fixture
def service(monkeypatch, config, tmp_path):
    svc = module.LlamaCppASRService()
    runtime = SimpleNamespace(config=None, stopped=False)
    def ensure(value):
        runtime.config = value
        return "http://localhost:19000"
    svc.runtime = SimpleNamespace(ensure=ensure, stop=lambda: setattr(runtime, "stopped", True))
    samples = np.concatenate([np.full(16000, x, dtype=np.float32) for x in (0.1, 0.2, 0.3)])
    calls = []
    def decode(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=samples.tobytes(), stderr=b"")
    monkeypatch.setattr(module.subprocess, "run", decode)
    audio = tmp_path / "音频 🎧.wav"
    audio.write_bytes(b"input")
    return svc, audio, runtime, calls


def mock_client(monkeypatch, *, fail=False):
    state = SimpleNamespace(active=0, peak=0, payloads=[], finished=[])
    lock = threading.Lock()
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, path, json):
            audio = json["messages"][1]["content"][0]["input_audio"]["data"]
            wave, sr = sf.read(io.BytesIO(base64.b64decode(audio)))
            assert sr == 16000
            index = round(float(wave[0]) * 10)
            with lock:
                state.active += 1
                state.peak = max(state.peak, state.active)
                state.payloads.append(json)
            time.sleep(0.08 if index == 1 else 0.01)
            with lock:
                state.active -= 1
                state.finished.append(index)
            choice = {"finish_reason": "length" if fail and index == 2 else "stop",
                      "message": {"content": f"language Chinese<asr_text>片段{index}。"}}
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"choices": [choice]})
    monkeypatch.setattr(module.httpx, "Client", Client)
    return state


def test_parallel_asr_decodes_once_orders_results_and_releases(monkeypatch, service, config):
    svc, audio, runtime, calls = service
    requests = mock_client(monkeypatch)
    updates = []
    result = svc.transcribe(str(audio), runtime_config=config, hotwords=["Agentic RL"], progress_callback=updates.append)
    assert requests.peak == 2
    assert requests.finished[0] == 2
    assert [s["text"] for s in result["segments"]] == ["片段1。", "片段2。", "片段3。"]
    assert [(s["start"], s["end"]) for s in result["segments"]] == [(0, 1), (1, 2), (2, 3)]
    assert len(calls) == 1
    assert runtime.stopped
    assert runtime.config["cache_type"] == "f16"
    assert runtime.config["parallel"] == 2
    assert requests.payloads[0]["messages"][0]["content"] == "Agentic RL"
    assert updates[-1]["progress"] == 1
    assert "00:00:02,000 --> 00:00:03,000" in svc.to_srt(svc.to_segments(result))


def test_length_limit_fails_task_and_releases_runtime(monkeypatch, service, config):
    svc, audio, runtime, _ = service
    mock_client(monkeypatch, fail=True)
    with pytest.raises(RuntimeError, match="output token limit"):
        svc.transcribe(str(audio), runtime_config=config)
    assert runtime.stopped


def test_reuses_decoded_samples_for_vad(monkeypatch, service, config):
    svc, audio, _, calls = service
    mock_client(monkeypatch)
    def vad(self, path, *, samples, **kwargs):
        assert len(samples) == 48000
        return [module.AudioChunk(1, 2)]
    monkeypatch.setattr(module.ASRChunker, "sherpa_vad_chunks", vad)
    config.update(chunk_strategy="vad", vad_model_path="test-vad")
    result = svc.transcribe(str(audio), runtime_config=config)
    assert len(calls) == 1
    assert result["timestamp_source"] == "vad"
    assert result["segments"] == [{"start": 1, "end": 2, "text": "片段2。"}]


def test_default_binding_and_explicit_provider_override(config):
    settings = RuntimeSettings(llama_cpp_binary_path=config["binary_path"],
        llama_asr_model_path=config["model_path"], llama_asr_mmproj_path=config["mmproj_path"])
    binding = resolve_asr_binding(settings)
    assert binding.provider == "llama_cpp"
    assert binding.configured
    assert binding.request_kwargs["parallel"] == 8
    other = resolve_asr_binding(settings, task_options={"asr_provider": "sherpa_onnx"})
    assert other.model == settings.sherpa_model_id


def test_missing_projector_and_unsupported_native_timestamps(config):
    assert "mmproj_path" in module.validate_llama_asr_config({**config, "mmproj_path": ""})
    assert "timestamps" in module.validate_llama_asr_config({**config, "timestamp_mode": "native"})
    assert "required" in module.validate_llama_asr_config({**config, "timestamp_mode": "qwen_forced"})


def test_parse_asr_scaffolding():
    assert module.parse_asr_text("language English<asr_text>Hello.<|im_end|>") == ("Hello.", "english")
    assert module.parse_asr_text("你好", "zh") == ("你好", "zh")
