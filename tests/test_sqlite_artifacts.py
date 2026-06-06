from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from app.core.database import get_task_store, reset_db_path
from app.core.pipeline import _write_detail_file, _write_mindmap_files, _write_text_artifact
from app.models.task import Task, TaskStatus, TaskType


def test_task_store_persists_text_artifacts(tmp_path):
    reset_db_path(tmp_path)
    store = get_task_store()
    task = Task(
        id=uuid4(),
        task_type=TaskType.PIPELINE,
        status=TaskStatus.PROCESSING,
        source="test.mp4",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    store.save(task)

    store.save_artifact(task.id, "mindmap.md", "# Mindmap\n", content_type="text/markdown")
    store.save_artifact(task.id, "detail.md", "# Detail\n", content_type="text/markdown")
    store.save_artifact(task.id, "transcript_polished.srt", "1\n00:00:00,000 --> 00:00:01,000\nHi\n")

    assert store.get_artifact(task.id, "mindmap.md")["content"] == "# Mindmap\n"
    assert store.get_artifact(task.id, "detail.md")["content_type"] == "text/markdown"
    assert store.get_artifact(task.id, "transcript_polished.srt")["content"].endswith("Hi\n")

    store.save_artifact(task.id, "mindmap.md", "# Updated\n")
    assert store.get_artifact(task.id, "mindmap.md")["content"] == "# Updated\n"


def test_task_store_resolves_artifact_by_output_dir(tmp_path):
    reset_db_path(tmp_path)
    store = get_task_store()
    output_dir = tmp_path / "archive"
    task = Task(
        id=uuid4(),
        task_type=TaskType.PIPELINE,
        status=TaskStatus.COMPLETED,
        source="test.mp4",
        result={"output_dir": str(output_dir)},
    )
    store.save(task)
    store.save_artifact(task.id, "mindmap.json", '{"title":"x"}', content_type="application/json")

    artifact = store.get_artifact_by_output_dir(output_dir, "mindmap.json")

    assert artifact is not None
    assert artifact["task_id"] == str(task.id)
    assert artifact["content"] == '{"title":"x"}'


@pytest.mark.asyncio
async def test_pipeline_artifact_writers_mirror_mindmap_detail_and_subtitles_to_sqlite(tmp_path, monkeypatch):
    reset_db_path(tmp_path)
    store = get_task_store()
    task = Task(
        id=uuid4(),
        task_type=TaskType.PIPELINE,
        status=TaskStatus.PROCESSING,
        source="test.mp4",
    )
    store.save(task)
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()

    async def noop_emit(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.core.pipeline._emit_file_ready", noop_emit)

    await _write_mindmap_files(task, archive_dir, "- Root [00:00:01]\n  - Child [00:00:02]")
    await _write_detail_file(task, archive_dir, "# Detailed outline\n")
    await _write_text_artifact(task, archive_dir, "transcript_polished.srt", "1\n00:00:01,000 --> 00:00:02,000\nHi\n")

    assert (archive_dir / "mindmap.md").exists()
    assert store.get_artifact(task.id, "mindmap.md")["content"] == "- Root\n  - Child"
    assert store.get_artifact(task.id, "mindmap.json")["content_type"] == "application/json"
    assert store.get_artifact(task.id, "detail.md")["content"] == "# Detailed outline\n"
    assert "Hi" in store.get_artifact(task.id, "transcript_polished.srt")["content"]
