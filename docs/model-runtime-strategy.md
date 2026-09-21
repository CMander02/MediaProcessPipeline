# Model Runtime Strategy

## Current ASR Runtime

The default `asr_provider` is `llama_cpp`: Qwen3-ASR GGUF runs in an owned
llama-server process with up to 8 concurrent audio requests. `sherpa_onnx` and
`siliconflow` remain selectable providers.

Configuration:

- `llama_cpp_binary_path`: shared llama.cpp server executable.
- `llama_asr_model_path`, `llama_asr_mmproj_path`: ASR GGUF and matching projector.
- `llama_asr_device`: auto, cuda, or cpu.
- `llama_asr_concurrency`: 1–16, default 8.
- `llama_asr_max_new_tokens`: default 512; truncated responses fail explicitly.
- `llama_asr_timeout_sec`: per-request timeout, default 120 seconds.

The ASR binding mirrors the selected provider and model path. Audio is decoded
once to 16 kHz mono; bounded workers transcribe chunks and restore source order.
The server is released before speaker diarization and LLM postprocessing.

Local providers share the existing `sherpa_chunk_strategy`, VAD and chunk
length settings. `auto` timestamps use VAD boundaries; `qwen_forced` applies the
configured ForcedAligner. Speaker diarization continues through the common
pyannote stage. Word-level timestamps require the optional aligner.

## Performance Evidence

On the 2026-09-21 local 333-chunk / 2735-second benchmark, Qwen3-ASR-0.6B Q8
with 8 llama.cpp slots took 36.0 seconds of inference; Transformers BF16 with
batch size 16 took 92.6 seconds. These exclude preparation and later pipeline
stages. See `agentspace/records/2026-09/2026-09-21-qwen-asr-parallel-benchmark.md`.

## Environment Isolation

Do not build a ComfyUI-style node ecosystem yet. The product has one stable
pipeline and a small set of ASR providers; full per-node Python environments would
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
