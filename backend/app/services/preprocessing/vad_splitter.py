"""VAD-based audio splitting for long audio files."""

import logging
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 30 minutes in seconds
SPLIT_THRESHOLD_SECONDS = 30 * 60


class VADSplitter:
    """Split long audio files at VAD silence points near 30-minute boundaries."""

    def __init__(self):
        self._model = None
        self._utils = None

    def _load_model(self):
        """Lazy load Silero VAD model."""
        if self._model is None:
            import torch
            logger.info("Loading Silero VAD model...")
            model, utils = torch.hub.load(
                repo_or_dir='snakers4/silero-vad',
                model='silero_vad',
                force_reload=False,
                onnx=False,
                trust_repo=True
            )
            self._model = model
            self._utils = utils
            logger.info("Silero VAD model loaded")

    def get_speech_timestamps(
        self,
        audio_path: str | Path,
        sampling_rate: int = 16000
    ) -> list[dict[str, int]]:
        """Get speech timestamps from audio file."""
        self._load_model()

        import torchaudio

        # Load audio
        waveform, sr = torchaudio.load(str(audio_path))

        # Resample if needed
        if sr != sampling_rate:
            resampler = torchaudio.transforms.Resample(sr, sampling_rate)
            waveform = resampler(waveform)

        # Convert to mono if needed
        if waveform.shape[0] > 1:
            waveform = waveform.mean(dim=0, keepdim=True)

        # Flatten
        waveform = waveform.squeeze()

        # Get VAD function from utils
        get_speech_ts = self._utils[0]

        # Get speech timestamps
        speech_timestamps = get_speech_ts(
            waveform,
            self._model,
            sampling_rate=sampling_rate,
            return_seconds=False
        )

        return speech_timestamps

    def get_silence_points(
        self,
        audio_path: str | Path,
        sampling_rate: int = 16000
    ) -> list[float]:
        """Get silence points (gaps between speech) in seconds."""
        speech_ts = self.get_speech_timestamps(audio_path, sampling_rate)

        silence_points = []
        for i in range(len(speech_ts) - 1):
            # End of current speech segment
            end_sample = speech_ts[i]['end']
            # Start of next speech segment
            start_sample = speech_ts[i + 1]['start']

            # Calculate midpoint of silence gap
            gap_midpoint = (end_sample + start_sample) / 2
            silence_points.append(gap_midpoint / sampling_rate)

        return silence_points

    def find_split_points(
        self,
        audio_duration: float,
        silence_points: list[float],
        target_segment_duration: float = SPLIT_THRESHOLD_SECONDS
    ) -> list[float]:
        """
        Find optimal split points near target boundaries.

        For each 30-minute boundary, finds the closest silence point.
        """
        if audio_duration <= target_segment_duration:
            return []

        split_points = []
        num_segments = int(audio_duration // target_segment_duration)

        for i in range(1, num_segments + 1):
            target_time = i * target_segment_duration

            # Find closest silence point to target
            if not silence_points:
                # No silence points, use target directly
                split_points.append(target_time)
            else:
                closest = min(silence_points, key=lambda x: abs(x - target_time))
                # Only use if within 2 minutes of target
                if abs(closest - target_time) <= 120:
                    split_points.append(closest)
                else:
                    split_points.append(target_time)

        return split_points

    def split_audio(
        self,
        audio_path: str | Path,
        output_dir: Path | None = None,
        target_segment_duration: float = SPLIT_THRESHOLD_SECONDS
    ) -> list[dict[str, Any]]:
        """
        Split audio file at VAD silence points.

        Returns list of dicts with:
        - path: Path to segment file
        - start_time: Start time offset in seconds
        - end_time: End time in seconds
        - duration: Segment duration in seconds
        """
        audio_path = Path(audio_path)

        import torchaudio

        # Load audio to get duration
        waveform, sr = torchaudio.load(str(audio_path))
        duration = waveform.shape[1] / sr

        logger.info(f"Audio duration: {duration:.1f}s ({duration/60:.1f} min)")

        # If short enough, return as single segment
        if duration <= target_segment_duration:
            return [{
                'path': str(audio_path),
                'start_time': 0.0,
                'end_time': duration,
                'duration': duration,
                'is_original': True
            }]

        # Get silence points
        logger.info("Analyzing audio for silence points...")
        silence_points = self.get_silence_points(audio_path, 16000)
        logger.info(f"Found {len(silence_points)} silence points")

        # Find split points
        split_points = self.find_split_points(duration, silence_points, target_segment_duration)
        logger.info(f"Split points: {split_points}")

        if not split_points:
            return [{
                'path': str(audio_path),
                'start_time': 0.0,
                'end_time': duration,
                'duration': duration,
                'is_original': True
            }]

        # Create output directory
        if output_dir is None:
            output_dir = Path(tempfile.mkdtemp(prefix="vad_split_"))
        output_dir.mkdir(parents=True, exist_ok=True)

        # Split audio
        segments = []
        boundaries = [0.0] + split_points + [duration]

        for i in range(len(boundaries) - 1):
            start_time = boundaries[i]
            end_time = boundaries[i + 1]

            # Calculate sample boundaries
            start_sample = int(start_time * sr)
            end_sample = int(end_time * sr)

            # Extract segment
            segment_waveform = waveform[:, start_sample:end_sample]

            # Save segment
            segment_path = output_dir / f"segment_{i:03d}.wav"
            torchaudio.save(str(segment_path), segment_waveform, sr)

            segments.append({
                'path': str(segment_path),
                'start_time': start_time,
                'end_time': end_time,
                'duration': end_time - start_time,
                'is_original': False
            })

            logger.info(f"Segment {i}: {start_time:.1f}s - {end_time:.1f}s ({(end_time-start_time)/60:.1f} min)")

        return segments


# Global instance
_splitter: VADSplitter | None = None


def get_vad_splitter() -> VADSplitter:
    """Get or create the VAD splitter instance."""
    global _splitter
    if _splitter is None:
        _splitter = VADSplitter()
    return _splitter


async def split_long_audio(
    audio_path: str | Path,
    output_dir: Path | None = None,
    target_segment_duration: float = SPLIT_THRESHOLD_SECONDS
) -> list[dict[str, Any]]:
    """Split long audio file at VAD silence points."""
    import asyncio
    return await asyncio.to_thread(
        get_vad_splitter().split_audio, audio_path, output_dir, target_segment_duration
    )


def merge_srt_segments(
    segments: list[dict[str, Any]],
    srt_contents: list[str]
) -> str:
    """
    Merge SRT segments into a single SRT with corrected timestamps.

    Args:
        segments: List of segment info dicts with start_time offsets
        srt_contents: List of SRT content strings for each segment

    Returns:
        Merged SRT content with corrected timestamps
    """
    import re

    merged_entries = []
    global_index = 1

    for seg, srt_content in zip(segments, srt_contents):
        offset = seg['start_time']

        # Parse SRT entries
        blocks = re.split(r'\n\n+', srt_content.strip())

        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) < 3:
                continue

            try:
                # Parse timestamp line
                timestamp_line = lines[1]
                match = re.match(
                    r'(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})',
                    timestamp_line
                )
                if not match:
                    continue

                # Convert to seconds and add offset
                start_secs = (
                    int(match.group(1)) * 3600 +
                    int(match.group(2)) * 60 +
                    int(match.group(3)) +
                    int(match.group(4)) / 1000
                ) + offset

                end_secs = (
                    int(match.group(5)) * 3600 +
                    int(match.group(6)) * 60 +
                    int(match.group(7)) +
                    int(match.group(8)) / 1000
                ) + offset

                # Format back to SRT timestamp
                def format_ts(secs):
                    h = int(secs // 3600)
                    m = int((secs % 3600) // 60)
                    s = int(secs % 60)
                    ms = int((secs % 1) * 1000)
                    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

                new_timestamp = f"{format_ts(start_secs)} --> {format_ts(end_secs)}"

                # Build entry
                text = '\n'.join(lines[2:])
                merged_entries.append(f"{global_index}\n{new_timestamp}\n{text}")
                global_index += 1

            except (ValueError, IndexError):
                continue

    return '\n\n'.join(merged_entries)
