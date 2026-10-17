"""Single-level virtual folders; physical media paths never change here."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

from app.core.content_identity import archive_content_key, task_content_key
from app.core.database import _db_lock, _get_conn, _row_to_task
from app.core.workspace_lifecycle import uses_workspace


def _now():
    return datetime.now().astimezone().isoformat()


def revision(conn) -> int:
    return conn.execute('SELECT revision FROM library_meta WHERE id=1').fetchone()[0]


def changed(conn):
    conn.execute('UPDATE library_meta SET revision=revision+1 WHERE id=1')


def processing_time(item=None, task=None) -> str:
    """Processing timestamps stay independent of folder edits and file modification times."""
    from app.core.archive_index import timestamp

    if task:
        values = [task.created_at.isoformat(), task.updated_at.isoformat()]
        if task.completed_at:
            values.append(task.completed_at.isoformat())
    else:
        item = item or {}
        values = [item.get('last_processed_at') or '', item.get('created_at') or '']
    return max(values, key=timestamp)


def content_processing_time(entry) -> str:
    from app.core.archive_index import timestamp

    times = [processing_time(task=task) for task in entry['tasks']]
    times.extend(processing_time(item=item) for item in entry['archives'])
    return max(times, key=timestamp, default='')


def _cover_item(key, entry):
    """The archive whose thumbnail stands for one content entry."""
    from copy import deepcopy
    from app.core.library_batch import _representative

    # Decoding paths mutates snapshots; the catalog stays reusable.
    item, _ = _representative({'archives': deepcopy(entry['archives']), 'tasks': entry['tasks']})
    if not item or not item.get('path'):
        return None
    return {'content_key': key, 'archive_id': item.get('archive_id'), 'path': item['path'],
            'title': str(item.get('title') or '')}


def folder_directory(conn, contents=None):
    from app.core.archive_index import timestamp

    contents = catalog(conn) if contents is None else contents
    mapping = {row['content_key']: row['folder_id'] for row in conn.execute('SELECT * FROM library_folder_members')}
    folders = {row['id']: {**dict(row), 'count': 0, 'last_processed_at': ''}
               for row in conn.execute('SELECT * FROM library_folders')}
    chosen = {row['folder_id']: row['content_key'] for row in conn.execute('SELECT folder_id,content_key FROM library_folder_covers')}
    collections = {row['collection_key']: dict(row) for row in conn.execute(
        'SELECT collection_key,title,owner_name,owner_avatar_url,checked_at,avatar IS NOT NULL AS cached '
        'FROM library_collections')}
    members: dict[str, list] = {}
    for key, entry in contents.items():
        folder = folders.get(mapping.get(key))
        if not folder:
            continue
        folder['count'] += 1
        latest = content_processing_time(entry)
        members.setdefault(folder['id'], []).append((timestamp(latest), key))
        if timestamp(latest) > timestamp(folder['last_processed_at']):
            folder['last_processed_at'] = latest
    for folder_id, folder in folders.items():
        # A chosen cover leads; the newest contents follow, so a folder shows what arrived last.
        ranked = [key for _, key in sorted(members.get(folder_id, []), reverse=True)]
        cover = chosen.get(folder_id) if chosen.get(folder_id) in ranked else None
        previews = []
        for key in ([cover] if cover else []) + [key for key in ranked if key != cover]:
            item = _cover_item(key, contents[key])
            if item:
                previews.append(item)
            if len(previews) == 3:
                break
        folder['cover_content_key'] = cover
        folder['previews'] = previews
        collection_key = folder.get('source_collection_key')
        if collection_key:
            meta = collections.get(collection_key)
            folder['collection'] = {
                'title': meta['title'] if meta else '',
                'owner_name': meta['owner_name'] if meta else '',
                'avatar': _avatar_state(collection_key, meta),
            }
    return sorted(folders.values(), key=lambda folder: (-timestamp(folder['last_processed_at']), folder['id']))


_AVATAR_RETRY_SECONDS = 6 * 3600
_AVATAR_MAX_BYTES = 4 * 1024 * 1024


def _avatar_state(collection_key, meta) -> str:
    """cached: stored image; available: can be fetched on request; none: nothing to show."""
    from app.core.archive_index import timestamp

    if meta and meta['cached']:
        return 'cached'
    # A failed lookup or download waits before the next attempt.
    if meta and datetime.now().timestamp() - timestamp(meta['checked_at']) < _AVATAR_RETRY_SECONDS:
        return 'none'
    if (meta and meta['owner_avatar_url']) or collection_key.startswith('bilibili:'):
        return 'available'
    return 'none'


def _collection_video(conn, collection_key) -> str | None:
    """A Bilibili video id from the collection, used to read its owner."""
    import re

    if not collection_key.startswith('bilibili:'):
        return None
    if not collection_key.startswith('bilibili:ugc:'):
        return collection_key.split(':', 1)[1]
    keys = [row[0] for row in conn.execute(
        'SELECT content_key FROM library_collection_members WHERE collection_key=? ORDER BY content_key LIMIT 5',
        (collection_key,))]
    keys += [row[0] for row in conn.execute(
        'SELECT m.content_key FROM library_folder_members m JOIN library_folders f ON f.id=m.folder_id '
        'WHERE f.source_collection_key=? LIMIT 5', (collection_key,))]
    return next((match[1] for key in keys if (match := re.match(r'bilibili:(BV[0-9A-Za-z]+)', key))), None)


def _bilibili_owner(bvid: str) -> dict:
    from app.services.ingestion.platform.bilibili.api import view

    data = view(bvid)
    owner = data.get('owner') or {}
    season = data.get('ugc_season') or {}
    return {'title': str(season.get('title') or data.get('title') or ''), 'owner_name': str(owner.get('name') or ''),
            'owner_avatar_url': str(owner.get('face') or '')}


def _download_avatar(url: str) -> bytes:
    import io
    import urllib.request
    from urllib.parse import urlparse
    from PIL import Image, ImageOps
    from app.core.public_urls import public_urlopen as urllib_urlopen

    url = f'https:{url}' if url.startswith('//') else url
    parsed = urlparse(url)
    if parsed.scheme not in {'http', 'https'} or not (parsed.hostname or '').endswith(('hdslb.com', 'bilibili.com', 'biliimg.com')):
        raise ValueError('unsupported_avatar_host')
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/'})
    with urllib_urlopen(request, timeout=12) as response:
        data = response.read(_AVATAR_MAX_BYTES + 1)
    if len(data) > _AVATAR_MAX_BYTES:
        raise ValueError('avatar_too_large')
    with Image.open(io.BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode not in {'RGB', 'L'}:
            image = image.convert('RGB')
        image.thumbnail((160, 160), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, 'JPEG', quality=85, optimize=True)
    return output.getvalue()


def task_is_deleting(task) -> bool:
    with _db_lock:
        conn = _get_conn()
        pending = {row[0] for row in conn.execute('SELECT content_key FROM library_deletions')}
        if task_content_key(task) in pending:
            return True
        if not pending:
            return False
        for row in conn.execute('SELECT archive_id,snapshot FROM archive_sync_index WHERE task_id=? AND deleted=0', (str(task.id),)):
            item = json.loads(row['snapshot'])
            item['archive_id'] = row['archive_id']
            if archive_content_key(item, task) in pending:
                return True
        return False


def require_folder(conn, folder_id):
    row = conn.execute('SELECT * FROM library_folders WHERE id=?', (folder_id,)).fetchone()
    if not row:
        raise LookupError('folder_not_found')
    return dict(row)


def catalog(conn) -> dict:
    """Include historical tasks without visible archives, as well as standalone archives."""
    tasks = {str(row['id']): _row_to_task(row) for row in conn.execute('SELECT * FROM tasks')}
    contents = {}
    linked = {}
    for row in conn.execute('SELECT archive_id,task_id,snapshot FROM archive_sync_index WHERE deleted=0'):
        item = json.loads(row['snapshot'])
        item['archive_id'] = row['archive_id']
        task_id = str(item.get('task_id') or row['task_id'] or '')
        task = tasks.get(task_id)
        key = archive_content_key(item, task)
        entry = contents.setdefault(key, {'archives': [], 'tasks': []})
        entry['archives'].append(item)
        if task:
            from app.core.task_import import _archive_identity
            if _archive_identity(task, item.get('metadata') or {})[1]:
                linked[task_id] = key
    for task_id, task in tasks.items():
        key = linked.get(task_id) or task_content_key(task)
        contents.setdefault(key, {'archives': [], 'tasks': []})['tasks'].append(task)
    return contents


def assign_import(conn, key, folder_id=None, *, explicit=False, move_existing=False):
    """Automatic imports respect manual choices, including an explicit move out."""
    if conn.execute('SELECT 1 FROM library_deletions WHERE content_key=?', (key,)).fetchone():
        raise ValueError('content_deleting')
    previous = conn.execute('SELECT folder_id,origin FROM library_folder_members WHERE content_key=?', (key,)).fetchone()
    if not explicit:
        targets = conn.execute(
            'SELECT DISTINCT f.id FROM library_folders f JOIN library_collection_members c '
            "ON c.collection_key=f.source_collection_key WHERE c.content_key=? OR "
            "(f.source_collection_key LIKE 'bilibili:ugc:%' AND c.content_key LIKE ?)",
            (key, key.rsplit(':p', 1)[0] + ':p%' if key.startswith('bilibili:') else key),
        ).fetchall()
        if len(targets) != 1:
            return {'folder_id': previous['folder_id'] if previous else None,
                    'folder_outcome': 'ambiguous' if targets else 'unchanged' if previous else 'unclassified'}
        folder_id = targets[0]['id']
    if folder_id is not None:
        require_folder(conn, folder_id)
    if previous and previous['folder_id'] == folder_id:
        return {'folder_id': folder_id, 'folder_outcome': 'unchanged'}
    if previous and not move_existing:
        return {'folder_id': previous['folder_id'], 'folder_outcome': 'kept_other'}
    conn.execute(
        'INSERT INTO library_folder_members VALUES (?,?,?,?) ON CONFLICT(content_key) DO UPDATE '
        'SET folder_id=excluded.folder_id,origin=excluded.origin,added_at=excluded.added_at',
        (key, folder_id, 'manual' if explicit else 'collection', _now()),
    )
    changed(conn)
    return {'folder_id': folder_id, 'folder_outcome': 'moved'}


class LibraryService:
    @uses_workspace
    def decorate(self, item):
        from app.core.database import get_task_store
        from uuid import UUID

        task_id = item.get('task_id') or (item.get('metadata') or {}).get('task_id')
        task = get_task_store().get(UUID(task_id)) if task_id else None
        key = archive_content_key(item, task)
        with _db_lock:
            row = _get_conn().execute('SELECT folder_id FROM library_folder_members WHERE content_key=?', (key,)).fetchone()
            return {**item, 'content_key': key, 'folder_id': row[0] if row else None}

    @uses_workspace
    def resolve(self, key):
        from app.core.archive_sync import get_archive_sync_service
        from app.core.library_batch import _representative

        get_archive_sync_service().flush_changes()
        with _db_lock:
            conn = _get_conn()
            entry = catalog(conn).get(key)
            if not entry:
                raise LookupError('content_not_found')
            item, task = _representative(entry)
            row = conn.execute('SELECT folder_id FROM library_folder_members WHERE content_key=?', (key,)).fetchone()
            folder_id = row[0] if row else None
            if item:
                item = {**item, 'content_key': key, 'folder_id': folder_id}
            return {'content_key': key, 'folder_id': folder_id, 'archive': item,
                    'task_id': str(task.id) if task else None}

    @uses_workspace
    def has_collection_folders(self):
        with _db_lock:
            return bool(_get_conn().execute('SELECT 1 FROM library_folders WHERE source_collection_key IS NOT NULL LIMIT 1').fetchone())

    @uses_workspace
    def key_for_task(self, task_id):
        with _db_lock:
            for key, entry in catalog(_get_conn()).items():
                if any(str(task.id) == task_id for task in entry['tasks']):
                    return {'content_key': key}
        raise LookupError('content_not_found')

    @uses_workspace
    def knows_collection_content(self, key):
        with _db_lock:
            return bool(_get_conn().execute(
                'SELECT 1 FROM library_folders f JOIN library_collection_members c ON c.collection_key=f.source_collection_key '
                "WHERE c.content_key=? OR (f.source_collection_key LIKE 'bilibili:ugc:%' AND c.content_key LIKE ?) LIMIT 1",
                (key, key.rsplit(':p', 1)[0] + ':p%' if key.startswith('bilibili:') else key),
            ).fetchone())

    @uses_workspace
    def directory(self):
        from pathlib import Path
        from app.core.settings import get_runtime_settings
        with _db_lock:
            conn = _get_conn()
            contents = catalog(conn)
            mapping = {row['content_key']: row['folder_id'] for row in conn.execute('SELECT * FROM library_folder_members')}
            folders = folder_directory(conn, contents)
            archive_keys = {item['archive_id']: key for key, entry in contents.items() for item in entry['archives']}
            return {'workspace_id': str(Path(get_runtime_settings().data_root).resolve()),
                    'folders': folders, 'members': mapping, 'archive_keys': archive_keys,
                    'processed_at': {key: content_processing_time(entry) for key, entry in contents.items()},
                    'unclassified_count': sum(not mapping.get(key) for key in contents),
                    'library_revision': revision(conn)}

    @uses_workspace
    def create(self, name: str):
        name = name.strip()
        if not name or len(name) > 120:
            raise ValueError('文件夹名称须为1至120个字符')
        with _db_lock, _get_conn() as conn:
            folder = {'id': str(uuid4()), 'name': name, 'source_collection_key': None,
                      'created_at': _now(), 'updated_at': _now()}
            conn.execute('INSERT INTO library_folders VALUES (?,?,?,?,?)', tuple(folder.values()))
            changed(conn)
            return folder

    @uses_workspace
    def rename(self, folder_id, name):
        name = name.strip()
        if not name or len(name) > 120:
            raise ValueError('文件夹名称须为1至120个字符')
        with _db_lock, _get_conn() as conn:
            require_folder(conn, folder_id)
            conn.execute('UPDATE library_folders SET name=?,updated_at=? WHERE id=?', (name, _now(), folder_id))
            changed(conn)
            return require_folder(conn, folder_id)

    @uses_workspace
    def delete(self, folder_id):
        with _db_lock, _get_conn() as conn:
            require_folder(conn, folder_id)
            released = conn.execute('SELECT COUNT(*) FROM library_folder_members WHERE folder_id=?', (folder_id,)).fetchone()[0]
            # Keep an explicit unclassified choice so future automatic imports cannot undo it.
            conn.execute("UPDATE library_folder_members SET origin='manual' WHERE folder_id=?", (folder_id,))
            conn.execute('DELETE FROM library_folder_covers WHERE folder_id=?', (folder_id,))
            conn.execute('DELETE FROM library_folders WHERE id=?', (folder_id,))
            changed(conn)
            return {'released': released, 'library_revision': revision(conn)}

    @uses_workspace
    def set_cover(self, folder_id, content_key):
        """Pin one content as the folder cover; None returns to the newest content."""
        with _db_lock, _get_conn() as conn:
            require_folder(conn, folder_id)
            if content_key is None:
                conn.execute('DELETE FROM library_folder_covers WHERE folder_id=?', (folder_id,))
            else:
                row = conn.execute('SELECT folder_id FROM library_folder_members WHERE content_key=?', (content_key,)).fetchone()
                if not row or row[0] != folder_id:
                    raise ValueError('封面只能选用这个文件夹里的内容')
                conn.execute(
                    'INSERT INTO library_folder_covers VALUES (?,?,?) ON CONFLICT(folder_id) DO UPDATE '
                    'SET content_key=excluded.content_key,updated_at=excluded.updated_at',
                    (folder_id, content_key, _now()),
                )
            changed(conn)
            return {'folder_id': folder_id, 'cover_content_key': content_key, 'library_revision': revision(conn)}

    @uses_workspace
    def folder_avatar(self, folder_id) -> bytes:
        """The collection owner's avatar, fetched once from the platform and kept in the library."""
        with _db_lock:
            conn = _get_conn()
            key = require_folder(conn, folder_id).get('source_collection_key')
            if not key:
                raise LookupError('folder_has_no_collection')
            meta = conn.execute('SELECT * FROM library_collections WHERE collection_key=?', (key,)).fetchone()
            meta = dict(meta) if meta else None
            if meta and meta['avatar']:
                return meta['avatar']
            if _avatar_state(key, meta and {**meta, 'cached': False}) == 'none':
                raise LookupError('avatar_unavailable')
            video = _collection_video(conn, key)
        owner = {'title': meta['title'], 'owner_name': meta['owner_name'],
                 'owner_avatar_url': meta['owner_avatar_url']} if meta else {}
        avatar = None
        try:
            if not owner.get('owner_avatar_url') and video:
                owner = {**owner, **_bilibili_owner(video)}
            if owner.get('owner_avatar_url'):
                avatar = _download_avatar(owner['owner_avatar_url'])
        except Exception as exc:
            import logging
            logging.getLogger(__name__).info('Collection avatar unavailable for %s: %s', key, exc)
        with _db_lock, _get_conn() as conn:
            conn.execute(
                'INSERT INTO library_collections VALUES (?,?,?,?,?,?) ON CONFLICT(collection_key) DO UPDATE SET '
                'title=excluded.title,owner_name=excluded.owner_name,owner_avatar_url=excluded.owner_avatar_url,'
                'avatar=excluded.avatar,checked_at=excluded.checked_at',
                (key, owner.get('title') or '', owner.get('owner_name') or '', owner.get('owner_avatar_url') or '',
                 avatar, _now()),
            )
            changed(conn)
        if avatar is None:
            raise LookupError('avatar_unavailable')
        return avatar

    @uses_workspace
    def move(self, keys, folder_id):
        with _db_lock, _get_conn() as conn:
            if folder_id is not None:
                require_folder(conn, folder_id)
            existing = catalog(conn)
            results = []
            for key in dict.fromkeys(keys):
                if key not in existing:
                    results.append({'content_key': key, 'outcome': 'skipped', 'reason': 'content_not_found'})
                    continue
                previous = conn.execute('SELECT folder_id FROM library_folder_members WHERE content_key=?', (key,)).fetchone()
                try:
                    # An unchanged manual move also opts out of later automatic reassignment.
                    result = assign_import(conn, key, folder_id, explicit=True, move_existing=True)
                    conn.execute("UPDATE library_folder_members SET origin='manual' WHERE content_key=?", (key,))
                    results.append({'content_key': key, 'previous_folder_id': previous[0] if previous else None,
                                    'outcome': result['folder_outcome'], **result})
                except ValueError as exc:
                    results.append({'content_key': key, 'outcome': 'failed', 'reason': str(exc)})
            return {'items': results, 'library_revision': revision(conn)}

    @uses_workspace
    def register_collection(self, collection: dict, *, create=False):
        """Only called with platform-inspected metadata, never with client member claims."""
        result = self._register_collection(collection, create=create)
        for related in collection.get('related_collections') or []:
            self._register_collection(related)
        return result

    def _register_collection(self, collection, *, create=False):
        key = collection.get('collection_key')
        if not collection.get('is_collection') or not key:
            if create:
                raise ValueError('无法确认合集身份，请刷新后重试')
            return None
        with _db_lock, _get_conn() as conn:
            row = conn.execute('SELECT * FROM library_folders WHERE source_collection_key=?', (key,)).fetchone()
            if not row and create:
                name = str(collection.get('title') or '合集').strip()[:120] or '合集'
                conn.execute('INSERT INTO library_folders VALUES (?,?,?,?,?)', (str(uuid4()), name, key, _now(), _now()))
                changed(conn)
                row = conn.execute('SELECT * FROM library_folders WHERE source_collection_key=?', (key,)).fetchone()
            if row:
                before = conn.total_changes
                for item in collection.get('items') or []:
                    if item.get('content_key'):
                        conn.execute('INSERT OR IGNORE INTO library_collection_members VALUES (?,?)', (key, item['content_key']))
                owner = collection.get('owner') or {}
                if owner.get('face') or owner.get('name'):
                    # The avatar itself is downloaded when a folder card first asks for it.
                    conn.execute(
                        "INSERT INTO library_collections VALUES (?,?,?,?,NULL,'') ON CONFLICT(collection_key) DO UPDATE SET "
                        'title=excluded.title,owner_name=excluded.owner_name,owner_avatar_url=excluded.owner_avatar_url,'
                        "avatar=CASE WHEN owner_avatar_url=excluded.owner_avatar_url THEN avatar END,"
                        "checked_at=CASE WHEN owner_avatar_url=excluded.owner_avatar_url THEN checked_at ELSE '' END",
                        (key, str(collection.get('title') or ''), str(owner.get('name') or ''), str(owner.get('face') or '')),
                    )
                if conn.total_changes != before:
                    changed(conn)
            return dict(row) if row else None

    @uses_workspace
    def sync_snapshot(self):
        with _db_lock:
            conn = _get_conn()
            contents = catalog(conn)
            mapping = {row['content_key']: row['folder_id'] for row in conn.execute('SELECT * FROM library_folder_members')}
            return {'library_revision': revision(conn),
                    'folders': [dict(row) for row in conn.execute('SELECT * FROM library_folders ORDER BY created_at,id')],
                    'members': [{'content_key': key, 'folder_id': folder_id} for key, folder_id in mapping.items()],
                    'archives': [{'archive_id': item['archive_id'], 'content_key': key, 'folder_id': mapping.get(key)}
                                 for key, entry in contents.items() for item in entry['archives']]}


_service = LibraryService()


def get_library_service():
    return _service
