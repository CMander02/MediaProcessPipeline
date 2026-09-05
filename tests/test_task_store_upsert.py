from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import database
from app.models import Task, TaskStatus, TaskType


@pytest.fixture
def store(tmp_path):
    database.reset_db_path(tmp_path)
    yield database.get_task_store()
    database.close_db()


def test_save_updates_task_without_deleting_artifacts_or_events(store):
    task = Task(task_type=TaskType.PIPELINE, source="before.mp4")
    store.save(task)
    created = task.created_at
    store.save_artifact(task.id, "summary.md", "# retained")
    store.add_event(task.id, "queued", message="original event")
    store.add_event(task.id, "processing")
    events = store.list_events(task.id)
    artifact = store.get_artifact(task.id, "summary.md")

    task.source = "after.mp4"
    task.status = TaskStatus.COMPLETED
    task.result = {"output_dir": "archive"}
    task.created_at = created + timedelta(days=1)
    task.updated_at = datetime.now()
    store.save(task)
    store.save(task)

    updated = store.get(task.id)
    assert updated.source == "after.mp4"
    assert updated.status == TaskStatus.COMPLETED
    assert updated.result == {"output_dir": "archive"}
    assert updated.created_at == created
    assert store.get_artifact(task.id, "summary.md") == artifact
    assert store.list_events(task.id) == events

    assert store.delete(task.id)
    assert store.get_artifact(task.id, "summary.md") is None
    assert store.list_events(task.id) == []


def test_failed_save_rolls_back_and_next_save_succeeds(store):
    task = Task(task_type=TaskType.PIPELINE, source="test.mp4")
    store.save(task)
    store.save_artifact(task.id, "summary.md", "retained")
    conn = database._get_conn()
    conn.execute("""
        CREATE TRIGGER reject_message BEFORE INSERT ON tasks
        WHEN NEW.message = 'reject'
        BEGIN SELECT RAISE(ABORT, 'test save failure'); END
    """)
    conn.commit()

    task.message = "reject"
    with pytest.raises(sqlite3.IntegrityError, match="test save failure"):
        store.save(task)

    assert not conn.in_transaction
    assert store.get(task.id).message is None
    task.message = "accepted"
    store.save(task)
    assert store.get(task.id).message == "accepted"
    assert store.get_artifact(task.id, "summary.md")["content"] == "retained"
