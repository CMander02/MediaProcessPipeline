from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import database, settings
from app.core.paths import LAYOUT_FILE, get_workspace_paths, reset_workspace_paths
from app.core.storage_migration import StorageMigration
from app.models import Task, TaskStatus, TaskType


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    (root / LAYOUT_FILE).write_text('{"version": 1}', encoding="utf-8")
    monkeypatch.setattr(
        settings, "_runtime_settings", settings.RuntimeSettings(data_root=str(root))
    )
    database.reset_db_path(root)
    directory = root / "访谈 🎙️"
    directory.mkdir()
    media = directory / "source.mp4"
    media.write_bytes(b"representative media")
    task = Task(
        task_type=TaskType.PIPELINE,
        source=str(media),
        status=TaskStatus.COMPLETED,
        result={"output_dir": str(directory)},
    )
    store = database.get_task_store()
    store.save(task)
    metadata = json.dumps(
        {"task_id": str(task.id), "archive_id": "stable-id", "media_file": str(media)},
        ensure_ascii=False,
    )
    (directory / "metadata.json").write_text(metadata, encoding="utf-8")
    store.save_artifact(task.id, "metadata.json", metadata)
    store.add_event(task.id, "completed", data={"output_dir": str(directory)})
    auth = root / "auth"
    auth.mkdir()
    (auth / "cookies.txt").write_text("retained cookies", encoding="utf-8")
    yield root, task.id
    database.close_db()
    reset_workspace_paths()


def test_apply_preserves_identity_children_media_and_auth(library):
    root, task_id = library
    migration = StorageMigration(root)
    preview = migration.preview()
    assert preview["conflicts"] == []
    assert preview["files"] == 4
    assert preview["operations"][0]["archive_id"] == "stable-id"
    assert (root / "访谈 🎙️").exists()
    result = migration.apply()
    assert result["status"] == "complete"
    assert get_workspace_paths(root).version == 2
    store = database.get_task_store()
    directory = root / "archives" / "访谈 🎙️"
    assert store.get(task_id).source == str(directory / "source.mp4")
    assert store.get(task_id).result["output_dir"] == str(directory)
    assert len(store.list_events(task_id)) == 1
    artifact = json.loads(store.get_artifact(task_id, "metadata.json")["content"])
    assert artifact["archive_id"] == "stable-id"
    assert artifact["media_file"] == str(directory / "source.mp4")
    assert (directory / "source.mp4").read_bytes() == b"representative media"
    assert (root / "state/auth/cookies.txt").read_text() == "retained cookies"
    assert migration.apply()["status"] == "complete"
    assert len(list((root / "archives").iterdir())) == 1


@pytest.mark.parametrize("phase", ["move", "rewrite"])
@pytest.mark.parametrize("recovery", ["resume", "rollback"])
def test_interrupted_migration_can_recover(library, monkeypatch, phase, recovery):
    root, task_id = library
    migration = StorageMigration(root)
    method = "_move_or_copy" if phase == "move" else "_rewrite_json"
    original = getattr(migration, method)

    def fail(*args):
        original(*args)
        raise OSError("injected interruption")

    monkeypatch.setattr(migration, method, fail)
    with pytest.raises(OSError, match="injected"):
        migration.apply()
    with pytest.raises(ValueError, match="incomplete"):
        get_workspace_paths(root)
    recovered = StorageMigration(root)
    if recovery == "resume":
        recovered.apply()
        expected = root / "archives" / "访谈 🎙️"
    else:
        recovered.rollback()
        expected = root / "访谈 🎙️"
    assert database.get_task_store().get(task_id).source == str(expected / "source.mp4")
    assert expected.is_dir()
    assert len(database.get_task_store().list_events(task_id)) == 1


def test_copy_preserves_source_and_resolves_new_paths(library, tmp_path):
    root, task_id = library
    target = tmp_path / "copied"
    StorageMigration(root, target).apply()
    assert (root / "访谈 🎙️/source.mp4").exists()
    assert database.get_task_store().get(task_id).source == str(root / "访谈 🎙️/source.mp4")
    database.reset_db_path(target)
    assert database.get_task_store().get(task_id).source == str(
        target / "archives/访谈 🎙️/source.mp4"
    )


