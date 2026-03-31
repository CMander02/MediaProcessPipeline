# Subtitle Fast Path — Parallel Pipeline Optimization

**Date:** 2026-03-30
**Status:** Approved

## Problem

When a Bilibili/YouTube video has platform AI subtitles, the current pipeline still:
1. Downloads the full video+audio before anything else happens
2. Moves the task to the GPU queue (even though no GPU work is needed)
3. Waits behind other GPU tasks in the queue

The subtitle processing + LLM analysis path is entirely CPU/API-bound and has no dependency on the video file. The video download and subtitle-based analysis can run in parallel.

## Design

### Flow: Subtitle Fast Path (default, `force_asr = false`)

```
download worker receives task
  │
  ├─ 1. Create task_dir, resolve title/metadata stub
  ├─ 2. Attempt subtitle download
  │     Bilibili: BBDown --sub-only --skip-ai false (BBDown defaults to skipping AI subs)
  │     YouTube/other: yt-dlp --skip_download --writesubtitles --writeautomaticsub
  │     takes ~2-10 seconds
  │
  ├─ Subtitle found?
  │   ├─ YES → fork into two concurrent branches:
  │   │   ├─ Branch A (subtitle path):
  │   │   │   process_subtitles → LLM polish → summarize → mindmap → analyze
  │   │   │   writes all text outputs to task_dir
  │   │   │
  │   │   └─ Branch B (media download):
  │   │       download video+audio+thumbnail via download_media()
  │   │       writes video/audio/metadata to task_dir
  │   │
  │   │   asyncio.gather(A, B) → archive → done
  │   │   Task NEVER enters GPU queue.
  │   │
  │   └─ NO → fall back to current full pipeline
  │         (download → GPU queue → UVR → ASR → LLM)
  │
  └─ force_asr = true → skip subtitle probe, go straight to full pipeline
```

### Flow: Full Pipeline (force_asr or no subtitle)

No changes. Identical to current behavior:
```
download worker: download video+audio+subtitle → advance to GPU queue
GPU worker:      UVR → ASR → LLM analysis → archive
```

### Key Changes

#### pipeline.py

- Split the DOWNLOAD step: subtitle probe happens first, before `download_media()`
- New internal function `_run_subtitle_fast_path(task, task_dir, platform_subtitle, metadata)`:
  - Runs subtitle processing → all LLM steps (polish, summarize, mindmap, analyze)
  - Writes output files, emits SSE events
  - Returns the result dict that would normally come from the end of the pipeline
- When fast path activates, the download worker runs:
  ```python
  text_result, media_result = await asyncio.gather(
      _run_subtitle_fast_path(task, task_dir, platform_subtitle, metadata),
      download_media(source, output_dir=task_dir),
  )
  ```
- Merge results: text outputs from Branch A, video/audio paths from Branch B
- Mark all steps complete (DOWNLOAD, SEPARATE, TRANSCRIBE, ANALYZE, POLISH, ARCHIVE)
- Task does NOT call `advance_to_gpu()`

#### queue.py

- No structural changes needed. The download worker simply completes the task without handing off to GPU queue.
- From the queue's perspective, the task finishes in the download worker — same as if the full pipeline ran in a single stage.

#### Settings

- `force_asr` already exists as a per-task option in `task.options`
- Add `force_asr` as a runtime setting (default `false`) in `RuntimeSettings`
- Per-task option overrides the runtime default
- Pipeline checks: `rt.force_asr or task.options.get("force_asr", False)`

#### SSE / Progress

- Fast path emits the same step events (DOWNLOAD, SEPARATE, TRANSCRIBE, ANALYZE, etc.) so the frontend progress bar works unchanged
- SEPARATE step is emitted as instantly completed (skipped)
- Actual step ordering in fast path: DOWNLOAD(subtitle) → TRANSCRIBE(subtitle processing) → concurrent with DOWNLOAD(video) → ANALYZE → POLISH → ARCHIVE

#### Checkpoint / Resume

- `completed_steps` tracks which steps finished
- If task resumes with TRANSCRIBE done but DOWNLOAD not done → fast path was interrupted during video download → just re-download the video
- If task resumes with DOWNLOAD done → normal restore logic (already fixed with platform_subtitle restore from subtitles/ dir)

### Edge Cases

| Scenario | Behavior |
|----------|----------|
| Subtitle probe fails (network/timeout) | Fall back to full pipeline |
| Subtitle exists but too short (<3 lines) | Already filtered by `_download_bilibili_subtitle`, returns None → full pipeline |
| Video download fails, subtitle processing succeeds | Task marked partially complete; text outputs available, video unavailable. User can retry. |
| `force_asr = true` | Skip subtitle probe entirely, go to full pipeline |
| Local file upload with subtitle | Existing `find_local_subtitle` logic, same fast path applies |
| Task cancelled mid-processing | Both branches respect cancellation via existing task status checks |

### What Does NOT Change

- GPU worker logic
- UVR / ASR services
- LLM service calls (same functions, just called from download worker context)
- Frontend (same SSE events, same result structure)
- CLI
- Database schema
- Archive format

## Metadata Timing

In the fast path, metadata is needed before video download completes (for subtitle processing context). Two approaches:

- **Bilibili**: BBDown doesn't give us metadata before download. Use `yt-dlp --skip-download` to extract metadata first (just an API call, no download), then fork.
- **YouTube**: Same — `yt-dlp --skip-download` for metadata extraction.

This metadata probe is fast (~1-2s) and gives us title, description, duration, thumbnail URL — enough for LLM context.

## Implementation Order

1. Add `force_asr` to `RuntimeSettings` with default `false`
2. Extract subtitle processing + LLM steps into `_run_subtitle_fast_path()`
3. Restructure DOWNLOAD step: subtitle probe → fork decision
4. Implement `asyncio.gather` parallel execution
5. Handle checkpoint/resume for the new flow
6. Test: Bilibili with AI subtitle, YouTube with auto-sub, no-subtitle fallback, force_asr override
