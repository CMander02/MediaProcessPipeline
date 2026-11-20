from __future__ import annotations

import json
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.api.routes import filesystem, pipeline, tasks, voiceprints  # noqa: E402
from app.cli.client import MppClient  # noqa: E402
from app.cli.commands import operations, speaker  # noqa: E402
from app.cli.context import CliContext  # noqa: E402
from app.cli.main import app  # noqa: E402
from app.core import archive_sync, database, queue, settings  # noqa: E402
from app.core.artifacts import get_artifact_store  # noqa: E402
from app.models import Task, TaskStatus, TaskType  # noqa: E402
from app.services.analysis.speaker_cli_profile import PREVIEW_FILE  # noqa: E402
from app.services.analysis.transcript_outputs import srt_to_markdown  # noqa: E402
from app.services.voiceprint import enrollment  # noqa: E402
from app.services.voiceprint import store as voiceprint_store  # noqa: E402

TITLE = "Interview"
POLISHED = (
    "1\n00:00:01,000 --> 00:00:03,000\n[Alice] It's Alice at the microphone.\n\n"
    "2\n00:00:03,000 --> 00:00:05,000\n[UnKnOwN] How did that start?\n\n"
    "4\n00:00:05,000 --> 00:00:09,000\n[SPEAKER_01] Here's the whole story.\n"
    "It took two years.\n\n"
    "5\n00:00:09,000 --> 00:00:11,000\nThanks for having me.\n\n"
    "7\n00:00:11,000 --> 00:00:12,000\n[unknown] See you next time.\n"
)
RAW = "93\n00:00:03,000 --> 00:00:05,000\n[unknown] How did that start?\n"
PREVIEW = [
    {
        "index": 2,
        "timestamp": "00:00:03,000 --> 00:00:05,000",
        "old_speaker": "UnKnOwN",
        "proposed_speaker": "Alice",
        "text": "[UnKnOwN] How did that start?",
    },
    {
        "index": 7,
        "timestamp": "00:00:11,000 --> 00:00:12,000",
        "old_speaker": "unknown",
        "proposed_speaker": "Bob",
        "text": "[unknown] See you next time.",
    },
]
EVIDENCE = {"source": "frame:8.00", "quote": "Alice | Host", "verification": "visual_observation"}
VECTOR = np.array([1.0, 2.0, 3.0], dtype=np.float32)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    config = settings.RuntimeSettings(data_root=str(tmp_path))
    monkeypatch.setattr(settings, "_runtime_settings", config)
    monkeypatch.setattr(archive_sync, "_sync_service", archive_sync.ArchiveSyncService())
    db_root = database._db_root
    database.reset_db_path(tmp_path)
    voices = voiceprint_store.VoiceprintStore(tmp_path / "voiceprints.db", tmp_path / "clips")
    monkeypatch.setattr(voiceprint_store, "_store", voices)
    path = tmp_path / "archives" / TITLE
    path.mkdir(parents=True)
    task = Task(
        source="interview.wav",
        task_type=TaskType.PIPELINE,
        status=TaskStatus.COMPLETED,
        result={"output_dir": str(path)},
    )
    tasks_db = database.get_task_store()
    tasks_db.save(task)
    artifacts = get_artifact_store()
    fixtures = {
        "transcript_polished.srt": POLISHED,
        "transcript.srt": RAW,
        "transcript_polished.md": srt_to_markdown(POLISHED, TITLE),
        "metadata.json": {
            "title": TITLE,
            "status": "completed",
            "extra": {
                "derived_content_stale": True,
                "derived_content_stale_reason": "名字待更新",
                "speaker_revision": 4,
                "speaker_name_sources": {"Alice": "片头身份文字"},
            },
        },
        "speaker_map.json": {
            "mappings": [
                {"source_label": "SPEAKER_00", "current_name": "Alice", "status": "text_inferred"},
            ]
        },
        "identity_evidence.checkpoint.json": {
            "report": {
                "evidence": [
                    {"index": 1, "quote": "Alice at the microphone", "kind": "self_introduction"},
                ]
            }
        },
        "speaker_identity.json": {
            "suggestions": [
                {"source_label": "SPEAKER_01", "current_name": "Bob", "similarity": 0.82},
            ]
        },
        "participant_evidence.json": {
            "participants": [{"name": "Alice", "role": "Host", "evidence": [EVIDENCE]}],
            "conflicts": ["Credits list a second host"],
            "uncertainties": ["Off-screen guest"],
            "count_estimate": 2,
        },
        "speaker_voice_evidence.json": {
            "cues": [
                {"index": 93, "start": 3, "end": 5, "similarity": {"SPEAKER_00": 0.87}},
            ]
        },
        "diarization.json": {"turns": [{"start": 3, "end": 5, "speaker": "SPEAKER_00"}]},
        PREVIEW_FILE: {
            "status": "needs_review",
            "baseline_srt": POLISHED,
            "source_file": "transcript_polished.srt",
            "index_scope": "current_transcript",
            "candidates": PREVIEW,
        },
    }
    for filename, content in fixtures.items():
        artifacts.write(
            task.id,
            path,
            filename,
            content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
        )
    try:
        yield SimpleNamespace(task=task, path=path, tasks=tasks_db, voices=voices, config=config)
    finally:
        voices._close_conn()
        database.reset_db_path(db_root)


