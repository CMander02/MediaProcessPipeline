import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.pipeline import (  # noqa: E402
    _fallback_note_analysis,
    _fallback_note_mindmap,
    _fallback_note_summary,
)
from app.models import MediaMetadata  # noqa: E402


ARTICLE_TEXT = """### 网页正文
The concept of **recursive self-improvement (RSI)** dates back to I. J. Good and modern harness systems around model deployment.

## Harness Design Patterns

Harnesses orchestrate workflow design, evaluation, permission controls, and persistent state management.

## Pattern 1: Workflow Automation

The model can operate, test, and iterate inside a goal-oriented loop.

## Pattern 2: File System as Persistent Memory

Durable files keep artifacts and logs outside the active context window.
"""


def test_webpage_fallback_summary_uses_clean_article_body():
    analysis = _fallback_note_analysis(ARTICLE_TEXT)
    summary = _fallback_note_summary(ARTICLE_TEXT)

    assert analysis["language"] == "en"
    assert summary["tldr"].startswith("The concept of")
    assert "网页正文" not in summary["tldr"]
    assert "Harness Design Patterns" in summary["topics"]
    assert summary["key_facts"][0] == "章节：Harness Design Patterns"


def test_webpage_fallback_mindmap_uses_headings_and_metadata_images():
    metadata = MediaMetadata(
        title="Harness Engineering for Self-Improvement",
        platform="webpage",
        content_subtype="text_note",
        extra={"image_count": 17},
    )

    mindmap = _fallback_note_mindmap(metadata, 0, ARTICLE_TEXT)

    assert mindmap.splitlines()[0] == "- Harness Engineering for Self-Improvement"
    assert "  - Harness Design Patterns" in mindmap
    assert "  - Pattern 1: Workflow Automation" in mindmap
    assert "  - 图片素材：17 张" in mindmap
    assert "原始笔记正文已归档" not in mindmap
