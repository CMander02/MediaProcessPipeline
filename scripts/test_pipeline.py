"""
Test script to run the full pipeline without GUI.
Run from project root: uv run python scripts/test_pipeline.py
"""

import sys
import io
from pathlib import Path

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

# Fix PyTorch 2.6+ weights_only issue BEFORE importing anything else
import torch
try:
    from omegaconf import ListConfig, DictConfig
    from omegaconf.base import ContainerMetadata, SCMode
    from omegaconf.nodes import ValueNode, AnyNode, InterpolationResultNode

    safe_classes = [
        ListConfig,
        DictConfig,
        ContainerMetadata,
        SCMode,
        ValueNode,
        AnyNode,
        InterpolationResultNode,
    ]

    # Try to add more omegaconf internals
    try:
        from omegaconf._utils import _get_value
        safe_classes.append(_get_value)
    except ImportError:
        pass

    torch.serialization.add_safe_globals(safe_classes)
    print(f"✓ Added {len(safe_classes)} omegaconf classes to PyTorch safe globals")
except Exception as e:
    print(f"Warning: Could not setup omegaconf safe globals: {e}")


def test_whisperx_only():
    """Test WhisperX transcription without diarization."""
    import whisperx

    # Config
    AUDIO_PATH = r"C:\Users\cmander\工具箱\AI\MediaProcessPipeline\data\processing\具身智能最有趣论文颁奖！.wav"
    MODEL = "large-v3-turbo"  # Will use cached model
    DEVICE = "cuda"
    COMPUTE_TYPE = "float16"

    print(f"\n{'='*60}")
    print("Testing WhisperX (no diarization)")
    print(f"{'='*60}")
    print(f"Audio: {AUDIO_PATH}")
    print(f"Model: {MODEL}")

    # Check if audio exists
    if not Path(AUDIO_PATH).exists():
        print(f"ERROR: Audio file not found!")
        return False

    # Load model - use silero VAD instead of pyannote to avoid PyTorch 2.6 issues
    print("\n1. Loading WhisperX model (with Silero VAD)...")
    model = whisperx.load_model(
        MODEL, DEVICE, compute_type=COMPUTE_TYPE,
        vad_method="silero",  # Use Silero VAD instead of Pyannote
    )
    print("   Model loaded")

    # Load audio
    print("\n2. Loading audio...")
    audio = whisperx.load_audio(AUDIO_PATH)
    print(f"   ✓ Audio loaded, duration: {len(audio)/16000:.1f}s")

    # Transcribe
    print("\n3. Transcribing...")
    result = model.transcribe(audio, batch_size=16)
    print(f"   ✓ Transcribed, detected language: {result.get('language')}")
    print(f"   ✓ Segments: {len(result.get('segments', []))}")

    # Show first 3 segments
    print("\n   First 3 segments:")
    for i, seg in enumerate(result.get("segments", [])[:3]):
        print(f"   [{seg['start']:.1f}s - {seg['end']:.1f}s] {seg['text'][:50]}...")

    return True


def test_whisperx_with_alignment():
    """Test WhisperX with word-level alignment."""
    import whisperx

    AUDIO_PATH = r"C:\Users\cmander\工具箱\AI\MediaProcessPipeline\data\processing\具身智能最有趣论文颁奖！.wav"
    MODEL = "large-v3-turbo"
    DEVICE = "cuda"
    COMPUTE_TYPE = "float16"

    print(f"\n{'='*60}")
    print("Testing WhisperX with alignment")
    print(f"{'='*60}")

    # Load and transcribe
    print("\n1. Loading model and transcribing...")
    model = whisperx.load_model(MODEL, DEVICE, compute_type=COMPUTE_TYPE)
    audio = whisperx.load_audio(AUDIO_PATH)
    result = model.transcribe(audio, batch_size=16)
    detected_lang = result.get("language", "zh")
    print(f"   ✓ Done, language: {detected_lang}")

    # Align
    print("\n2. Loading alignment model...")
    model_a, metadata = whisperx.load_align_model(language_code=detected_lang, device=DEVICE)
    print("   ✓ Alignment model loaded")

    print("\n3. Aligning...")
    result = whisperx.align(result["segments"], model_a, metadata, audio, DEVICE)
    print(f"   ✓ Aligned, segments: {len(result.get('segments', []))}")

    return True


def test_whisperx_full():
    """Test WhisperX with diarization (speaker identification)."""
    import whisperx

    AUDIO_PATH = r"C:\Users\cmander\工具箱\AI\MediaProcessPipeline\data\processing\具身智能最有趣论文颁奖！.wav"
    MODEL = "large-v3-turbo"
    DEVICE = "cuda"
    COMPUTE_TYPE = "float16"
    HF_TOKEN = ""  # Set your token here or leave empty to skip diarization

    print(f"\n{'='*60}")
    print("Testing WhisperX FULL (with diarization)")
    print(f"{'='*60}")

    if not HF_TOKEN:
        print("WARNING: No HF_TOKEN set, skipping diarization")
        return test_whisperx_with_alignment()

    # Load and transcribe
    print("\n1. Loading model and transcribing...")
    model = whisperx.load_model(MODEL, DEVICE, compute_type=COMPUTE_TYPE)
    audio = whisperx.load_audio(AUDIO_PATH)
    result = model.transcribe(audio, batch_size=16)
    detected_lang = result.get("language", "zh")
    print(f"   ✓ Done, language: {detected_lang}")

    # Align
    print("\n2. Aligning...")
    model_a, metadata = whisperx.load_align_model(language_code=detected_lang, device=DEVICE)
    result = whisperx.align(result["segments"], model_a, metadata, audio, DEVICE)
    print(f"   ✓ Aligned")

    # Diarize
    print("\n3. Diarizing (speaker identification)...")
    diarize_model = whisperx.DiarizationPipeline(use_auth_token=HF_TOKEN, device=DEVICE)
    diarize_segments = diarize_model(audio)
    result = whisperx.assign_word_speakers(diarize_segments, result)
    print(f"   ✓ Diarization complete")

    # Show results
    print("\n   First 3 segments with speakers:")
    for seg in result.get("segments", [])[:3]:
        speaker = seg.get("speaker", "?")
        print(f"   [{speaker}] {seg['text'][:50]}...")

    return True


if __name__ == "__main__":
    print("MediaProcessPipeline - Backend Test Script")
    print("=" * 60)

    # Choose test level
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--level", choices=["basic", "align", "full"], default="basic",
                       help="Test level: basic (no align), align (with alignment), full (with diarization)")
    args = parser.parse_args()

    try:
        if args.level == "basic":
            success = test_whisperx_only()
        elif args.level == "align":
            success = test_whisperx_with_alignment()
        else:
            success = test_whisperx_full()

        if success:
            print(f"\n{'='*60}")
            print("✓ Test PASSED")
            print(f"{'='*60}")
        else:
            print(f"\n{'='*60}")
            print("✗ Test FAILED")
            print(f"{'='*60}")
            sys.exit(1)

    except Exception as e:
        print(f"\n{'='*60}")
        print(f"✗ Test FAILED with error:")
        print(f"  {type(e).__name__}: {e}")
        print(f"{'='*60}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
