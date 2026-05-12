# Model Runtime Strategy

## Current ASR Runtime

The product ASR runtime is Qwen3-ASR. The backend uses the official `qwen-asr`
Python package through `Qwen3ASRModel.from_pretrained(...)`; this is the
supported in-process Transformers backend.

`backend/app/services/recognition/__init__.py` owns provider selection. Today
`asr_provider` only accepts `qwen3`, and unsupported values fail explicitly at
settings validation/transcription startup instead of silently falling back.

Qwen3-specific configuration remains intentionally scoped:

- `qwen3_asr_model_path`
- `qwen3_aligner_model_path`
- `qwen3_enable_timestamps`
- `qwen3_batch_size`
- `qwen3_max_new_tokens`
- `qwen3_device`

Speaker diarization and voiceprint matching are optional capabilities attached
to the current provider through narrow hooks:

- `get_pyannote_pipeline()`
- `get_last_diarization()`
- `release()`

This means pipeline code does not need to import Qwen3 directly, while the
existing Qwen3 behavior stays unchanged.

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

- In-process Qwen3 is the stable default for product use.
- vLLM should be introduced only if ASR throughput becomes the bottleneck and
  the operational cost of running a separate ASR server is acceptable.
- ForcedAligner should be preferred when timestamp quality matters; without it,
  the current implementation falls back to Silero VAD chunk boundaries.

## Environment Isolation

Do not build a ComfyUI-style node ecosystem yet. The product has one stable
pipeline and one supported ASR provider; full per-node Python environments would
add packaging, GPU ownership, observability, and recovery complexity before the
need is proven.

Use this staged approach instead:

1. Keep core runtime dependencies in the main `uv` environment.
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
