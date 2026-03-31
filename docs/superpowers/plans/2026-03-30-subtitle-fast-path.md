# Subtitle Fast Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When platform subtitles are available and `force_asr` is off, run subtitle processing + LLM analysis in parallel with video download, bypassing the GPU queue entirely.

**Architecture:** Add a metadata+subtitle probe at the start of the DOWNLOAD step. If subtitles are found, fork into two `asyncio.gather` branches: (A) subtitle→LLM pipeline and (B) video download. The task completes entirely within the download worker, never touching the GPU queue.

**Tech Stack:** Python/asyncio, FastAPI, existing `YtdlpService`, `process_subtitles`, LLM services.

**Spec:** `docs/superpowers/specs/2026-03-30-subtitle-fast-path-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `backend/app/core/settings.py` | Modify | Add `force_asr` runtime setting |
| `backend/app/services/ingestion/ytdlp.py` | Modify | Add `fetch_metadata` async function for metadata-only probe |
| `backend/app/core/pipeline.py` | Modify | Restructure DOWNLOAD step, add `_run_subtitle_fast_path()`, implement parallel fork |
| `backend/app/core/queue.py` | Modify | Handle fast-path task restore on restart |

---

### Task 1: Add `force_asr` Runtime Setting

**Files:**
- Modify: `backend/app/core/settings.py:66-68`

- [ ] **Step 1: Add `force_asr` field to `RuntimeSettings`**

In `backend/app/core/settings.py`, add `force_asr` to the Platform Subtitles section:

```python
    # Platform Subtitles
    prefer_platform_subtitles: bool = True  # Use platform subtitles when available
    subtitle_languages: str = "zh,en"  # Comma-separated language priority
    force_asr: bool = False  # Force ASR even when platform subtitles are available
```

- [ ] **Step 2: Verify settings load correctly**

Run: `cd backend && uv run python -c "from app.core.settings import RuntimeSettings; s = RuntimeSettings(); print(f'force_asr={s.force_asr}')"`

Expected: `force_asr=False`

- [ ] **Step 3: Commit**

```bash
git add backend/app/core/settings.py
git commit -m "feat: add force_asr runtime setting (default false)"
```

---

### Task 2: Add `fetch_metadata` Async Function

**Files:**
- Modify: `backend/app/services/ingestion/ytdlp.py`

The fast path needs metadata (title, description, uploader, duration, chapters) before video download starts, to give LLM context during subtitle processing. We need a lightweight metadata-only fetch.

- [ ] **Step 1: Add `fetch_metadata` method to `YtdlpService`**

Add after `_download_bilibili_subtitle` (after line 292):

```python
    def fetch_metadata(self, url: str) -> dict[str, Any]:
        """Fetch video metadata without downloading the video.

        Returns the same info dict format as download() so extract_metadata() works.
        Bilibili: uses public API. YouTube/other: uses yt-dlp --skip-download.
        """
        if _is_bilibili_url(url):
            info = self._fetch_bilibili_metadata(url)
            # _fetch_bilibili_metadata already returns title, description, etc.
            return info

        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        if info is None:
            raise RuntimeError(f"Failed to extract metadata: {url}")
        return info
```

- [ ] **Step 2: Add async wrapper at module level**

Add after the existing `download_subtitles` async function (after line 555):

```python
async def fetch_metadata(url: str) -> "MediaMetadata":
    """Fetch metadata without downloading — for subtitle fast path."""
    import asyncio
    service = get_ytdlp_service()
    info = await asyncio.to_thread(service.fetch_metadata, url)
    return service.extract_metadata(info)
```

- [ ] **Step 3: Verify the function works**

Run: `cd backend && uv run python -c "import asyncio; from app.services.ingestion.ytdlp import fetch_metadata; m = asyncio.run(fetch_metadata('https://www.bilibili.com/video/BV1XcXYBLE7F/')); print(m.title, m.uploader, m.duration_seconds)"`

Expected: prints title, uploader, and duration (not None).

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/ingestion/ytdlp.py
git commit -m "feat: add fetch_metadata for lightweight metadata probe"
```

---

### Task 3: Extract `_run_subtitle_fast_path` from Pipeline

**Files:**
- Modify: `backend/app/core/pipeline.py`