@pytest.fixture
def server(workspace):
    server = FastAPI()
    for module in (voiceprints, tasks, filesystem, pipeline):
        server.include_router(module.router, prefix="/api")

    @server.get("/health")
    def health():
        return {"status": "healthy"}

    return server


@pytest.fixture
def http(server):
    with TestClient(server, client=("127.0.0.1", 54321)) as client:
        yield client


@pytest.fixture
def cli(http, monkeypatch):
    api = MppClient("http://localhost:18000")
    api.close()
    api._client = http
    for commands in (speaker, operations):
        monkeypatch.setattr(commands, "client", lambda: api)

    def invoke(*args, expected=0, input=None):
        result = CliRunner().invoke(
            app,
            ["--local", "--skip-version-check", "--plain", *args],
            input=input,
        )
        assert result.exit_code == expected, (result.output, result.exception)
        return result.stdout

    return SimpleNamespace(invoke=invoke, api=api)


def test_speaker_profile_from_stored_evidence(workspace, http):
    url = f"/api/tasks/{workspace.task.id}/speakers"
    response = http.get(url)
    assert response.status_code == 200, response.text
    dossier = response.json()
    assert (dossier["total_cues"], dossier["speaker_count"], dossier["anonymous_count"]) == (
        5,
        2,
        1,
    )
    assert dossier["source_file"] == "transcript_polished.srt"
    assert dossier["derived_content_stale"] is True
    assert dossier["derived_content_stale_reason"] == "名字待更新"
    assert dossier["visual_participants"]["participants"][0]["evidence"] == [EVIDENCE]
    assert dossier["visual_participants"]["count_estimate"] == 2
    alice, anonymous = dossier["speakers"]
    assert alice["source_label"] == "SPEAKER_00"
    assert alice["status"] == "text_inferred"
    assert alice["provenance"] == "片头身份文字"
    assert alice["duration_seconds"] == 2.0
    assert alice["share_pct"] == 18.18
    assert alice["identity_quotes"][0]["text"] == "Alice at the microphone"
    assert anonymous["sample_quotes"][0]["text"].endswith("It took two years.")
    assert anonymous["voiceprint_suggestions"][0]["current_name"] == "Bob"
    pending, untagged, final = dossier["pending_cues"]
    assert (pending["index"], pending["prev_cue"]["speaker"], pending["next_cue"]["index"]) == (
        2,
        "Alice",
        4,
    )
    assert pending["candidates"] == [
        {"speaker": "Alice", "acoustic_seconds": 2.0, "voice_similarity": 0.87},
    ]
    assert untagged["index"] == 5
    assert final["next_cue"] is None
    assert dossier["has_repair_preview"] is True
    (workspace.path / "transcript_polished.srt").unlink()
    assert http.get(url).json()["source_file"] == "transcript_polished.srt"
    assert http.get(f"/api/tasks/{uuid4()}/speakers").status_code == 404


