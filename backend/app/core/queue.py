"""Async task queue — parallel downloads, serialised GPU.

New tasks enter a download queue consumed by N concurrent download workers
(I/O-bound, configurable via settings.max_download_concurrency, default 2).
After the DOWNLOAD step completes, each task is moved to the GPU queue,
which is consumed by a single GPU worker that runs UVR separation and ASR
transcription serially to avoid VRAM conflicts. LLM steps (analysis, polish,
summary) run freely inside the GPU worker after the GPU is released.

On backend restart, tasks that already completed DOWNLOAD are restored
directly into the GPU queue so they skip re-downloading.
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine
from uuid import UUID

from app.core.database import get_task_store
from app.core.events import TaskEvent, get_event_bus
from app.core.logging_setup import (
    log_event,
    set_task_context, set_worker_context, reset_context,
    task_id_var, worker_var,
)
from app.models.task import TaskStatus

logger = logging.getLogger(__name__)


def _flush_gpu_models() -> None:
    """Release all GPU models to free VRAM. Runs in a thread (torch calls are blocking)."""
    import gc
    try:
        from app.services.preprocessing.uvr import get_uvr_service, release_uvr_service

        if get_uvr_service()._separator is not None:
            log_event(logger, logging.INFO, "gpu.uvr.release")
            release_uvr_service()
    except Exception as e:
        log_event(logger, logging.WARNING, "gpu.uvr.release_failed", error=e)
    try:
        from app.services.recognition import release_asr_models

        release_asr_models()
    except Exception as e:
        log_event(logger, logging.WARNING, "gpu.asr.release_failed", error=e)
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            log_event(logger, logging.INFO, "gpu.cuda_cache.cleared")
    except Exception as e:
        log_event(logger, logging.WARNING, "gpu.cuda_cache.clear_failed", error=e)
    try:
        gc.collect()
        log_event(logger, logging.INFO, "runtime.gc.collected")
    except Exception:
        pass


class TaskQueue:
    """Two-stage queue: parallel download → serialised GPU → free LLM."""

    def __init__(self):
        self._download_queue: asyncio.Queue[UUID] = asyncio.Queue()
        self._gpu_queue: asyncio.Queue[UUID] = asyncio.Queue()

        self._download_worker_tasks: list[asyncio.Task] = []
        self._gpu_worker_task: asyncio.Task | None = None

        # Called by the download worker to run just the DOWNLOAD step.
        # Called by the GPU worker to run SEPARATE → TRANSCRIBE → ANALYZE → POLISH → ARCHIVE.
        self._pipeline_fn: Callable[[UUID, bool], Coroutine[Any, Any, None]] | None = None

        self._active_download_ids: set[UUID] = set()
        self._active_gpu_id: UUID | None = None
        self._running_tasks: dict[UUID, asyncio.Task] = {}
        self._running = False

        # Exposed so pipeline.py can acquire it around GPU-heavy steps.
        # Always Semaphore(1) — one task on GPU at a time.
        self.gpu_semaphore = asyncio.Semaphore(1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def current_task_id(self) -> UUID | None:
        """Backwards-compat: return current GPU task (or any download task)."""
        return self._active_gpu_id or next(iter(self._active_download_ids), None)

    @property
    def active_task_ids(self) -> set[UUID]:
        ids = set(self._active_download_ids)
        if self._active_gpu_id:
            ids.add(self._active_gpu_id)
        return ids

    @property
    def pending_count(self) -> int:
        return self._download_queue.qsize() + self._gpu_queue.qsize()

    def set_pipeline(self, fn: Callable[[UUID, bool], Coroutine[Any, Any, None]]) -> None:
        self._pipeline_fn = fn

    async def submit(self, task_id: UUID) -> None:
        """Enqueue a new task — it goes straight to the download queue."""
        store = get_task_store()
        bus = get_event_bus()
        store.update_status(task_id, TaskStatus.QUEUED)
        await bus.publish(TaskEvent(task_id, "queued"))
        t_token = set_task_context(str(task_id))
        try:
            await self._download_queue.put(task_id)
            log_event(
                logger,
                logging.INFO,
                "queue.download.enqueued",
                depth=self._download_queue.qsize(),
            )
        finally:
            reset_context(t_token, task_id_var)

    async def cancel(self, task_id: UUID) -> bool:
        store = get_task_store()
        task = store.get(task_id)
        if not task:
            return False
        if task.status not in (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.PROCESSING, TaskStatus.PAUSED):
            return False
        self._remove_from_queue(self._download_queue, task_id)
        self._remove_from_queue(self._gpu_queue, task_id)
        store.update_status(task_id, TaskStatus.CANCELLED, completed_at=datetime.now())
        if task.result and task.result.get("output_dir"):
            try:
                from pathlib import Path
                from app.core.pipeline import update_metadata_status
                update_metadata_status(Path(task.result["output_dir"]), "cancelled")
            except Exception:
                log_event(logger, logging.DEBUG, "task.metadata_status.update_failed", status="cancelled", exc_info=True)
        bus = get_event_bus()
        running = self._running_tasks.get(task_id)
        if running:
            running.cancel()
        else:
            await bus.publish(TaskEvent(task_id, "cancelled"))
        t_token = set_task_context(str(task_id))
        try:
            log_event(logger, logging.INFO, "task.cancelled")
        finally:
            reset_context(t_token, task_id_var)
        return True

    async def pause(self, task_id: UUID) -> bool:
        """Pause a queued or running task and keep its checkpointed outputs."""
        store = get_task_store()
        task = store.get(task_id)
        if not task:
            return False
        if task.status not in (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.PROCESSING):
            return False

        self._remove_from_queue(self._download_queue, task_id)
        self._remove_from_queue(self._gpu_queue, task_id)

        flow = task.flow
        if flow:
            flow = dict(flow)
            flow["status"] = "paused"
        store.update_status(
            task_id,
            TaskStatus.PAUSED,
            message="已暂停",
            flow=flow,
            completed_at=None,
        )
        if task.result and task.result.get("output_dir"):
            try:
                from app.core.pipeline import update_metadata_status
                update_metadata_status(Path(task.result["output_dir"]), "paused")
            except Exception:
                log_event(logger, logging.DEBUG, "task.metadata_status.update_failed", status="paused", exc_info=True)

        running = self._running_tasks.get(task_id)
        if running:
            running.cancel()

        bus = get_event_bus()
        await bus.publish(TaskEvent(task_id, "paused", {"status": "paused", "message": "已暂停"}))
        t_token = set_task_context(str(task_id))
        try:
            log_event(logger, logging.INFO, "task.paused")
        finally:
            reset_context(t_token, task_id_var)
        return True

    async def resume(self, task_id: UUID, *, force: bool = False) -> bool:
        """Resume a task by putting it back on the queue.

        Normal resume accepts paused/failed tasks. ``force=True`` is used by
        checkpoint rerun: completed tasks keep durable local artifacts and drop
        downstream completed steps so the pipeline recomputes analysis/archive.
        """
        store = get_task_store()
        task = store.get(task_id)
        resumable = {TaskStatus.PAUSED, TaskStatus.FAILED}
        if force:
            resumable.add(TaskStatus.COMPLETED)
        if not task or task.status not in resumable:
            return False

        completed_steps = task.completed_steps
        current_step = task.current_step
        progress = task.progress
        if force and task.status == TaskStatus.COMPLETED:
            completed_steps = self._checkpoint_completed_steps(task)
            current_step = self._next_pipeline_step(completed_steps)
            progress = self._checkpoint_progress(completed_steps)

        flow = task.flow
        if flow:
            flow = dict(flow)
            flow["status"] = "queued"
            if force and task.status == TaskStatus.COMPLETED:
                flow_steps = flow.get("steps") if isinstance(flow.get("steps"), list) else []
                flow_step_ids = [str(step.get("id")) for step in flow_steps if isinstance(step, dict) and step.get("id")]
                if flow_step_ids:
                    previous_flow_done = flow.get("completed_steps")
                    if not isinstance(previous_flow_done, list):
                        previous_flow_done = []
                    flow["completed_steps"] = [step for step in previous_flow_done if step in flow_step_ids]
                    flow["current_step"] = flow.get("current_step") or flow_step_ids[0]
        store.update_status(
            task_id,
            TaskStatus.QUEUED,
            message="已重新排队，将从历史进度继续" if not force else "已重新排队，将基于已有文件断点续做",
            progress=progress,
            flow=flow,
            completed_at=None,
            completed_steps=completed_steps,
            current_step=current_step,
            error=None,
        )
        if task.result and task.result.get("output_dir"):
            try:
                from app.core.pipeline import update_metadata_status
                update_metadata_status(Path(task.result["output_dir"]), "queued")
            except Exception:
                log_event(logger, logging.DEBUG, "task.metadata_status.update_failed", status="queued", exc_info=True)
        await get_event_bus().publish(TaskEvent(
            task_id,
            "resumed",
            {"status": "queued", "message": "已重新排队，将从历史进度继续" if not force else "已重新排队，将基于已有文件断点续做"},
        ))
        await self.submit(task_id)
        return True

    async def rerun_from_checkpoint(self, task_id: UUID) -> bool:
        """Queue an in-place checkpoint rerun for failed, paused, or completed tasks."""
        return await self.resume(task_id, force=True)

    async def delete(self, task_id: UUID) -> dict[str, Any] | None:
        """Delete a task record and best-effort remove its output directory."""
        store = get_task_store()
        task = store.get(task_id)
        if not task:
            return None

        self._remove_from_queue(self._download_queue, task_id)
        self._remove_from_queue(self._gpu_queue, task_id)
        running = self._running_tasks.get(task_id)
        if running:
            running.cancel()
            try:
                await asyncio.wait_for(running, timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:
                pass

        output_dirs = self._task_output_dirs(task)
        deleted_paths, errors = self._delete_output_dirs(output_dirs)
        deleted = store.delete(task_id)
        if not deleted:
            return None

        payload = {
            "status": "deleted",
            "deleted_paths": deleted_paths,
            "errors": errors,
        }
        await get_event_bus().publish(TaskEvent(task_id, "deleted", payload))
        log_event(
            logger,
            logging.INFO,
            "task.deleted",
            deleted_paths=len(deleted_paths),
            errors=len(errors),
        )
        return payload

    def _remove_from_queue(self, queue: asyncio.Queue[UUID], task_id: UUID) -> int:
        removed = 0
        retained: list[UUID] = []
        raw_queue = queue._queue  # type: ignore[attr-defined]
        while raw_queue:
            item = raw_queue.popleft()
            if item == task_id:
                removed += 1
            else:
                retained.append(item)
        raw_queue.extend(retained)
        for _ in range(removed):
            try:
                queue.task_done()
            except ValueError:
                break
        return removed

    def _task_output_dirs(self, task: Any) -> list[Path]:
        result = task.result if isinstance(task.result, dict) else {}
        candidates: list[Any] = [result.get("output_dir")]
        archive = result.get("archive")
        if isinstance(archive, dict):
            candidates.append(archive.get("output_dir"))
        paths: list[Path] = []
        seen: set[str] = set()
        for value in candidates:
            if not value:
                continue
            try:
                path = Path(str(value)).resolve()
            except Exception:
                continue
            key = str(path)
            if key not in seen:
                seen.add(key)
                paths.append(path)
        return paths

    def _delete_output_dirs(self, paths: list[Path]) -> tuple[list[str], list[dict[str, str]]]:
        import shutil

        from app.core.settings import get_runtime_settings

        data_root = Path(get_runtime_settings().data_root).resolve()
        deleted: list[str] = []
        errors: list[dict[str, str]] = []
        for path in paths:
            try:
                target = path.resolve()
                if target == data_root:
                    raise RuntimeError("refusing to delete data_root")
                target.relative_to(data_root)
                if not target.exists():
                    continue
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                deleted.append(str(target))
            except Exception as exc:
                errors.append({"path": str(path), "error": str(exc)})
        return deleted, errors

    def _checkpoint_completed_steps(self, task: Any) -> list[str]:
        """Infer durable steps that can be reused during a forced checkpoint rerun."""
        ordered = self._pipeline_order(task)
        task_dir = next((path for path in self._task_output_dirs(task) if path.exists()), None)
        if task_dir is None:
            return []

        metadata = self._read_task_metadata(task_dir)
        result = task.result if isinstance(task.result, dict) else {}
        result_metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        content_subtype = (
            getattr(task, "content_subtype", None)
            or result_metadata.get("content_subtype")
            or metadata.get("content_subtype")
        )

        if content_subtype in {"image_note", "text_note"}:
            retained = {"download", "separate", "transcribe", "voiceprint", "polish"}
            return [step for step in ordered if step in retained]

        has_download_artifact = (task_dir / "metadata.json").exists() or self._has_media_artifact(task_dir)
        has_raw_transcript = (task_dir / "transcript.srt").exists()
        has_polished_transcript = (task_dir / "transcript_polished.srt").exists()

        retained: set[str] = set()
        if has_download_artifact:
            retained.add("download")
        if has_raw_transcript or has_polished_transcript:
            retained.update({"download", "separate", "transcribe", "voiceprint"})
        if has_polished_transcript:
            retained.add("polish")
        return [step for step in ordered if step in retained]

    def _pipeline_order(self, task: Any) -> list[str]:
        configured = [str(step) for step in (getattr(task, "steps", None) or []) if step]
        if configured:
            return configured
        return ["download", "separate", "transcribe", "voiceprint", "polish", "analyze", "archive"]

    def _checkpoint_progress(self, completed_steps: list[str]) -> float:
        total = 7
        return min(1.0, max(0.0, len(completed_steps) / total))

    def _next_pipeline_step(self, completed_steps: list[str]) -> str | None:
        completed = set(completed_steps)
        for step in ["download", "separate", "transcribe", "voiceprint", "polish", "analyze", "archive"]:
            if step not in completed:
                return step
        return "archive"

    def _read_task_metadata(self, task_dir: Path) -> dict[str, Any]:
        try:
            meta_path = task_dir / "metadata.json"
            if meta_path.exists():
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            log_event(logger, logging.DEBUG, "task.metadata.read_failed", path=task_dir, exc_info=True)
        return {}

    def _has_media_artifact(self, task_dir: Path) -> bool:
        media_suffixes = {
            ".mp4", ".mkv", ".mov", ".webm", ".avi",
            ".mp3", ".m4a", ".wav", ".flac", ".ogg",
            ".jpg", ".jpeg", ".png", ".webp",
        }
        try:
            for path in task_dir.rglob("*"):
                if path.is_file() and path.suffix.lower() in media_suffixes:
                    return True
        except Exception:
            log_event(logger, logging.DEBUG, "task.media_artifact.scan_failed", path=task_dir, exc_info=True)
        return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        from app.core.settings import get_runtime_settings
        n_dl = get_runtime_settings().max_download_concurrency

        # Restore stale tasks from DB
        store = get_task_store()
        from app.models.task import TaskStatus as TS
        from app.core.pipeline import PipelineStep
        stale = store.list_by_statuses([TS.QUEUED, TS.PROCESSING])
        for task in stale:
            if task.status == TS.PROCESSING:
                store.update_status(task.id, TS.QUEUED, message="已重新排队")
            # If download was already done, skip straight to GPU queue
            completed = set(task.completed_steps or [])
            if PipelineStep.DOWNLOAD in completed:
                # Check if this was a fast-path task that already finished LLM steps.
                # If TRANSCRIBE+ANALYZE+POLISH are done but ARCHIVE isn't, it was a
                # fast-path task interrupted during video download — send to download
                # queue to re-download the video, not GPU queue.
                fast_path_done = {PipelineStep.TRANSCRIBE, PipelineStep.ANALYZE, PipelineStep.POLISH}
                t_token = set_task_context(str(task.id))
                try:
                    if fast_path_done.issubset(completed) and PipelineStep.ARCHIVE not in completed:
                        await self._download_queue.put(task.id)
                        log_event(
                            logger,
                            logging.INFO,
                            "queue.download.restored",
                            reason="fast_path_redownload",
                            depth=self._download_queue.qsize(),
                        )
                    else:
                        await self._gpu_queue.put(task.id)
                        log_event(
                            logger,
                            logging.INFO,
                            "queue.gpu.restored",
                            reason="download_done",
                            depth=self._gpu_queue.qsize(),
                        )
                finally:
                    reset_context(t_token, task_id_var)
            else:
                t_token = set_task_context(str(task.id))
                try:
                    await self._download_queue.put(task.id)
                    log_event(
                        logger,
                        logging.INFO,
                        "queue.download.restored",
                        reason="restart",
                        depth=self._download_queue.qsize(),
                    )
                finally:
                    reset_context(t_token, task_id_var)

        # Start download workers
        for i in range(n_dl):
            t = asyncio.create_task(
                self._download_worker(worker_index=i + 1), name=f"dl-worker-{i + 1}"
            )
            self._download_worker_tasks.append(t)

        # Start single GPU worker
        self._gpu_worker_task = asyncio.create_task(
            self._gpu_worker(), name="gpu-worker"
        )

        log_event(logger, logging.INFO, "queue.started", download_workers=n_dl, gpu_workers=1)

    async def stop(self) -> None:
        self._running = False
        for t in self._download_worker_tasks:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._download_worker_tasks.clear()

        if self._gpu_worker_task:
            self._gpu_worker_task.cancel()
            try:
                await self._gpu_worker_task
            except asyncio.CancelledError:
                pass
            self._gpu_worker_task = None

        log_event(logger, logging.INFO, "queue.stopped")

    # ------------------------------------------------------------------
    # VRAM flush
    # ------------------------------------------------------------------

    async def _maybe_flush_all_models(self) -> None:
        """If queue is fully drained, release all GPU models to free VRAM."""
        if (
            self._download_queue.empty()
            and self._gpu_queue.empty()
            and not self._active_download_ids
            and self._active_gpu_id is None
        ):
            log_event(logger, logging.INFO, "queue.drained", action="release_gpu_models")
            await asyncio.to_thread(_flush_gpu_models)

    # ------------------------------------------------------------------
    # Workers
    # ------------------------------------------------------------------

    async def _download_worker(self, worker_index: int = 0) -> None:
        """Pull tasks, run DOWNLOAD step, then hand off to GPU queue."""
        worker_name = f"dl-{worker_index}"
        w_token = set_worker_context(worker_name)
        try:
            while self._running:
                try:
                    task_id = await asyncio.wait_for(self._download_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break

                store = get_task_store()
                task = store.get(task_id)
                if not task or task.status == TaskStatus.CANCELLED:
                    self._download_queue.task_done()
                    continue

                self._active_download_ids.add(task_id)
                t_token = set_task_context(str(task_id))
                log_event(logger, logging.INFO, "queue.download.started")

                try:
                    if self._pipeline_fn:
                        running = asyncio.create_task(self._pipeline_fn(task_id, True))
                        self._running_tasks[task_id] = running
                        await running  # download-worker call
                except asyncio.CancelledError:
                    log_event(logger, logging.INFO, "queue.download.cancelled")
                except Exception:
                    log_event(logger, logging.ERROR, "queue.download.failed", exc_info=True)
                finally:
                    self._running_tasks.pop(task_id, None)
                    reset_context(t_token, task_id_var)
                    self._active_download_ids.discard(task_id)
                    self._download_queue.task_done()
                    await self._maybe_flush_all_models()
        finally:
            reset_context(w_token, worker_var)

    async def _gpu_worker(self) -> None:
        """Pull downloaded tasks and run GPU + LLM steps sequentially."""
        w_token = set_worker_context("gpu-1")
        try:
            while self._running:
                try:
                    task_id = await asyncio.wait_for(self._gpu_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break

                store = get_task_store()
                task = store.get(task_id)
                if not task or task.status == TaskStatus.CANCELLED:
                    self._gpu_queue.task_done()
                    continue

                # Serial mode: wait until all downloads are idle before using GPU
                from app.core.settings import get_runtime_settings
                if not get_runtime_settings().pipeline_overlap:
                    waited = 0
                    while self._active_download_ids and self._running:
                        if waited == 0:
                            log_event(
                                logger,
                                logging.INFO,
                                "queue.gpu.waiting_for_downloads",
                                active_downloads=len(self._active_download_ids),
                            )
                        await asyncio.sleep(0.5)
                        waited += 1

                self._active_gpu_id = task_id
                t_token = set_task_context(str(task_id))
                log_event(logger, logging.INFO, "queue.gpu.started")

                try:
                    if self._pipeline_fn:
                        running = asyncio.create_task(self._pipeline_fn(task_id, False))
                        self._running_tasks[task_id] = running
                        await running  # gpu-worker call
                except asyncio.CancelledError:
                    log_event(logger, logging.INFO, "queue.gpu.cancelled")
                except Exception:
                    log_event(logger, logging.ERROR, "queue.gpu.failed", exc_info=True)
                finally:
                    self._running_tasks.pop(task_id, None)
                    reset_context(t_token, task_id_var)
                    self._active_gpu_id = None
                    self._gpu_queue.task_done()
                    await self._maybe_flush_all_models()
        finally:
            reset_context(w_token, worker_var)

    # ------------------------------------------------------------------
    # Called by pipeline to hand a task from download → GPU queue
    # ------------------------------------------------------------------

    async def advance_to_gpu(self, task_id: UUID) -> None:
        """Move a task from download stage to the GPU queue."""
        await self._gpu_queue.put(task_id)
        log_event(logger, logging.INFO, "queue.gpu.enqueued", depth=self._gpu_queue.qsize())

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def get_queue_snapshot(self) -> list[UUID]:
        dl = list(self._download_queue._queue)   # type: ignore[attr-defined]
        gpu = list(self._gpu_queue._queue)        # type: ignore[attr-defined]
        return dl + gpu


# Singleton
_task_queue: TaskQueue | None = None


def get_task_queue() -> TaskQueue:
    global _task_queue
    if _task_queue is None:
        _task_queue = TaskQueue()
    return _task_queue