Extract the subtitle processing + LLM analysis steps into a standalone function that can run independently of the GPU worker flow. This function takes a subtitle path and metadata, runs through TRANSCRIBE → ANALYZE → POLISH → ARCHIVE, and returns the result.

- [ ] **Step 1: Add the `_run_subtitle_fast_path` function**

Add this function before `run_pipeline` (around line 270, after the existing helper functions). This function is extracted from the existing code paths in `run_pipeline` — it combines the `has_subtitle` branch of TRANSCRIBE (lines 648-661), the ANALYZE step (lines 728-778), the POLISH step for platform subtitles (lines 782-803), and the ARCHIVE step (lines 806-833).

```python
async def _run_subtitle_fast_path(
    task: Task,
    task_dir: Path,
    platform_subtitle: dict,
    metadata: "MediaMetadata",
) -> dict:
    """Run subtitle processing + LLM analysis — no GPU needed.

    This is the 'Branch A' of the fast path: processes platform subtitles
    through LLM for polish/analysis/summary/mindmap. Runs concurrently with
    video download (Branch B).

    Returns the text-related portion of the task result.
    """
    from app.services.recognition.subtitle_processor import process_subtitles
    from app.services.analysis import polish_text, summarize_text, generate_mindmap, analyze_content
    from app.services.archiving import archive_result

    # -- SEPARATE: skip (no audio to separate) --
    await _update_step(task, PipelineStep.SEPARATE, completed=True)

    # -- TRANSCRIBE: process platform subtitle --
    await _update_step(task, PipelineStep.TRANSCRIBE)
    sub_result = await process_subtitles(
        subtitle_path=platform_subtitle["subtitle_path"],
        subtitle_format=platform_subtitle["subtitle_format"],
        metadata=metadata,
    )
    transcript = " ".join(s["text"] for s in sub_result.get("segments", []))
    srt = sub_result.get("srt", "")
    polished = sub_result.get("polished_srt", "")
    polished_md = sub_result.get("polished_md", "")
    recognition_segments = sub_result.get("segments", [])

    # Write transcript files
    if srt:
        srt_path = task_dir / "transcript.srt"
        srt_path.write_text(srt, encoding="utf-8")
        await _emit_file_ready(task, "transcript.srt", str(srt_path))
    if polished:
        polished_srt_path = task_dir / "transcript_polished.srt"
        polished_srt_path.write_text(polished, encoding="utf-8")
        await _emit_file_ready(task, "transcript_polished.srt", str(polished_srt_path))
        if polished_md:
            polished_md_path = task_dir / "transcript_polished.md"
            polished_md_path.write_text(polished_md, encoding="utf-8")

    await _update_step(task, PipelineStep.TRANSCRIBE, completed=True)

    # Guard: skip LLM if transcript is empty
    if not transcript or len(transcript.strip()) < 10:
        logger.warning(f"Fast path: transcript too short ({len(transcript)} chars), skipping LLM")
        await _update_step(task, PipelineStep.ANALYZE, completed=True)
        await _update_step(task, PipelineStep.POLISH, completed=True)
        empty_analysis = {"language": "unknown", "content_type": "unknown", "main_topics": [],
                          "keywords": [], "proper_nouns": [], "speakers_detected": 0, "tone": "unknown"}
        empty_summary = {"tldr": "未检测到有效语音内容", "key_facts": [], "action_items": [], "topics": []}
        return {
            "transcript": transcript,
            "srt": srt,
            "polished": polished,
            "polished_md": polished_md,
            "recognition_segments": recognition_segments,
            "analysis": empty_analysis,
            "summary": empty_summary,
            "mindmap": "",
            "subtitle_source": "platform",
        }

    # -- ANALYZE: analyze + summarize + mindmap (parallel, CPU/network) --
    await _update_step(task, PipelineStep.ANALYZE)
    video_metadata = {
        "uploader": metadata.uploader,
        "description": metadata.description,
        "tags": metadata.tags,
        "chapters": [{"title": ch.title, "start_time": ch.start_time}
                     for ch in metadata.chapters] if metadata.chapters else None,
    }
    mindmap_metadata = {
        "title": metadata.title,
        "uploader": metadata.uploader,
        "description": metadata.description,
        "chapters": [{"title": ch.title, "start_time": ch.start_time}
                     for ch in metadata.chapters] if metadata.chapters else None,
    }
    analysis, summary, mindmap = await asyncio.gather(
        analyze_content(transcript, metadata.title, metadata=video_metadata),
        summarize_text(transcript),
        generate_mindmap(srt or transcript, metadata=mindmap_metadata),
    )

    # Write analysis outputs
    import json as _json
    if analysis:
        analysis_path = task_dir / "analysis.json"
        analysis_path.write_text(_json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
        await _emit_file_ready(task, "analysis.json", str(analysis_path))
    if summary:
        from app.services.archiving.archive import SUMMARY_TEMPLATE, get_archive_service
        _svc = get_archive_service()
        sum_path = task_dir / "summary.md"
        sum_content = SUMMARY_TEMPLATE.format(
            title=metadata.title,
            source_url=metadata.source_url or "",
            date=datetime.now().strftime("%Y-%m-%d"),
            tldr=summary.get("tldr", ""),
            key_facts=_svc._fmt_list(summary.get("key_facts", [])),
        )
        sum_path.write_text(sum_content, encoding="utf-8")
        await _emit_file_ready(task, "summary.md", str(sum_path))
    if mindmap:
        mm_path = task_dir / "mindmap.md"
        mm_path.write_text(mindmap, encoding="utf-8")
        await _emit_file_ready(task, "mindmap.md", str(mm_path))

    await _update_step(task, PipelineStep.ANALYZE, completed=True)

    # -- POLISH: skip (platform subtitle already polished above) --
    await _update_step(task, PipelineStep.POLISH, completed=True)

    return {
        "transcript": transcript,
        "srt": srt,
        "polished": polished,
        "polished_md": polished_md,
        "recognition_segments": recognition_segments,
        "analysis": analysis,
        "summary": summary,
        "mindmap": mindmap,
        "subtitle_source": "platform",
    }
```

