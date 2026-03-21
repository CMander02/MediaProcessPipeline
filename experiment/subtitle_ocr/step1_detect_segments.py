"""
Step 1: Detect subtitle time segments via frame differencing.

Extracts a center-bottom strip from each frame, computes frame-to-frame
pixel differences, and clusters change points into stable subtitle segments.

Usage:
    python step1_detect_segments.py <video_path> [--max-seconds 180] [--output-dir ./out]
"""

import argparse
import json
import subprocess
from pathlib import Path


def get_video_info(video: Path) -> tuple[int, int, float]:
    """Return (width, height, duration_sec)."""
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    data = json.loads(probe.stdout)
    for s in data["streams"]:
        if s["codec_type"] == "video":
            w, h = int(s["width"]), int(s["height"])
            break
    dur = float(data["format"]["duration"])
    return w, h, dur


def extract_strip_frames(video: Path, w: int, h: int, fps: int, max_sec: int,
                         strip_h: int = 80, y_offset: int = 15) -> tuple[list[bytes], int, int]:
    """Extract center-bottom strip as raw grayscale frames.

    Returns (frames_list, crop_width, crop_height).
    """
    # Center 60% width, bottom strip
    cw = int(w * 0.6)
    cx = (w - cw) // 2
    cy = h - strip_h - y_offset

    raw = subprocess.run([
        "ffmpeg", "-y", "-t", str(max_sec),
        "-i", str(video),
        "-vf", f"crop={cw}:{strip_h}:{cx}:{cy},fps={fps},format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"
    ], capture_output=True, timeout=300).stdout

    frame_size = cw * strip_h
    n = len(raw) // frame_size
    frames = [raw[i * frame_size: (i + 1) * frame_size] for i in range(n)]
    return frames, cw, strip_h


def compute_diffs(frames: list[bytes], frame_size: int) -> list[float]:
    """Compute mean absolute pixel diff between consecutive frames."""
    diffs = [0.0]
    for i in range(1, len(frames)):
        d = sum(abs(a - b) for a, b in zip(frames[i], frames[i - 1])) / frame_size
        diffs.append(d)
    return diffs


def cluster_changes(diffs: list[float], fps: int,
                    threshold: float = 3.0, debounce_frames: int = 3,
                    min_segment_frames: int = 4) -> list[dict]:
    """Find stable segments between change clusters.

    Returns list of {"start": sec, "end": sec, "start_frame": int, "end_frame": int}.
    """
    change_frames = [i for i, d in enumerate(diffs) if d > threshold]

    # Debounce
    clusters = []
    if change_frames:
        cs = ce = change_frames[0]
        for cf in change_frames[1:]:
            if cf - ce <= debounce_frames:
                ce = cf
            else:
                clusters.append((cs, ce))
                cs = ce = cf
        clusters.append((cs, ce))

    # Build segments between clusters
    segments = []
    prev_end = 0
    for cs, ce in clusters:
        if cs - prev_end >= min_segment_frames:
            segments.append({
                "start": prev_end / fps,
                "end": (cs - 1) / fps,
                "start_frame": prev_end,
                "end_frame": cs - 1,
            })
        prev_end = ce + 1

    n = len(diffs)
    if n - prev_end >= min_segment_frames:
        segments.append({
            "start": prev_end / fps,
            "end": (n - 1) / fps,
            "start_frame": prev_end,
            "end_frame": n - 1,
        })

    return segments


def extract_segment_frames(video: Path, segments: list[dict], output_dir: Path,
                           w: int, h: int, strip_h: int = 80, y_offset: int = 15):
    """Extract representative frame for each segment (subtitle strip + full frame)."""
    cw = int(w * 0.6)
    cx = (w - cw) // 2
    cy = h - strip_h - y_offset

    output_dir.mkdir(parents=True, exist_ok=True)

    for i, seg in enumerate(segments):
        t = (seg["start"] + seg["end"]) / 2

        # Strip crop
        strip_path = output_dir / f"strip_{i:03d}_{seg['start']:.1f}-{seg['end']:.1f}s.png"
        subprocess.run([
            "ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(video),
            "-frames:v", "1", "-vf", f"crop={cw}:{strip_h}:{cx}:{cy}",
            str(strip_path)
        ], capture_output=True, timeout=15)

        # Full frame (for VLM bbox detection in step 2)
        full_path = output_dir / f"full_{i:03d}_{t:.1f}s.png"
        subprocess.run([
            "ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(video),
            "-frames:v", "1", str(full_path)
        ], capture_output=True, timeout=15)

        seg["strip_image"] = strip_path.name
        seg["full_image"] = full_path.name


def main():
    parser = argparse.ArgumentParser(description="Detect subtitle segments via frame differencing")
    parser.add_argument("video", type=Path, help="Input video path")
    parser.add_argument("--max-seconds", type=int, default=180, help="Process first N seconds")
    parser.add_argument("--fps", type=int, default=4, help="Sampling FPS")
    parser.add_argument("--threshold", type=float, default=3.0, help="Change detection threshold")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory")
    args = parser.parse_args()

    video = args.video.resolve()
    if not video.exists():
        print(f"Error: {video} not found")
        return

    out_dir = args.output_dir or Path("out") / video.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    w, h, dur = get_video_info(video)
    max_sec = min(args.max_seconds, int(dur))
    print(f"Video: {w}x{h}, {dur:.0f}s (processing {max_sec}s)")

    # Step 1: Extract and diff
    print(f"Extracting bottom strip at {args.fps}fps...")
    frames, cw, ch = extract_strip_frames(video, w, h, args.fps, max_sec)
    print(f"Got {len(frames)} frames")

    print("Computing frame diffs...")
    diffs = compute_diffs(frames, cw * ch)

    # Step 2: Cluster into segments
    segments = cluster_changes(diffs, args.fps, threshold=args.threshold)
    print(f"\nFound {len(segments)} stable segments:")
    for seg in segments:
        print(f"  {seg['start']:6.1f}s - {seg['end']:6.1f}s  ({seg['end'] - seg['start']:.1f}s)")

    # Step 3: Extract representative frames
    print(f"\nExtracting frames to {out_dir}...")
    extract_segment_frames(video, segments, out_dir, w, h)

    # Save segments metadata
    meta_path = out_dir / "segments.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "video": str(video),
            "width": w, "height": h, "duration": dur,
            "params": {"fps": args.fps, "threshold": args.threshold, "max_seconds": max_sec},
            "segments": segments,
        }, f, indent=2, ensure_ascii=False)

    print(f"\nDone! Metadata: {meta_path}")
    print(f"Segments: {len(segments)}, frames in: {out_dir}")


if __name__ == "__main__":
    main()
