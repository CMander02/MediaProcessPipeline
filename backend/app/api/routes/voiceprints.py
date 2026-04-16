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

import logging
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

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

@router.patch("/tasks/{task_id}/speakers", response_model=SpeakerRenameResponse)
async def rename_task_speaker(task_id: UUID, req: SpeakerRenameRequest):
    """Rename a speaker for a task, propagating to the voiceprint library.

    Flow:
      1. Look up task_speaker_map[task_id, old_name] → person_id
      2. If no voiceprint linkage exists (legacy task), fall back to "name-only"
         semantics: just succeed. SRT update is the caller's responsibility
         via PATCH /api/filesystem/write (kept for backward compat).
      3. If linked, check whether new_name already exists in the library:
         - no conflict → rename person
         - conflict + on_conflict == "ask"   → return 409-ish body
         - conflict + on_conflict == "merge" → merge current person into existing
         - conflict + on_conflict == "new"   → disambiguate current name with suffix
    """
    store = get_voiceprint_store()
    new_name = req.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="new_name is empty")

    mapping = store.get_task_speaker(str(task_id), req.old_name)
    if not mapping:
        # No voiceprint linkage for this (task, speaker) — treat as successful no-op
        # so legacy SRT-based rename still works.
        logger.info(f"No voiceprint mapping for task={task_id} speaker={req.old_name}; skipping library update")
        return SpeakerRenameResponse(status="renamed", person_id=None, person_name=new_name)

    person_id = mapping["person_id"]
    current = store.get_person(person_id)
    if current and current.name == new_name:
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
            return SpeakerRenameResponse(status="merged", person_id=existing.id, person_name=existing.name)
        else:  # "new"
            suffix = hex(hash(str(task_id)) & 0xFFF)[2:]
            disambiguated = f"{new_name} ({suffix})"
            store.rename_person(person_id, disambiguated)
            return SpeakerRenameResponse(status="renamed", person_id=person_id, person_name=disambiguated)

    # No conflict — direct rename
    store.rename_person(person_id, new_name)
    return SpeakerRenameResponse(status="renamed", person_id=person_id, person_name=new_name)