- [ ] **Step 2: Verify syntax**

Run: `cd backend && uv run python -c "from app.core.pipeline import _run_subtitle_fast_path; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/core/pipeline.py
git commit -m "refactor: extract _run_subtitle_fast_path from pipeline"
```

---

### Task 4: Restructure DOWNLOAD Step for Parallel Fork

**Files:**
- Modify: `backend/app/core/pipeline.py` — the `run_pipeline` function's URL download branch (lines 527-598)

This is the core change. The URL download branch currently does: create task_dir → download video → download subtitle → advance to GPU. We restructure to: create task_dir → probe metadata + subtitle → if subtitle found, fork into parallel branches; otherwise fall back to current flow.

- [ ] **Step 1: Restructure the URL download branch**

Replace the URL download section in `run_pipeline` (the `else:` branch starting at line 527 with comment "For Bilibili URLs...") with the new logic. The section to replace begins at `else:` (line 527) and ends just before `has_subtitle = platform_subtitle is not None` (line 592).

The new structure:

```python
        else:
            # ── URL source: probe metadata + subtitle first ──
            source_type = _detect_source_type(source)

            # 1. Resolve title for task_dir naming
            if source_type == "bilibili":
                bv_match = re.search(r'(BV[0-9A-Za-z]+)', source)
                title = bv_match.group(1) if bv_match else None
            elif source_type == "youtube":
                yt_match = re.search(r'(?:v=|youtu\.be/)([\w-]{11})', source)
                title = yt_match.group(1) if yt_match else None
                if not title:
                    import yt_dlp
                    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
                        info = ydl.extract_info(source, download=False)
                        title = info.get("title", "unknown") if info else "unknown"
            else:
                import yt_dlp
                with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
                    info = ydl.extract_info(source, download=False)
                    title = info.get("title", "unknown") if info else "unknown"

            if not task_dir:
                task_dir = create_task_dir(task.id, title or str(task.id)[:8])

            # 2. Decide whether to attempt fast path
            force_asr = rt.force_asr or task.options.get("force_asr", False)

            if use_platform_subtitles and not force_asr:
                # Probe: fetch metadata + subtitle (lightweight, no video download)
                from app.services.ingestion.ytdlp import fetch_metadata as _fetch_meta
                try:
                    probe_metadata = await _fetch_meta(source)
                except Exception as e:
                    logger.warning(f"Metadata probe failed: {e}, falling back to full pipeline")
                    probe_metadata = None

                probe_subtitle = None
                if probe_metadata:
                    try:
                        sub_dir = task_dir / "subtitles"
                        probe_subtitle = await download_subtitles(source, sub_dir)
                        if not probe_subtitle or not probe_subtitle.get("subtitle_path"):
                            probe_subtitle = None
                            if sub_dir.exists() and not any(sub_dir.iterdir()):
                                sub_dir.rmdir()
                    except Exception as e:
                        logger.warning(f"Subtitle probe failed: {e}")
                        probe_subtitle = None

                if probe_metadata and probe_subtitle:
                    # ── FAST PATH: subtitle + video download in parallel ──
                    logger.info(f"Task {task.id}: fast path — subtitle found, running parallel")
                    metadata = probe_metadata

                    # Rename task_dir to real title
                    real_title = metadata.title
                    if real_title and task_dir.name != _sanitize_filename(real_title):
                        new_dir = task_dir.parent / _sanitize_filename(real_title)
                        if not new_dir.exists():
                            task_dir.rename(new_dir)
                            task_dir = new_dir
                            # Update subtitle path after rename
                            old_sub_path = Path(probe_subtitle["subtitle_path"])
                            new_sub_path = task_dir / "subtitles" / old_sub_path.name
                            probe_subtitle["subtitle_path"] = str(new_sub_path)
                            logger.info(f"Renamed task dir to: {new_dir}")
                        else:
                            logger.warning(f"Cannot rename to {new_dir} (already exists), keeping {task_dir}")

                    logger.info(f"Downloaded platform subtitle: {probe_subtitle['subtitle_path']}")

                    # Write metadata.json
                    meta_path = write_metadata_json(task_dir, metadata, status="processing")
                    await _emit_file_ready(task, "metadata.json", str(meta_path))

                    await _update_step(task, PipelineStep.DOWNLOAD, completed=True)

                    # Persist output_dir so resume can find task_dir
                    task.result = {"output_dir": str(task_dir)}
                    store = get_task_store()
                    store.update_status(task.id, task.status, result=task.result)

                    # Fork: Branch A (subtitle→LLM) + Branch B (video download)
                    async def _branch_video_download():
                        ingest = await download_media(source, output_dir=task_dir)
                        nonlocal audio_path
                        audio_path = ingest.get("file_path")
                        # Update metadata with file paths from download
                        if ingest.get("video_path"):
                            metadata.file_path = ingest["video_path"]
                        write_metadata_json(task_dir, metadata, status="processing")

                    text_result, _ = await asyncio.gather(
                        _run_subtitle_fast_path(task, task_dir, probe_subtitle, metadata),
                        _branch_video_download(),
                    )

                    # Archive
                    from app.services.archiving import archive_result
                    await _update_step(task, PipelineStep.ARCHIVE)
                    archive = await archive_result(
                        metadata,
                        polished_srt=text_result.get("polished", ""),
                        summary=text_result.get("summary", {}),
                        mindmap=text_result.get("mindmap", ""),
                        original_srt=text_result.get("srt", ""),
                        work_dir=task_dir,
                        analysis=text_result.get("analysis", {}),
                    )
                    write_metadata_json(task_dir, metadata, status="completed")
                    _cleanup_extracted_audio(task_dir, audio_path, metadata.media_type if metadata else None)
                    await _update_step(task, PipelineStep.ARCHIVE, completed=True)

                    task.result = {
                        "metadata": metadata.model_dump(mode="json"),
                        "transcript_segments": len(text_result.get("recognition_segments", [])),
                        "archive": archive,
                        "output_dir": str(task_dir),
                        "analysis": text_result.get("analysis"),
                        "subtitle_source": "platform",
                    }
                    return  # Done — skip the rest of run_pipeline

            # ── FULL PIPELINE: no subtitle or force_asr ──
            # (existing code path, unchanged)
            ingest = await download_media(source, output_dir=task_dir)
            audio_path = ingest.get("file_path")
            metadata = MediaMetadata(**ingest.get("metadata", {"title": source}))
            if ingest.get("video_path"):
                metadata.file_path = ingest["video_path"]

            # Rename task_dir from temp name to real title
            real_title = metadata.title
            if real_title and task_dir.name != _sanitize_filename(real_title):
                new_dir = task_dir.parent / _sanitize_filename(real_title)
                if not new_dir.exists():
                    task_dir.rename(new_dir)
                    task_dir = new_dir
                    if audio_path:
                        audio_path = str(new_dir / Path(audio_path).name)
                    if metadata.file_path:
                        metadata.file_path = str(new_dir / Path(metadata.file_path).name)
                    logger.info(f"Renamed task dir to: {new_dir}")
                else:
                    logger.warning(f"Cannot rename to {new_dir} (already exists), keeping {task_dir}")

            # Try to download platform subtitles (for full pipeline, still useful)
            if use_platform_subtitles:
                try:
                    sub_dir = task_dir / "subtitles"
                    platform_subtitle = await download_subtitles(source, sub_dir)
                    if platform_subtitle.get("subtitle_path"):
                        logger.info(f"Downloaded platform subtitle: {platform_subtitle['subtitle_path']}")
                    else:
                        platform_subtitle = None
                        if sub_dir.exists() and not any(sub_dir.iterdir()):
                            sub_dir.rmdir()
                except Exception as e:
                    logger.warning(f"Subtitle download failed: {e}")
                    platform_subtitle = None

            has_subtitle = platform_subtitle is not None
```

