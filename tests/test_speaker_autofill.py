from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import settings
from app.services.analysis import llm
from app.services.analysis import speaker_repair as repair
from app.services.analysis.prompts.speaker_identity import PARTICIPANT_BINDING_SYSTEM_PROMPT
from app.services.analysis.speaker_resolution import RESOLVE_SYSTEM_PROMPT
from app.services.analysis.transcript_outputs import _parse_srt, _split_speaker_prefix

TITLE = "美国人要用AI创死所有人？【瓜熟迪落拉XEric Hovagim02】"


def transcript(rows):
    return "\n\n".join(
        f"{index}\n00:00:{index * 2:02d},000 --> 00:00:{index * 2 + 2:02d},000\n[{speaker}] {text}"
        for index, (speaker, text) in enumerate(rows, 1)
    ) + "\n"


def labels(path: Path):
    return [_split_speaker_prefix(cue["text"])[0] for cue in _parse_srt(path.read_text(encoding="utf-8"))]


@pytest.fixture
def archive(tmp_path, monkeypatch):
    polished = transcript([
        ("Unknown-e64e", "Why are Americans so angry about data centers?"),
        ("unknown", "Yeah, it's very clear cut."),
        ("Unknown-c072", "They are built for immediate return on investment."),
        ("Unknown-c072", "Corporations bribe city councils."),
    ])
    (tmp_path / "transcript_polished.srt").write_text(polished, encoding="utf-8")
    (tmp_path / "transcript_polished.md").write_text("手工整理过的文稿\n", encoding="utf-8")
    (tmp_path / "transcript.srt").write_text(transcript([
        ("Unknown-e64e", "Why are Americans so angry about data centers?"),
        ("Unknown-c072", "Yeah, it's very clear cut. They are built for immediate return."),
    ]), encoding="utf-8")
    (tmp_path / "metadata.json").write_text(json.dumps({
        "title": TITLE, "uploader": "瓜熟迪落拉", "description": "",
        "extra": {"subtitle_source": "asr", "speaker_label_contract": 1, "speakers": ["Unknown-c072", "Unknown-e64e"],
                  "speaker_quality": {"status": "needs_review", "unassigned_segments": 1, "failed_chunks": 2}},
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "diarization.json").write_text(json.dumps({"turns": [
        {"start": 4.0, "end": 4.9, "speaker": "SPEAKER_00"}, {"start": 4.9, "end": 6.0, "speaker": "SPEAKER_01"},
    ]}), encoding="utf-8")
    (tmp_path / "speaker_map.json").write_text(json.dumps({"version": 1, "mappings": [
        {"source_label": "SPEAKER_00", "current_name": "Unknown-e64e", "status": "voiceprint_new"},
        {"source_label": "SPEAKER_01", "current_name": "Unknown-c072", "status": "voiceprint_new"},
    ]}), encoding="utf-8")
    writes = []

    def write(task_id, directory, name, content, expected_content=None):
        target = directory / name
        if expected_content is not None and target.read_text(encoding="utf-8") != expected_content:
            raise FileExistsError("changed")
        writes.append(name)
        target.write_text(content, encoding="utf-8")

    monkeypatch.setattr(repair, "repair_directory", lambda task: tmp_path)
    monkeypatch.setattr(repair, "get_artifact_store", lambda: SimpleNamespace(write=write))
    monkeypatch.setattr(repair, "get_task_store", lambda: SimpleNamespace(save=lambda task: None))
    monkeypatch.setattr(settings, "_runtime_settings", settings.RuntimeSettings(data_root=str(tmp_path)))
    from app.services.analysis import speaker_identity_artifacts
    suggestions = [{"source_label": "Unknown-6610", "current_name": "Eric Hovagim", "similarity": 0.68, "reason": "声音相似度 0.68"}]
    monkeypatch.setattr(speaker_identity_artifacts, "voice_suggestions", lambda *args, **kwargs: suggestions)
    return SimpleNamespace(directory=tmp_path, writes=writes)


def model(monkeypatch, on_resolve=None):
    async def answer(prompt, **kwargs):
        system = kwargs["system_prompt"]
        if system == RESOLVE_SYSTEM_PROMPT:
            if on_resolve:
                on_resolve()
            return json.dumps({"assignments": [{"index": 2, "speaker": "Unknown-c072", "reason": "回答上一句的提问"}]},
                              ensure_ascii=False)
        if system == PARTICIPANT_BINDING_SYSTEM_PROMPT:
            voices = [voice["id"] for voice in json.loads(prompt.split("资料：\n")[1])["voices"]]
            rows = [{"source_label": "Unknown-e64e", "current_name": "瓜熟迪落拉", "reason": "开场提问"},
                    {"source_label": "Unknown-c072", "current_name": "Eric Hovagim", "reason": "长篇回答"}]
            return json.dumps({"mappings": [row for row in rows if row["source_label"] in voices]}, ensure_ascii=False)
        return json.dumps({"mappings": []})

    call = AsyncMock(side_effect=answer)
    monkeypatch.setattr(llm, "get_llm_service", lambda: SimpleNamespace(_call=call))
    return call


TASK = SimpleNamespace(id="task-1", status="completed", options={}, result={})


@pytest.mark.asyncio
async def test_auto_fill_resolves_pending_lines_and_names_voices_from_the_title(archive, monkeypatch):
    model(monkeypatch)
    result = await repair.run_auto_fill(TASK)
    assert result["resolved_lines"] == 1 and result["names"] == 2
    directory = archive.directory
    assert labels(directory / "transcript_polished.srt") == ["瓜熟迪落拉", "Eric Hovagim", "Eric Hovagim", "Eric Hovagim"]
    assert labels(directory / "transcript.srt") == ["瓜熟迪落拉", "Eric Hovagim"]
    # A hand-edited Markdown export is left alone.
    assert (directory / "transcript_polished.md").read_text(encoding="utf-8") == "手工整理过的文稿\n"
    extra = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))["extra"]
    assert extra["speakers"] == ["Eric Hovagim", "瓜熟迪落拉"]
    assert extra["speaker_quality"]["unassigned_segments"] == 0 and extra["speaker_quality"]["failed_chunks"] == 0
    assert extra["speaker_quality"]["status"] == "completed"
    assert set(extra["speaker_name_sources"]) == {"瓜熟迪落拉", "Eric Hovagim"}
    assert extra["derived_content_stale"] is True
    speaker_map = json.loads((directory / "speaker_map.json").read_text(encoding="utf-8"))
    assert {row["current_name"]: row["status"] for row in speaker_map["mappings"]} == {
        "瓜熟迪落拉": "source_inferred", "Eric Hovagim": "source_inferred",
    }
    assert result["metadata"]["extra"]["speakers"] == extra["speakers"]


