import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.recognition.qwen3_gguf_asr import (  # noqa: E402
    LlamaCppRuntime,
    Qwen3GGUFASRService,
)


def test_llama_cpp_runtime_uses_hf_repo_when_local_paths_empty():
    args = LlamaCppRuntime._model_args(
        {
            "model_path": "",
            "mmproj_path": "",
            "hf_repo": "ggml-org/Qwen3-ASR-1.7B-GGUF:Q8_0",
        }
    )

    assert args == ["-hf", "ggml-org/Qwen3-ASR-1.7B-GGUF:Q8_0"]


def test_llama_cpp_runtime_requires_model_and_mmproj_pair():
    try:
        LlamaCppRuntime._model_args({"model_path": "model.gguf", "mmproj_path": "", "hf_repo": ""})
    except RuntimeError as exc:
        assert "must be set together" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_llama_cpp_runtime_device_args_auto_uses_cuda_when_gpu_available(monkeypatch):
    runtime = LlamaCppRuntime()
    monkeypatch.setattr(runtime, "_has_nvidia_gpu", lambda: True)

    args = runtime._device_args({"device": "auto", "n_gpu_layers": 77})

    assert args == ["-fa", "on", "-ngl", "77"]


def test_llama_cpp_runtime_device_args_cpu_uses_zero_gpu_layers():
    runtime = LlamaCppRuntime()

    args = runtime._device_args({"device": "cpu", "n_gpu_layers": 99})

    assert args == ["-ngl", "0"]


def test_llama_cpp_runtime_stops_process_when_startup_fails(monkeypatch):
    runtime = LlamaCppRuntime()

    class Process:
        terminated = False
        killed = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

        def wait(self, timeout):
            return 0

    process = Process()
    monkeypatch.setattr(LlamaCppRuntime, "_binary_args", staticmethod(lambda _: ["llama-server"]))
    monkeypatch.setattr(LlamaCppRuntime, "_free_port", staticmethod(lambda: 38123))
    monkeypatch.setattr(
        "app.services.recognition.qwen3_gguf_asr.subprocess.Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(
        runtime,
        "_wait_until_ready",
        lambda timeout: (_ for _ in ()).throw(RuntimeError("startup failed")),
    )

    try:
        runtime.ensure(
            {
                "model_path": "",
                "mmproj_path": "",
                "hf_repo": "ggml-org/Qwen3-ASR-1.7B-GGUF:Q8_0",
                "device": "cpu",
                "ctx": 4096,
                "n_gpu_layers": 99,
                "timeout_sec": 1,
                "keepalive_sec": 300,
                "alias": "Qwen3-ASR-1.7B",
            }
        )
    except RuntimeError as exc:
        assert "startup failed" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert process.terminated is True
    assert process.killed is False
    assert runtime.base_url == ""


def test_qwen3_gguf_post_chunk_sends_input_audio_and_cleans_text(tmp_path):
    service = Qwen3GGUFASRService()
    wav = tmp_path / "chunk.wav"
    wav.write_bytes(b"RIFFtest")
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": "language English<asr_text>Hello world<|end|>",
                        }
                    }
                ]
            }

    class Client:
        def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return Response()

    text = service._post_chunk(
        Client(),
        "http://127.0.0.1:8080/v1/chat/completions",
        "Qwen3-ASR-1.7B",
        wav,
        hotwords=["vLLM", "AI Infra"],
    )

    assert text == "Hello world"
    assert captured["json"]["model"] == "Qwen3-ASR-1.7B"
    content = captured["json"]["messages"][0]["content"]
    assert content[0]["type"] == "input_audio"
    assert base64.b64decode(content[0]["input_audio"]["data"]) == b"RIFFtest"
    assert "vLLM, AI Infra" in content[1]["text"]


def test_qwen3_gguf_splits_chunk_text_into_short_segments():
    segments = Qwen3GGUFASRService._split_chunk_text(
        10.0,
        20.0,
        "第一句比较短。第二句会继续说明这个主题，并且补充一些上下文，方便字幕显示。第三句结束。",
    )

    assert len(segments) >= 3
    assert segments[0]["start"] == 10.0
    assert segments[-1]["end"] == 20.0
    assert all(item["text"] for item in segments)
    assert all(item["end"] > item["start"] for item in segments)
    assert max(len(item["text"]) for item in segments) <= 63


def test_qwen3_gguf_release_is_deferred_while_transcribing():
    service = Qwen3GGUFASRService()
    stopped = []
    service._runtime.stop = lambda: stopped.append(True)

    service._begin_transcribe()
    service.release()

    assert stopped == []
    service._end_transcribe()
    assert stopped == [True]
