"""
Test script for LLM analysis service.
Run from project root: uv run python scripts/test_llm.py

This script reads LLM configuration from data/settings.json (synced from frontend).
If you haven't saved settings from the frontend yet, do that first.
"""

import sys
import asyncio
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.api.routes.settings import get_runtime_settings, RuntimeSettings, SETTINGS_FILE


def show_current_config():
    """Show current LLM configuration from settings file."""
    rt = get_runtime_settings()
    print(f"\nSettings file: {SETTINGS_FILE}")
    print(f"  Exists: {SETTINGS_FILE.exists()}")
    print(f"\nLLM Configuration:")
    print(f"  Provider: {rt.llm_provider}")
    if rt.llm_provider == "anthropic":
        print(f"  Model: {rt.anthropic_model}")
        print(f"  API Key: {'***' + rt.anthropic_api_key[-4:] if rt.anthropic_api_key else '(not set)'}")
        print(f"  API Base: {rt.anthropic_api_base or '(default)'}")
    elif rt.llm_provider == "openai":
        print(f"  Model: {rt.openai_model}")
        print(f"  API Key: {'***' + rt.openai_api_key[-4:] if rt.openai_api_key else '(not set)'}")
        print(f"  API Base: {rt.openai_api_base or '(default)'}")
    elif rt.llm_provider == "custom":
        print(f"  Model: {rt.custom_model}")
        print(f"  API Key: {'***' + rt.custom_api_key[-4:] if rt.custom_api_key else '(not set)'}")
        print(f"  API Base: {rt.custom_api_base or '(not set)'}")
    return rt


def configure_llm(provider: str = "custom", **kwargs):
    """Configure LLM settings for testing."""
    from app.api.routes import settings as settings_module

    current = settings_module.get_runtime_settings().model_dump()
    current["llm_provider"] = provider
    current.update(kwargs)
    settings_module._runtime_settings = RuntimeSettings(**current)
    # Also save to file
    settings_module._save_settings_to_file(settings_module._runtime_settings)
    print(f"Configured LLM: provider={provider}")
    return settings_module._runtime_settings


async def test_llm_service():
    """Test LLM service with a sample transcript."""
    from app.services.analysis.llm import get_llm_service, polish_text, summarize_text, generate_mindmap

    # Sample transcript text (from the test video)
    sample_text = """
    今天我们要聊一聊具身智能领域最有趣的一些论文
    其实这个领域发展非常快 每年都有很多新的进展
    我们先来看第一篇论文 这篇论文是关于机器人操作的
    作者提出了一个新的方法 可以让机器人更好地理解和执行复杂的任务
    第二篇论文是关于视觉导航的 机器人可以通过视觉信息来导航
    第三篇是关于人机交互的 这个研究很有意思
    总的来说 具身智能是一个非常活跃的研究领域
    """

    print("\n" + "=" * 60)
    print("Testing LLM Service")
    print("=" * 60)

    service = get_llm_service()
    config = service._get_llm_config()

    if not config:
        print("\nERROR: LLM not configured!")
        print("Please configure LLM settings first.")
        return False

    print(f"\nLLM Config:")
    print(f"  Model: {config.get('model')}")
    print(f"  API Base: {config.get('api_base', 'default')}")

    # Test 1: Polish
    print("\n" + "-" * 40)
    print("1. Testing text polishing...")
    try:
        polished = await polish_text(sample_text)
        print(f"   Result ({len(polished)} chars):")
        print(f"   {polished[:200]}...")
    except Exception as e:
        print(f"   ERROR: {e}")
        return False

    # Test 2: Summarize
    print("\n" + "-" * 40)
    print("2. Testing summarization...")
    try:
        summary = await summarize_text(sample_text)
        print(f"   TLDR: {summary.get('tldr', 'N/A')}")
        print(f"   Key Facts: {summary.get('key_facts', [])}")
        print(f"   Topics: {summary.get('topics', [])}")
    except Exception as e:
        print(f"   ERROR: {e}")
        return False

    # Test 3: Mindmap
    print("\n" + "-" * 40)
    print("3. Testing mindmap generation...")
    try:
        mindmap = await generate_mindmap(sample_text)
        print(f"   Result:")
        for line in mindmap.split("\n")[:10]:
            print(f"   {line}")
    except Exception as e:
        print(f"   ERROR: {e}")
        return False

    print("\n" + "=" * 60)
    print("All LLM tests passed!")
    print("=" * 60)
    return True


