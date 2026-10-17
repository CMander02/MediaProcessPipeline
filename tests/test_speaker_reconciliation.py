import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.analysis import speaker_reconciliation as reconciliation


@pytest.fixture
def case(tmp_path, monkeypatch):
    srt = "1\n00:00:00,000 --> 00:00:01,000\n[李华] 张明是本文讨论对象。"
    (tmp_path / "transcript_polished.srt").write_text(srt, encoding="utf-8")
    (tmp_path / "metadata.json").write_text('{"extra":{"speakers":["张明"]}}', encoding="utf-8")
    (tmp_path / "speaker_identity.json").write_text('{"status":"completed"}', encoding="utf-8")
    task = SimpleNamespace(id="test", status="completed", options={}, result={})
    monkeypatch.setattr(reconciliation, "repair_directory", lambda task: tmp_path)
    monkeypatch.setattr(reconciliation, "get_task_store", lambda: SimpleNamespace(save=lambda task: None))
    def write(task, directory, filename, content):
        (directory / filename).write_text(content, encoding="utf-8")
    monkeypatch.setattr(reconciliation, "get_artifact_store", lambda: SimpleNamespace(write=write))
    return tmp_path, task, srt, write


def test_confirm_current_labels_keeps_prose_and_records_superseded_report(case):
    directory, task, srt, _ = case
    result = reconciliation.reconcile_speakers(task, srt)
    assert result["speakers"] == ["李华"]
    assert task.result["metadata"]["extra"]["derived_content_stale"]
    assert (directory / "transcript_polished.srt").read_text(encoding="utf-8") == srt
    report = json.loads((directory / "speaker_reconciliation.json").read_text(encoding="utf-8"))
    assert report["supersedes"] == ["speaker_identity.json"]
    assert (directory / "speaker_reconciliation.backup.json").exists()
    assert not (directory / "speaker_reconciliation.pending.json").exists()


def test_partial_publication_can_resume_after_mirror_failure(case, monkeypatch):
    directory, task, srt, write = case
    def interrupted(task_id, root, filename, content):
        write(task_id, root, filename, content)
        if filename == "speaker_reconciliation.json":
            raise OSError("SQLite unavailable")
    monkeypatch.setattr(reconciliation, "get_artifact_store", lambda: SimpleNamespace(write=interrupted))
    with pytest.raises(OSError):
        reconciliation.reconcile_speakers(task, srt)
    assert (directory / "speaker_reconciliation.pending.json").exists()
    monkeypatch.setattr(reconciliation, "get_artifact_store", lambda: SimpleNamespace(write=write))
    result = reconciliation.reconcile_speakers(task, srt)
    assert result["revision"] == 1
    assert task.result["metadata"]["extra"]["speakers"] == ["李华"]


def test_stale_confirmation_does_not_write_metadata(case):
    directory, task, srt, _ = case
    before = (directory / "metadata.json").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="字幕已更新"):
        reconciliation.reconcile_speakers(task, srt + "旧编辑")
    assert (directory / "metadata.json").read_text(encoding="utf-8") == before
