import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

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
