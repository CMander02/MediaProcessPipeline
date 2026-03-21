"""Quick OCR runner using Qwen3.5-4B on full frames."""
import torch, json
from pathlib import Path
from PIL import Image
from transformers import AutoModelForImageTextToText, AutoProcessor

model_path = "C:/zychen/AIGC/Models/Qwen3.5-4B"
seg_dir = Path("experiment/subtitle_ocr/out/sovits")

print("Loading model...")
processor = AutoProcessor.from_pretrained(model_path, trust_remote_code=True)
model = AutoModelForImageTextToText.from_pretrained(model_path, dtype=torch.bfloat16, device_map="auto")
model.eval()
print("Loaded!\n")

with open(seg_dir / "segments.json", encoding="utf-8") as f:
    segments = json.load(f)["segments"]

results = []
for idx in range(len(segments)):
    seg = segments[idx]
    full_img = seg_dir / seg["full_image"]
    if not full_img.exists():
        continue
    image = Image.open(full_img).convert("RGB")

    messages = [{"role": "user", "content": [
        {"type": "image", "image": image},
        {"type": "text", "text": "读出图片底部中央蓝色描边的字幕文字，只输出文字。没有字幕输出[无]"},
    ]}]
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = processor(text=[text], images=[image], return_tensors="pt", padding=True).to(model.device)

    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=1024, do_sample=False)

    generated = out[0][inputs["input_ids"].shape[1]:]
    resp = processor.decode(generated, skip_special_tokens=True).strip()
    if "</think>" in resp:
        resp = resp.split("</think>")[-1].strip()

    results.append({"idx": idx, "start": seg["start"], "end": seg["end"], "text": resp})
    print(f"  {seg['start']:5.1f}-{seg['end']:5.1f}s | {resp}")

# Write results
out_path = seg_dir / "ocr_final.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

# Generate SRT
srt_lines = []
srt_idx = 0
for r in results:
    t = r["text"]
    if t and t != "[无]" and t != "[空]":
        srt_idx += 1
        def fmt(s):
            h = int(s // 3600); m = int((s % 3600) // 60); sec = int(s % 60); ms = int((s % 1) * 1000)
            return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"
        srt_lines.append(str(srt_idx))
        srt_lines.append(f"{fmt(r['start'])} --> {fmt(r['end'])}")
        srt_lines.append(t)
        srt_lines.append("")

srt_path = seg_dir / "subtitles_ocr.srt"
srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
print(f"\nDone! {srt_idx} subtitles -> {srt_path}")
