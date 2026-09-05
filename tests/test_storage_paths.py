from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core import database
from app.core.paths import LAYOUT_FILE, get_workspace_paths, reset_workspace_paths
from app.models import Task, TaskStatus, TaskType


@pytest.fixture
def store(tmp_path):
    database.reset_db_path(tmp_path)
    yield database.get_task_store(), tmp_path
    database.close_db()
    reset_workspace_paths()


def test_task_paths_roundtrip_and_status_update(store):
    tasks, root = store
    source = root / "archives/同名 🎧 (2)/source.wav"
    task = Task(
        task_type=TaskType.PIPELINE,
        source=str(source),
        options={"staging_dir": str(root / "tmp/staging/upload")},
        result={"output_dir": str(source.parent), "files": {"audio": str(source)}},
    )
    tasks.save(task)
    raw = database._get_conn().execute("SELECT * FROM tasks").fetchone()
    assert raw["source"] == "archives/同名 🎧 (2)/source.wav"
    assert raw["external_source"] == 0
    assert tasks.get(task.id).source == str(source)
    tasks.update_status(
        task.id, TaskStatus.COMPLETED, result={"output_dir": str(root / "archives/moved")}
    )
    loaded = tasks.get(task.id)
    assert loaded.result["output_dir"] == str(root / "archives/moved")
    assert loaded.options["staging_dir"] == str(root / "tmp/staging/upload")
    assert loaded.source == str(source)


def test_external_source_and_legacy_absolute_row(store):
    tasks, root = store
    external = root.parent / "external.wav"
    task = Task(task_type=TaskType.PIPELINE, source=str(external))
    tasks.save(task)
    assert tasks.get(task.id).external_source
    conn = database._get_conn()
    legacy = root / "旧目录/source.wav"
    conn.execute(
        "UPDATE tasks SET source=?, path_fields='[]' WHERE id=?", (str(legacy), str(task.id))
    )
    conn.commit()
    assert tasks.get(task.id).source == str(legacy)


def test_windows_case_variant_is_stored_relative(store):
    tasks, root = store
    if os.name != "nt":
        pytest.skip("Windows path case semantics")
    task = Task(task_type=TaskType.PIPELINE, source=str(root / "archives/source.wav").upper())
    tasks.save(task)
    assert not tasks.get(task.id).external_source
    assert (
        database._get_conn().execute("SELECT source FROM tasks").fetchone()[0]
        == "ARCHIVES/SOURCE.WAV"
    )


def test_daemon_state_follows_its_live_process(tmp_path):
    from app.core.workspace_lifecycle import relocate_daemon_state

    old, new = tmp_path / "old", tmp_path / "new"
    old_paths, new_paths = get_workspace_paths(old), get_workspace_paths(new)
    old_paths.ensure()
    new_paths.ensure()
    state = old_paths.state / ".mpp-daemon.json"
    state.write_text(
        json.dumps({"pid": os.getpid(), "server": "http://localhost:18000"}), encoding="utf-8"
    )
    relocate_daemon_state(old, new)
    assert not state.exists()
    assert json.loads((new_paths.state / state.name).read_text())["pid"] == os.getpid()


def test_legacy_metadata_only_library_uses_existing_root(tmp_path):
    directory = tmp_path / "访谈"
    directory.mkdir()
    (directory / "metadata.json").write_text("{}", encoding="utf-8")
    assert get_workspace_paths(tmp_path).archives == tmp_path
    assert not (tmp_path / LAYOUT_FILE).exists()
