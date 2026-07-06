# Model Runtime Strategy

## Current ASR Runtime

The product ASR runtime is selected through `asr_provider`. The lightweight
default is `qwen3_gguf`, which starts an external llama.cpp server and talks to
it through an OpenAI-compatible local HTTP endpoint. API ASR is available
through `siliconflow`, and the in-process Qwen3 package remains available as
`qwen3`.

`backend/app/services/recognition/__init__.py` owns provider selection. The
supported values are `qwen3_gguf`, `siliconflow`, and `qwen3`; unsupported
values fail explicitly at settings validation/transcription startup.

Qwen3 package configuration remains intentionally scoped:

- `qwen3_asr_model_path`
- `qwen3_aligner_model_path`
- `qwen3_enable_timestamps`
- `qwen3_batch_size`
- `qwen3_max_new_tokens`
- `qwen3_device`

GGUF/llama.cpp configuration is isolated from the PyTorch path:

- `llama_cpp_binary_path`
- `qwen3_gguf_model_path`
- `qwen3_gguf_mmproj_path`
- `qwen3_gguf_hf_repo`
- `qwen3_gguf_device`
- `qwen3_gguf_chunk_strategy`

Speaker diarization and voiceprint matching are optional capabilities attached
to providers through narrow hooks:

- `get_pyannote_pipeline()`
- `get_last_diarization()`
- `release()`

This means pipeline code does not need to import Qwen3 or llama.cpp directly.

## Dependency Profiles

The base install is designed for API-first and VPS deployments:

- Base: daemon, CLI, yt-dlp, OpenAI-compatible clients, LiteLLM, Playwright
  Python package, Transformers utilities, and ffmpeg fixed ASR chunking.
- `asr-api-vad`: adds `onnxruntime` for Silero ONNX VAD chunking.
- `local-asr`: adds `torch`, `torchaudio`, `qwen-asr`, and `pyannote-audio`.
- `uvr`: adds `torch`, `torchaudio`, and `audio-separator[gpu]`.
- `hf-local-inference`: adds `torch` and `accelerate`; `transformers` is in
  base.
- `local-models`: installs the full local model stack.

Playwright browser binaries are managed separately with
`uv run playwright install chromium`.

## Performance Path

The current runtime uses the official in-process package and passes batching,
device, dtype, max token, and optional ForcedAligner settings to
`Qwen3ASRModel.from_pretrained(...)`.

The Qwen3-ASR upstream project also documents a richer toolkit, including vLLM
batch inference, asynchronous serving, streaming inference, timestamp
prediction, Docker images, source installs with the `vllm` extra, and
FlashAttention 2 as an optional speed/memory optimization. Those are not enabled
in the product runtime yet. Treat them as a future server-mode backend, not a
hidden optimization already active in this repo.

Practical interpretation:

- API ASR plus ffmpeg fixed chunking is the stable VPS path.
- GGUF/llama.cpp keeps Python free of PyTorch for local ASR experiments.
- In-process Qwen3 is the full local ASR path.
- Silero ONNX VAD is an optional quality improvement for chunk boundaries.
- ForcedAligner should be preferred when timestamp quality matters on the
  in-process Qwen3 path.

## Environment Isolation

Do not build a ComfyUI-style node ecosystem yet. The product has one stable
pipeline and one supported ASR provider; full per-node Python environments would
add packaging, GPU ownership, observability, and recovery complexity before the
need is proven.

Use this staged approach instead:

1. Keep API-first runtime dependencies in the main `uv` environment.
2. Put provider-specific settings behind explicit provider namespaces.
3. If a backend needs incompatible dependencies, run it out-of-process behind a
   small HTTP/client adapter.
4. Only introduce plugin/node isolation after there are multiple production
   providers with incompatible dependencies that must coexist.

This matches the product goal: if a clearly better model appears, switch the
default provider deliberately rather than maintaining many speculative backends.

## Future Provider Checklist

When replacing Qwen3 or adding a temporary candidate, implement the same small
contract:

- `transcribe(audio_path, language=None, diarize=True, num_speakers=None)`
- `to_segments(result)`
- `to_srt(segments)`
- Optional diarization cache hooks if voiceprint reuse is needed
- Optional `release()` hook for VRAM cleanup

Add the provider only after deciding its dependency model:

- In-process: compatible with the main environment and GPU scheduler.
- Out-of-process: separate environment/server, called through an adapter.
