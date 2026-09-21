from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api.routes import voiceprints
from app.core import artifacts, database, settings
from app.core.pipeline_steps import artifacts as pipeline_artifacts
from app.models import Task, TaskStatus, TaskType
from app.services.analysis import speaker_identity_artifacts as identity
from app.services.analysis.transcript_outputs import srt_to_markdown

SRT = "1\n00:00:01,000 --> 00:00:02,000\n[Unknown-abcd] 我是张明，正文提及 Unknown-abcd。\n"
REPORT = {
    "status": "completed",
    "mappings": [{
        "source_label": "Unknown-abcd", "current_name": "张明",
        "evidence_indexes": [1], "reason": "第一条发言明确自报姓名",
    }],
}


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "_runtime_settings", settings.RuntimeSettings(data_root=str(tmp_path)),
    )
    database.reset_db_path(tmp_path)
    monkeypatch.setattr(artifacts.ArtifactStore, "_changed", staticmethod(lambda _path: None))
    monkeypatch.setattr(database.TaskStore, "_archive_changed", staticmethod(lambda _task: None))
    monkeypatch.setattr(pipeline_artifacts, "_emit_file_ready", AsyncMock())
    monkeypatch.setattr(pipeline_artifacts, "_schedule_kb_index", lambda *_args: None)
    directory = tmp_path / "对话 🎙️"
    directory.mkdir()
    metadata = {"title": "采访", "extra": {"speakers": ["Unknown-abcd"], "speaker_count": 1}}
    task = Task(
        task_type=TaskType.PIPELINE, status=TaskStatus.COMPLETED, source="fixture.wav",
        result={"output_dir": str(directory), "metadata": metadata},
    )
    store = database.get_task_store()
    store.save(task)
    files = {
        "transcript.srt": SRT,
        "transcript_polished.srt": SRT,
        "transcript_polished.md": srt_to_markdown(SRT, "采访"),
        "metadata.json": json.dumps(metadata, ensure_ascii=False),
        "speaker_map.json": json.dumps({"version": 1, "confirmed": False, "mappings": [{
            "source_label": "SPEAKER_00", "current_name": "Unknown-abcd",
            "status": "voiceprint_new",
        }]}),
        "summary.md": "Unknown-abcd建议继续；Unknown-abcd-extra 是独立标签。",
        "analysis.json": json.dumps({
            "content_type": "技术讲座", "speakers_detected": 1,
            "description": "Unknown-abcd介绍项目",
        }),
        "summary.json": json.dumps({
            "tldr": "张明主讲，Unknown-abcd介绍项目。", "key_facts": [],
        }, ensure_ascii=False),
    }
    for filename, content in files.items():
        artifacts.get_artifact_store().write(task.id, directory, filename, content)
    app = FastAPI()
    app.include_router(voiceprints.router, prefix="/api")
    with TestClient(app) as client:
        yield SimpleNamespace(task=task, store=store, directory=directory, client=client)
    database.close_db()


