"""Check subtitle time spans against the source video's duration."""

import math


def validate_subtitle_timing(segments: list[dict], video_duration: float | None) -> dict:
    result = {"valid": False, "status": "rejected", "cue_count": len(segments)}
    if not segments:
        return {**result, "reason": "empty_subtitle"}
    intervals = []
    for cue in segments:
        start = float(cue.get("start_ms", 0)) / 1000
        end = float(cue.get("end_ms", 0)) / 1000
        if not math.isfinite(start + end) or start < 0 or end <= start:
            return {**result, "reason": "invalid_timestamps"}
        intervals.append((start, end))
    first = min(start for start, _ in intervals)
    last = max(end for _, end in intervals)
    result.update(first_start=first, last_end=last, subtitle_span=last - first)
    duration = float(video_duration or 0)
    if not math.isfinite(duration) or duration <= 0:
        return {**result, "reason": "video_duration_unknown"}

    # Compare the subtitle span, not the sum of cue durations (which excludes pauses).
    # Normal title cards and end credits may have no speech.
    edge_tolerance = max(2.0, min(30.0, duration * 0.05))
    overrun_tolerance = max(2.0, min(5.0, duration * 0.01))
    result.update(
        video_duration=duration,
        span_ratio=(last - first) / duration,
        start_gap=first,
        end_gap=duration - last,
        edge_tolerance=edge_tolerance,
        overrun_tolerance=overrun_tolerance,
    )
    if last > duration + overrun_tolerance:
        return {**result, "reason": "ends_after_video"}
    if first > edge_tolerance:
        return {**result, "reason": "starts_too_late"}
    if duration - last > edge_tolerance:
        return {**result, "reason": "ends_too_early"}
    return {**result, "valid": True, "status": "accepted", "reason": "duration_consistent"}
