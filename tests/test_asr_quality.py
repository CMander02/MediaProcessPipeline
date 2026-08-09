from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.recognition.quality import assess_asr_text, filter_asr_segments  # noqa: E402


def test_asr_quality_isolates_repetition_and_keeps_normal_speech():
    normal = "这一段讨论合成数据、模型训练和评测方法。"
    hallucination = "la " * 500

    assert assess_asr_text(normal, 5)["valid"] is True
    rejected = assess_asr_text(hallucination, 10)
    assert rejected["valid"] is False
    assert "repetition" in rejected["reasons"]

    accepted, diagnostics = filter_asr_segments(
        [
            {"start": 0, "end": 5, "text": normal},
            {"start": 5, "end": 15, "text": hallucination},
        ]
    )
    assert accepted == [{"start": 0, "end": 5, "text": normal}]
    assert diagnostics[0]["segment_index"] == 1
    assert diagnostics[0]["action"] == "isolated"