Key differences from current code:
- Before downloading the video, probe for metadata + subtitle
- If both available and not `force_asr`: run parallel branches, `return` early
- If probe fails or `force_asr`: fall through to the original full pipeline code (unchanged)

- [ ] **Step 2: Verify the pipeline module loads without syntax errors**

Run: `cd backend && uv run python -c "from app.core.pipeline import run_pipeline; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/core/pipeline.py
git commit -m "feat: subtitle fast path — parallel subtitle+LLM and video download"
```

---

### Task 5: Handle Fast-Path Task Restore on Restart

**Files:**
- Modify: `backend/app/core/queue.py:110-121`

Currently, on restart, tasks with DOWNLOAD complete go to GPU queue. But fast-path tasks that completed all text processing but crashed during video download have TRANSCRIBE+ANALYZE+POLISH done — they should NOT go to GPU queue. They just need the video re-downloaded.

- [ ] **Step 1: Update task restore logic in `queue.py`**

Replace the restore section in `start()` (lines 110-121):

```python
        stale = store.list_by_statuses([TS.QUEUED, TS.PROCESSING])
        for task in stale:
            if task.status == TS.PROCESSING:
                store.update_status(task.id, TS.QUEUED, message="已重新排队")
            completed = set(task.completed_steps or [])
            if PipelineStep.DOWNLOAD in completed:
                # Check if this was a fast-path task that already finished LLM steps.
                # If TRANSCRIBE is done but task isn't complete, it was a fast-path
                # task interrupted during video download — send to download queue
                # to re-download the video, not GPU queue.
                fast_path_done = {PipelineStep.TRANSCRIBE, PipelineStep.ANALYZE, PipelineStep.POLISH}
                if fast_path_done.issubset(completed) and PipelineStep.ARCHIVE not in completed:
                    await self._download_queue.put(task.id)
                    logger.info(f"Restored fast-path task {task.id} → download queue (video re-download)")
                else:
                    await self._gpu_queue.put(task.id)
                    logger.info(f"Restored task {task.id} → gpu queue (download already done)")
            else:
                await self._download_queue.put(task.id)
                logger.info(f"Restored task {task.id} → download queue")
```

