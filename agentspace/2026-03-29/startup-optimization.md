# Startup Time Optimization — Lazy Imports

**Date**: 2026-03-29
**Result**: 13,774ms → 366ms (37x faster)

## Problem

`app.main` import took ~14 seconds before the server could accept any request. The root cause was `routes/pipeline.py` eagerly importing all service modules at the top level, which triggered a chain of heavy dependency imports:

| Module | Import Time | Triggered By |
|--------|-------------|--------------|
| `torch` | 1,445ms | `preprocessing/__init__.py` → `vad_splitter.py` |
| `transformers` | 1,008ms | `recognition/__init__.py` → `whisperx.py` → torch → transformers |
| `openai` | 560ms | `analysis/__init__.py` → `llm.py` |
| `yt_dlp` | 120ms | `ingestion/__init__.py` → `ytdlp.py` |
| `whisperx` chain | ~5,600ms total | recognition `__init__` eagerly importing WhisperXService |

None of these are needed at startup — they're only used when processing tasks.

## Import Chain

```
app.main
  → app.api.routes.pipeline (top-level)
    → app.services.ingestion        → ytdlp.py       → yt_dlp (120ms)
    → app.services.preprocessing    → vad_splitter.py → torch + torchaudio (1,500ms)
    → app.services.recognition      → whisperx.py     → torch + whisperx + transformers (5,600ms)
    → app.services.analysis         → llm.py          → openai (560ms)
```

## Fix

### 1. routes/pipeline.py — Moved service imports into function bodies

Before:
```python
from app.services.ingestion import download_media, scan_inbox
from app.services.preprocessing import separate_vocals
from app.services.recognition import transcribe_audio
from app.services.analysis import polish_text, summarize_text, generate_mindmap
```

After: Each route function imports its service lazily:
```python
@router.post("/download")
async def download(req: DownloadRequest):
    from app.services.ingestion import download_media
    return await download_media(req.url)
```

### 2. Service `__init__.py` files — Lazy re-exports

Before (`preprocessing/__init__.py`):
```python
from app.services.preprocessing.uvr import UVRService, separate_vocals
from app.services.preprocessing.vad_splitter import VADSplitter, split_long_audio, merge_srt_segments
```

After:
```python
def separate_vocals(*args, **kwargs):
    from app.services.preprocessing.uvr import separate_vocals as _fn
    return _fn(*args, **kwargs)
```

Same pattern applied to `recognition/__init__.py`, `analysis/__init__.py`, `ingestion/__init__.py`.

### 3. Module-level `import torch` → method-level

Files changed:
- `vad_splitter.py`: Removed top-level `import torch; import torchaudio`, added to methods that use them
- `whisperx.py`: Moved torch import + PyTorch 2.6 patches into `_patch_torch_for_whisperx()`, called lazily in `_ensure_init()`
- `qwen3_asr.py`: Removed top-level `import torch`, added to `_ensure_init()`, `_load_diarization_model()`, `transcribe()`
- `llm.py`: Moved `from openai import AsyncOpenAI` into `_get_client_and_model()`

## Results

| Module | Before | After |
|--------|--------|-------|
| `app.api.routes.pipeline` | 6,187ms | 6ms |
| `app.main` total | 13,774ms | 366ms |

The heavy imports now happen on first task execution instead of server start.

## Note

Python's import system caches modules in `sys.modules`, so the lazy `import` inside functions is only slow on the first call. Subsequent calls hit the cache and cost < 1us.
