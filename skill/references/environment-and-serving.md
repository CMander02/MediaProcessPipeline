# Environment And Serving

## Prerequisites

Assume these baseline requirements for the CLI:

- Python `>=3.11,<3.13`
- `uv`
- `ffmpeg` on `PATH`
- Python dependencies installed from `pyproject.toml`

Optional but common:

- CUDA-capable PyTorch for GPU-backed ASR and separation
- Local model paths for Qwen3 ASR, pyannote, UVR, or a local LLM

## `serve`

Use `serve` to run the FastAPI daemon in the foreground:

```bash
mpp serve
mpp serve --host 0.0.0.0 --port 18000
mpp serve --reload
```

Behavior:

- Start the API server on `127.0.0.1:18000` by default.
- Print API and SSE endpoints at startup.
- Refuse to start if the target port is already occupied.
- On Windows, set UTF-8 output and configure a Job Object so child processes die with the parent process.

Use `--reload` only for development.

## `doctor`

Use `doctor` to diagnose runtime readiness:

```bash
mpp doctor
```

Checks include:

- daemon reachability
- `ffmpeg` on `PATH`
- CUDA availability via `torch.cuda.is_available()`
- whether `data_root` exists
- whether the active LLM provider has an API key configured
- whether the configured Qwen3 ASR model path exists

Use `doctor` before debugging a failed first run. It is the fastest way to catch missing binaries, missing keys, and broken paths.

## Typical Startup Advice

Use one of these sequences:

```bash
mpp serve
mpp run <source>
```

or rely on auto-start:

```bash
mpp run <source>
```

For direct backend invocation, use:

```bash
cd backend
uv run python -m app.cli serve
```