def test_backup_includes_committed_wal(library):
    root, task_id = library
    # Keep an independent connection open so closing the application leaves a WAL.
    conn = sqlite3.connect(root / "tasks.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("UPDATE tasks SET message='from WAL' WHERE id=?", (str(task_id),))
    conn.commit()
    try:
        assert Path(str(root / "tasks.db") + "-wal").stat().st_size > 0
        preview = StorageMigration(root).preview()
        StorageMigration(root)._backup(preview)
        with sqlite3.connect(preview["databases"][0]["backup"]) as backup:
            assert backup.execute("SELECT message FROM tasks").fetchone()[0] == "from WAL"
    finally:
        conn.close()


def test_destination_conflict_prevents_changes(library):
    root, _ = library
    (root / "archives/访谈 🎙️").mkdir(parents=True)
    migration = StorageMigration(root)
    assert migration.preview()["conflicts"]
    with pytest.raises(ValueError, match="Destination exists"):
        migration.apply()
    assert (root / "访谈 🎙️/source.mp4").exists()


def test_two_source_archives_cannot_merge_into_one_destination(library, tmp_path):
    root, _ = library
    other = root / "archives/访谈 🎙️"
    other.mkdir(parents=True)
    (other / "metadata.json").write_text('{"archive_id":"different"}', encoding="utf-8")
    assert StorageMigration(root, tmp_path / "target").preview()["conflicts"]


def test_new_and_existing_layout_detection(tmp_path):
    fresh = tmp_path / "fresh"
    paths = get_workspace_paths(fresh)
    assert paths.version == 2
    paths.ensure()
    assert (fresh / "state").is_dir()
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    sqlite3.connect(legacy / "tasks.db").close()
    assert get_workspace_paths(legacy).version == 1
    assert not (legacy / "state").exists()


def test_cli_preview_and_running_daemon_guard(library, monkeypatch):
    from app.cli.client import MppClient
    from app.cli.main import app
    from typer.testing import CliRunner

    root, _ = library
    runner = CliRunner()
    args = ["--skip-version-check", "--json", "storage", "migrate", "--source", str(root)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert '"preview"' in result.output
    monkeypatch.setattr(MppClient, "ping", lambda self: True)
    result = runner.invoke(app, [*args, "--apply"])
    assert result.exit_code == 4
    assert "daemon_running" in result.output
    assert (root / "访谈 🎙️").is_dir()


def test_completed_rollback_retains_later_edits(library):
    root, task_id = library
    migration = StorageMigration(root)
    result = migration.apply()
    store = database.get_task_store()
    task = store.get(task_id)
    task.message = "after migration"
    store.save(task)
    migrated_json = root / "archives/访谈 🎙️/metadata.json"
    migrated_json.write_text('{"later_edit": true}', encoding="utf-8")
    migration.rollback()
    assert store.get(task_id).message != "after migration"
    retained = Path(result["backup"]) / "before-rollback"
    with sqlite3.connect(retained / "tasks.db") as conn:
        assert conn.execute("SELECT message FROM tasks").fetchone()[0] == "after migration"
    assert json.loads((retained / "json/访谈 🎙️/metadata.json").read_text())["later_edit"]
    assert migration.rollback()["status"] == "rolled_back"


def test_vectors_voice_clips_and_database_only_artifacts(library):
    import numpy as np
    from app.services.kb.store import KBStore
    from app.services.voiceprint.store import VoiceprintStore

    root, task_id = library
    directory = root / "访谈 🎙️"
    kb = KBStore(root / "kb.db", 3, root)
    kb.upsert_task(
        str(task_id),
        [
            {
                "archive_path": str(directory),
                "source_type": "transcript",
                "chunk_index": 0,
                "text": "retained vector",
                "embedding": [1.0, 0.0, 0.0],
            }
        ],
    )
    kb.close()
    clips = root / "voiceprints/clips"
    clips.mkdir(parents=True)
    clip = clips / "sample.wav"
    clip.write_bytes(b"voice sample")
    voice = VoiceprintStore(root / "voiceprints/library.db", clips, 3)
    person = voice.create_person("人物")
    sample = voice.add_sample(
        person.id, np.array([1.0, 0.0, 0.0]), str(task_id), 2.0, 1.0, str(clip)
    )
    voice.set_task_speaker(str(task_id), "SPEAKER_00", sample, person.id)
    voice._close_conn()
    database.get_task_store().save_artifact(
        task_id, "extra.json", json.dumps({"output_dir": str(directory)})
    )
    StorageMigration(root).apply()
    kb = KBStore(root / "state/kb.db", 3, root)
    voice = VoiceprintStore(
        root / "state/voiceprints/library.db", root / "state/voiceprints/clips", 3
    )
    try:
        assert kb.chunk_count() == 1
        assert kb.search([1.0, 0.0, 0.0])[0]["text"] == "retained vector"
        assert voice.get_person(person.id).name == "人物"
        assert Path(voice.clip_path_for_sample(sample)).read_bytes() == b"voice sample"
        assert voice.get_task_speaker(str(task_id), "SPEAKER_00")["person_id"] == person.id
        payload = json.loads(
            database.get_task_store().get_artifact(task_id, "extra.json")["content"]
        )
        assert payload["output_dir"] == str(root / "archives/访谈 🎙️")
    finally:
        kb.close()
        voice._close_conn()
