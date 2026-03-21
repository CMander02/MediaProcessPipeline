"""
Step 2: Use Qwen3.5-4B to locate subtitle bounding boxes.

Takes full-frame images from step 1, sends to Qwen3.5-4B with a grounding
prompt, and extracts the subtitle region bbox.

Qwen3.5 bbox format: normalized coordinates 0-1000 (image treated as 1000x1000).
Output: [x1, y1, x2, y2] in pixel coordinates.

Usage:
    python step2_vlm_bbox.py <segments_dir> [--model-path C:/zychen/AIGC/Models/Qwen3.5-4B]
"""

import argparse
import json
import re
import torch
from pathlib import Path
from PIL import Image


def load_model(model_path: str):
    """Load Qwen3.5 model and processor."""
    from transformers import AutoModelForCausalLM, AutoProcessor

    print(f"Loading model from {model_path}...")
    processor = AutoProcessor.from_pretrained(model_path, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
    )
    model.eval()
    print("Model loaded.")
    return model, processor


def detect_subtitle_bbox(model, processor, image_path: Path, w: int, h: int) -> dict | None:
    """Ask VLM to locate subtitle text in the image.

    Returns {"bbox": [x1, y1, x2, y2], "raw": str} or None.
    """
    image = Image.open(image_path).convert("RGB")

    # Grounding prompt — locate bottom text
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": image},
                {"type": "text", "text": (
                    "这张图片底部中央有一行大字（蓝色描边的中文字幕），"
                    "请给出这行字幕文字的边界框坐标。"
                    "输出JSON格式：{\"bbox_2d\": [x1, y1, x2, y2]}，坐标范围0-1000。"
                    "如果底部没有字幕文字，输出：{\"no_subtitle\": true}"
                )},
            ],
        }
    ]

    # Process with chat template
    text = processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True,
        enable_thinking=False,
    )
    inputs = processor(
        text=[text],
        images=[image],
        return_tensors="pt",
        padding=True,
    ).to(model.device)

    with torch.no_grad():
        input_ids = inputs["input_ids"].to(model.device)
        attention_mask = inputs["attention_mask"].to(model.device)

        # Prepare multimodal kwargs for model.forward (bypassing generate validation)
        mm_kwargs = {}
        for k in ["pixel_values", "image_grid_thw", "mm_token_type_ids"]:
            if k in inputs:
                mm_kwargs[k] = inputs[k].to(model.device)

        # Monkey-patch to skip model_kwargs validation for multimodal keys
        orig_validate = model._validate_model_kwargs
        model._validate_model_kwargs = lambda kwargs: None

        output_ids = model.generate(
            input_ids=input_ids,
            attention_mask=attention_mask,
            max_new_tokens=256,
            do_sample=False,
            **mm_kwargs,
        )

        model._validate_model_kwargs = orig_validate

    # Decode only new tokens
    generated = output_ids[0][inputs["input_ids"].shape[1]:]
    response = processor.decode(generated, skip_special_tokens=True).strip()

    # Parse bbox from response
    # Try to find bbox_2d pattern
    bbox_match = re.search(r'"bbox_2d"\s*:\s*\[(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\]', response)
    if bbox_match:
        # Convert from 0-1000 normalized to pixel coordinates
        nx1, ny1, nx2, ny2 = [int(x) for x in bbox_match.groups()]
        x1 = int(nx1 * w / 1000)
        y1 = int(ny1 * h / 1000)
        x2 = int(nx2 * w / 1000)
        y2 = int(ny2 * h / 1000)
        return {"bbox": [x1, y1, x2, y2], "bbox_norm": [nx1, ny1, nx2, ny2], "raw": response}

    # Check for no_subtitle
    if "no_subtitle" in response.lower():
        return {"bbox": None, "raw": response}

    return {"bbox": None, "raw": response}


