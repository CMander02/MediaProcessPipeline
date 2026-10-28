"""Thin HTTP wrappers for virtual library organization."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from typing import Literal

from app.core.library import get_library_service
from app.core.workspace_lifecycle import run_in_thread

router = APIRouter(prefix='/library', tags=['library'])


class FolderName(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class MemberMove(BaseModel):
    content_keys: list[str] = Field(min_length=1, max_length=100)
    folder_id: str | None


async def _call(function, *args, **kwargs):
    try:
        return await run_in_thread(function, *args, **kwargs)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409 if str(exc) == 'content_deleting' else 400, str(exc)) from exc


async def _mutate(function, *args):
    result = await _call(function, *args)
    from app.core.events import TaskEvent, get_event_bus
    # This invalidates a shared catalog; it has no task timeline to persist into.
    await get_event_bus().publish(TaskEvent('library', 'library_changed'), persist=False)
    return result


@router.get('/folders')
async def folders():
    return await _call(get_library_service().directory)


@router.post('/folders')
async def create_folder(body: FolderName):
    return await _mutate(get_library_service().create, body.name)


@router.patch('/folders/{folder_id}')
async def rename_folder(folder_id: str, body: FolderName):
    return await _mutate(get_library_service().rename, folder_id, body.name)


@router.delete('/folders/{folder_id}')
async def delete_folder(folder_id: str):
    return await _mutate(get_library_service().delete, folder_id)


class FolderCover(BaseModel):
    content_key: str | None = Field(default=None, max_length=4096)


@router.put('/folders/{folder_id}/cover')
async def set_folder_cover(folder_id: str, body: FolderCover):
    return await _mutate(get_library_service().set_cover, folder_id, body.content_key)


@router.get('/folders/{folder_id}/avatar')
async def folder_avatar(folder_id: str):
    from fastapi import Response
    data = await _call(get_library_service().folder_avatar, folder_id)
    return Response(data, media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=86400'})


@router.post('/folder-members/move')
async def move_members(body: MemberMove):
    return await _mutate(get_library_service().move, body.content_keys, body.folder_id)


@router.get('/contents/resolve')
async def resolve_content(content_key: str):
    return await _call(get_library_service().resolve, content_key)


@router.get('/contents/task/{task_id}')
async def task_content(task_id: str):
    return await _call(get_library_service().key_for_task, task_id)


class BatchScope(BaseModel):
    type: Literal['selected', 'folder']
    folder_id: str | None = None
    content_keys: list[str] = Field(default_factory=list, max_length=10000)

    @model_validator(mode='after')
    def valid_scope(self):
        if self.type == 'folder' and not self.folder_id:
            raise ValueError('folder_id is required')
        if self.type == 'selected' and not self.content_keys:
            raise ValueError('content_keys are required')
        return self


class BatchPreview(BaseModel):
    action: Literal['retry', 'checkpoint_rerun', 'full_rerun', 'delete', 'export', 'retention', 'move']
    scope: BatchScope


class ContentDelete(BaseModel):
    operation_id: str = Field(min_length=1, max_length=100)
    content_key: str = Field(min_length=1, max_length=4096)
    folder_id: str | None = None
    task_ids: list[str] = Field(default_factory=list, max_length=10000)
    archive_ids: list[str] = Field(default_factory=list, max_length=10000)
    records: int = Field(ge=0)


@router.post('/batch/preview')
async def preview_batch(body: BatchPreview):
    from app.core.library_batch import get_library_batch_service
    return await _call(get_library_batch_service().preview, body.action, body.scope.model_dump())


@router.post('/contents/delete')
async def delete_content(body: ContentDelete):
    from app.core.library_batch import get_library_batch_service, LibraryPreviewChanged
    try:
        return await get_library_batch_service().delete_content(body.model_dump())
    except LibraryPreviewChanged as exc:
        raise HTTPException(409, str(exc)) from exc
