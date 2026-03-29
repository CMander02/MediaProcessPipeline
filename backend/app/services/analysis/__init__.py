"""Analysis service - LLM polishing, summary, mindmap.

The openai SDK (~560ms) is loaded lazily on first use, not at import time.
"""


def analyze_content(*args, **kwargs):
    from app.services.analysis.llm import analyze_content as _fn
    return _fn(*args, **kwargs)


def polish_text(*args, **kwargs):
    from app.services.analysis.llm import polish_text as _fn
    return _fn(*args, **kwargs)


def summarize_text(*args, **kwargs):
    from app.services.analysis.llm import summarize_text as _fn
    return _fn(*args, **kwargs)


def generate_mindmap(*args, **kwargs):
    from app.services.analysis.llm import generate_mindmap as _fn
    return _fn(*args, **kwargs)


def srt_to_markdown(*args, **kwargs):
    from app.services.analysis.llm import srt_to_markdown as _fn
    return _fn(*args, **kwargs)


__all__ = ["analyze_content", "polish_text", "summarize_text", "generate_mindmap", "srt_to_markdown"]
