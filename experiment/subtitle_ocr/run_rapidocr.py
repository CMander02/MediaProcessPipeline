"""
Fast subtitle OCR pipeline without Qwen.

Designed for burned-in subtitles that stay near the bottom-center.
It uses RapidOCR on the existing strip images from step1_detect_segments.py.

Usage:
    uv run --with rapidocr_onnxruntime --with opencv-python-headless \
        python experiment/subtitle_ocr/run_rapidocr.py experiment/subtitle_ocr/out/sovits
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import cv2
from rapidocr_onnxruntime import RapidOCR


def load_segments(seg_dir: Path) -> tuple[list[dict], dict]:
    meta_path = seg_dir / "segments.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return meta["segments"], meta


def read_image(path: Path):
    image = cv2.imread(str(path))
    if image is None:
        raise FileNotFoundError(path)
    return image


def preprocess_crop(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)


def clean_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    return text


def join_result(result, min_conf: float, min_chars: int) -> tuple[str, float, list[list[float]]]:
    if not result:
        return "", 0.0, []

    texts = []
    confs = []
    boxes = []
    for box, text, conf in result:
        text = clean_text(text)
        if not text:
            continue
        texts.append(text)
        confs.append(float(conf))
        boxes.append(box)

    if not texts:
        return "", 0.0, []

    merged_text = "\n".join(texts)
    mean_conf = sum(confs) / len(confs)
    char_count = len(merged_text.replace("\n", ""))

    if mean_conf < min_conf or char_count < min_chars:
        return "", mean_conf, boxes
    return merged_text, mean_conf, boxes


def estimate_consensus_roi(seg_dir: Path, segments: list[dict], engine: RapidOCR,
                           min_conf: float, min_chars: int):
    x1s, y1s, x2s, y2s = [], [], [], []

    for idx, seg in enumerate(segments):
        strip_path = seg_dir / seg["strip_image"]
        result, _ = engine(str(strip_path))
        text, conf, boxes = join_result(result, min_conf=min_conf, min_chars=min_chars)
        if not text or conf < min_conf:
            continue

        for box in boxes:
            xs = [pt[0] for pt in box]
            ys = [pt[1] for pt in box]
            x1s.append(min(xs))
            y1s.append(min(ys))
            x2s.append(max(xs))
            y2s.append(max(ys))

    if not x1s:
        return None

    x1s.sort()
    y1s.sort()
    x2s.sort()
    y2s.sort()

    def pct(values, ratio):
        idx = int((len(values) - 1) * ratio)
        return values[idx]

    roi = [
        max(0, int(pct(x1s, 0.2) - 12)),
        max(0, int(pct(y1s, 0.2) - 8)),
        int(pct(x2s, 0.8) + 12),
        int(pct(y2s, 0.8) + 8),
    ]
    return roi


def ocr_with_roi(seg_dir: Path, segments: list[dict], engine: RapidOCR,
                 roi, min_conf: float, min_chars: int) -> list[dict]:
    results = []
    for idx, seg in enumerate(segments):
        strip_path = seg_dir / seg["strip_image"]
        image = read_image(strip_path)
        crop = image[roi[1]:roi[3], roi[0]:roi[2]] if roi else image
        proc = preprocess_crop(crop)
        result, _ = engine(proc)
        text, conf, _ = join_result(result, min_conf=min_conf, min_chars=min_chars)
        results.append(
            {
                "idx": idx,
                "start": seg["start"],
                "end": seg["end"],
                "text": text,
                "conf": conf,
                "image": seg["strip_image"],
            }
        )
    return results


def merge_duplicates(results: list[dict]) -> list[dict]:
    merged = []
    for row in results:
        if not row["text"]:
            continue
        if merged and merged[-1]["text"] == row["text"]:
            merged[-1]["end"] = row["end"]
            merged[-1]["conf"] = max(merged[-1]["conf"], row["conf"])
        else:
            merged.append(dict(row))
    return merged


def format_srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(path: Path, rows: list[dict]) -> None:
    lines = []
    for i, row in enumerate(rows, 1):
        lines.append(str(i))
        lines.append(f"{format_srt_time(row['start'])} --> {format_srt_time(row['end'])}")
        lines.append(row["text"])
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Fast subtitle OCR with RapidOCR")
    parser.add_argument("segments_dir", type=Path, help="Directory containing segments.json and strip_*.png")
    parser.add_argument("--min-conf", type=float, default=0.88)
    parser.add_argument("--min-chars", type=int, default=4)
    args = parser.parse_args()

    seg_dir = args.segments_dir.resolve()
    segments, _ = load_segments(seg_dir)
    engine = RapidOCR()

    roi = estimate_consensus_roi(
        seg_dir, segments, engine, min_conf=args.min_conf, min_chars=args.min_chars
    )
    results = ocr_with_roi(
        seg_dir, segments, engine, roi=roi, min_conf=args.min_conf, min_chars=args.min_chars
    )
    merged = merge_duplicates(results)

    out_json = seg_dir / "rapidocr_results.json"
    out_srt = seg_dir / "subtitles_rapidocr.srt"
    out_json.write_text(
        json.dumps({"roi": roi, "results": results, "merged": merged}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_srt(out_srt, merged)

    print(f"ROI: {roi}")
    print(f"Detected {sum(1 for row in results if row['text'])} / {len(results)} subtitle segments")
    print(f"Merged into {len(merged)} subtitle entries")
    print(f"JSON: {out_json}")
    print(f"SRT: {out_srt}")


if __name__ == "__main__":
    main()
