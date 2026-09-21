import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.pipeline_steps import subtitles
from app.services.recognition.subtitle_validation import validate_subtitle_timing


def cues(*intervals):
    return [
        {"start_ms": start * 1000, "end_ms": end * 1000, "text": "字幕"} for start, end in intervals
    ]


@pytest.mark.parametrize(
    "segments,duration,reason",
    [
        ([], 100, "empty_subtitle"),
        (cues((0, 20)), 100, "ends_too_early"),
        (cues((95, 100)), 100, "starts_too_late"),
        (cues((0, 130)), 100, "ends_after_video"),
        (cues((2, 1)), 100, "invalid_timestamps"),
        (cues((0, float("inf"))), 100, "invalid_timestamps"),
        (cues((0, 100)), None, "video_duration_unknown"),
        # A long video must not admit several minutes of missing captions.
        (cues((0, 3400)), 3600, "ends_too_early"),
    ],
)
def test_invalid_or_incomplete_subtitles_fall_back(segments, duration, reason):
    report = validate_subtitle_timing(segments, duration)
    assert report["valid"] is False
    assert report["reason"] == reason


def test_coverage_uses_first_and_last_cue_and_allows_silent_gaps():
    report = validate_subtitle_timing(cues((2, 3), (50, 51), (98, 99)), 100)
    assert report["valid"] is True
    assert report["subtitle_span"] == 97
    assert report["edge_tolerance"] == 5


@pytest.mark.asyncio
async def test_filter_tracks_and_restore_relative_paths_after_handoff(tmp_path, monkeypatch):
    folder = tmp_path / "subtitles"
    folder.mkdir()
    good = folder / "zh.srt"
    bad = folder / "en.srt"
    good.write_text("1\n00:00:01,000 --> 00:01:39,000\n完整字幕", encoding="utf-8")
    bad.write_text("1\n00:00:01,000 --> 00:00:09,000\nShort", encoding="utf-8")
    metadata = SimpleNamespace(duration_seconds=100, extra={})

    async def write(_task, directory, name, content):
        (directory / name).write_text(content, encoding="utf-8")

    monkeypatch.setattr(subtitles, "_write_text_artifact", write)
    event = AsyncMock()
    monkeypatch.setattr(subtitles, "_emit_timeline_event", event)
    result = await subtitles.validate_platform_subtitles(
        None,
        tmp_path,
        {
            "subtitle_engine": "fixture",
            "tracks": [
                {"path": str(bad), "lang": "en", "format": "srt"},
                {"path": str(good), "lang": "zh", "format": "srt"},
            ],
        },
        metadata,
    )
    assert result["subtitle_path"] == str(good)
    assert len(result["tracks"]) == 1
    event.assert_not_awaited()
    report = json.loads((tmp_path / "subtitle_validation.json").read_text(encoding="utf-8"))
    assert [item["timing"]["valid"] for item in report["tracks"]] == [False, True]
    restored, result = subtitles.restore_validated_subtitles(tmp_path)
    assert restored is True
    assert result["subtitle_path"] == str(good)
    good.unlink()
    # Invalid captions left on disk cannot be resurrected by the legacy scan.
    assert subtitles.restore_validated_subtitles(tmp_path) == (True, None)


@pytest.mark.asyncio
async def test_missing_tracks_preserve_ingestion_diagnostics(tmp_path):
    metadata = SimpleNamespace(duration_seconds=100, extra={})
    diagnostics = [{"stage": "download", "reason": "login_required"}]
    assert (
        await subtitles.validate_platform_subtitles(
            None,
            tmp_path,
            {
                "subtitle_engine": "fixture",
                "diagnostics": diagnostics,
            },
            metadata,
        )
        is None
    )
    assert metadata.extra["subtitle_diagnostics"] == diagnostics
