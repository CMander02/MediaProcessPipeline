"""
Deeper OCR experiments for hard subtitle extraction.

Goals:
1. Compare current Qwen-VLM OCR output with a CPU-friendly OCR baseline.
2. Train a lightweight subtitle-presence classifier on bottom strips.
3. Produce a report that can guide replacing Qwen as the main OCR engine.

Usage:
    python experiment/subtitle_ocr/deeper_experiments.py
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = ROOT / "experiment" / "subtitle_ocr"
OUT_ROOT = EXPERIMENT_ROOT / "out"
REPORT_PATH = EXPERIMENT_ROOT / "deeper_experiments_report.json"


@dataclass
class OcrItem:
    idx: int
    start: float
    end: float
    text: str


def load_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"</?think>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text


def is_qwen_hallucination(text: str) -> bool:
    text = normalize_text(text)
    if not text:
        return True
    if text.startswith("1.  **") or "分析用户请求" in text:
        return True
    if len(text) > 180 and ("扫描图片" in text or "定位字幕区域" in text):
        return True
    return False


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(a=normalize_text(a), b=normalize_text(b)).ratio()


def join_rapidocr_result(result) -> tuple[str, float, int]:
    if not result:
        return "", 0.0, 0
    texts = []
    confs = []
    for box, text, conf in result:
        del box
        cleaned = normalize_text(text)
        if cleaned:
            texts.append(cleaned)
            confs.append(float(conf))
    if not texts:
        return "", 0.0, 0
    return "\n".join(texts), float(np.mean(confs)), len(texts)


def read_image(path: Path) -> np.ndarray:
    image = cv2.imread(str(path))
    if image is None:
        raise FileNotFoundError(path)
    return image


def extract_strip_from_video(
    video_path: Path,
    timestamp: float,
    width_ratio: float = 0.6,
    strip_h: int = 80,
    y_offset: int = 15,
) -> np.ndarray:
    cap = cv2.VideoCapture(str(video_path))
    cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000.0)
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        raise RuntimeError(f"Failed to extract frame at {timestamp:.2f}s from {video_path}")

    h, w = frame.shape[:2]
    crop_w = int(w * width_ratio)
    x = (w - crop_w) // 2
    y = h - strip_h - y_offset
    return frame[y : y + strip_h, x : x + crop_w]


def strip_features(image: np.ndarray, ocr_engine: RapidOCR) -> dict[str, float]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blur, 80, 180)
    _, binary = cv2.threshold(
        blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )

    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(binary)
    cc_areas = stats[1:, cv2.CC_STAT_AREA] if num_labels > 1 else np.array([], dtype=np.int32)
    valid_cc_areas = cc_areas[(cc_areas >= 8) & (cc_areas <= 4000)]

    grad_x = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
    grad_mag = cv2.magnitude(grad_x, grad_y)
    row_energy = grad_mag.mean(axis=1)

    ocr_result, _ = ocr_engine(image)
    ocr_text, ocr_conf, ocr_box_count = join_rapidocr_result(ocr_result)
    char_count = len(ocr_text.replace("\n", ""))

    return {
        "gray_mean": float(gray.mean()),
        "gray_std": float(gray.std()),
        "edge_density": float((edges > 0).mean()),
        "binary_foreground_ratio": float((binary > 0).mean()),
        "connected_components": float(len(valid_cc_areas)),
        "largest_component_ratio": float(valid_cc_areas.max() / gray.size) if len(valid_cc_areas) else 0.0,
        "row_energy_peak": float(row_energy.max()),
        "row_energy_std": float(row_energy.std()),
        "laplacian_var": float(cv2.Laplacian(blur, cv2.CV_32F).var()),
        "ocr_conf": ocr_conf,
        "ocr_boxes": float(ocr_box_count),
        "ocr_chars": float(char_count),
    }


def sovits_source_video() -> Path:
    candidates = sorted((ROOT / "data" / "uploads").glob("*GPT-SoVITS*.mp4"))
    if not candidates:
        raise FileNotFoundError("Could not find source GPT-SoVITS video in data/uploads")
    return candidates[0]


def load_qwen_items(name: str) -> list[OcrItem]:
    raw = load_json(OUT_ROOT / name / "ocr_final.json")
    items = []
    for row in raw:
        items.append(OcrItem(idx=int(row["idx"]), start=float(row["start"]), end=float(row["end"]), text=row["text"]))
    return items


def compare_rapidocr_to_qwen_sovits(ocr_engine: RapidOCR) -> dict:
    items = load_qwen_items("sovits")
    crop_dir = OUT_ROOT / "sovits" / "crops_fixed"
    rows = []
    positive_scores = []
    hallucination_flags = []

    for item in items:
        crop = next(crop_dir.glob(f"crop_{item.idx:03d}_*.png"))
        result, _ = ocr_engine(str(crop))
        rapid_text, conf, box_count = join_rapidocr_result(result)
        score = similarity(item.text, rapid_text)
        row = {
            "idx": item.idx,
            "qwen_hallucination": is_qwen_hallucination(item.text),
            "qwen_text": normalize_text(item.text),
            "rapidocr_text": rapid_text,
            "rapidocr_conf": conf,
            "rapidocr_boxes": box_count,
            "similarity": score,
        }
        rows.append(row)
        if row["qwen_hallucination"]:
            hallucination_flags.append(
                {
                    "idx": item.idx,
                    "rapidocr_text": rapid_text,
                    "rapidocr_conf": conf,
                    "rapidocr_boxes": box_count,
                }
            )
        else:
            positive_scores.append(score)

    usable = [r for r in rows if not r["qwen_hallucination"]]
    high_quality = [r for r in usable if r["similarity"] >= 0.9]
    return {
        "samples": len(rows),
        "usable_samples": len(usable),
        "mean_similarity": float(np.mean(positive_scores)) if positive_scores else 0.0,
        "median_similarity": float(np.median(positive_scores)) if positive_scores else 0.0,
        "high_quality_matches": len(high_quality),
        "hallucination_checks": hallucination_flags,
        "examples": rows[:8],
    }


def analyze_cs336_with_rapidocr(ocr_engine: RapidOCR) -> dict:
    items = load_qwen_items("cs336")
    strip_dir = OUT_ROOT / "cs336"
    rows = []
    english_like = 0
    bilingual_like = 0

    for item in items:
        strip = next(strip_dir.glob(f"strip_{item.idx:03d}_*.png"))
        result, _ = ocr_engine(str(strip))
        rapid_text, conf, box_count = join_rapidocr_result(result)
        qwen_text = normalize_text(item.text)
        contains_cn = bool(re.search(r"[\u4e00-\u9fff]", rapid_text))
        contains_en = bool(re.search(r"[A-Za-z]", rapid_text))
        if contains_en and not contains_cn:
            english_like += 1
        if contains_en and contains_cn:
            bilingual_like += 1
        rows.append(
            {
                "idx": item.idx,
                "qwen_hallucination": is_qwen_hallucination(qwen_text),
                "rapidocr_text": rapid_text,
                "rapidocr_conf": conf,
                "rapidocr_boxes": box_count,
                "rapid_has_cn": contains_cn,
                "rapid_has_en": contains_en,
            }
        )

    valid_rows = [r for r in rows if not r["qwen_hallucination"]]
    return {
        "samples": len(rows),
        "usable_samples": len(valid_rows),
        "english_only_outputs": english_like,
        "bilingual_outputs": bilingual_like,
        "examples": rows[:8],
        "hard_failure_examples": [r for r in rows if r["qwen_hallucination"]][:2],
    }


def build_presence_dataset(ocr_engine: RapidOCR) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    items = load_qwen_items("sovits")
    seg_meta = load_json(OUT_ROOT / "sovits" / "segments.json")
    strip_dir = OUT_ROOT / "sovits"
    video_path = sovits_source_video()

    feature_rows: list[dict] = []

    for item in items:
        strip_path = next(strip_dir.glob(f"strip_{item.idx:03d}_*.png"))
        image = read_image(strip_path)
        label = 0 if is_qwen_hallucination(item.text) else 1
        feats = strip_features(image, ocr_engine)
        feats.update(
            {
                "label": label,
                "source": "segment",
                "idx": item.idx,
                "path": str(strip_path.relative_to(ROOT)),
            }
        )
        feature_rows.append(feats)

    segments = seg_meta["segments"]
    prev_end = 0.0
    gap_idx = 0
    for seg in segments:
        gap_start = prev_end
        gap_end = float(seg["start"])
        prev_end = float(seg["end"])
        if gap_end - gap_start < 0.35:
            continue
        timestamp = (gap_start + gap_end) / 2
        image = extract_strip_from_video(video_path, timestamp)
        feats = strip_features(image, ocr_engine)
        feats.update(
            {
                "label": 0,
                "source": "gap",
                "idx": 1000 + gap_idx,
                "path": f"gap@{timestamp:.2f}s",
            }
        )
        feature_rows.append(feats)
        gap_idx += 1

    feature_names = [
        "gray_mean",
        "gray_std",
        "edge_density",
        "binary_foreground_ratio",
        "connected_components",
        "largest_component_ratio",
        "row_energy_peak",
        "row_energy_std",
        "laplacian_var",
        "ocr_conf",
        "ocr_boxes",
        "ocr_chars",
    ]
    x = np.array([[row[name] for name in feature_names] for row in feature_rows], dtype=np.float32)
    y = np.array([int(row["label"]) for row in feature_rows], dtype=np.int32)
    return x, y, feature_rows


def evaluate_presence_classifier(x: np.ndarray, y: np.ndarray, rows: list[dict]) -> dict:
    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=2000, class_weight="balanced")),
        ]
    )
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    prob = cross_val_predict(model, x, y, cv=cv, method="predict_proba")[:, 1]
    pred = (prob >= 0.5).astype(np.int32)

    failures = []
    for row, label, p, pr in zip(rows, y.tolist(), pred.tolist(), prob.tolist()):
        if label != p:
            failures.append(
                {
                    "path": row["path"],
                    "source": row["source"],
                    "label": label,
                    "pred": p,
                    "prob_subtitle": round(pr, 4),
                    "ocr_conf": round(row["ocr_conf"], 4),
                    "ocr_chars": row["ocr_chars"],
                }
            )

    model.fit(x, y)
    clf = model.named_steps["clf"]
    feature_names = [
        "gray_mean",
        "gray_std",
        "edge_density",
        "binary_foreground_ratio",
        "connected_components",
        "largest_component_ratio",
        "row_energy_peak",
        "row_energy_std",
        "laplacian_var",
        "ocr_conf",
        "ocr_boxes",
        "ocr_chars",
    ]
    importance = sorted(
        (
            {"feature": name, "coef": float(coef)}
            for name, coef in zip(feature_names, clf.coef_[0])
        ),
        key=lambda item: abs(item["coef"]),
        reverse=True,
    )

    return {
        "samples": int(len(y)),
        "positive": int(y.sum()),
        "negative": int(len(y) - y.sum()),
        "accuracy": float(accuracy_score(y, pred)),
        "f1": float(f1_score(y, pred)),
        "roc_auc": float(roc_auc_score(y, prob)),
        "mistakes": failures[:10],
        "top_features": importance[:6],
    }


def main() -> None:
    ocr_engine = RapidOCR()
    report = {
        "rapidocr_vs_qwen_sovits": compare_rapidocr_to_qwen_sovits(ocr_engine),
        "rapidocr_on_cs336": analyze_cs336_with_rapidocr(ocr_engine),
    }

    x, y, rows = build_presence_dataset(ocr_engine)
    report["presence_classifier"] = evaluate_presence_classifier(x, y, rows)

    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved report to {REPORT_PATH}")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
