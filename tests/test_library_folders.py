from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from app.api.routes import library, pipeline, tasks
from app.core import archive_sync, database, settings
from app.core.archive_index import source_key
from app.core.library import get_library_service
from app.models.task import Task, TaskStatus, TaskType
from app.services.ingestion.platform.bilibili import collection as collections

VIDEO = 'https://www.bilibili.com/video/BV1DK4y1b7bY'
OTHER = 'https://www.bilibili.com/video/BV1xx411c7mD'


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, '_runtime_settings', settings.RuntimeSettings(data_root=str(tmp_path), allow_remote_filesystem=True))
    database.reset_db_path(tmp_path)
    database.init_db(tmp_path)
    sync = archive_sync.ArchiveSyncService()
    monkeypatch.setattr(archive_sync, '_sync_service', sync)

    class Queue:
        active_task_ids = set()

        async def submit(self, task_id):
            pass

    monkeypatch.setattr(tasks, 'get_task_queue', Queue)
    monkeypatch.setattr(collections, 'inspect_bilibili_collection', lambda _url: {'is_collection': False})
    app = FastAPI()
    for router in (library.router, pipeline.router, tasks.router):
        app.include_router(router, prefix='/api')
    with TestClient(app) as client:
        yield client, tmp_path, sync
    database.close_db()


def imported(client, sources, **options):
    response = client.post('/api/tasks/import', json={'sources': sources, **options})
    assert response.status_code == 200, response.text
    return response.json()['items']


def folder(client, name='数学 🎤'):
    response = client.post('/api/library/folders', json={'name': name})
    assert response.status_code == 200, response.text
    return response.json()['id']


def directory(client):
    return client.get('/api/library/folders').json()


def test_folder_crud_move_scope_and_restart_preserve_media(api):
    client, root, sync = api
    item = imported(client, [VIDEO])[0]
    key = item['content_key']
    physical = Path(item['task']['result']['output_dir'])
    first = folder(client)
    second = folder(client, '同名可独立创建')
    moved = client.post('/api/library/folder-members/move', json={'folder_id': first, 'content_keys': [key, key, 'missing']}).json()
    assert [x['outcome'] for x in moved['items']] == ['moved', 'skipped']
    sync.flush_changes()
    page = client.get('/api/pipeline/archives', params={'page': 1, 'folder_id': first}).json()
    assert page['total'] == 1 and page['archives'][0]['content_key'] == key
    assert page['archives'][0]['folder_id'] == first
    assert page['facets']['total'] == 1
    assert client.get('/api/pipeline/archives', params={'page': 1, 'unclassified': True}).json()['total'] == 0
    assert client.get('/api/pipeline/archives', params={'page': 1, 'folder_id': 'unknown'}).status_code == 404
    assert client.get('/api/pipeline/archives', params={'page': 1, 'folder_id': first, 'unclassified': True}).status_code == 400
    assert client.patch(f'/api/library/folders/{first}', json={'name': '已改名'}).status_code == 200
    database.close_db()
    database.init_db(root)
    sync.rebuild()
    snapshot = get_library_service().sync_snapshot()
    assert any(x['id'] == second for x in snapshot['folders'])  # Empty folders survive.
    assert snapshot['archives'][0]['folder_id'] == first
    released = client.delete(f'/api/library/folders/{first}').json()
    assert released['released'] == 1
    assert physical.is_dir() and (physical / 'metadata.json').exists()
    assert client.get('/api/pipeline/archives', params={'page': 1, 'unclassified': True}).json()['total'] == 1


def test_all_historical_parts_share_membership_but_other_parts_do_not(api):
    client, root, sync = api
    task = Task(task_type=TaskType.PIPELINE, source=VIDEO + '?p=2', status=TaskStatus.COMPLETED)
    database.get_task_store().save(task)
    for index in range(2):
        path = root / f'old-{index}'
        path.mkdir()
        (path / 'metadata.json').write_text(json.dumps({'title': '旧P2', 'source_url': VIDEO,
            'archive_id': str(uuid4()), 'task_id': str(task.id), 'status': 'completed'}), encoding='utf-8')
    sync.reconcile()
    target = folder(client)
    key = source_key(VIDEO + '?p=2')
    client.post('/api/library/folder-members/move', json={'folder_id': target, 'content_keys': [key]})
    page = sync.list_page(folder_id=target)
    assert page['total'] == 1 and page['archives'][0]['attempts'] == 2
    assert sync.list_page(folder_id=target, duplicates=True)['total'] == 2
    assert imported(client, [VIDEO + '?p=2'])[0]['folder_id'] == target
    assert imported(client, [VIDEO])[0]['folder_id'] is None