def test_completed_task_identifies_names_and_updates_files_and_database(archive, monkeypatch):
    infer = AsyncMock(return_value=REPORT)
    monkeypatch.setattr(identity, "infer_speaker_names", infer)
    response = archive.client.post(f"/api/tasks/{archive.task.id}/speakers/identify")
    assert response.status_code == 200
    assert response.json()["mappings"] == REPORT["mappings"]
    infer.assert_awaited_once()
    assert infer.call_args.args == (SRT,)
    context = infer.call_args.kwargs["context"]
    assert context["title"] == "采访" and context["description"] == ""
    assert context["analysis"]["content_type"] == "技术讲座"
    assert context["summary"]["tldr"] == "张明主讲，Unknown-abcd介绍项目。"
    assert context["speaker_activity"][0]["speaker"] == "Unknown-abcd"
    assert context["speaker_activity"][0]["duration_seconds"] == pytest.approx(1)
    assert context["speaker_activity"][0]["share"] == pytest.approx(1)
    updated_srt = (archive.directory / "transcript_polished.srt").read_text(encoding="utf-8")
    assert updated_srt == SRT.replace("[Unknown-abcd]", "[张明]")
    markdown = (archive.directory / "transcript_polished.md").read_text(encoding="utf-8")
    assert "**[张明]**" in markdown
    assert (archive.directory / "summary.md").read_text(encoding="utf-8") == (
        "张明建议继续；Unknown-abcd-extra 是独立标签。"
    )
    mapping = json.loads((archive.directory / "speaker_map.json").read_text(encoding="utf-8"))
    assert mapping["mappings"][0]["source_label"] == "SPEAKER_00"
    assert mapping["mappings"][0]["current_name"] == "张明"
    assert not mapping["confirmed"]
    assert archive.store.get(archive.task.id).result["metadata"]["extra"]["speakers"] == ["张明"]
    assert archive.store.get(archive.task.id).result["analysis"]["description"] == "张明介绍项目"
    assert json.loads((archive.directory / "summary.json").read_text(encoding="utf-8"))["tldr"] == (
        "张明主讲，张明介绍项目。"
    )
    for filename in response.json()["changed_files"]:
        assert archive.store.get_artifact(archive.task.id, filename)["content"] == (
            archive.directory / filename
        ).read_text(encoding="utf-8")


def test_completed_archive_uses_diarization_activity_and_preserves_other_speakers(
    archive, monkeypatch,
):
    source = SRT + "\n2\n00:00:02,000 --> 00:00:12,000\n[Unknown-other] 谢谢分享。\n"
    files = {
        "transcript.srt": source,
        "transcript_polished.srt": source,
        "diarization.json": json.dumps({"turns": [
            {"start": 0.0, "end": 90.0, "speaker": "SPEAKER_00"},
            {"start": 90.0, "end": 100.0, "speaker": "SPEAKER_01"},
        ]}),
        "speaker_map.json": json.dumps({"version": 1, "confirmed": False, "mappings": [
            {
                "source_label": "SPEAKER_00", "current_name": "Unknown-abcd",
                "status": "voiceprint_new",
            },
            {
                "source_label": "SPEAKER_01", "current_name": "Unknown-other",
                "status": "voiceprint_new",
            },
        ]}),
    }
    for filename, content in files.items():
        artifacts.get_artifact_store().write(archive.task.id, archive.directory, filename, content)
    infer = AsyncMock(return_value=REPORT)
    monkeypatch.setattr(identity, "infer_speaker_names", infer)
    response = archive.client.post(f"/api/tasks/{archive.task.id}/speakers/identify")
    assert response.status_code == 200
    context = infer.call_args.kwargs["context"]
    activity = {item["speaker"]: item for item in context["speaker_activity"]}
    assert activity["Unknown-abcd"]["duration_seconds"] == pytest.approx(90)
    assert activity["Unknown-abcd"]["share"] == pytest.approx(0.9)
    assert activity["Unknown-other"]["duration_seconds"] == pytest.approx(10)
    assert activity["Unknown-other"]["share"] == pytest.approx(0.1)
    for filename in ("transcript.srt", "transcript_polished.srt"):
        expected = source.replace("[Unknown-abcd]", "[张明]")
        assert (archive.directory / filename).read_text(encoding="utf-8") == expected
        assert archive.store.get_artifact(archive.task.id, filename)["content"] == expected
    mappings = json.loads((archive.directory / "speaker_map.json").read_text(encoding="utf-8"))
    assert mappings["mappings"][0]["source_label"] == "SPEAKER_00"
    assert mappings["mappings"][1] == {
        "source_label": "SPEAKER_01", "current_name": "Unknown-other", "status": "voiceprint_new",
    }


def test_reapplying_report_preserves_stable_map_without_duplicates(archive):
    identity.persist_speaker_names(str(archive.task.id), archive.directory, REPORT)
    changed = identity.persist_speaker_names(str(archive.task.id), archive.directory, REPORT)
    mapping = json.loads((archive.directory / "speaker_map.json").read_text(encoding="utf-8"))
    assert len(mapping["mappings"]) == 1
    assert mapping["mappings"][0]["source_label"] == "SPEAKER_00"
    assert changed == []


