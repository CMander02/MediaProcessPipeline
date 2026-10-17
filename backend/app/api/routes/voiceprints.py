"""Voiceprint library HTTP API.

Endpoints:
  GET    /voiceprints/persons               — list all persons
  PATCH  /voiceprints/persons/{id}          — rename / edit notes
  DELETE /voiceprints/persons/{id}          — delete person + samples
  POST   /voiceprints/persons/{id}/merge    — merge src into dst
  GET    /voiceprints/samples/{id}/clip     — stream audio clip
  PATCH  /tasks/{task_id}/speakers          — rename a task speaker (voiceprint-aware)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.database import get_task_store
from app.services.analysis.artifact_sync import sync_speaker_artifacts
from app.services.voiceprint import get_voiceprint_store

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voiceprints"])


# ---- Models ----

class PersonOut(BaseModel):
    id: str
    name: str
    notes: str
    created_at: str
    sample_count: int


class PersonPatch(BaseModel):
    name: str | None = None
    notes: str | None = None


class MergeRequest(BaseModel):
    src_person_id: str


class SpeakerRenameRequest(BaseModel):
    old_name: str
    new_name: str
    # How to resolve when new_name already exists:
    #   "ask"   — server returns 409 + conflict info; client prompts user
    #   "merge" — merge current speaker's person into the existing one
    #   "new"   — keep as a separate person with a disambiguated name
    on_conflict: Literal["ask", "merge", "new"] = "ask"
    expected_srt: str | None = None


class SpeakerRepairRequest(BaseModel):
    # auto: decide pending lines and name anonymous voices on the finished transcript, applied directly.
    mode: Literal["failed", "all", "auto"]


class SpeakerReconcileRequest(BaseModel):
    expected_srt: str


class SpeakerRenameResponse(BaseModel):
    status: Literal["renamed", "merged", "conflict"]
    person_id: str | None = None
    person_name: str | None = None
    # When status == "conflict":
    conflict_person_id: str | None = None
    conflict_person_name: str | None = None
    conflict_sample_count: int | None = None


# ---- Person endpoints ----

@router.get("/voiceprints/persons", response_model=list[PersonOut])
async def list_persons():
    store = get_voiceprint_store()
    return [
        PersonOut(
            id=p.id, name=p.name, notes=p.notes,
            created_at=p.created_at, sample_count=p.sample_count,
        )
        for p in store.list_persons()
    ]


@router.patch("/voiceprints/persons/{person_id}", response_model=PersonOut)
async def patch_person(person_id: str, patch: PersonPatch):
    store = get_voiceprint_store()
    current = store.get_person(person_id)
    if not current:
        raise HTTPException(status_code=404, detail="Person not found")
    if patch.name is not None and patch.name != current.name:
        # Prevent duplicate names silently — caller should use merge endpoint for that
        existing = store.find_person_by_name(patch.name)
        if existing and existing.id != person_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"Name '{patch.name}' already exists",
                    "existing_person_id": existing.id,
                    "existing_sample_count": existing.sample_count,
                },
            )
        store.rename_person(person_id, patch.name)
    # notes support: we don't have an update method yet; add inline
    if patch.notes is not None:
        conn = store._get_conn()
        with store._lock:
            conn.execute("UPDATE persons SET notes = ? WHERE id = ?", (patch.notes, person_id))
            conn.commit()
    refreshed = store.get_person(person_id)
    assert refreshed is not None
    return PersonOut(
        id=refreshed.id, name=refreshed.name, notes=refreshed.notes,
        created_at=refreshed.created_at, sample_count=refreshed.sample_count,
    )


@router.delete("/voiceprints/persons/{person_id}")
async def delete_person(person_id: str):
    store = get_voiceprint_store()
    if not store.get_person(person_id):
        raise HTTPException(status_code=404, detail="Person not found")
    store.delete_person(person_id)
    return {"success": True}


@router.post("/voiceprints/persons/{dst_person_id}/merge", response_model=PersonOut)
async def merge_persons(dst_person_id: str, req: MergeRequest):
    store = get_voiceprint_store()
    dst = store.get_person(dst_person_id)
    src = store.get_person(req.src_person_id)
    if not dst or not src:
        raise HTTPException(status_code=404, detail="Person not found")
    if dst.id == src.id:
        raise HTTPException(status_code=400, detail="Cannot merge a person into itself")
    store.merge_persons(src_id=req.src_person_id, dst_id=dst_person_id)
    merged = store.get_person(dst_person_id)
    assert merged is not None
    return PersonOut(
        id=merged.id, name=merged.name, notes=merged.notes,
        created_at=merged.created_at, sample_count=merged.sample_count,
    )


@router.get("/voiceprints/samples/{sample_id}/clip")
async def get_sample_clip(sample_id: str):
    store = get_voiceprint_store()
    clip = store.clip_path_for_sample(sample_id)
    if not clip or not Path(clip).exists():
        raise HTTPException(status_code=404, detail="Sample clip not found")
    return FileResponse(clip, media_type="audio/wav")


# ---- Task speaker rename (voiceprint-aware) ----

@router.post("/tasks/{task_id}/speakers/repair")
async def repair_task_speakers(task_id: UUID, req: SpeakerRepairRequest):
    from app.core.queue import get_task_queue
    try:
        queued = await get_task_queue().repair_speakers(task_id, req.mode)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not queued:
        raise HTTPException(status_code=409, detail="任务完成后才能补全人物" if req.mode == "auto" else "当前任务状态不能启动人物复核")
    message = ("将补全待定字幕和人物姓名，你改过的名字保持不变" if req.mode == "auto"
               else "将生成候选清单，已有字幕需手动确认修改")
    return {"status": "queued", "mode": req.mode, "message": message}


@router.get("/tasks/{task_id}/speakers/repair-preview")
async def speaker_repair_preview(task_id: UUID):
    from app.services.analysis.speaker_repair import repair_directory
    task = get_task_store().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    path = repair_directory(task) / "speaker_repair.preview.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="人物复核候选尚未生成")
    preview = json.loads(path.read_text(encoding="utf-8"))
    return {key: value for key, value in preview.items() if key not in {"baseline_srt", "input_srt"}}


@router.post("/tasks/{task_id}/speakers/reconcile")
async def reconcile_task_speakers(task_id: UUID, req: SpeakerReconcileRequest):
    from app.services.analysis.speaker_reconciliation import reconcile_speakers
    from app.core.events import get_event_bus, TaskEvent
    task = get_task_store().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        result = reconcile_speakers(task, req.expected_srt)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await get_event_bus().publish(TaskEvent(task_id, "file_ready", {"file": "metadata.json"}))
    return result

@router.post("/tasks/{task_id}/speakers/identify")
async def identify_task_speaker_names(task_id: UUID):
    if get_task_store().get(task_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    raise HTTPException(status_code=409, detail="人物自动识别在润色前完成；已有结果请手动编辑人物名称")


@router.patch("/tasks/{task_id}/speakers", response_model=SpeakerRenameResponse)
async def rename_task_speaker(task_id: UUID, req: SpeakerRenameRequest):
    """Rename a speaker for a task, propagating to the voiceprint library.

    Flow:
      1. Look up task_speaker_map[task_id, old_name] → person_id
      2. Synchronize the final name to every generated artifact and SQLite mirror.
      3. If linked, check whether new_name already exists in the library:
         - no conflict → rename person
         - conflict + on_conflict == "ask"   → return 409-ish body
         - conflict + on_conflict == "merge" → merge current person into existing
         - conflict + on_conflict == "new"   → disambiguate current name with suffix
    """
    new_name = req.new_name.strip()
    if not new_name or any(ord(c) < 32 or c in "[]<>" for c in new_name):
        raise HTTPException(status_code=400, detail="人物名称为空或含有无效字符")
    if req.old_name.strip().casefold() == "unknown" or new_name.casefold() == "unknown":
        raise HTTPException(status_code=409, detail="归属待定的片段不能作为同一个人整体改名")
    task = get_task_store().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if getattr(task, "status", "completed") != "completed":
        raise HTTPException(status_code=409, detail="请等待任务完成后编辑人物")
    result = task.result or {}
    output_dir = result.get("output_dir") or (result.get("archive") or {}).get("output_dir")
    if req.expected_srt is not None:
        directory = Path(output_dir) if output_dir else None
        source = next((directory / name for name in ("transcript_polished.srt", "transcript.srt")
                       if (directory / name).is_file()), None) if directory else None
        if source is None or source.read_text(encoding="utf-8") != req.expected_srt:
            raise HTTPException(status_code=409, detail="字幕已更新，请刷新后重试，当前编辑没有保存")
    store = get_voiceprint_store()

    def sync_artifacts(final_name: str) -> None:
        task = get_task_store().get(task_id)
        result = task.result if task and task.result else {}
        output_dir = result.get("output_dir")
        if not output_dir and isinstance(result.get("archive"), dict):
            output_dir = result["archive"].get("output_dir")
        if output_dir:
            changed = sync_speaker_artifacts(
                str(task_id), output_dir, req.old_name, final_name
            )
            if changed:
                logger.info(
                    "Synchronized speaker rename task=%s old=%s new=%s files=%s",
                    task_id,
                    req.old_name,
                    final_name,
                    ",".join(changed),
                )

    mapping = store.get_task_speaker(str(task_id), req.old_name)
    if not mapping:
        mapping = next(
            (
                item
                for item in store.list_task_speakers(str(task_id))
                if item.get("person_name") == req.old_name
            ),
            None,
        )
    if not mapping:
        # Text inference names only this task. Resolve its stable acoustic label
        # when a later manual rename confirms a voiceprint identity.
        task = get_task_store().get(task_id)
        result = task.result if task and task.result else {}
        output_dir = result.get("output_dir") or (result.get("archive") or {}).get("output_dir")
        map_path = Path(output_dir) / "speaker_map.json" if output_dir else None
        if map_path and map_path.is_file():
            speaker_map = json.loads(map_path.read_text(encoding="utf-8"))
            for item in speaker_map.get("mappings", []):
                if item.get("current_name") != req.old_name:
                    continue
                source_label = item.get("source_label")
                mapping = store.get_task_speaker(str(task_id), source_label)
                if not mapping:
                    mapping = next((
                        person for person in store.list_task_speakers(str(task_id))
                        if person.get("person_name") == source_label
                    ), None)
                if mapping:
                    break
    if not mapping:
        logger.info(
            "No voiceprint mapping for task=%s speaker=%s; skipping library update",
            task_id,
            req.old_name,
        )
        sync_artifacts(new_name)
        return SpeakerRenameResponse(status="renamed", person_id=None, person_name=new_name)

    person_id = mapping["person_id"]
    current = store.get_person(person_id)
    if current and current.name == new_name:
        sync_artifacts(new_name)
        return SpeakerRenameResponse(status="renamed", person_id=person_id, person_name=new_name)

    existing = store.find_person_by_name(new_name)
    if existing and existing.id != person_id:
        if req.on_conflict == "ask":
            return SpeakerRenameResponse(
                status="conflict",
                person_id=person_id,
                person_name=current.name if current else None,
                conflict_person_id=existing.id,
                conflict_person_name=existing.name,
                conflict_sample_count=existing.sample_count,
            )
        elif req.on_conflict == "merge":
            # Merge current (src) into existing (dst)
            store.merge_persons(src_id=person_id, dst_id=existing.id)
            sync_artifacts(existing.name)
            return SpeakerRenameResponse(
                status="merged",
                person_id=existing.id,
                person_name=existing.name,
            )
        else:  # "new"
            suffix = hex(hash(str(task_id)) & 0xFFF)[2:]
            disambiguated = f"{new_name} ({suffix})"
            store.rename_person(person_id, disambiguated)
            sync_artifacts(disambiguated)
            return SpeakerRenameResponse(
                status="renamed",
                person_id=person_id,
                person_name=disambiguated,
            )

    # No conflict — direct rename
    store.rename_person(person_id, new_name)
    sync_artifacts(new_name)
    return SpeakerRenameResponse(status="renamed", person_id=person_id, person_name=new_name)
