from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from test_library_folders import api, VIDEO, imported, folder
from app.core import database, queue as queue_module
from app.core.archive_index import source_key
from app.core.library import get_library_service
from app.core.library_batch import LibraryBatchService
from app.core.archive_lifecycle import ArchiveBusyError
from app.models.task import Task, TaskStatus, TaskType


def preview(client, keys=None, folder_id=None, action='delete'):
    scope = {'type': 'folder', 'folder_id': folder_id} if folder_id else {'type': 'selected', 'content_keys': keys}
    response = client.post('/api/library/batch/preview', json={'scope': scope, 'action': action})
    assert response.status_code == 200, response.text
    return response.json()


def real_queue(monkeypatch):
    queue = queue_module.TaskQueue()
    monkeypatch.setattr(queue_module, 'get_task_queue', lambda: queue)
    return queue


def standalone(root, sync, source=VIDEO):
    path = root / ('standalone-' + uuid4().hex)
    path.mkdir()
    identity = str(uuid4())
    (path / 'metadata.json').write_text(json.dumps({'title': '无任务归档', 'archive_id': identity,
        'source_url': source, 'status': 'completed'}), encoding='utf-8')
    sync.reconcile()
    return path, identity


def test_delete_all_attempts_and_standalone_keeps_folder(api, monkeypatch):
    client, root, sync = api
    real_queue(monkeypatch)
    first = imported(client, [VIDEO])[0]
    path = Path(first['task']['result']['output_dir'])
    database.get_task_store().update_status(UUID(first['task']['id']), TaskStatus.PAUSED)
    old = Task(task_type=TaskType.PIPELINE, source=VIDEO, status=TaskStatus.FAILED)
    database.get_task_store().save(old)  # This historical task has no visible archive.
    standalone_path, archive_id = standalone(root, sync)
    target_folder = folder(client)
    get_library_service().move([first['content_key']], target_folder)
    data = preview(client, folder_id=target_folder)
    assert data['contents'] == 1 and data['records'] == 3
    assert set(data['targets'][0]['task_ids']) == {str(old.id), first['task']['id']}
    assert archive_id in data['targets'][0]['archive_ids']
    response = client.post('/api/library/contents/delete', json=data['targets'][0])
    assert response.status_code == 200, response.text
    assert response.json()['outcome'] == 'deleted' and response.json()['deleted_records'] == 3
    assert not path.exists() and not standalone_path.exists()
    assert database.get_task_store().count() == 0
    assert sync.list_page()['total'] == 0
    assert get_library_service().directory()['folders'][0]['id'] == target_folder
    assert get_library_service().directory()['folders'][0]['count'] == 0


def test_deleted_physical_output_failure_retains_membership_and_can_resume(api, monkeypatch):
    from app.core import archive_lifecycle

    client, root, sync = api
    real_queue(monkeypatch)
    _path, _id = standalone(root, sync)
    key = source_key(VIDEO)
    target_folder = folder(client)
    get_library_service().move([key], target_folder)
    target = preview(client, folder_id=target_folder)['targets'][0]
    remove = archive_lifecycle.shutil.rmtree
    monkeypatch.setattr(archive_lifecycle.shutil, 'rmtree', lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError('占用')))
    failed = client.post('/api/library/contents/delete', json=target).json()
    assert failed['outcome'] == 'partial' and failed['remaining_records'] == 1
    assert not failed['folder_released']
    assert database._get_conn().execute('SELECT folder_id FROM library_folder_members WHERE content_key=?', (key,)).fetchone()[0] == target_folder
    # Even if the archive tombstone is already published, retry preview includes durable pending work.
    retry = preview(client, folder_id=target_folder)['targets'][0]
    assert retry['content_key'] == key
    monkeypatch.setattr(archive_lifecycle.shutil, 'rmtree', remove)
    success = client.post('/api/library/contents/delete', json=retry).json()
    assert success['outcome'] == 'deleted' and success['folder_released']
    assert not database._get_conn().execute('SELECT * FROM library_deletions').fetchall()


