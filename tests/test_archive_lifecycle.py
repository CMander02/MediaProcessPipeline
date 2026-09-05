from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import database, settings
from app.core.archive_lifecycle import ArchiveBusyError, ArchiveLifecycle
from app.core.archive_sync import get_archive_sync_service
from app.models import Task, TaskStatus, TaskType


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "_runtime_settings", settings.RuntimeSettings(data_root=str(tmp_path))
    )
    database.reset_db_path(tmp_path)
    directory = tmp_path / "访谈 🎙"
    directory.mkdir()
    task = Task(
        task_type=TaskType.PIPELINE,
        source="test.wav",
        status=TaskStatus.COMPLETED,
        result={"output_dir": str(directory)},
    )
    database.get_task_store().save(task)
    (directory / "metadata.json").write_text(
        json.dumps({"title": "访谈", "task_id": str(task.id)}), encoding="utf-8"
    )
    (directory / "transcript.txt").write_text("interview", encoding="utf-8")
    yield directory, task, ArchiveLifecycle()
    database.close_db()


def pending():
    return database._get_conn().execute("SELECT * FROM archive_deletions").fetchall()


def test_locked_rename_preserves_records_and_retry(archive, monkeypatch):
    directory, task, lifecycle = archive
    original = Path.rename

    def locked(path, destination):
        if path == directory:
            raise PermissionError("file in use")
        return original(path, destination)

    monkeypatch.setattr(Path, "rename", locked)
    with pytest.raises(PermissionError):
        lifecycle.delete(directory)
    assert directory.is_dir()
    assert database.get_task_store().get(task.id)
    assert len(pending()) == 1
    monkeypatch.setattr(Path, "rename", original)
    assert lifecycle.recover() == []
    assert not directory.exists()
    assert database.get_task_store().get(task.id) is None
    assert not pending()


@pytest.mark.parametrize("failure_stage", ["references", "task", "files"])
def test_interruption_recovers_after_database_reopen(archive, monkeypatch, failure_stage):
    import app.core.archive_lifecycle as module

    directory, task, lifecycle = archive
    store = database.get_task_store()

    def fail(*args):
        raise OSError("injected interruption")

    with monkeypatch.context() as fault:
        if failure_stage == "references":
            fault.setattr(lifecycle, "_clear_references", fail)
        elif failure_stage == "task":
            fault.setattr(store, "delete", fail)
        else:
            fault.setattr(module.shutil, "rmtree", fail)
        with pytest.raises(OSError):
            lifecycle.delete(directory)
    assert not directory.exists()
    assert len(pending()) == 1
    database.close_db()
    assert lifecycle.recover() == []
    assert not pending()
    assert database.get_task_store().get(task.id) is None
    assert list((directory.parent / "_deleting").iterdir()) == []
    assert lifecycle.recover() == []


def test_retry_checks_new_active_owner(archive, monkeypatch):
    directory, task, lifecycle = archive
    with monkeypatch.context() as fault:
        fault.setattr(Path, "rename", lambda *args: (_ for _ in ()).throw(PermissionError()))
        with pytest.raises(PermissionError):
            lifecycle.delete(directory)
    task.status = TaskStatus.PROCESSING
    database.get_task_store().save(task)
    with pytest.raises(ArchiveBusyError):
        lifecycle.delete(directory)
    assert directory.is_dir()


def test_sync_tombstone_is_published_once(archive):
    directory, task, lifecycle = archive
    sync = get_archive_sync_service()
    sync.reconcile()
    archive_id = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))["archive_id"]
    lifecycle.delete(directory)
    lifecycle.recover()
    rows = (
        database._get_conn()
        .execute("SELECT operation FROM archive_sync_changes WHERE archive_id = ?", (archive_id,))
        .fetchall()
    )
    assert [r[0] for r in rows] == ["upsert", "delete"]


def test_delete_clears_kb_and_preserves_named_voice_samples(archive):
    import numpy as np
    from app.services.kb import store as kb
    from app.services.voiceprint import store as voice

    directory, task, lifecycle = archive
    kb.reset_kb_store()
    voice.reset_voiceprint_store()
    try:
        knowledge = kb.get_kb_store()
        knowledge.upsert_task(
            str(task.id),
            [
                {
                    "archive_path": str(directory),
                    "source_type": "audio",
                    "chunk_index": 0,
                    "text": "访谈",
                    "embedding": [1.0] * settings.get_runtime_settings().kb_embedding_dim,
                }
            ],
        )
        library = voice.get_voiceprint_store()
        person = library.create_person("用户命名的人物")
        clip = directory.parent / "voiceprints" / "clips" / "keep.wav"
        clip.parent.mkdir(parents=True, exist_ok=True)
        clip.write_bytes(b"voice sample")
        sample = library.add_sample(person.id, np.ones(256), str(task.id), 2.0, 0.9, str(clip))
        library.set_task_speaker(str(task.id), "speaker_0", sample, person.id)
        lifecycle.delete(directory)
        assert knowledge.chunk_count() == 0
        assert knowledge._get_conn().execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0] == 0
        assert library.get_person(person.id).name == "用户命名的人物"
        assert library.list_task_speakers(str(task.id)) == []
        assert library._get_conn().execute("SELECT task_id FROM sample_meta").fetchone()[0] is None
        assert clip.read_bytes() == b"voice sample"
    finally:
        kb.reset_kb_store()
        voice.reset_voiceprint_store()


def test_task_retry_finishes_files_after_record_removed(archive, monkeypatch):
    import app.core.archive_lifecycle as module

    directory, task, lifecycle = archive
    with monkeypatch.context() as fault:
        fault.setattr(
            module.shutil, "rmtree", lambda *args: (_ for _ in ()).throw(PermissionError())
        )
        with pytest.raises(PermissionError):
            lifecycle.delete_task(task.id)
    assert database.get_task_store().get(task.id) is None
    result = lifecycle.delete_task(task.id)
    assert result["status"] == "deleted"
    assert not pending()


def test_multiple_outputs_all_move_before_record_deletion(archive, monkeypatch):
    directory, task, lifecycle = archive
    second = directory.parent / "second"
    second.mkdir()
    task.result["archive"] = {"output_dir": str(second)}
    database.get_task_store().save(task)
    rename = Path.rename

    def fail_second(path, destination):
        if path == second:
            raise PermissionError("locked")
        return rename(path, destination)

    with monkeypatch.context() as fault:
        fault.setattr(Path, "rename", fail_second)
        with pytest.raises(PermissionError):
            lifecycle.delete_task(task.id)
    assert database.get_task_store().get(task.id)
    assert second.exists()
    assert lifecycle.recover() == []
    assert database.get_task_store().get(task.id) is None
    assert not second.exists()