def test_apply_preview_holds_lineage_across_a_partial_application(workspace, http):
    task_id = workspace.task.id
    url = f"/api/tasks/{task_id}/speakers/assign"
    selected = {"apply_preview": True, "preview_indices": [2]}
    first = http.post(url, json=selected)
    assert first.status_code == 200, first.text
    assert first.json()["indices"] == [2]
    preview = json.loads((workspace.path / PREVIEW_FILE).read_text())
    assert [row["index"] for row in preview["candidates"]] == [7]
    assert preview["baseline_srt"] == (workspace.path / "transcript_polished.srt").read_text()
    second = http.post(url, json={"apply_preview": True})
    assert second.status_code == 200, second.text
    assert second.json()["indices"] == [7]
    assert http.get(f"/api/tasks/{task_id}/speakers/repair-preview").json()["status"] == "applied"
    for name in (
        "transcript_polished.srt",
        "transcript_polished.md",
        "metadata.json",
        PREVIEW_FILE,
    ):
        assert (
            workspace.tasks.get_artifact(task_id, name)["content"]
            == (workspace.path / name).read_text()
        )
    metadata = workspace.tasks.get(task_id).result["metadata"]
    assert metadata["extra"]["speaker_revision"] == 6
    assert metadata["extra"]["speaker_quality"]["unassigned_segments"] == 1
    assert metadata["extra"]["derived_content_stale"] is True


def test_assignment_retries_after_srt_mirror_write_fails(workspace, http, monkeypatch):
    task_id = workspace.task.id
    save_artifact = workspace.tasks.save_artifact
    failures = []

    def fail_first_write(owner_id, name, content, content_type):
        if name == "transcript_polished.srt" and not failures:
            failures.append(name)
            raise OSError("simulated SQLite failure")
        return save_artifact(owner_id, name, content, content_type)

    monkeypatch.setattr(workspace.tasks, "save_artifact", fail_first_write)
    url = f"/api/tasks/{task_id}/speakers/assign"
    request = {"apply_preview": True, "preview_indices": [2], "expected_srt": POLISHED}
    assert http.post(url, json=request).status_code == 500
    assert "[Alice] How did that start?" in (workspace.path / "transcript_polished.srt").read_text()
    exported = workspace.path.parent / "pending-export.zip"
    archive_sync.build_archive_zip(workspace.path, exported)
    with ZipFile(exported) as bundle:
        assert not any("speaker_assignment.pending" in name for name in bundle.namelist())
    recovered = http.post(url, json=request)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["indices"] == [2]
    assert workspace.tasks.get(task_id).result["metadata"]["extra"]["speaker_revision"] == 5
    assert (
        workspace.tasks.get_artifact(task_id, "transcript_polished.srt")["content"]
        == (workspace.path / "transcript_polished.srt").read_text()
    )
    assert http.post(url, json={"apply_preview": True}).json()["indices"] == [7]


def test_platform_raw_cues_can_be_assigned_and_confirmed(workspace, cli, http):
    task_id = workspace.task.id
    for filename in ("transcript_polished.srt", "transcript_polished.md"):
        (workspace.path / filename).unlink()
        workspace.tasks.delete_artifact(task_id, filename)
    raw = (
        "93\n00:00:03,000 --> 00:00:05,000\n[Applause] Welcome.\n\n"
        "94\n00:00:05,000 --> 00:00:07,000\n[Laughter]\n"
    )
    store = get_artifact_store()
    store.write(task_id, workspace.path, "transcript.srt", raw)
    store.write(
        task_id,
        workspace.path,
        "metadata.json",
        json.dumps(
            {
                "title": TITLE,
                "extra": {"subtitle_source": "platform", "raw_speaker_label_contract": 0},
            }
        ),
    )
    url = f"/api/tasks/{task_id}/speakers"
    before = http.get(url).json()
    response = http.post(f"{url}/assign", json={"assignments": [{"index": 93, "speaker": "Alice"}]})
    assert response.status_code == 200, response.text
    assert (workspace.path / "transcript.srt").read_text() == raw
    assert before["speaker_count"] == 0 and before["pending_count"] == 2
    polished = (workspace.path / "transcript_polished.srt").read_text()
    assert "[Alice] [Applause] Welcome." in polished
    assert "[unknown] [Laughter]" in polished
    assert http.get(url).json()["pending_count"] == 1
    assert "confirmed" in cli.invoke("speaker", "confirm", str(task_id))