@pytest.mark.asyncio
async def test_names_the_user_set_stay_and_only_anonymous_voices_are_matched(archive, monkeypatch):
    path = archive.directory / "transcript_polished.srt"
    path.write_text(path.read_text(encoding="utf-8").replace("Unknown-e64e", "瓜熟迪落拉"), encoding="utf-8")
    call = model(monkeypatch)
    await repair.run_auto_fill(TASK)
    assert labels(path) == ["瓜熟迪落拉", "Eric Hovagim", "Eric Hovagim", "Eric Hovagim"]
    binding = next(call.args[0] for call in call.call_args_list
                   if call.kwargs["system_prompt"] == PARTICIPANT_BINDING_SYSTEM_PROMPT)
    payload = json.loads(binding.split("资料：\n")[1])
    assert [row["name"] for row in payload["participants"]] == ["Eric Hovagim"]


@pytest.mark.asyncio
async def test_an_edit_during_the_fill_wins_and_the_task_stays_finished(archive, monkeypatch):
    path = archive.directory / "transcript_polished.srt"
    edited = path.read_text(encoding="utf-8").replace("clear cut", "clear-cut")
    model(monkeypatch, on_resolve=lambda: path.write_text(edited, encoding="utf-8"))
    with pytest.raises(ValueError):
        await repair.run_auto_fill(TASK)
    assert path.read_text(encoding="utf-8") == edited
    assert archive.writes == []

    import app.core.pipeline_steps.state as state
    monkeypatch.setattr(state, "_raise_if_cancelled", AsyncMock())
    monkeypatch.setattr(state, "_update_step_progress", AsyncMock())

    def missing(task, mode):
        raise ValueError("任务缺少字幕")

    monkeypatch.setattr(repair, "repair_inputs", missing)
    task = SimpleNamespace(id="task-1", status="processing", result={"summary": "保留"},
                           options={"speaker_repair": {"mode": "auto", "status": "queued", "run_id": "r", "attempt": 1}})
    await repair.run_speaker_repair(task)
    assert task.options["speaker_repair"]["status"] == "failed"
    assert task.result["speaker_repair"] == {"status": "failed", "error": "任务缺少字幕"}
    assert task.result["summary"] == "保留"
