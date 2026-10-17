"""Fixed library operation previews and recoverable removal of every content attempt."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import UUID, uuid4

from app.core.database import _db_lock, _get_conn, get_task_store
from app.core.library import catalog, changed, require_folder, _now
from app.core.workspace_lifecycle import run_in_thread, uses_workspace

_ACTIVE = {'pending', 'queued', 'processing'}
_RANK = {'processing': 5, 'queued': 5, 'pending': 5, 'completed': 4, 'paused': 3, 'failed': 2, 'cancelled': 1}


class LibraryPreviewChanged(ValueError):
    pass


def _task_ids(entry):
    return sorted(str(task.id) for task in entry['tasks'])


def _archive_ids(entry):
    return sorted(item['archive_id'] for item in entry['archives'])


def _record_count(entry):
    ids = set(_task_ids(entry))
    return len(ids) + sum(str(item.get('task_id') or '') not in ids for item in entry['archives'])


def _representative(entry):
    from app.core.archive_sync import ArchiveSyncService
    from app.core.database import _database_root

    tasks = {str(task.id): task for task in entry['tasks']}
    archives = sorted(entry['archives'], key=lambda item: (
        _RANK.get(str((item.get('metadata') or {}).get('status') or ('processing' if item.get('processing') else 'completed')), 0),
        str(item.get('created_at') or ''), str(item.get('archive_id') or ''),
    ), reverse=True)
    if archives:
        item = ArchiveSyncService.decode_snapshot(archives[0], _database_root())
        task = tasks.get(str(item.get('task_id') or ''))
    else:
        item = None
        task = max(entry['tasks'], key=lambda task: (_RANK.get(str(task.status), 0), task.created_at), default=None)
    return item, task


class LibraryBatchService:
    def __init__(self):
        self._delete_lock = asyncio.Lock()

    @uses_workspace
    def preview(self, action, scope):
        from app.core.archive_sync import get_archive_sync_service

        get_archive_sync_service().flush_changes()
        with _db_lock:
            conn = _get_conn()
            contents = catalog(conn)
            members = {row['content_key']: row['folder_id'] for row in conn.execute('SELECT * FROM library_folder_members')}
            pending = {row['content_key']: json.loads(row['targets']) for row in conn.execute('SELECT * FROM library_deletions')}
            if scope['type'] == 'folder':
                require_folder(conn, scope['folder_id'])
                keys = sorted(key for key in (contents.keys() | pending.keys()) if members.get(key) == scope['folder_id'])
            else:
                keys = list(dict.fromkeys(scope.get('content_keys') or []))
            operation_id = str(uuid4())
            targets, skipped = [], []
            records = active_records = 0
            for key in keys:
                entry = contents.get(key)
                if key in pending and action == 'delete':
                    target = {**pending[key], 'operation_id': operation_id, 'folder_id': members.get(key)}
                    targets.append(target)
                    records += target['records']
                    active_records += sum(str(task.status) in _ACTIVE for task in (entry or {}).get('tasks', []))
                    continue
                if not entry:
                    skipped.append({'content_key': key, 'reason': 'content_not_found'})
                    continue
                records += _record_count(entry)
                active_records += sum(str(task.status) in _ACTIVE for task in entry['tasks'])
                item, task = _representative(entry)
                status = str(task.status) if task else (item.get('metadata') or {}).get('status', 'completed')
                target = {'content_key': key, 'folder_id': members.get(key), 'operation_id': operation_id,
                          'title': (item or {}).get('title') or (task.source if task else key),
                          'task_ids': _task_ids(entry), 'archive_ids': _archive_ids(entry),
                          'records': _record_count(entry), 'task_id': str(task.id) if task else None,
                          'status': status, 'archive': item}
                reason = None
                if conn.execute('SELECT 1 FROM library_deletions WHERE content_key=?', (key,)).fetchone() and action != 'delete':
                    reason = 'content_deleting'
                elif action in {'retry', 'checkpoint_rerun', 'full_rerun'}:
                    if not task:
                        reason = 'no_task'
                    elif status in _ACTIVE:
                        reason = 'task_active'
                    elif action == 'retry' and status not in {'paused', 'failed'}:
                        reason = 'not_retryable'
                elif action in {'export', 'retention'} and not item:
                    reason = 'no_archive'
                if reason:
                    skipped.append({**target, 'reason': reason})
                else:
                    targets.append(target)
            return {'operation_id': operation_id, 'action': action, 'contents': len(keys),
                    'records': records, 'active_records': active_records, 'runnable': len(targets),
                    'targets': targets, 'skipped_items': skipped}

    def _prepare_delete(self, target):
        from app.core.paths import task_output_paths
        from app.core.database import _database_root
        from app.core.archive_sync import ArchiveSyncService

        with _db_lock, _get_conn() as conn:
            key = target['content_key']
            pending = conn.execute('SELECT * FROM library_deletions WHERE content_key=?', (key,)).fetchone()
            if pending:
                if pending['operation_id'] != target['operation_id']:
                    # A new preview may resume the same durable deletion, but cannot widen it.
                    old = json.loads(pending['targets'])
                    if set(target['task_ids']) - set(old['task_ids']) or set(target['archive_ids']) - set(old['archive_ids']):
                        raise LibraryPreviewChanged('删除范围已改变，请重新预览')
                return dict(pending)
            contents = catalog(conn)
            entry = contents.get(key, {'tasks': [], 'archives': []})
            member = conn.execute('SELECT folder_id FROM library_folder_members WHERE content_key=?', (key,)).fetchone()
            folder_id = member[0] if member else None
            if folder_id != target.get('folder_id'):
                raise LibraryPreviewChanged('文件夹归属已改变，请重新预览')
            confirmed_tasks, confirmed_archives = set(target['task_ids']), set(target['archive_ids'])
            current_tasks, current_archives = set(_task_ids(entry)), set(_archive_ids(entry))
            if current_tasks - confirmed_tasks or current_archives - confirmed_archives:
                raise LibraryPreviewChanged('新增了处理记录，请重新预览')
            # Existing IDs supplied by a client must all belong to this content.
            for other_key, other in contents.items():
                if other_key != key and (confirmed_tasks.intersection(_task_ids(other)) or confirmed_archives.intersection(_archive_ids(other))):
                    raise LibraryPreviewChanged('记录不属于所选内容，请重新预览')
            paths = {path.resolve() for task in entry['tasks'] for path in task_output_paths(task)}
            paths.update(Path(ArchiveSyncService.decode_snapshot(item, _database_root())['path']).resolve() for item in entry['archives'])
            for other_key, other in contents.items():
                if other_key != key and any(path.resolve() in paths for task in other['tasks'] for path in task_output_paths(task)):
                    raise LibraryPreviewChanged('输出目录被其他内容使用，请先解决目录冲突')
            saved_target = {**target, 'task_ids': sorted(current_tasks), 'archive_ids': sorted(current_archives),
                            'archive_records': {item['archive_id']: (
                                f"task:{item['task_id']}" if str(item.get('task_id') or '') in current_tasks
                                else f"archive:{item['archive_id']}"
                            ) for item in entry['archives']}}
            conn.execute('INSERT INTO library_deletions VALUES (?,?,?,?,?)',
                         (key, target['operation_id'], json.dumps(saved_target, ensure_ascii=False), folder_id, _now()))
            return dict(conn.execute('SELECT * FROM library_deletions WHERE content_key=?', (key,)).fetchone())

    @uses_workspace
    async def delete_content(self, target):
        async with self._delete_lock:
            entry = await run_in_thread(self._prepare_delete, target)
            return await self._finish_delete(entry)

    async def _finish_delete(self, entry):
        from app.core.archive_lifecycle import get_archive_lifecycle
        from app.core.archive_sync import get_archive_sync_service, ArchiveSyncService
        from app.core.database import _database_root
        from app.core.queue import get_task_queue

        target = json.loads(entry['targets'])
        errors = []
        for task_id in target['task_ids']:
            try:
                result = await get_task_queue().delete(UUID(task_id))
                if result and result.get('errors'):
                    errors.extend(result['errors'])
            except Exception as exc:
                errors.append({'task_id': task_id, 'error': str(exc)})
        # Standalone archives and extra historical archive directories are server-resolved.
        for archive_id in target['archive_ids']:
            if errors:
                break
            row = _get_conn().execute('SELECT snapshot FROM archive_sync_index WHERE archive_id=? AND deleted=0', (archive_id,)).fetchone()
            if not row:
                continue
            item = ArchiveSyncService.decode_snapshot(json.loads(row[0]), _database_root())
            try:
                await run_in_thread(get_archive_lifecycle().delete, item['path'])
            except FileNotFoundError:
                await run_in_thread(get_archive_sync_service()._record_delete, archive_id)
            except Exception as exc:
                errors.append({'archive_id': archive_id, 'error': str(exc)})
        if not errors:
            # Retry a staged standalone archive even if its index tombstone is already published.
            pending = [dict(row) for row in _get_conn().execute('SELECT * FROM archive_deletions').fetchall()
                       if set(json.loads(row['task_ids'])).intersection(target['task_ids'])
                       or set(json.loads(row['archive_ids'])).intersection(target['archive_ids'])]
            for physical in pending:
                try:
                    await run_in_thread(get_archive_lifecycle().delete, physical['original_path'])
                except Exception as exc:
                    errors.append({'path': physical['original_path'], 'error': str(exc)})
        await run_in_thread(get_archive_sync_service().flush_changes)
        return await run_in_thread(self._deletion_result, entry, errors)

    def _deletion_result(self, entry, errors):
        with _db_lock, _get_conn() as conn:
            target = json.loads(entry['targets'])
            current = catalog(conn).get(entry['content_key'], {'tasks': [], 'archives': []})
            remaining_tasks, remaining_archives = _task_ids(current), _archive_ids(current)
            pending_physical = [dict(row) for row in conn.execute('SELECT * FROM archive_deletions')
                                if set(json.loads(row['task_ids'])).intersection(target['task_ids'])
                                or set(json.loads(row['archive_ids'])).intersection(target['archive_ids'])]
            complete = not remaining_tasks and not remaining_archives and not pending_physical
            folder_released = False
            if complete:
                row = conn.execute('SELECT folder_id FROM library_folder_members WHERE content_key=?', (entry['content_key'],)).fetchone()
                folder_released = bool(row and row[0])
                conn.execute('DELETE FROM library_folder_members WHERE content_key=?', (entry['content_key'],))
                conn.execute('DELETE FROM library_deletions WHERE content_key=?', (entry['content_key'],))
                changed(conn)
            record_ids = {f'task:{task_id}' for task_id in remaining_tasks}
            archive_records = target.get('archive_records') or {}
            record_ids.update(archive_records.get(archive_id, f'archive:{archive_id}') for archive_id in remaining_archives)
            for physical in pending_physical:
                record_ids.update(f'task:{task_id}' for task_id in json.loads(physical['task_ids']) if task_id in target['task_ids'])
                record_ids.update(archive_records.get(archive_id, f'archive:{archive_id}') for archive_id in json.loads(physical['archive_ids']))
            remaining_records = len(record_ids)
            original_records = target['records']
            return {'content_key': entry['content_key'], 'outcome': 'deleted' if complete else 'partial',
                    'deleted_records': max(0, original_records - remaining_records),
                    'remaining_records': remaining_records, 'folder_released': folder_released,
                    'remaining_task_ids': remaining_tasks, 'remaining_archive_ids': remaining_archives,
                    'errors': errors}

    @uses_workspace
    async def recover(self):
        failures = []
        for row in _get_conn().execute('SELECT * FROM library_deletions ORDER BY created_at').fetchall():
            async with self._delete_lock:
                try:
                    result = await self._finish_delete(dict(row))
                    if result['outcome'] != 'deleted':
                        failures.append(result)
                except Exception as exc:
                    failures.append({'content_key': row['content_key'], 'error': str(exc)})
        return failures


_service = LibraryBatchService()


def get_library_batch_service():
    return _service