- [ ] **Step 2: Handle fast-path resume in `run_pipeline`**

In `pipeline.py`, in the `PipelineStep.DOWNLOAD in done` restore block (around line 422), add a check: if TRANSCRIBE is also already done (fast-path resume), skip the subtitle probe and just re-download the video, then archive.

After the existing restore block at line 422, add:

```python
    if PipelineStep.DOWNLOAD in done:
        logger.info(f"Task {task.id}: skipping DOWNLOAD (already done), restoring from disk")
        _restore_metadata()
        _restore_audio_paths()
        # Restore has_subtitle + platform_subtitle from disk
        if task_dir:
            sub_dir = task_dir / "subtitles"
            if sub_dir.exists():
                for ext in ("*.srt", "*.ass", "*.vtt"):
                    srt_files = list(sub_dir.glob(ext))
                    if srt_files:
                        sub_file = srt_files[0]
                        platform_subtitle = {
                            "subtitle_path": str(sub_file),
                            "subtitle_lang": "zh",
                            "subtitle_format": sub_file.suffix.lstrip("."),
                        }
                        has_subtitle = True
                        break

        # Fast-path resume: LLM steps done, just need video + archive
        fast_path_steps = {PipelineStep.TRANSCRIBE, PipelineStep.ANALYZE, PipelineStep.POLISH}
        if fast_path_steps.issubset(done) and PipelineStep.ARCHIVE not in done:
            logger.info(f"Task {task.id}: fast-path resume — re-downloading video")
            source = _clean_source_path(task.source)
            ingest = await download_media(source, output_dir=task_dir)
            audio_path = ingest.get("file_path")
            if not metadata:
                metadata = MediaMetadata(**ingest.get("metadata", {"title": source}))
            if ingest.get("video_path"):
                metadata.file_path = ingest["video_path"]

            # Restore text outputs from disk
            _restore_transcript()
            _restore_analysis()
            _restore_summary()
            _restore_mindmap()

            # Archive
            from app.services.archiving import archive_result
            await _update_step(task, PipelineStep.ARCHIVE)
            archive = await archive_result(
                metadata,
                polished_srt=polished or "",
                summary=summary,
                mindmap=mindmap,
                original_srt=srt,
                work_dir=task_dir,
                analysis=analysis,
            )
            write_metadata_json(task_dir, metadata, status="completed")
            _cleanup_extracted_audio(task_dir, audio_path, metadata.media_type if metadata else None)
            await _update_step(task, PipelineStep.ARCHIVE, completed=True)

            task.result = {
                "metadata": metadata.model_dump(mode="json"),
                "transcript_segments": len(recognition_segments),
                "archive": archive,
                "output_dir": str(task_dir),
                "analysis": analysis,
                "subtitle_source": "platform",
            }
            return
```

