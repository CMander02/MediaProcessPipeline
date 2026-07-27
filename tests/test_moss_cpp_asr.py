import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.recognition import moss_cpp_asr as moss_mod  # noqa: E402
from app.services.recognition.moss_cpp_asr import MossCppASRService  # noqa: E402


def test_moss_cpp_normalizes_segments_and_speaker_labels():
    service = MossCppASRService()
    result = {
        "segments": [
            {"start": 0.25, "end": 1.5, "speaker": "S01", "text": " 你好 "},
            {"start": 1.5, "end": 2.75, "speaker": "S02", "text": "世界"},
        ]
    }

    segments = service.to_segments(result)
    srt = service.to_srt(segments)

    assert [segment.speaker for segment in segments] == ["SPEAKER_00", "SPEAKER_01"]
    assert [segment.text for segment in segments] == ["你好", "世界"]
    assert "00:00:00,250 --> 00:00:01,500" in srt
    assert "[SPEAKER_01] 世界" in srt


def test_moss_cpp_splits_long_audio_and_stitches_speakers(monkeypatch):
    service = MossCppASRService()
    monkeypatch.setattr(moss_mod, "resolve_moss_cpp_binary", lambda _path="": "moss.exe")
    monkeypatch.setattr(moss_mod, "resolve_moss_cpp_model", lambda _path="": "model.gguf")
    monkeypatch.setattr(service, "_probe_duration", lambda _path: 2500.0)

    prepared: list[tuple[int, float, float]] = []

    def prepare_chunk(_audio, _temp, *, chunk_index, start_sec, duration_sec):
        prepared.append((chunk_index, start_sec, duration_sec))
        return str(ROOT / f"missing-{chunk_index}.wav")

    payloads = {
        1: [
            {"start": 1138.0, "end": 1160.0, "speaker": "S01", "text": "甲"},
            {"start": 1160.0, "end": 1200.0, "speaker": "S02", "text": "乙"},
        ],
        2: [
            {"start": 0.0, "end": 20.0, "speaker": "S02", "text": "甲"},
            {"start": 20.0, "end": 60.0, "speaker": "S01", "text": "乙"},
            {"start": 70.0, "end": 100.0, "speaker": "S02", "text": "甲继续"},
            {"start": 100.0, "end": 130.0, "speaker": "S01", "text": "乙继续"},
        ],
        3: [
            {"start": 70.0, "end": 100.0, "speaker": "S02", "text": "甲结尾"},
        ],
    }

    monkeypatch.setattr(service, "_prepare_wav_chunk", prepare_chunk)
    monkeypatch.setattr(
        service,
        "_run_cli",
        lambda **kwargs: payloads[kwargs["chunk_index"]],
    )

    progress: list[dict] = []
    result = service.transcribe(
        "long.wav",
        chunk_duration_sec=1200,
        chunk_overlap_sec=60,
        max_new_tokens=8192,
        progress_callback=progress.append,
    )

    assert prepared == [
        (1, 0.0, 1200.0),
        (2, 1140.0, 1200.0),
        (3, 2280.0, 220.0),
    ]
    assert result["chunked"] is True
    assert result["chunk_count"] == 3
    assert result["speakers"] == ["SPEAKER_00", "SPEAKER_01"]
    assert [(item["speaker"], item["text"]) for item in result["segments"]] == [
        ("SPEAKER_00", "甲"),
        ("SPEAKER_01", "乙"),
        ("SPEAKER_00", "甲继续"),
        ("SPEAKER_01", "乙继续"),
        ("SPEAKER_00", "甲结尾"),
    ]
    assert result["segments"][-1]["start"] == 2350.0
    assert progress[-1]["progress"] == 1.0
