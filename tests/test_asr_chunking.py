import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.recognition.chunking import ASRChunker, AudioChunk  # noqa: E402


def test_explicit_silero_onnx_can_fail_without_fixed_chunk_fallback(monkeypatch, tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"wav")
    chunker = ASRChunker(silero_onnx_model_path="model.onnx")

    def fail(*args, **kwargs):
        raise RuntimeError("onnxruntime missing")

    monkeypatch.setattr(chunker, "_silero_onnx_chunks", fail)
    monkeypatch.setattr(
        chunker,
        "fixed_chunks",
        lambda *args, **kwargs: [AudioChunk(0.0, 30.0)],
    )

    with pytest.raises(RuntimeError, match="onnxruntime missing"):
        chunker.chunks(audio, strategy="silero_onnx", allow_fallback=False)

    assert chunker.chunks(audio, strategy="auto", allow_fallback=True) == [
        AudioChunk(0.0, 30.0)
    ]
