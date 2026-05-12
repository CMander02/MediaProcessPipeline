"""Shared ASR service contracts.

The pipeline intentionally exposes a small boundary here instead of a plugin
framework. A provider must produce transcript segments and SRT; optional
diarization hooks are used by voiceprint matching when available.
"""

from typing import Any, Protocol

from app.models import TranscriptSegment


class ASRService(Protocol):
    """Minimal contract required by the pipeline transcription step."""

    def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        diarize: bool = True,
        num_speakers: int | None = None,
    ) -> dict[str, Any]:
        """Transcribe an audio file and return provider-native result data."""

    def to_segments(self, result: dict[str, Any]) -> list[TranscriptSegment]:
        """Convert provider-native result data to pipeline transcript segments."""

    def to_srt(self, segments: list[TranscriptSegment]) -> str:
        """Render transcript segments as SRT."""


class DiarizationCacheProvider(Protocol):
    """Optional hooks used by voiceprint extraction."""

    def get_pyannote_pipeline(self) -> Any | None:
        """Return the loaded pyannote pipeline when the provider owns one."""

    def get_last_diarization(self) -> tuple[Any, str | None]:
        """Return the most recent diarization dataframe and source audio path."""


class ReleasableASRService(Protocol):
    """Optional model lifecycle hook for VRAM cleanup."""

    def release(self) -> None:
        """Release loaded ASR/diarization resources."""
