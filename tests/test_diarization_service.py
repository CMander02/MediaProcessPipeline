import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.models import TranscriptSegment  # noqa: E402
from app.services import recognition  # noqa: E402
from app.services.recognition.diarization import DiarizationService  # noqa: E402


def test_assign_speakers_uses_largest_global_turn_overlap():
    segments = [
        {"start": 0.2, "end": 3.0, "text": "主持人开场"},
        {"start": 4.8, "end": 7.0, "text": "嘉宾回答"},
        {"start": 10.0, "end": 10.0, "text": "无时间戳"},
    ]
    turns = [
        {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00"},
        {"start": 5.0, "end": 10.0, "speaker": "SPEAKER_01"},
    ]

    mapped = DiarizationService.assign_speakers(segments, turns)

    assert mapped[0]["speaker"] == "SPEAKER_00"
    assert mapped[1]["speaker"] == "SPEAKER_01"
    assert "speaker" not in mapped[2]
    assert "speaker" not in segments[0]


def test_assign_speakers_splits_a_cue_that_crosses_speaker_turns():
    mapped = DiarizationService.assign_speakers(
        [{"start": 0.0, "end": 10.0, "text": "前半段内容后半段内容"}],
        [
            {"start": 0.0, "end": 4.0, "speaker": "SPEAKER_00"},
            {"start": 4.0, "end": 10.0, "speaker": "SPEAKER_01"},
        ],
    )

    assert len(mapped) == 2
    assert [(item["start"], item["end"], item["speaker"]) for item in mapped] == [
        (0.0, 4.0, "SPEAKER_00"),
        (4.0, 10.0, "SPEAKER_01"),
    ]
    assert "".join(item["text"] for item in mapped) == "前半段内容后半段内容"


def test_annotation_to_turns_supports_pyannote_3_annotation():
    class Segment:
        def __init__(self, start, end):
            self.start = start
            self.end = end

    class Annotation:
        def itertracks(self, yield_label=False):
            assert yield_label is True
            yield Segment(2.3456, 4.5678), "track", "SPEAKER_01"
            yield Segment(0.1111, 1.2222), "track", "SPEAKER_00"

    turns = DiarizationService._annotation_to_turns(Annotation())

    assert turns == [
        {"start": 0.111, "end": 1.222, "speaker": "SPEAKER_00"},
        {"start": 2.346, "end": 4.568, "speaker": "SPEAKER_01"},
    ]


def test_diarization_cache_round_trip(tmp_path):
    cache_path = tmp_path / "diarization.json"
    signature = {"version": 1, "audio_path": "podcast.wav", "num_speakers": 2}
    turns = [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]

    DiarizationService._write_cache(cache_path, signature, turns)

    assert DiarizationService._load_cache(cache_path, signature) == turns
    assert json.loads(cache_path.read_text(encoding="utf-8"))["signature"] == signature
    assert DiarizationService._load_cache(cache_path, {**signature, "num_speakers": 3}) is None


def test_checkpoint_file_resolves_model_directory(tmp_path):
    model_dir = tmp_path / "segmentation"
    model_dir.mkdir()
    checkpoint = model_dir / "pytorch_model.bin"
    checkpoint.write_bytes(b"weights")

    assert DiarizationService._checkpoint_file(
        str(model_dir), "segmentation"
    ) == checkpoint.resolve()


@pytest.mark.parametrize("provider", ["qwen3_gguf", "qwen3"])
@pytest.mark.asyncio
async def test_transcribe_audio_runs_global_diarization_on_original_audio(
    tmp_path,
    monkeypatch,
    provider,
):
    asr_audio = tmp_path / "vocals.wav"
    original_audio = tmp_path / "original.wav"
    asr_audio.write_bytes(b"asr")
    original_audio.write_bytes(b"original")

    binding = SimpleNamespace(
        provider=provider,
        language="zh",
        diarize=True,
        num_speakers=2,
        chunk_strategy="silero_onnx",
        request_kwargs={},
    )
    monkeypatch.setattr(
        "app.core.model_router.resolve_asr_binding",
        lambda *args, **kwargs: binding,
    )

    class ASRService:
        def transcribe(self, *args, **kwargs):
            captured["asr_diarize"] = kwargs.get("diarize")
            return {
                "language": "zh",
                "segments": [{"start": 0.0, "end": 2.0, "text": "你好"}],
            }

        def to_segments(self, result):
            return [TranscriptSegment(**item) for item in result["segments"]]

        def to_srt(self, segments):
            return "\n".join(f"[{item.speaker}] {item.text}" for item in segments)

    monkeypatch.setattr(recognition, "get_asr_service", lambda provider=None: ASRService())
    captured = {}

    class GlobalDiarization:
        def apply(self, audio_path, segments, **kwargs):
            captured["audio_path"] = str(audio_path)
            captured["segments"] = segments
            captured["kwargs"] = kwargs
            return {
                "segments": [{**segments[0], "speaker": "SPEAKER_00"}],
                "speakers": ["SPEAKER_00"],
                "speaker_count": 1,
                "diarization": "pyannote",
                "cache_hit": False,
            }

    monkeypatch.setattr(recognition, "get_diarization_service", GlobalDiarization)

    result = await recognition.transcribe_audio(
        str(asr_audio),
        output_dir=tmp_path,
        provider="qwen3_gguf",
        num_speakers=2,
        min_speakers=1,
        max_speakers=3,
        diarization_audio_path=str(original_audio),
    )

    assert captured["audio_path"] == str(original_audio)
    assert captured["kwargs"]["num_speakers"] == 2
    assert captured["kwargs"]["min_speakers"] == 1
    assert captured["kwargs"]["max_speakers"] == 3
    assert captured["kwargs"]["cache_path"] == tmp_path / "diarization.json"
    if provider == "qwen3":
        assert captured["asr_diarize"] is False
    assert result["segments"][0]["speaker"] == "SPEAKER_00"
    assert result["speaker_count"] == 1
    assert result["diarization"] == "pyannote"
