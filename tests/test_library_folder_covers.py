from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from app.api.routes import library, pipeline, tasks
from app.core import archive_sync, database, settings
from app.core import library as library_core
from app.core.archive_index import source_key
from app.services.ingestion.platform.bilibili import collection as collections

VIDEO = 'https://www.bilibili.com/video/BV1DK4y1b7bY'
OTHER = 'https://www.bilibili.com/video/BV1xx411c7mD'
THIRD = 'https://www.bilibili.com/video/BV1GJ411x7h7'


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
        yield client, sync
    database.close_db()


def imported(client, sources, **options):
    response = client.post('/api/tasks/import', json={'sources': sources, **options})
    assert response.status_code == 200, response.text
    return response.json()['items']


def only_folder(client):
    folders = client.get('/api/library/folders').json()['folders']
    assert len(folders) == 1
    return folders[0]


def test_cover_follows_newest_content_until_one_is_chosen(api):
    client, sync = api
    target = client.post('/api/library/folders', json={'name': '课程'}).json()['id']
    first, second = (imported(client, [url])[0] for url in (VIDEO, OTHER))
    outside = imported(client, [THIRD])[0]
    keys = [first['content_key'], second['content_key']]
    client.post('/api/library/folder-members/move', json={'folder_id': target, 'content_keys': keys})
    sync.flush_changes()

    folder = only_folder(client)
    assert folder['cover_content_key'] is None
    assert [item['content_key'] for item in folder['previews']] == [keys[1], keys[0]]
    assert all(item['path'] for item in folder['previews'])
    revision = client.get('/api/library/folders').json()['library_revision']

    chosen = client.put(f'/api/library/folders/{target}/cover', json={'content_key': keys[0]})
    assert chosen.status_code == 200 and chosen.json()['library_revision'] > revision
    folder = only_folder(client)
    assert folder['cover_content_key'] == keys[0]
    assert [item['content_key'] for item in folder['previews']] == keys
    # The root grid reads the same folder entries.
    root = client.get('/api/pipeline/archives', params={'page': 1, 'unclassified': True, 'include_folders': True}).json()
    assert root['folders'][0]['previews'][0]['content_key'] == keys[0]

    rejected = client.put(f'/api/library/folders/{target}/cover', json={'content_key': outside['content_key']})
    assert rejected.status_code == 400
    # A chosen cover that leaves the folder falls back to the newest content.
    client.post('/api/library/folder-members/move', json={'folder_id': None, 'content_keys': [keys[0]]})
    folder = only_folder(client)
    assert folder['cover_content_key'] is None
    assert [item['content_key'] for item in folder['previews']] == [keys[1]]
    assert client.put(f'/api/library/folders/{target}/cover', json={'content_key': None}).status_code == 200
    assert client.delete(f'/api/library/folders/{target}').status_code == 200
    assert client.put(f'/api/library/folders/{target}/cover', json={'content_key': None}).status_code == 404


def test_collection_avatar_is_downloaded_once_and_failures_wait(api, monkeypatch):
    client, _sync = api
    monkeypatch.setattr(collections, 'inspect_bilibili_collection', lambda _url: {
        'is_collection': True, 'collection_key': 'bilibili:ugc:42', 'title': '节目',
        'items': [{'content_key': source_key(VIDEO), 'url': VIDEO}],
        'owner': {'name': '瓜熟迪落拉', 'face': 'https://i0.hdslb.com/bfs/face/a.jpg'},
    })
    imported(client, [VIDEO], collection_folders=[{'source': VIDEO, 'create_folder': True}])
    folder = only_folder(client)
    assert folder['collection'] == {'title': '节目', 'owner_name': '瓜熟迪落拉', 'avatar': 'available'}

    downloads = []
    monkeypatch.setattr(library_core, '_download_avatar', lambda url: downloads.append(url) or b'jpeg')
    monkeypatch.setattr(library_core, '_bilibili_owner', lambda _video: pytest.fail('owner already known'))
    for _ in range(2):
        response = client.get(f"/api/library/folders/{folder['id']}/avatar")
        assert response.status_code == 200 and response.content == b'jpeg'
    assert downloads == ['https://i0.hdslb.com/bfs/face/a.jpg']
    assert only_folder(client)['collection']['avatar'] == 'cached'


def test_old_collection_folder_looks_up_owner_and_backs_off_after_failure(api, monkeypatch):
    client, _sync = api
    monkeypatch.setattr(collections, 'inspect_bilibili_collection', lambda _url: {
        'is_collection': True, 'collection_key': 'bilibili:ugc:7', 'title': '旧合集',
        'items': [{'content_key': source_key(VIDEO), 'url': VIDEO}],
    })
    imported(client, [VIDEO], collection_folders=[{'source': VIDEO, 'create_folder': True}])
    folder = only_folder(client)
    assert folder['collection']['avatar'] == 'available'
    videos = []

    def owner(video):
        videos.append(video)
        raise OSError('offline')

    monkeypatch.setattr(library_core, '_bilibili_owner', owner)
    assert client.get(f"/api/library/folders/{folder['id']}/avatar").status_code == 404
    assert videos == ['BV1DK4y1b7bY']
    assert only_folder(client)['collection']['avatar'] == 'none'
    assert client.get(f"/api/library/folders/{folder['id']}/avatar").status_code == 404
    assert videos == ['BV1DK4y1b7bY']  # No second request inside the retry window.
