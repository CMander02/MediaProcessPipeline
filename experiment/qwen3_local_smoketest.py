from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.settings import get_runtime_settings, patch_runtime_settings
from app.services.analysis.llm import analyze_content, summarize_text, generate_mindmap, polish_text
from app.services.recognition import transcribe_audio
from app.services.recognition import get_asr_service
from app.services.voiceprint.extractor import extract_voiceprints
from app.services.voiceprint import get_voiceprint_store


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Minimal smoketest for local Qwen3 ASR + local Qwen3 analysis.",
    )
    parser.add_argument("audio", type=Path, help="Path to a local audio file")
    parser.add_argument(
        "--title",
        default="Smoketest Audio",
        help="Synthetic title used for analyze_content",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("experiment") / "smoketest_output",
        help="Where to store the generated SRT",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    audio_path = args.audio.resolve()
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    patch_runtime_settings(
        {
            "llm_provider": "local",
            "polish_provider": "local",
            "local_llm_model_path": "C:/zychen/AIGC/Models/Qwen3-4B-Instruct-2507",
            "local_llm_device": "cuda",
            "local_llm_dtype": "bfloat16",
        }
    )
    rt = get_runtime_settings()
    print(f"ASR model: {rt.qwen3_asr_model_path}")
    print(f"Local LLM: {rt.local_llm_model_path}")

    recognition = await transcribe_audio(
        str(audio_path),
        language=None,
        output_dir=args.output_dir.resolve(),
    )
    transcript = recognition["srt"]
    print(f"Transcript chars: {len(transcript)}")
    print(f"SRT path: {recognition['srt_path']}")

    analysis = await analyze_content(transcript, args.title, metadata={"title": args.title})
    print("Analysis:")
    print(analysis)

    summary = await summarize_text(transcript)
    print("Summary keys:", list(summary.keys()))

    mindmap = await generate_mindmap(transcript, metadata={"title": args.title})
    print("Mindmap chars:", len(mindmap))

    polished = await polish_text(transcript, context=analysis)
    print("Polished chars:", len(polished))

    service = get_asr_service()
    diarize_df, diarize_audio = service.get_last_diarization()
    pipeline_obj = service.get_pyannote_pipeline()
    if diarize_df is None or diarize_audio is None or pipeline_obj is None:
        print("Voiceprint: skipped (no diarization cache or pyannote pipeline)")
        return

    store = get_voiceprint_store()
    voiceprints = extract_voiceprints(
        audio_path=diarize_audio,
        diarize_df=diarize_df,
        pyannote_pipeline=pipeline_obj,
        clips_dir=store.clips_dir,
        sample_id_prefix="smoketest_",
    )
    print(f"Voiceprints extracted: {len(voiceprints)}")


if __name__ == "__main__":
    asyncio.run(main())
