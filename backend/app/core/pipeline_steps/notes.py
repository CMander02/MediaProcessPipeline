"""Notes responsibilities for the media pipeline."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.database import get_task_store
from app.core.logging_setup import log_event
from app.core.pipeline_steps.artifacts import (
    _schedule_kb_index,
    _write_detail_file,
    _write_mindmap_files,
    _write_summary_files,
    _write_text_artifact,
    write_metadata_json,
)
from app.core.pipeline_steps.sources import _localize_note_markdown_image_refs
from app.core.pipeline_steps.state import (
    PipelineStep,
    _emit_timeline_event,
    _raise_if_cancelled,
    _task_download_cancelled,
    _update_step,
)
from app.core.pipeline_steps.transcript import _user_language_hint
from app.core.settings import get_runtime_settings
from app.models import MediaMetadata, Task

logger = logging.getLogger(__name__)


def _note_text_excerpt(text: str, limit: int = 600) -> str:
    body_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not re.match(r"^#{1,6}\s+", line.strip())
    ]
    compact = re.sub(r"\s+", " ", " ".join(body_lines)).strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "..."


def _fallback_note_analysis(text: str) -> dict[str, Any]:
    chinese_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_count = len(re.findall(r"[A-Za-z]", text))
    language = "zh-CN" if chinese_count > latin_count else "en" if latin_count else "unknown"
    return {
        "language": language,
        "content_type": "note",
        "main_topics": [],
        "keywords": [],
        "proper_nouns": [],
        "speakers_detected": 1,
        "tone": "unknown",
    }


def _fallback_note_summary(text: str) -> dict[str, Any]:
    topics = [
        match.group(1).strip()
        for match in re.finditer(r"^#{2,6}\s+(.+?)\s*$", text, re.MULTILINE)
        if match.group(1).strip() not in {"网页正文", "正文"}
    ]
    return {
        "tldr": _note_text_excerpt(text),
        "key_facts": [f"章节：{topic}" for topic in topics],
        "action_items": [],
        "topics": topics,
    }


def _fallback_note_mindmap(metadata: "MediaMetadata", image_count: int, text: str) -> str:
    title = metadata.title or "图片笔记"
    headings = [
        match.group(1).strip()
        for match in re.finditer(r"^#{2,6}\s+(.+?)\s*$", text, re.MULTILINE)
        if match.group(1).strip() not in {"网页正文", "正文"}
    ]
    metadata_images = (
        (metadata.extra or {}).get("image_count") if isinstance(metadata.extra, dict) else None
    )
    resolved_image_count = int(metadata_images or image_count or 0)
    lines = [f"- {title}", *[f"  - {heading}" for heading in headings]]
    if resolved_image_count:
        image_label = (
            f"图片素材：{resolved_image_count} 张"
            if metadata.platform == "webpage"
            else f"图片数量: {resolved_image_count}"
        )
        lines.append(f"  - {image_label}")
    return "\n".join(lines)


def _safe_pipeline_error(error: Exception) -> str:
    message = str(error) or error.__class__.__name__
    return re.sub(
        r"sk-[A-Za-z0-9_-]{8,}", lambda m: f"{m.group(0)[:6]}...{m.group(0)[-4:]}", message
    )


def _append_task_warning(task: Task, code: str, message: str, **details: Any) -> None:
    result = dict(task.result or {})
    warnings = list(result.get("warnings") or [])
    warning: dict[str, Any] = {"code": code, "message": message}
    if details:
        warning["details"] = {
            key: str(value) if isinstance(value, (Path, Exception)) else value
            for key, value in details.items()
            if value is not None
        }
    warnings.append(warning)
    result["warnings"] = warnings
    task.result = result
    get_task_store().update_status(task.id, task.status, result=task.result)


async def _write_note_fallback_outputs(
    task: Task,
    task_dir: Path,
    metadata: "MediaMetadata",
    image_count: int,
    combined_text: str,
    *,
    reason: str,
    analysis: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    if analysis is None:
        analysis = _fallback_note_analysis(combined_text)
        analysis["_fallback"] = {"reason": reason}
        await _write_text_artifact(
            task,
            task_dir,
            "analysis.json",
            json.dumps(analysis, indent=2, ensure_ascii=False),
        )

    summary = _fallback_note_summary(combined_text)
    mindmap = _fallback_note_mindmap(metadata, image_count, combined_text)
    await _write_summary_files(task, task_dir, metadata, summary)
    await _write_mindmap_files(task, task_dir, mindmap)
    return analysis, summary, mindmap


def _image_note_index(position: int, path: Path) -> int:
    try:
        return int(path.stem)
    except ValueError:
        return position


def _note_image_download_diagnostics(ingest_info: dict) -> dict[str, Any] | None:
    if not isinstance(ingest_info, dict):
        return None
    extra = ingest_info.get("extra")
    if not isinstance(extra, dict):
        return None
    diagnostics = extra.get("image_download_diagnostics")
    return diagnostics if isinstance(diagnostics, dict) else None


def _note_expected_image_count(ingest_info: dict) -> int:
    if not isinstance(ingest_info, dict):
        return 0
    extra = ingest_info.get("extra")
    if not isinstance(extra, dict):
        return 0
    candidates = extra.get("image_url_candidates")
    if isinstance(candidates, list) and candidates:
        return len(candidates)
    urls = extra.get("image_urls")
    if isinstance(urls, list):
        return len(urls)
    return 0


def _note_should_fail_on_missing_images(ingest_info: dict) -> bool:
    diagnostics = _note_image_download_diagnostics(ingest_info)
    if isinstance(diagnostics, dict) and "fail_on_missing_images" in diagnostics:
        return bool(diagnostics.get("fail_on_missing_images"))
    return True


def _note_image_download_failure_message(
    expected: int,
    downloaded: int,
    diagnostics: dict[str, Any] | None,
    fallback: dict[str, Any] | None = None,
) -> str:
    summary = diagnostics or fallback or {"expected": expected, "downloaded": downloaded}
    return (
        f"图文图片下载不完整：{downloaded}/{expected}，已停止后续处理。"
        f"诊断: {json.dumps(summary, ensure_ascii=False)[:800]}"
    )


def _downloaded_note_image_paths(ingest_info: dict) -> list[Path]:
    if not isinstance(ingest_info, dict):
        return []
    extra = ingest_info.get("extra")
    if not isinstance(extra, dict):
        return []
    raw_paths = extra.get("downloaded_image_paths")
    if not isinstance(raw_paths, list):
        return []
    paths: list[Path] = []
    for value in raw_paths:
        try:
            path = Path(str(value))
        except Exception:
            continue
        if path.exists() and path.is_file():
            paths.append(path)
    return paths


def _existing_note_image_paths(task_dir: Path) -> list[Path]:
    images_dir = task_dir / "images"
    if not images_dir.exists():
        return []
    image_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
    return sorted(
        (
            path
            for path in images_dir.iterdir()
            if path.is_file() and path.suffix.lower() in image_exts
        ),
        key=lambda path: (_image_note_index(0, path), path.name),
    )


def _restored_note_image_descriptions(task: Task, task_dir: Path) -> dict[int, dict[str, Any]]:
    restored: dict[int, dict[str, Any]] = {}
    result = task.result if isinstance(task.result, dict) else {}
    raw_descriptions = result.get("image_descriptions")
    if isinstance(raw_descriptions, list):
        for item in raw_descriptions:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            text = str(item.get("text") or "").strip()
            status = str(item.get("status") or ("completed" if text else "")).lower()
            if status == "completed" and text:
                restored[idx] = dict(item)

    desc_dir = task_dir / "descriptions"
    if desc_dir.exists():
        for path in sorted(desc_dir.glob("*.md")):
            try:
                idx = int(path.stem)
            except ValueError:
                continue
            if idx in restored:
                continue
            try:
                text = path.read_text(encoding="utf-8").strip()
            except Exception:
                continue
            if (
                not text
                or text.startswith("VLM caption 失败")
                or text.startswith("VLM caption 跳过")
            ):
                continue
            restored[idx] = {
                "index": idx,
                "image_path": "",
                "kind": "content",
                "text": text,
                "status": "completed",
            }
    return restored


def _note_image_downloader(metadata: MediaMetadata):
    if metadata.platform == "zhihu":
        from app.services.ingestion.platform.zhihu.api import download_images
    elif metadata.platform in {"bilibili", "bilibili_opus"}:
        from app.services.ingestion.platform.bilibili.note import download_images
    elif metadata.platform == "twitter":
        from app.services.ingestion.platform.twitter.api import download_images
    else:
        from app.services.ingestion.platform.xiaohongshu.api import download_images
    return download_images


def _downloader_accepts_cancel(downloader: Any) -> bool:
    try:
        params = inspect.signature(downloader).parameters
    except (TypeError, ValueError):
        return False
    return "should_cancel" in params or any(
        param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()
    )


def _run_note_image_downloader(
    downloader: Any,
    ingest_info: dict,
    task_dir: Path,
    task_id: UUID,
) -> list[Path]:
    should_cancel = lambda: _task_download_cancelled(task_id)
    if should_cancel():
        raise RuntimeError("note image download cancelled")
    if _downloader_accepts_cancel(downloader):
        return downloader(ingest_info, task_dir, should_cancel=should_cancel)
    return downloader(ingest_info, task_dir)


def _cache_note_cover_thumbnail(
    metadata: MediaMetadata, image_paths: list[Path], task_dir: Path
) -> Path | None:
    if metadata.content_subtype != "image_note" or not image_paths:
        return None
    if metadata.platform not in {"bilibili", "bilibili_opus"}:
        return None
    try:
        from app.services.archiving.thumbnails import create_image_thumbnail

        thumb_path = create_image_thumbnail(image_paths[0], task_dir)
    except Exception as exc:
        log_event(
            logger, logging.DEBUG, "note.thumbnail.failed", platform=metadata.platform, error=exc
        )
        return None
    if not thumb_path:
        return None

    metadata.extra["thumbnail"] = str(thumb_path)
    metadata.extra["thumbnail_source"] = str(image_paths[0])
    metadata.extra["thumbnail_kind"] = "compressed_first_image"
    return thumb_path


async def _download_note_images_for_download_step(
    task: Task,
    metadata: MediaMetadata,
    task_dir: Path,
    ingest_info: dict,
) -> list[Path]:
    """Download note images during the pipeline DOWNLOAD step."""
    if metadata.content_subtype != "image_note":
        return []
    if not isinstance(ingest_info, dict):
        ingest_info = {}
    extra = ingest_info.get("extra")
    if not isinstance(extra, dict):
        extra = {}
        ingest_info["extra"] = extra

    expected = _note_expected_image_count(ingest_info)
    if expected <= 0:
        raise RuntimeError("图文笔记没有可下载的图片候选 URL")

    await _emit_timeline_event(
        task,
        "note.images.download_started",
        stage="download",
        step_id="download",
        level="info",
        message="开始下载图文图片",
        data={"platform": metadata.platform, "expected": expected},
    )

    try:
        downloader = _note_image_downloader(metadata)
        image_paths = await asyncio.get_event_loop().run_in_executor(
            None,
            _run_note_image_downloader,
            downloader,
            ingest_info,
            task_dir,
            task.id,
        )
    except Exception as exc:
        if _task_download_cancelled(task.id):
            raise asyncio.CancelledError() from exc
        error_message = _safe_pipeline_error(exc)
        extra["image_download_error"] = {
            "error_type": type(exc).__name__,
            "error": error_message,
        }
        metadata.extra["image_download_error"] = extra["image_download_error"]
        image_paths = []
        _append_task_warning(
            task,
            "note_images_download_failed",
            "图片下载失败，已停止后续图像理解。",
            platform=metadata.platform,
            expected=expected,
            error=error_message,
        )

    diagnostics = _note_image_download_diagnostics(ingest_info)
    result = {
        "expected": expected,
        "downloaded": len(image_paths),
        "failed": max(expected - len(image_paths), 0),
    }
    extra["downloaded_image_paths"] = [str(path) for path in image_paths]
    extra["image_download_result"] = result
    metadata.extra["downloaded_image_paths"] = extra["downloaded_image_paths"]
    metadata.extra["image_download_result"] = result
    _cache_note_cover_thumbnail(metadata, image_paths, task_dir)
    if diagnostics:
        metadata.extra["image_download_diagnostics"] = diagnostics

    task.result = dict(task.result or {})
    task.result["output_dir"] = str(task_dir)
    task.result["_image_ingest_info"] = ingest_info
    task.result["image_download_result"] = result
    if diagnostics:
        task.result["image_download_diagnostics"] = diagnostics
    get_task_store().update_status(task.id, task.status, result=task.result)
    write_metadata_json(task_dir, metadata, status="processing", task_id=str(task.id))

    await _emit_timeline_event(
        task,
        "note.images.download_completed" if image_paths else "note.images.download_empty",
        stage="download",
        step_id="download",
        level="info" if image_paths else "error",
        message=f"图片下载完成：{len(image_paths)}/{expected}",
        data={"platform": metadata.platform, **result, "diagnostics": diagnostics or {}},
    )

    if result["failed"] > 0 and _note_should_fail_on_missing_images(ingest_info):
        raise RuntimeError(
            _note_image_download_failure_message(
                expected,
                len(image_paths),
                diagnostics,
                extra.get("image_download_error") or result,
            )
        )

    return image_paths


async def _process_image_note(
    task: Task,
    metadata: "MediaMetadata",
    task_dir: Path,
    ingest_info: dict,
) -> None:
    """Process a note-style source: download images when present, summarize, archive."""
    import asyncio as _aio

    from app.services.analysis import (
        analyze_content,
        generate_detail,
        generate_mindmap,
        summarize_text,
    )
    from app.services.archiving import archive_result

    # Mark all audio steps as skipped immediately
    for step in (PipelineStep.SEPARATE, PipelineStep.TRANSCRIBE, PipelineStep.POLISH):
        await _update_step(task, step, completed=True)
    await _raise_if_cancelled(task.id)

    await _update_step(task, PipelineStep.ANALYZE)

    source_text = ""
    extra = ingest_info.get("extra") if isinstance(ingest_info, dict) else None
    source_path_value = extra.get("source_markdown_path") if isinstance(extra, dict) else None
    if source_path_value:
        try:
            source_path = Path(str(source_path_value))
            if source_path.exists():
                source_text = source_path.read_text(encoding="utf-8")
        except Exception as e:
            log_event(
                logger, logging.WARNING, "note.source.read_failed", path=source_path_value, error=e
            )
    if not source_text and metadata.description:
        source_text = metadata.description
    if source_text:
        await _write_text_artifact(task, task_dir, "source.md", source_text)

    # Download images when the note actually has image media.
    image_warning_recorded = False
    if metadata.content_subtype == "text_note":
        image_paths = []
    else:
        image_paths = _downloaded_note_image_paths(ingest_info)
        if not image_paths:
            image_paths = _existing_note_image_paths(task_dir)
        if not image_paths:
            download_images = _note_image_downloader(metadata)
            try:
                image_paths = await _aio.get_event_loop().run_in_executor(
                    None,
                    _run_note_image_downloader,
                    download_images,
                    ingest_info,
                    task_dir,
                    task.id,
                )
            except Exception as e:
                if _task_download_cancelled(task.id):
                    raise _aio.CancelledError() from e
                error_message = _safe_pipeline_error(e)
                log_event(
                    logger, logging.WARNING, "note.images.download_failed", error=error_message
                )
                _append_task_warning(
                    task,
                    "note_images_download_failed",
                    "图片下载失败，已继续处理正文。",
                    error=error_message,
                )
                image_warning_recorded = True
                image_paths = []
    image_download_diagnostics = _note_image_download_diagnostics(ingest_info)
    if image_download_diagnostics:
        metadata.extra["image_download_diagnostics"] = image_download_diagnostics
        task.result = dict(task.result or {})
        task.result["image_download_diagnostics"] = image_download_diagnostics
        get_task_store().update_status(task.id, task.status, result=task.result)
        write_metadata_json(task_dir, metadata, status="processing")
    if metadata.content_subtype == "image_note":
        if not isinstance(extra, dict):
            extra = {}
            if isinstance(ingest_info, dict):
                ingest_info["extra"] = extra
        expected = _note_expected_image_count(ingest_info)
        if expected > 0:
            result = {
                "expected": expected,
                "downloaded": len(image_paths),
                "failed": max(expected - len(image_paths), 0),
            }
            extra["downloaded_image_paths"] = [str(path) for path in image_paths]
            extra["image_download_result"] = result
            metadata.extra["downloaded_image_paths"] = extra["downloaded_image_paths"]
            metadata.extra["image_download_result"] = result
            task.result = dict(task.result or {})
            task.result["image_download_result"] = result
            if image_download_diagnostics:
                task.result["image_download_diagnostics"] = image_download_diagnostics
            get_task_store().update_status(task.id, task.status, result=task.result)
            if result["failed"] > 0 and _note_should_fail_on_missing_images(ingest_info):
                raise RuntimeError(
                    _note_image_download_failure_message(
                        expected,
                        len(image_paths),
                        image_download_diagnostics,
                        extra.get("image_download_error") or result,
                    )
                )
        elif not image_paths and not image_warning_recorded:
            raise RuntimeError("图文笔记没有可下载的图片候选 URL")

    _cache_note_cover_thumbnail(metadata, image_paths, task_dir)

    task.result = dict(task.result or {})
    task.result["output_dir"] = str(task_dir)
    task.result["_image_ingest_info"] = ingest_info
    get_task_store().update_status(task.id, task.status, result=task.result)

    if source_text and image_paths:
        localized_source_text = _localize_note_markdown_image_refs(
            source_text, metadata, image_paths
        )
        if localized_source_text != source_text:
            source_text = localized_source_text
            metadata.description = source_text
            await _write_text_artifact(task, task_dir, "source.md", source_text)

    await _raise_if_cancelled(task.id)

    # Run VLM on each image (limited by vlm_concurrency)
    rt = get_runtime_settings()
    descriptions: list[dict] = []
    restored_descriptions = _restored_note_image_descriptions(task, task_dir)
    from app.core.model_router import (
        resolve_deepseek_llm_binding,
        resolve_llm_binding,
        resolve_vlm_binding,
    )

    vlm_binding = resolve_vlm_binding(rt)
    if image_paths and vlm_binding.configured:
        from app.services.analysis.vlm import get_vlm_service

        vlm = get_vlm_service()
        try:
            vlm_concurrency = max(
                1, int(vlm_binding.request_kwargs.get("concurrency") or rt.vlm_concurrency)
            )
        except (TypeError, ValueError):
            vlm_concurrency = 1
        sem = _aio.Semaphore(vlm_concurrency)

        async def _describe(position: int, path: Path) -> dict:
            idx = _image_note_index(position, path)
            restored = restored_descriptions.get(idx)
            if restored and str(restored.get("text") or "").strip():
                await _emit_timeline_event(
                    task,
                    "vlm.image.reused",
                    stage="analyze",
                    step_id="analyze",
                    level="info",
                    message=f"图片 {idx + 1} caption 复用",
                    data={"index": idx, "file": path.name},
                )
                return {
                    **restored,
                    "index": idx,
                    "image_path": str(path),
                    "status": "completed",
                    "reused": True,
                }
            queued_at = time.monotonic()
            async with sem:
                queue_wait_ms = int((time.monotonic() - queued_at) * 1000)
                await _emit_timeline_event(
                    task,
                    "vlm.image.started",
                    stage="analyze",
                    step_id="analyze",
                    level="info",
                    message=f"图片 {idx + 1} caption 开始",
                    data={
                        "index": idx,
                        "file": path.name,
                        "queue_wait_ms": queue_wait_ms,
                        "concurrency": vlm_concurrency,
                        "timeout_sec": vlm_binding.request_kwargs.get("timeout_sec"),
                    },
                )
                try:
                    result = await _aio.get_event_loop().run_in_executor(
                        None,
                        vlm.describe_image,
                        path,
                        vlm_binding,
                    )
                    if not str(result.get("text") or "").strip():
                        error_message = "VLM returned empty caption text"
                        payload = {
                            "index": idx,
                            "file": path.name,
                            "error": error_message,
                            "payload_meta": result.get("payload_meta"),
                            "duration_ms": result.get("duration_ms"),
                            "queue_wait_ms": queue_wait_ms,
                        }
                        log_event(
                            logger,
                            logging.WARNING,
                            "vlm.image.failed",
                            index=idx,
                            file=path.name,
                            error=error_message,
                        )
                        await _emit_timeline_event(
                            task,
                            "vlm.image.failed",
                            stage="analyze",
                            step_id="analyze",
                            level="warning",
                            message=f"图片 {idx + 1} caption 失败",
                            data=payload,
                        )
                        return {
                            "index": idx,
                            "image_path": str(path),
                            "kind": result.get("kind", "content"),
                            "text": "",
                            "status": "failed",
                            "error": error_message,
                            "payload_meta": result.get("payload_meta"),
                            "duration_ms": result.get("duration_ms"),
                            "queue_wait_ms": queue_wait_ms,
                        }
                    payload = {
                        "index": idx,
                        "file": path.name,
                        "chars": len(result.get("text") or ""),
                        "queue_wait_ms": queue_wait_ms,
                    }
                    if result.get("payload_meta"):
                        payload["payload_meta"] = result["payload_meta"]
                    if result.get("duration_ms") is not None:
                        payload["duration_ms"] = result["duration_ms"]
                    await _emit_timeline_event(
                        task,
                        "vlm.image.completed",
                        stage="analyze",
                        step_id="analyze",
                        level="info",
                        message=f"图片 {idx + 1} caption 完成",
                        data=payload,
                    )
                    return {
                        "index": idx,
                        "image_path": str(path),
                        "status": "completed",
                        "queue_wait_ms": queue_wait_ms,
                        **result,
                    }
                except Exception as e:
                    error_message = _safe_pipeline_error(e)
                    log_event(
                        logger,
                        logging.WARNING,
                        "vlm.image.failed",
                        index=idx,
                        file=path.name,
                        error=error_message,
                    )
                    await _emit_timeline_event(
                        task,
                        "vlm.image.failed",
                        stage="analyze",
                        step_id="analyze",
                        level="warning",
                        message=f"图片 {idx + 1} caption 失败",
                        data={
                            "index": idx,
                            "file": path.name,
                            "error": error_message,
                            "queue_wait_ms": queue_wait_ms,
                        },
                    )
                    return {
                        "index": idx,
                        "image_path": str(path),
                        "kind": "content",
                        "text": "",
                        "status": "failed",
                        "error": error_message,
                        "queue_wait_ms": queue_wait_ms,
                    }

        descriptions = list(
            await _aio.gather(*[_describe(i, p) for i, p in enumerate(image_paths)])
        )
    else:
        for i, p in enumerate(image_paths):
            idx = _image_note_index(i, p)
            restored = restored_descriptions.get(idx)
            if restored and str(restored.get("text") or "").strip():
                descriptions.append(
                    {
                        **restored,
                        "index": idx,
                        "image_path": str(p),
                        "status": "completed",
                        "reused": True,
                    }
                )
            else:
                descriptions.append(
                    {
                        "index": idx,
                        "image_path": str(p),
                        "kind": "content",
                        "text": "",
                        "status": "skipped",
                        "error": vlm_binding.reason or "VLM not configured",
                    }
                )
        if image_paths:
            log_event(
                logger,
                logging.WARNING,
                "vlm.skipped",
                reason=vlm_binding.reason or "not_configured",
                images=len(image_paths),
            )

    await _raise_if_cancelled(task.id)

    # Write per-image description files
    desc_dir = task_dir / "descriptions"
    desc_dir.mkdir(parents=True, exist_ok=True)
    for d in descriptions:
        desc_path = desc_dir / f"{d['index']:02d}.md"
        if d.get("text"):
            await _write_text_artifact(
                task, task_dir, desc_path.relative_to(task_dir).as_posix(), d["text"]
            )
        elif d.get("status") == "failed":
            await _write_text_artifact(
                task,
                task_dir,
                desc_path.relative_to(task_dir).as_posix(),
                f"VLM caption 失败：{d.get('error') or 'unknown error'}\n",
            )
        elif d.get("status") == "skipped":
            await _write_text_artifact(
                task,
                task_dir,
                desc_path.relative_to(task_dir).as_posix(),
                f"VLM caption 跳过：{d.get('error') or 'not configured'}\n",
            )

    # Combine all descriptions into a pseudo-transcript
    combined_parts = []
    for d in descriptions:
        if d.get("text"):
            label = f"图片 {d['index'] + 1}"
            combined_parts.append(f"### {label}\n{d['text']}")
    if source_text:
        body_label = "网页正文" if metadata.platform == "webpage" else "笔记正文"
        combined_parts.insert(0, f"### {body_label}\n{source_text}")
    combined_text = "\n\n".join(combined_parts)
    if combined_text:
        combined_path = desc_dir / "combined.md"
        await _write_text_artifact(
            task, task_dir, combined_path.relative_to(task_dir).as_posix(), combined_text
        )

    # Write descriptions/ into task result early
    task.result = task.result or {}
    task.result["image_descriptions"] = descriptions
    task.result["output_dir"] = str(task_dir)
    if image_download_diagnostics:
        task.result["image_download_diagnostics"] = image_download_diagnostics
    failed_vlm = [d for d in descriptions if d.get("status") == "failed"]
    if failed_vlm:
        _append_task_warning(
            task,
            "note_vlm_partial_failed",
            "图片 caption 失败，已停止后续总结。",
            failed=len(failed_vlm),
            total=len(descriptions),
        )
    get_task_store().update_status(task.id, task.status, result=task.result)
    if failed_vlm:
        detail = [
            {
                "index": d.get("index"),
                "file": Path(str(d.get("image_path") or "")).name,
                "error": d.get("error"),
                "queue_wait_ms": d.get("queue_wait_ms"),
            }
            for d in failed_vlm[:5]
        ]
        raise RuntimeError(
            f"VLM caption 失败：{len(failed_vlm)}/{len(descriptions)}，已停止后续总结。"
            f"诊断: {json.dumps(detail, ensure_ascii=False)[:800]}"
        )

    # Analyze + summarize + mindmap using combined text
    video_metadata = {
        "uploader": metadata.uploader,
        "description": source_text or metadata.description,
        "tags": metadata.tags,
        "chapters": None,
    }
    mindmap_metadata = {
        "title": metadata.title,
        "uploader": metadata.uploader,
        "description": source_text or metadata.description,
        "chapters": None,
    }

    analysis = None
    summary: dict = {}
    mindmap = ""
    detail = ""

    if combined_text and len(combined_text.strip()) >= 10:
        import json as _json

        deepseek_summary_binding = resolve_deepseek_llm_binding(rt, stage="summary")
        llm_provider_override = "deepseek" if deepseek_summary_binding.configured else ""
        llm_binding = (
            deepseek_summary_binding
            if llm_provider_override
            else resolve_llm_binding(rt, stage="summary")
        )
        if not llm_binding.configured:
            log_event(
                logger,
                logging.WARNING,
                "image_note.llm.skipped",
                provider=llm_binding.provider,
                reason=llm_binding.reason,
            )
            analysis, summary, mindmap = await _write_note_fallback_outputs(
                task,
                task_dir,
                metadata,
                len(image_paths),
                combined_text,
                reason=llm_binding.reason or "not_configured",
            )
        else:
            try:
                analysis = await analyze_content(
                    combined_text,
                    metadata.title,
                    metadata=video_metadata,
                    provider_override=llm_provider_override,
                )
                await _raise_if_cancelled(task.id)
                user_language = _user_language_hint(analysis)

                if analysis:
                    await _write_text_artifact(
                        task,
                        task_dir,
                        "analysis.json",
                        _json.dumps(analysis, indent=2, ensure_ascii=False),
                    )

                tasks = [
                    summarize_text(
                        combined_text,
                        user_language=user_language,
                        provider_override=llm_provider_override,
                    ),
                    generate_mindmap(
                        combined_text,
                        metadata=mindmap_metadata,
                        user_language=user_language,
                        provider_override=llm_provider_override,
                    ),
                ]
                if rt.generate_video_detail:
                    tasks.append(
                        generate_detail(
                            combined_text,
                            user_language=user_language,
                            provider_override=llm_provider_override,
                        )
                    )
                results = await _aio.gather(*tasks)
                summary = results[0]
                mindmap = results[1]
                detail = results[2] if len(results) > 2 else ""
                await _raise_if_cancelled(task.id)

                if summary:
                    await _write_summary_files(task, task_dir, metadata, summary)
                if mindmap:
                    await _write_mindmap_files(task, task_dir, mindmap)
                if detail:
                    await _write_detail_file(task, task_dir, detail)
            except Exception as e:
                error_message = _safe_pipeline_error(e)
                log_event(
                    logger,
                    logging.WARNING,
                    "image_note.llm.failed_fallback",
                    provider=llm_binding.provider,
                    model=llm_binding.model,
                    error=error_message,
                )
                _append_task_warning(
                    task,
                    "note_llm_failed",
                    "模型分析失败，已使用正文生成基础结果。",
                    provider=llm_binding.provider,
                    model=llm_binding.model,
                    error=error_message,
                )
                analysis, summary, mindmap = await _write_note_fallback_outputs(
                    task,
                    task_dir,
                    metadata,
                    len(image_paths),
                    combined_text,
                    reason="llm_failed",
                    analysis=analysis,
                )
    else:
        log_event(
            logger, logging.WARNING, "image_note.llm.skipped", reason="combined_text_too_short"
        )

    await _update_step(task, PipelineStep.ANALYZE, completed=True)

    await _update_step(task, PipelineStep.ARCHIVE)
    archive = await archive_result(
        metadata,
        polished_srt=None,
        summary=summary,
        mindmap=mindmap,
        work_dir=task_dir,
        task_id=str(task.id),
        analysis=analysis,
    )
    write_metadata_json(task_dir, metadata, status="completed")
    await _update_step(task, PipelineStep.ARCHIVE, completed=True)

    existing_result = dict(task.result or {})
    warnings = list(existing_result.get("warnings") or [])
    image_download_diagnostics = existing_result.get("image_download_diagnostics")
    task.result = {
        "metadata": metadata.model_dump(mode="json"),
        "image_descriptions": descriptions,
        "archive": archive,
        "output_dir": str(task_dir),
        "analysis": analysis,
        "content_subtype": metadata.content_subtype,
    }
    if image_download_diagnostics:
        task.result["image_download_diagnostics"] = image_download_diagnostics
    if warnings:
        task.result["warnings"] = warnings

    # Async KB indexing (fail-soft)
    _schedule_kb_index(str(task.id), str(task_dir))