def main():
    parser = argparse.ArgumentParser(description="VLM subtitle bbox detection")
    parser.add_argument("segments_dir", type=Path, help="Directory from step 1 with segments.json")
    parser.add_argument("--model-path", type=str, default="C:/zychen/AIGC/Models/Qwen3.5-4B")
    parser.add_argument("--sample-count", type=int, default=5,
                        help="Number of frames to sample for bbox detection (default: 5)")
    args = parser.parse_args()

    seg_dir = args.segments_dir.resolve()
    meta_path = seg_dir / "segments.json"
    if not meta_path.exists():
        print(f"Error: {meta_path} not found. Run step1 first.")
        return

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    segments = meta["segments"]
    w, h = meta["width"], meta["height"]
    print(f"Video: {w}x{h}, {len(segments)} segments")

    # Sample a subset of frames for bbox detection
    # Pick evenly spaced segments
    sample_indices = list(range(0, len(segments), max(1, len(segments) // args.sample_count)))[:args.sample_count]
    sample_segments = [(i, segments[i]) for i in sample_indices]

    print(f"Sampling {len(sample_segments)} frames for bbox detection...")

    model, processor = load_model(args.model_path)

    results = []
    for idx, seg in sample_segments:
        full_img = seg_dir / seg["full_image"]
        if not full_img.exists():
            print(f"  Skip: {full_img.name} not found")
            continue

        print(f"  Processing: {full_img.name} ({seg['start']:.1f}-{seg['end']:.1f}s)...")
        result = detect_subtitle_bbox(model, processor, full_img, w, h)
        result["segment_index"] = idx
        result["time"] = f"{seg['start']:.1f}-{seg['end']:.1f}s"
        results.append(result)

        if result["bbox"]:
            bx = result["bbox"]
            print(f"    FOUND: bbox=[{bx[0]}, {bx[1]}, {bx[2]}, {bx[3]}]")
        else:
            print(f"    No subtitle detected. Response: {result['raw'][:100]}")

    # Aggregate: find consensus bbox region
    valid_bboxes = [r["bbox"] for r in results if r["bbox"]]
    if valid_bboxes:
        # Use median of detected bboxes as the subtitle region
        import statistics
        consensus = [
            statistics.median([b[0] for b in valid_bboxes]),
            statistics.median([b[1] for b in valid_bboxes]),
            statistics.median([b[2] for b in valid_bboxes]),
            statistics.median([b[3] for b in valid_bboxes]),
        ]
        # Expand slightly for safety margin
        margin_x = int((consensus[2] - consensus[0]) * 0.05)
        margin_y = int((consensus[3] - consensus[1]) * 0.15)
        padded = [
            max(0, int(consensus[0]) - margin_x),
            max(0, int(consensus[1]) - margin_y),
            min(w, int(consensus[2]) + margin_x),
            min(h, int(consensus[3]) + margin_y),
        ]
        print(f"\nConsensus subtitle region: {padded}")
        print(f"  (median of {len(valid_bboxes)} detections, with padding)")
    else:
        padded = None
        print("\nNo subtitle regions detected in any sample.")

    # Save results
    bbox_path = seg_dir / "bbox_results.json"
    with open(bbox_path, "w", encoding="utf-8") as f:
        json.dump({
            "model": args.model_path,
            "sample_count": len(sample_segments),
            "detections": results,
            "consensus_bbox": padded,
            "consensus_bbox_raw": [int(x) for x in consensus] if valid_bboxes else None,
        }, f, indent=2, ensure_ascii=False)

    print(f"\nResults saved to: {bbox_path}")

    # If consensus found, extract cropped subtitle images for all segments
    if padded:
        print(f"\nExtracting subtitle crops using bbox {padded}...")
        crop_dir = seg_dir / "crops"
        crop_dir.mkdir(exist_ok=True)

        for i, seg in enumerate(segments):
            full_img = seg_dir / seg["full_image"]
            if not full_img.exists():
                continue
            img = Image.open(full_img)
            crop = img.crop(padded)
            crop_path = crop_dir / f"crop_{i:03d}_{seg['start']:.1f}-{seg['end']:.1f}s.png"
            crop.save(crop_path)

        print(f"Saved {len(segments)} crops to {crop_dir}")


if __name__ == "__main__":
    main()
