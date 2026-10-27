import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core.settings import RuntimeSettings
from app.core.workflow import WorkflowConfig, snapshot_workflow


def test_workflow_snapshot_merges_partial_override_and_survives_settings_change():
    settings = RuntimeSettings(pipeline_workflow={"separate": False, "polish": False})
    options = snapshot_workflow({"workflow": {"polish": True}}, settings)
    assert options["workflow"]["separate"] is False
    assert options["workflow"]["polish"] is True
    assert options["force_asr"] is False
    settings.pipeline_workflow = WorkflowConfig()
    assert snapshot_workflow(options, settings) == options


def test_unconfigured_legacy_options_retain_subtitle_strategy():
    options = {"force_asr": False, "skip_separation": True}
    assert snapshot_workflow(options, RuntimeSettings()) == options


@pytest.mark.parametrize("global_force,override,expected", [
    (False, {}, False),
    (True, {}, True),
    (True, {"force_asr": False}, False),
    (False, {"force_asr": True}, True),
])
def test_workflow_preserves_explicit_asr_strategy(global_force, override, expected):
    settings = RuntimeSettings(pipeline_workflow={}, force_asr=global_force)
    options = snapshot_workflow(override, settings)
    assert options["force_asr"] is expected
    settings.force_asr = not global_force
    assert snapshot_workflow(options, settings) == options


@pytest.mark.parametrize("invalid", [{"transcribe": False}, {"separate": "false"}, {"typo": True}])
def test_workflow_rejects_disabling_asr_and_malformed_switches(invalid):
    with pytest.raises(ValidationError):
        snapshot_workflow({"workflow": invalid}, RuntimeSettings())


def test_workflow_does_not_mutate_input():
    override = {"separate": False}
    snapshot_workflow({"workflow": override}, RuntimeSettings())
    assert override == {"separate": False}


def test_task_entry_point_freezes_global_workflow(tmp_path, monkeypatch):
    from starlette.requests import Request
    from app.api.routes import tasks
    from app.models import TaskCreate, TaskType
    rt = RuntimeSettings(data_root=str(tmp_path / "library"), pipeline_workflow={"separate": False})
    monkeypatch.setattr(tasks, "get_runtime_settings", lambda: rt)
    task, _ = tasks._prepare_task(
        TaskCreate(task_type=TaskType.PIPELINE, source="https://example.com/video"),
        Request({"type": "http", "client": ("localhost", 1), "headers": []}),
        "https://example.com/video",
    )
    assert task.options["workflow"]["separate"] is False
    assert task.options["force_asr"] is False


def test_checkpoint_restores_the_actual_vocals_file(tmp_path):
    from app.core.pipeline_steps.context import PipelineContext
    from app.models import Task, TaskType
    raw = tmp_path / "lecture.wav"
    vocals = tmp_path / "lecture_(Vocals).wav"
    raw.touch()
    vocals.touch()
    ctx = PipelineContext(task=Task(task_type=TaskType.PIPELINE, source="fixture"), rt=RuntimeSettings(), task_dir=tmp_path)
    assert ctx.restore_audio_paths()
    assert ctx.vocals_path == str(vocals)


def test_audio_merge_is_bounded_and_preserves_frames(tmp_path, monkeypatch):
    import numpy as np
    import soundfile as sf
    from app.services.preprocessing.audio_merge import merge_wav_chunks

    chunks = []
    for index, frames in enumerate((100000, 130000, 7000)):
        path = tmp_path / f"{index}.wav"
        sf.write(path, np.full((frames, 2), index / 8), 44100, subtype="PCM_16")
        chunks.append(path)
    original_read = sf.SoundFile.read

    def bounded_read(self, frames=-1, *args, **kwargs):
        assert 0 <= frames <= 65536
        return original_read(self, frames, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sf.SoundFile, "read", bounded_read)
        merge_wav_chunks(chunks, tmp_path / "vocals.wav")
    audio, rate = sf.read(tmp_path / "vocals.wav")
    assert rate == 44100 and audio.shape == (237000, 2)
    assert audio[100000, 0] == pytest.approx(1 / 8)
    assert audio[-1, 0] == pytest.approx(1 / 4)


def test_failed_audio_merge_preserves_existing_output(tmp_path):
    import numpy as np
    import soundfile as sf
    from app.services.preprocessing.audio_merge import merge_wav_chunks

    source = tmp_path / "source.wav"
    other = tmp_path / "other.wav"
    output = tmp_path / "output.wav"
    sf.write(source, np.zeros(10), 16000)
    sf.write(other, np.zeros(10), 44100)
    output.write_bytes(b"previous output")
    with pytest.raises(ValueError, match="incompatible"):
        merge_wav_chunks([source, other], output)
    assert output.read_bytes() == b"previous output"
    assert not (tmp_path / "output.wav.merging").exists()