async def test_full_analysis_pipeline():
    """Test the full analysis pipeline on existing transcript."""
    from app.services.analysis.llm import polish_text, summarize_text, generate_mindmap

    # Read existing transcript
    transcript_path = Path(__file__).parent.parent / "data/processing/84f323e4_具身智能最有趣论文颁奖！"
    srt_file = transcript_path / "transcript.srt"

    if not srt_file.exists():
        print(f"Transcript not found: {srt_file}")
        return False

    print("\n" + "=" * 60)
    print("Testing Full Analysis Pipeline")
    print("=" * 60)
    print(f"Transcript: {srt_file}")

    # Read and extract text from SRT
    srt_content = srt_file.read_text(encoding="utf-8")

    # Parse SRT to plain text
    lines = []
    for block in srt_content.strip().split("\n\n"):
        block_lines = block.split("\n")
        if len(block_lines) >= 3:
            # Skip index and timestamp, get text
            text = " ".join(block_lines[2:])
            lines.append(text)

    full_text = " ".join(lines)
    print(f"Extracted {len(full_text)} chars from {len(lines)} segments")

    # Limit text for testing (avoid excessive API costs)
    if len(full_text) > 3000:
        test_text = full_text[:3000] + "..."
        print(f"Truncated to 3000 chars for testing")
    else:
        test_text = full_text

    # Polish
    print("\n1. Polishing transcript...")
    polished = await polish_text(test_text)
    print(f"   Polished: {len(polished)} chars")

    # Save polished
    polished_path = transcript_path / "transcript_polished.md"
    polished_path.write_text(f"# 具身智能最有趣论文颁奖！\n\n{polished}", encoding="utf-8")
    print(f"   Saved to: {polished_path}")

    # Summarize
    print("\n2. Generating summary...")
    summary = await summarize_text(polished)
    print(f"   TLDR: {summary.get('tldr', 'N/A')}")

    # Mindmap
    print("\n3. Generating mindmap...")
    mindmap = await generate_mindmap(polished)
    print(f"   Generated {len(mindmap.split(chr(10)))} lines")

    # Save summary.md
    summary_content = f"""---
title: "具身智能最有趣论文颁奖！"
source: "https://www.bilibili.com/video/BV1dwkjB5EfU/?spm_id_from=333.1365.list.card_archive.click"
date: 2026-01-20
tags: [media-pipeline]
---

# 具身智能最有趣论文颁奖！

## Summary
{summary.get('tldr', 'N/A')}

### Key Facts
{chr(10).join('- ' + f for f in summary.get('key_facts', ['None']))}

### Action Items
{chr(10).join('- ' + a for a in summary.get('action_items', ['None']))}

### Topics
{chr(10).join('- ' + t for t in summary.get('topics', ['None']))}

## Mind Map
```markmap
{mindmap}
```
"""
    summary_path = transcript_path / "summary.md"
    summary_path.write_text(summary_content, encoding="utf-8")
    print(f"\n   Saved summary to: {summary_path}")

    print("\n" + "=" * 60)
    print("Full analysis pipeline completed!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Test LLM analysis service")
    parser.add_argument("--provider", choices=["anthropic", "openai", "custom"],
                       help="LLM provider to use (overrides settings file)")
    parser.add_argument("--api-key", default="", help="API key")
    parser.add_argument("--api-base", default="", help="API base URL (for custom provider)")
    parser.add_argument("--model", default="", help="Model name")
    parser.add_argument("--full", action="store_true", help="Run full pipeline on existing transcript")
    parser.add_argument("--show-config", action="store_true", help="Show current config and exit")
    args = parser.parse_args()

    # Show current config
    rt = show_current_config()

    if args.show_config:
        sys.exit(0)

    # Override LLM config if provided via command line
    if args.provider:
        if args.provider == "anthropic":
            configure_llm(
                provider="anthropic",
                anthropic_api_key=args.api_key or rt.anthropic_api_key,
                anthropic_model=args.model or rt.anthropic_model,
                anthropic_api_base=args.api_base or rt.anthropic_api_base,
            )
        elif args.provider == "openai":
            configure_llm(
                provider="openai",
                openai_api_key=args.api_key or rt.openai_api_key,
                openai_model=args.model or rt.openai_model,
                openai_api_base=args.api_base or rt.openai_api_base,
            )
        elif args.provider == "custom":
            if not (args.api_base or rt.custom_api_base) or not (args.model or rt.custom_model):
                print("\nCustom provider requires --api-base and --model (or configure in settings)")
                print("Example: uv run python scripts/test_llm.py --provider custom --api-base https://api.deepseek.com/v1 --model deepseek-chat --api-key YOUR_KEY")
                sys.exit(1)
            configure_llm(
                provider="custom",
                custom_api_key=args.api_key or rt.custom_api_key or "not-needed",
                custom_api_base=args.api_base or rt.custom_api_base,
                custom_model=args.model or rt.custom_model,
            )
    elif not SETTINGS_FILE.exists():
        print("\n" + "=" * 60)
        print("No settings file found!")
        print("Please either:")
        print("  1. Save settings from the frontend (it will sync to backend)")
        print("  2. Run with explicit parameters:")
        print("     uv run python scripts/test_llm.py --provider custom --api-base URL --model MODEL --api-key KEY")
        print("=" * 60)
        sys.exit(1)

    try:
        if args.full:
            success = asyncio.run(test_full_analysis_pipeline())
        else:
            success = asyncio.run(test_llm_service())

        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