def test_collection_folder_creation_auto_merge_and_manual_choices(api, monkeypatch):
    client, _root, _sync = api
    current_members = [VIDEO]

    def inspect(_url):
        return {'is_collection': True, 'collection_key': 'bilibili:ugc:42', 'title': '课程',
                'items': [{'content_key': source_key(url), 'url': url} for url in current_members]}

    monkeypatch.setattr(collections, 'inspect_bilibili_collection', inspect)
    first = imported(client, [VIDEO], collection_folders=[{'source': VIDEO, 'create_folder': True}])[0]
    target = first['folder_id']
    assert target and first['folder_outcome'] == 'moved'
    client.patch(f'/api/library/folders/{target}', json={'name': '自定课程名称'})
    current_members.append(OTHER)  # The registry did not previously contain this video.
    new = imported(client, [OTHER])[0]
    assert new['folder_id'] == target
    assert directory(client)['folders'][0]['name'] == '自定课程名称'
    assert len(directory(client)['folders']) == 1
    client.post('/api/library/folder-members/move', json={'folder_id': None, 'content_keys': [first['content_key']]})
    assert imported(client, [VIDEO])[0]['folder_id'] is None
    manual = folder(client, '手动归类')
    client.post('/api/library/folder-members/move', json={'folder_id': manual, 'content_keys': [new['content_key']]})
    assert imported(client, [OTHER])[0]['folder_id'] == manual
    conflict = imported(client, [OTHER], folder_id=target)[0]
    assert conflict['folder_outcome'] == 'kept_other' and conflict['folder_id'] == manual
    assert imported(client, [OTHER], folder_id=target, move_existing=True)[0]['folder_id'] == target
    # Equal titles do not merge unrelated collection identities.
    monkeypatch.setattr(collections, 'inspect_bilibili_collection', lambda _url: {**inspect(_url), 'collection_key': 'bilibili:ugc:99'})
    imported(client, [VIDEO], collection_folders=[{'source': VIDEO, 'create_folder': True}])
    assert len(directory(client)['folders']) == 3


def test_local_content_identity_stays_stable_and_does_not_merge_by_path(api):
    client, root, sync = api
    source = str(root / '🎤.mp3')
    first = imported(client, [source])[0]
    assert first['content_key'] == f"archive:{first['task']['id']}"
    target = folder(client)
    client.post('/api/library/folder-members/move', json={'folder_id': target, 'content_keys': [first['content_key']]})
    sync.flush_changes()
    assert sync.list_page(folder_id=target)['archives'][0]['content_key'] == first['content_key']
    second = imported(client, [source])[0]
    assert second['content_key'] != first['content_key']


@pytest.mark.parametrize('url', ['https://youtube.com/watch?v=abc', 'https://youtu.be/abc',
                               'https://youtube.com/shorts/abc', 'https://youtube.com/live/abc'])
def test_youtube_identities(url):
    assert source_key(url) == 'youtube:abc'


def test_import_unknown_target_does_not_create_task_or_directory(api):
    client, _root, _sync = api
    result = imported(client, [VIDEO], folder_id='missing')[0]
    assert result['outcome'] == 'failed'
    assert database.get_task_store().count() == 0


def test_collection_inspection_failure_keeps_import_and_reports_folder_error(api, monkeypatch):
    client, _root, _sync = api
    monkeypatch.setattr(collections, 'inspect_bilibili_collection', lambda _url: (_ for _ in ()).throw(ValueError('暂时无法读取合集')))
    result = imported(client, [VIDEO], collection_folders=[{'source': VIDEO, 'create_folder': True}])[0]
    assert result['outcome'] == 'created' and result['folder_error']
    assert not directory(client)['folders']


def test_selected_collection_folder_takes_priority_over_current_folder(api, monkeypatch):
    client, _root, _sync = api
    manual = folder(client, '当前文件夹')
    monkeypatch.setattr(collections, 'inspect_bilibili_collection', lambda _url: {
        'is_collection': True, 'collection_key': 'bilibili:ugc:123', 'title': '课程合集',
        'items': [{'url': VIDEO, 'content_key': source_key(VIDEO)}],
    })
    result = imported(client, [VIDEO], folder_id=manual,
                      collection_folders=[{'source': VIDEO, 'create_folder': True}])[0]
    created = next(item for item in directory(client)['folders'] if item['source_collection_key'])
    assert result['folder_id'] == created['id'] != manual
    task_id = result['task']['id']
    assert client.get(f'/api/library/contents/task/{task_id}').json()['content_key'] == result['content_key']


def test_directory_snapshot_has_workspace_and_identity_mapping(api):
    client, root, _sync = api
    target = folder(client)
    result = imported(client, [VIDEO], folder_id=target)[0]
    snapshot = directory(client)
    assert snapshot['workspace_id'] == str(root.resolve())
    assert snapshot['members'][result['content_key']] == target