def test_reject_invalid_assignment_and_raw_or_stale_preview(workspace, http):
    url = f"/api/tasks/{workspace.task.id}/speakers/assign"
    original = workspace.path / "transcript_polished.srt"
    assert http.post(url, json={"assignments": [{"index": 3, "speaker": "Bob"}]}).status_code == 400
    assert http.post(url, json={"apply_preview": True, "preview_indices": [4]}).status_code == 400
    for name in ("", "[Bob]", "Bob\x7f", "<Bob>", "Bob\x00"):
        assert (
            http.post(
                url,
                json={
                    "assignments": [
                        {"index": 2, "speaker": "Valid"},
                        {"index": 4, "speaker": name},
                    ],
                },
            ).status_code
            == 400
        )
    assert http.post(url, json={"expected_srt": RAW, "apply_preview": True}).status_code == 409
    preview_path = workspace.path / PREVIEW_FILE
    preview = json.loads(preview_path.read_text())
    preview.update(index_scope="raw_cues", input_srt=RAW)
    preview_path.write_text(json.dumps(preview))
    denied = http.post(url, json={"apply_preview": True})
    assert denied.status_code == 409
    assert "原始字幕编号" in denied.json()["detail"]
    preview_path.write_text(
        json.dumps(
            {
                **preview,
                "index_scope": "current_transcript",
                "baseline_srt": RAW,
                "input_srt": POLISHED,
            }
        )
    )
    assert http.post(url, json={"apply_preview": True}).status_code == 409
    assert original.read_text() == POLISHED


