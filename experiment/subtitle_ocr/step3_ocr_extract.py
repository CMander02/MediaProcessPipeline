"""
Step 3: OCR extraction from subtitle crops.

Takes the cropped subtitle images from step 2 and runs OCR to extract text.
Also uses VLM for OCR since Qwen3.5 has strong OCR capabilities natively.

Usage:
    python step3_ocr_extract.py <segments_dir> [--model-path C:/zychen/AIGC/Models/Qwen3.5-4B]
"""

import argparse
import json
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
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
    )
    model.eval()
    print("Model loaded.")
    return model, processor


def ocr_subtitle(model, processor, image_path: Path) -> str:
    """Use VLM to read text from a subtitle crop image."""
    image = Image.open(image_path).convert("RGB")

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": image},
                {"type": "text", "text": (
                    "Read the subtitle text in this image. "
                    "Output ONLY the text content, nothing else. "
                    "If there is no readable text, output: [empty]"
                )},
            ],
        }
    ]

    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = processor(
        text=[text],
        images=[image],
        return_tensors="pt",
        padding=True,
    ).to(model.device)

    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=128,
            temperature=0.1,
            do_sample=False,
        )

    generated = output_ids[0][inputs["input_ids"].shape[1]:]
    response = processor.decode(generated, skip_special_tokens=True).strip()

    # Clean up thinking tags if present
    if "</think>" in response:
        response = response.split("</think>")[-1].strip()

    return response


def main():
    parser = argparse.ArgumentParser(description="OCR subtitle crops")
    parser.add_argument("segments_dir", type=Path, help="Directory with crops/ from step 2")
    parser.add_argument("--model-path", type=str, default="C:/zychen/AIGC/Models/Qwen3.5-4B")
    args = parser.parse_args()

    seg_dir = args.segments_dir.resolve()
    crops_dir = seg_dir / "crops"
    meta_path = seg_dir / "segments.json"

    if not crops_dir.exists():
        print(f"Error: {crops_dir} not found. Run step2 first.")
        return
    if not meta_path.exists():
        print(f"Error: {meta_path} not found. Run step1 first.")
        return

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    segments = meta["segments"]

    crop_files = sorted(crops_dir.glob("crop_*.png"))
    if not crop_files:
        print("No crop files found.")
        return

    print(f"Found {len(crop_files)} subtitle crops")

    model, processor = load_model(args.model_path)

    results = []
    for crop_path in crop_files:
        # Parse segment index from filename
        idx = int(crop_path.stem.split("_")[1])
        seg = segments[idx] if idx < len(segments) else {}

        print(f"  OCR: {crop_path.name}...", end=" ", flush=True)
        text = ocr_subtitle(model, processor, crop_path)
        print(f"=> {text[:60]}")

        results.append({
            "segment_index": idx,
            "start": seg.get("start", 0),
            "end": seg.get("end", 0),
            "text": text,
            "image": crop_path.name,
        })

    # Generate SRT
    srt_lines = []
    srt_idx = 0
    for r in results:
        if r["text"] and r["text"] != "[empty]":
            srt_idx += 1
            start_srt = format_srt_time(r["start"])
            end_srt = format_srt_time(r["end"])
            srt_lines.append(f"{srt_idx}")
            srt_lines.append(f"{start_srt} --> {end_srt}")
            srt_lines.append(r["text"])
            srt_lines.append("")

    srt_path = seg_dir / "subtitles_ocr.srt"
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")

    # Save full results
    ocr_path = seg_dir / "ocr_results.json"
    with open(ocr_path, "w", encoding="utf-8") as f:
        json.dump({"results": results}, f, indent=2, ensure_ascii=False)

    print(f"\nDone!")
    print(f"SRT: {srt_path}")
    print(f"Results: {ocr_path}")
    print(f"Subtitles found: {srt_idx} / {len(results)} segments")


def format_srt_time(seconds: float) -> str:
    """Format seconds to SRT timestamp: HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


if __name__ == "__main__":
    main()