- [ ] **Step 3: Verify modules load**

Run: `cd backend && uv run python -c "from app.core.queue import TaskQueue; from app.core.pipeline import run_pipeline; print('OK')"`

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/core/queue.py backend/app/core/pipeline.py
git commit -m "feat: handle fast-path task restore on daemon restart"
```

---

### Task 6: Manual Integration Test

No automated tests for this — it's an integration across network services (BBDown, yt-dlp, LLM API). Test manually.

- [ ] **Step 1: Test Bilibili fast path**

Start the daemon: `cd backend && uv run python -m app.cli serve`

Submit a Bilibili video with AI subtitles (from another terminal or the web UI):
```bash
cd backend && uv run python -m app.cli run "https://www.bilibili.com/video/BV1XcXYBLE7F/"
```

Watch the logs. Expected behavior:
- "fast path — subtitle found, running parallel" appears
- Subtitle processing + LLM analysis starts immediately
- Video download runs concurrently
- Task completes WITHOUT "→ gpu queue" in logs
- Output directory contains: video file, transcript.srt, transcript_polished.srt, summary.md, mindmap.md, analysis.json

- [ ] **Step 2: Test force_asr override**

Modify `data/settings.json` to set `"force_asr": true`, restart daemon.

Submit the same Bilibili video. Expected:
- No "fast path" log message
- Task goes through download → GPU queue → full pipeline
- "→ gpu queue" appears in logs

Reset `force_asr` to `false` after test.

- [ ] **Step 3: Test fallback (no subtitle)**

Submit a video that has no AI subtitles (e.g., a music-only Bilibili video or a very new video without AI subs).

Expected:
- Subtitle probe returns None
- Falls back to full pipeline automatically
- "→ gpu queue" appears in logs

- [ ] **Step 4: Test YouTube fast path**

Submit a YouTube video with auto-generated subtitles:
```bash
cd backend && uv run python -m app.cli run "https://www.youtube.com/watch?v=<some-video-with-auto-subs>"
```

Expected: same fast-path behavior as Bilibili.

- [ ] **Step 5: Commit any fixes from testing**

```bash
git add -u
git commit -m "fix: integration test fixes for subtitle fast path"
```
