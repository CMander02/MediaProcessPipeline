from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.api.routes import voiceprints as voiceprint_route  # noqa: E402
from app.services.analysis import artifact_sync  # noqa: E402


def test_speaker_rename_updates_text_json_and_sqlite_mirrors(tmp_path, monkeypatch):
    (tmp_path / "transcript_polished.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n[SPEAKER_00] 你好",
        encoding="utf-8",
    )
    (tmp_path / "summary.md").write_text(
        "SPEAKER_00 介绍了项目。", encoding="utf-8"
    )
    (tmp_path / "metadata.json").write_text(
        json.dumps({"speakers": ["SPEAKER_00"], "nested": {"host": "SPEAKER_00"}}),
        encoding="utf-8",
    )
    saved = {}

    class FakeTaskStore:
        def save_artifact(self, task_id, filename, content, content_type):
            saved[filename] = (task_id, content, content_type)

    monkeypatch.setattr(artifact_sync, "get_task_store", lambda: FakeTaskStore())

    changed = artifact_sync.sync_speaker_artifacts(
        "task-id", tmp_path, "SPEAKER_00", "孟繁青"
    )

    assert set(changed) == {"transcript_polished.srt", "summary.md", "metadata.json"}
    assert "[孟繁青]" in (tmp_path / "transcript_polished.srt").read_text(encoding="utf-8")
    assert "孟繁青" in (tmp_path / "summary.md").read_text(encoding="utf-8")
    metadata = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["speakers"] == ["孟繁青"]
    assert set(saved) == set(changed)
    assert saved["metadata.json"][2] == "application/json"


@pytest.mark.asyncio
async def test_task_rename_resolves_voiceprint_by_current_person_name(tmp_path, monkeypatch):
    task_id = uuid4()
    renamed = []
    synchronized = []

    class FakeVoiceprintStore:
        def get_task_speaker(self, _task_id, _speaker_label):
            return None

        def list_task_speakers(self, _task_id):
            return [{"person_id": "person-1", "person_name": "Unknown-abcd"}]

        def get_person(self, _person_id):
            return SimpleNamespace(id="person-1", name="Unknown-abcd")

        def find_person_by_name(self, _name):
            return None

        def rename_person(self, person_id, name):
            renamed.append((person_id, name))

    class FakeTaskStore:
        def get(self, _task_id):
            return SimpleNamespace(result={"output_dir": str(tmp_path)})

    monkeypatch.setattr(
        voiceprint_route, "get_voiceprint_store", lambda: FakeVoiceprintStore()
    )
    monkeypatch.setattr(voiceprint_route, "get_task_store", lambda: FakeTaskStore())
    monkeypatch.setattr(
        voiceprint_route,
        "sync_speaker_artifacts",
        lambda *args: synchronized.append(args) or ["transcript.srt"],
    )

    response = await voiceprint_route.rename_task_speaker(
        task_id,
        voiceprint_route.SpeakerRenameRequest(
            old_name="Unknown-abcd",
            new_name="孟繁青",
        ),
    )

    assert response.status == "renamed"
    assert renamed == [("person-1", "孟繁青")]
    assert synchronized[0][2:] == ("Unknown-abcd", "孟繁青")
