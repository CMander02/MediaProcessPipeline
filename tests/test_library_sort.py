from datetime import datetime
import json
from uuid import uuid4

from test_library_folders import api, folder  # noqa: F401 - shared isolated API fixture
from app.core import database
from app.core.content_identity import task_content_key
from app.models.task import Task, TaskStatus, TaskType


def processed_archive(root, title, day, *, source=None, status=TaskStatus.COMPLETED, created_day=1):
    archive_id = str(uuid4())
    path = root / title
    path.mkdir()
    task = Task(task_type=TaskType.PIPELINE, source=source or f'https://youtube.com/watch?v={archive_id}',
                status=status, created_at=datetime(2026, 9, created_day), updated_at=datetime(2026, 9, day),
                completed_at=datetime(2026, 9, day) if status == TaskStatus.COMPLETED else None,
                result={'output_dir': str(path)})
    database.get_task_store().save(task)
    (path / 'metadata.json').write_text(json.dumps({'archive_id': archive_id, 'task_id': str(task.id),
        'title': title, 'source_url': task.source, 'status': status, 'created_at': task.created_at.isoformat()}), encoding='utf-8')
    return task, archive_id


def root_page(client, **params):
    response = client.get('/api/pipeline/archives', params={'page': 1, 'unclassified': True,
                                                          'include_folders': True, **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_mixed_processing_order_is_applied_before_pagination(api):
    client, root, sync = api
    a, a_id = processed_archive(root, 'newest', 10, created_day=2)
    b, _b_id = processed_archive(root, 'folder member 9', 9)
    c, c_id = processed_archive(root, 'middle', 8, created_day=5)
    d, _d_id = processed_archive(root, 'folder member 7', 7)
    _e, e_id = processed_archive(root, 'old processing', 6, status=TaskStatus.PROCESSING)
    sync.reconcile()
    first = folder(client, 'Folder 9')
    second = folder(client, 'Folder 7')
    for task, target in [(b, first), (d, second)]:
        assert client.post('/api/library/folder-members/move', json={
            'content_keys': [task_content_key(task)], 'folder_id': target}).status_code == 200
    expected = [('archive', a_id), ('folder', first), ('archive', c_id), ('folder', second), ('archive', e_id)]
    seen = []
    for page in range(1, 4):
        result = root_page(client, page=page, page_size=2)
        assert result['total'] == 5 and result['archive_total'] == 3
        assert result['facets']['total'] == 5
        seen.extend((entry['kind'], entry['id']) for entry in result['entries'])
    assert seen == expected  # Neither the folder kind nor processing status gets priority.
    assert root_page(client, page=999, page_size=2)['page'] == 3
    assert root_page(client, search='Folder 9')['entries'] == [{'kind': 'folder', 'id': first}]
    assert sync.list_page(sort='created_desc')['archives'][0]['archive_id'] == e_id  # Existing explicit order keeps its behavior.


def test_moves_and_renames_keep_processing_time_and_empty_folders_sort_last(api):
    client, root, sync = api
    task, archive_id = processed_archive(root, 'content', 8)
    sync.reconcile()
    target = folder(client, 'empty')
    assert root_page(client)['entries'] == [{'kind': 'archive', 'id': archive_id}, {'kind': 'folder', 'id': target}]
    key = task_content_key(task)
    client.post('/api/library/folder-members/move', json={'content_keys': [key], 'folder_id': target})
    time = root_page(client)['folders'][0]['last_processed_at']
    client.patch(f'/api/library/folders/{target}', json={'name': 'renamed'})
    assert root_page(client)['folders'][0]['last_processed_at'] == time == task.updated_at.isoformat()
    client.post('/api/library/folder-members/move', json={'content_keys': [key], 'folder_id': None})
    page = root_page(client)
    assert page['archives'][0]['last_processed_at'] == time
    assert page['folders'][0]['last_processed_at'] == ''
    assert page['entries'][-1] == {'kind': 'folder', 'id': target}


def test_latest_failed_attempt_updates_order_without_replacing_successful_result(api):
    client, root, sync = api
    source = 'https://youtube.com/watch?v=repeat'
    successful, successful_id = processed_archive(root, 'successful', 8, source=source)
    processed_archive(root, 'later failure', 11, source=source, status=TaskStatus.FAILED)
    _other, other_id = processed_archive(root, 'other', 10)
    sync.reconcile()
    page = root_page(client)
    assert [entry['id'] for entry in page['entries']] == [successful_id, other_id]
    assert page['archives'][0]['last_processed_at'] == datetime(2026, 9, 11).isoformat()
    target = folder(client, 'attempts')
    client.post('/api/library/folder-members/move', json={'content_keys': [task_content_key(successful)], 'folder_id': target})
    page = root_page(client)
    assert page['entries'][0] == {'kind': 'folder', 'id': target}
    assert page['folders'][0]['last_processed_at'] == datetime(2026, 9, 11).isoformat()