def test_markdown_edits_are_preserved(archive):
    text = "# 我的采访\n\n**[Unknown-abcd]** 手动修改后的正文。\n\n附注。"
    (archive.directory / "transcript_polished.md").write_text(text, encoding="utf-8")
    identity.persist_speaker_names(str(archive.task.id), archive.directory, REPORT)
    assert (archive.directory / "transcript_polished.md").read_text(encoding="utf-8") == (
        text.replace("[Unknown-abcd]", "[张明]")
    )


@pytest.mark.parametrize("report", [
    {"status": "completed", "mappings": []},
    {"status": "failed", "mappings": [], "error": "模型请求失败"},
])
def test_no_evidence_or_model_failure_preserves_transcripts(archive, monkeypatch, report):
    monkeypatch.setattr(identity, "infer_speaker_names", AsyncMock(return_value=report))
    response = archive.client.post(f"/api/tasks/{archive.task.id}/speakers/identify")
    assert response.json()["status"] == report["status"]
    assert (archive.directory / "transcript_polished.srt").read_text(encoding="utf-8") == SRT
    assert (archive.directory / "speaker_identity.json").is_file()


def test_concurrent_subtitle_edit_prevents_stale_name_write(archive, monkeypatch):
    async def infer(*_args, **_kwargs):
        (archive.directory / "transcript_polished.srt").write_text(
            SRT.replace("[Unknown-abcd]", "[人工姓名]"), encoding="utf-8",
        )
        return REPORT

    monkeypatch.setattr(identity, "infer_speaker_names", infer)
    response = archive.client.post(f"/api/tasks/{archive.task.id}/speakers/identify")
    assert response.status_code == 409
    assert "人工姓名" in (archive.directory / "transcript_polished.srt").read_text(encoding="utf-8")
    assert (archive.directory / "transcript.srt").read_text(encoding="utf-8") == SRT
    assert not (archive.directory / "speaker_identity.json").exists()


def test_missing_and_active_tasks_do_not_start_model(archive, monkeypatch):
    infer = AsyncMock()
    monkeypatch.setattr(identity, "infer_speaker_names", infer)
    assert archive.client.post(f"/api/tasks/{uuid4()}/speakers/identify").status_code == 404
    archive.task.status = TaskStatus.PROCESSING
    archive.store.save(archive.task)
    assert archive.client.post(f"/api/tasks/{archive.task.id}/speakers/identify").status_code == 409
    infer.assert_not_awaited()


def test_manual_rename_after_inference_resolves_original_voiceprint(archive, monkeypatch):
    identity.persist_speaker_names(str(archive.task.id), archive.directory, REPORT)
    renamed = []
    store = SimpleNamespace(
        get_task_speaker=lambda _task, label: (
            {"person_id": "person-1"} if label == "SPEAKER_00" else None
        ),
        list_task_speakers=lambda _task: [{"person_id": "person-1", "person_name": "Unknown-abcd"}],
        get_person=lambda _id: SimpleNamespace(name="Unknown-abcd"),
        find_person_by_name=lambda _name: None,
        rename_person=lambda person_id, name: renamed.append((person_id, name)),
    )
    monkeypatch.setattr(voiceprints, "get_voiceprint_store", lambda: store)
    response = archive.client.patch(f"/api/tasks/{archive.task.id}/speakers", json={
        "old_name": "张明", "new_name": "张铭",
    })
    assert response.status_code == 200
    assert response.json()["person_id"] == "person-1"
    assert renamed == [("person-1", "张铭")]
    mapping = json.loads((archive.directory / "speaker_map.json").read_text(encoding="utf-8"))
    assert mapping["mappings"][0]["source_label"] == "SPEAKER_00"
    assert mapping["mappings"][0]["current_name"] == "张铭"