def test_assignment_preserves_crlf_prose_and_diverged_markdown(workspace, http):
    task_id = workspace.task.id
    source = workspace.path / "transcript_polished.srt"
    original = POLISHED.replace("Here's", "  Here's").replace("years.", "years.  ")
    original = "\ufeff" + original.replace("\n", "\r\n")
    source.write_bytes(original.encode())
    md = workspace.path / "transcript_polished.md"
    md.write_text("Manually edited Markdown")
    response = http.post(
        f"/api/tasks/{task_id}/speakers/assign",
        json={
            "assignments": [{"index": index, "speaker": "Bob"} for index in (2, 4, 5)],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["indices"] == [2, 4, 5]
    expected = (
        original.replace("[UnKnOwN]", "[Bob]")
        .replace("[SPEAKER_01]", "[Bob]")
        .replace("Thanks for having me.", "[Bob] Thanks for having me.")
    )
    assert source.read_bytes() == expected.encode()
    assert workspace.tasks.get_artifact(task_id, source.name)["content"] == expected
    assert md.read_text() == "Manually edited Markdown"
    assert (workspace.path / "transcript.srt").read_text() == RAW


def _link(workspace):
    voices = workspace.voices
    voices.ensure_enrollment_dimension(VECTOR.size)
    person = voices.create_person("Unknown speaker")
    clip = voices.clips_dir / "original.wav"
    clip.write_bytes(b"original clip")
    task_id = str(workspace.task.id)
    sample_id = voices.add_sample(person.id, VECTOR, task_id, 8.0, 0.8, str(clip))
    voices.set_task_speaker(task_id, "SPEAKER_00", sample_id, person.id)
    return person, sample_id, clip


def test_enroll_from_task_reuses_embedding_and_deletes_only_the_selected_sample(
    workspace,
    http,
    monkeypatch,
):
    _, original_id, clip = _link(workspace)

    def unavailable():
        pytest.fail("Stored task embeddings do not require Pyannote.")

    monkeypatch.setattr(enrollment, "get_diarization_service", unavailable)
    payload = {
        "name": "Alice",
        "task_id": str(workspace.task.id),
        "speaker_label": "Alice",
        "notes": "Host",
    }
    response = http.post("/api/voiceprints/enroll", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    sample_id, person = result["sample_id"], result["person"]
    assert result["reused_existing_embedding"] is True
    assert person["name"] == "Alice" and person["sample_count"] == 1
    assert (
        workspace.voices.get_task_speaker(
            str(workspace.task.id),
            "SPEAKER_00",
        )["sample_id"]
        == sample_id
    )
    embeddings = workspace.voices.sample_embeddings([original_id, sample_id])
    assert np.allclose(embeddings[original_id], embeddings[sample_id])
    assert http.post("/api/voiceprints/enroll", json=payload).json()["sample_id"] == sample_id
    url = f"/api/voiceprints/persons/{person['id']}/samples"
    (sample,) = http.get(url).json()
    assert sample["audio_clip_path"] is None
    assert sample["duration_sec"] == 8.0
    deleted = http.delete(f"/api/voiceprints/samples/{sample_id}")
    assert deleted.json() == {"success": True, "sample_id": sample_id}
    assert http.delete(f"/api/voiceprints/samples/{sample_id}").status_code == 404
    assert http.get("/api/voiceprints/persons/nonexistent/samples").status_code == 404
    assert http.get(url).json() == []
    assert clip.read_bytes() == b"original clip"


@pytest.fixture
def model(monkeypatch):
    calls = []
    pipeline = object()

    def extract(audio, intervals, model, clips, prefix):
        assert model is pipeline
        calls.append((audio, list(intervals[["start", "end"]].itertuples(index=False, name=None))))
        clips.mkdir(parents=True, exist_ok=True)
        clip = clips / f"{prefix}enrollment.wav"
        clip.write_bytes(b"mocked voice sample")
        return [enrollment.ExtractedVoiceprint("enrollment", VECTOR, 3.0, 0.84, str(clip))]

    monkeypatch.setattr(
        enrollment,
        "get_diarization_service",
        lambda: SimpleNamespace(get_pyannote_pipeline=lambda: pipeline),
    )
    monkeypatch.setattr(enrollment, "extract_voiceprints", extract)
    return calls


def _audio(path: Path) -> Path:
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 16000 * 6)
    return path


def test_enroll_from_file_respects_filesystem_and_audio_interval(
    workspace,
    server,
    http,
    model,
):
    source = _audio(workspace.path.parent / "reference.wav")
    payload = {"name": "Guest", "audio_path": str(source), "start_sec": 1.0, "end_sec": 4.0}
    with TestClient(server, client=("192.0.2.1", 54321)) as remote:
        assert remote.post("/api/voiceprints/enroll", json=payload).status_code == 403
    assert http.post("/api/voiceprints/enroll", json={**payload, "end_sec": 2.0}).status_code == 400
    first = http.post("/api/voiceprints/enroll", json=payload)
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["reused_existing_embedding"] is False
    assert model == [(source, [(1.0, 4.0)])]
    sample = workspace.voices.get_sample(result["sample_id"])
    assert sample["duration_sec"] == 3.0
    clip = Path(workspace.voices.clip_path_for_sample(result["sample_id"]))
    assert clip.is_file()
    second = http.post("/api/voiceprints/enroll", json=payload).json()
    assert second["person"]["id"] == result["person"]["id"]
    assert second["person"]["sample_count"] == 2
    url = f"/api/voiceprints/persons/{result['person']['id']}/samples"
    assert [row["sample_id"] for row in http.get(url).json()] == [
        second["sample_id"],
        result["sample_id"],
    ]
    assert http.delete(f"/api/voiceprints/samples/{sample['sample_id']}").status_code == 200
    assert not clip.exists()


def test_enroll_reports_unavailable_pyannote(workspace, http, monkeypatch):
    def not_installed():
        raise ImportError("pyannote")

    monkeypatch.setattr(enrollment, "get_diarization_service", not_installed)
    source = _audio(workspace.path / "reference.wav")
    response = http.post(
        "/api/voiceprints/enroll", json={"name": "Guest", "audio_path": str(source)}
    )
    assert response.status_code == 503, response.text
    assert "Pyannote" in response.json()["detail"]
    assert workspace.voices.find_person_by_name("Guest") is None


def test_enroll_from_current_task_cue_when_no_voiceprint_exists(workspace, http, model):
    payload = {"name": "Alice", "task_id": str(workspace.task.id), "speaker_label": "SPEAKER_00"}
    assert http.post("/api/voiceprints/enroll", json=payload).status_code == 409
    audio = _audio(workspace.path / "interview.wav")
    response = http.post("/api/voiceprints/enroll", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["reused_existing_embedding"] is False
    assert model == [(audio, [(1.0, 3.0)])]


def test_cli_inspection_archive_reference_and_local_offline(workspace, cli, monkeypatch):
    task_id = str(workspace.task.id)
    invoke = cli.invoke
    assert "Alice at the microphone" in invoke("speaker", "inspect", task_id, "alice")
    listed = invoke("speaker", "list", TITLE)
    assert "frame:8.00 [Alice | Host]" in listed and "derived_content_stale" in listed
    pending = invoke("speaker", "pending", task_id)
    assert "#1 [Alice]" in pending and ">>> [UNKNOWN] How did that start? <<<" in pending
    assert "#4 [SPEAKER_01]" in pending and "voice similarity 0.87" in pending
    monkeypatch.setattr(cli.api, "ping", lambda: False)
    assert "Voiceprint candidate\tBob" in invoke("speaker", "inspect", TITLE)
    assert "Thanks for having me." in invoke("speaker", "pending", task_id)


def test_cli_assignment_preview_confirm_and_queued_repair(workspace, cli, monkeypatch):
    task_id = str(workspace.task.id)
    invoke = cli.invoke
    queued = []

    async def repair(request_task_id, mode):
        assert request_task_id == workspace.task.id
        queued.append(mode)
        return True

    monkeypatch.setattr(queue, "get_task_queue", lambda: SimpleNamespace(repair_speakers=repair))
    monkeypatch.setattr(
        cli.api,
        "stream_task_events",
        lambda requested_id: iter(
            [
                {"type": "snapshot", "data": {"status": "processing"}},
                {"type": "completed", "task_id": requested_id},
            ]
        ),
    )
    assert "INDEX\tTIMESTAMP\tOLD\tPROPOSED\tTEXT" in invoke("speaker", "preview", task_id)
    assert "INDEX\tTIMESTAMP" in invoke("speaker", "repair", task_id, "--mode", "failed", "--wait")
    invoke("speaker", "autofill", task_id)
    invoke("speaker", "autofill", task_id, "--wait")
    assert queued == ["failed", "auto", "auto"]
    assert "updated_cues\t2" in invoke("speaker", "apply-preview", task_id, "--all", "--yes")
    assert "indices\t2, 4, 5" in invoke("speaker", "assign", task_id, "Carol", "--cue", "2,4-5")
    assert "confirmed" in invoke("speaker", "confirm", task_id)
    assert (
        "[Carol] Here's the whole story."
        in (workspace.path / "transcript_polished.srt").read_text()
    )


def test_cli_voiceprint_samples_delete_and_enroll(workspace, cli, model):
    invoke = cli.invoke
    _link(workspace)
    task_id = str(workspace.task.id)
    enrolled = invoke(
        "voiceprint",
        "enroll",
        "Alice",
        "--from-task",
        task_id,
        "--speaker",
        "Alice",
    )
    assert "Reused embedding" in enrolled
    sample_id = enrolled.splitlines()[-1].split("\t")[-1]
    assert sample_id in invoke("voiceprint", "samples", "Alice")
    invoke("voiceprint", "sample-delete", sample_id, expected=2)
    assert sample_id in invoke("voiceprint", "sample-delete", sample_id, "--yes")
    assert sample_id not in invoke("voiceprint", "samples", "Alice")
    source = _audio(workspace.path / "guest.wav")
    assert "Extracted embedding" in invoke(
        "voiceprint",
        "enroll",
        "Guest",
        "--from-file",
        str(source),
        "--start",
        "1",
        "--end",
        "3",
    )
    guest = workspace.voices.find_person_by_name("Guest")
    assert "Samples\t1" in invoke("voiceprint", "show", guest.id)
    assert "Notes\tNew note" in invoke("voiceprint", "update", guest.id, "--notes", "New note")
    alice_id = workspace.voices.find_person_by_name("Alice").id
    merged = invoke("voiceprint", "merge", alice_id, guest.id, "--yes")
    assert "Name\tAlice" in merged and "Samples\t1" in merged
    assert "SAMPLE ID" in invoke("voiceprint", "samples", alice_id)


def test_cli_rename_conflict_and_regeneration(workspace, cli, monkeypatch):
    invoke = cli.invoke
    task_id = str(workspace.task.id)
    _link(workspace)
    target = workspace.voices.create_person("Host")
    workspace.voices.add_sample(target.id, VECTOR, None, 3.0, 0.7, None)
    assert not invoke("speaker", "rename", task_id, "Alice", "Host", expected=4)
    monkeypatch.setattr(CliContext, "interactive", property(lambda self: True))
    reruns = []
    monkeypatch.setattr(
        cli.api, "checkpoint_rerun", lambda ref: reruns.append(ref) or {"task_id": ref}
    )
    output = invoke("speaker", "rename", task_id, "Alice", "Host", "--regenerate", input="merge\n")
    assert "derived_content_stale" in output and "Regeneration queued." in output
    assert reruns == [task_id]
    assert workspace.voices.get_person(target.id).sample_count == 2
