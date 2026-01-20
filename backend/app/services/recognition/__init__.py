"""Recognition service - WhisperX transcription."""

from app.services.recognition.whisperx import WhisperXService, transcribe_audio

__all__ = ["WhisperXService", "transcribe_audio"]