def test_preview_rejects_new_attempt_or_membership_change_and_unrelated_ids(api, monkeypatch):
    client, _root, _sync = api
    real_queue(monkeypatch)
    first = imported(client, [VIDEO])[0]
    target = preview(client, [first['content_key']])['targets'][0]
    extra = Task(task_type=TaskType.PIPELINE, source=VIDEO, status=TaskStatus.FAILED)
    database.get_task_store().save(extra)
    assert client.post('/api/library/contents/delete', json=target).status_code == 409
    target = preview(client, [first['content_key']])['targets'][0]
    other = Task(task_type=TaskType.PIPELINE, source='https://example.com/other', status=TaskStatus.FAILED)
    database.get_task_store().save(other)
    bad = {**target, 'task_ids': [*target['task_ids'], str(other.id)]}
    assert client.post('/api/library/contents/delete', json=bad).status_code == 409
    get_library_service().move([first['content_key']], folder(client))
    assert client.post('/api/library/contents/delete', json=target).status_code == 409
    assert database.get_task_store().count() == 3


def test_whole_folder_preview_is_independent_of_page_and_skips_active(api):
    client, _root, _sync = api
    target_folder = folder(client)
    keys = []
    for index in range(130):
        task = Task(task_type=TaskType.PIPELINE, source=f'https://example.com/video?id={index}',
                    status=TaskStatus.QUEUED if index < 3 else TaskStatus.COMPLETED)
        database.get_task_store().save(task)
        keys.append(source_key(task.source))
    for start in range(0, len(keys), 100):
        get_library_service().move(keys[start:start+100], target_folder)
    data = preview(client, folder_id=target_folder, action='full_rerun')
    assert data['contents'] == data['records'] == 130
    assert data['runnable'] == 127 and len(data['skipped_items']) == 3
    assert all(item['reason'] == 'task_active' for item in data['skipped_items'])


def test_old_local_archive_identity_links_owner_even_when_id_differs(api, monkeypatch):
    client, root, sync = api
    real_queue(monkeypatch)
    source = str(root / 'old-source.mp3')
    path, identity = standalone(root, sync, source)
    task = Task(task_type=TaskType.PIPELINE, source=source, status=TaskStatus.PAUSED, result={'output_dir': str(path)})
    database.get_task_store().save(task)
    sync.reconcile()
    key = f'archive:{identity}'
    target = preview(client, [key])['targets'][0]
    assert target['task_ids'] == [str(task.id)] and target['records'] == 1
    assert client.post('/api/library/contents/delete', json=target).json()['outcome'] == 'deleted'
    assert database.get_task_store().get(task.id) is None


def test_stop_timeout_cannot_delete_still_writing_outputs(api, monkeypatch):
    client, _root, _sync = api
    item = imported(client, [VIDEO])[0]
    path = Path(item['task']['result']['output_dir'])

    async def scenario():
        queue = real_queue(monkeypatch)
        task_id = UUID(item['task']['id'])
        drained = asyncio.Event()
        started = asyncio.Event()

        async def writer():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await drained.wait()

        running = asyncio.create_task(writer())
        queue._running_tasks[task_id] = running
        await started.wait()
        wait = queue_module.asyncio.wait

        async def short_wait(futures, **_kwargs):
            return await wait(futures, timeout=0.01)

        monkeypatch.setattr(queue_module.asyncio, 'wait', short_wait)
        with pytest.raises(ArchiveBusyError):
            await queue.delete(task_id)
        assert path.exists() and database.get_task_store().get(task_id) is not None
        drained.set()
        await running
        await queue.delete(task_id)
        assert not path.exists()

    asyncio.run(scenario())


def test_delete_intent_blocks_import_and_rerun_and_restart_recovers(api, monkeypatch):
    client, root, sync = api
    real_queue(monkeypatch)
    item = imported(client, [VIDEO])[0]
    target = preview(client, [item['content_key']])['targets'][0]
    batch = LibraryBatchService()
    batch._prepare_delete(target)  # Simulate interruption just after the durable intent.
    assert imported(client, [VIDEO])[0]['outcome'] == 'failed'
    database.get_task_store().update_status(UUID(item['task']['id']), TaskStatus.FAILED)
    assert asyncio.run(queue_module.get_task_queue().rerun_full(UUID(item['task']['id']))) is False
    database.close_db()
    database.init_db(root)
    assert asyncio.run(batch.recover()) == []
    assert not Path(item['task']['result']['output_dir']).exists()
    assert not database._get_conn().execute('SELECT * FROM library_deletions').fetchall()
