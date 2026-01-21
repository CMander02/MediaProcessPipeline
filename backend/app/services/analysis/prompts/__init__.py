"""Prompt templates for LLM analysis."""

from app.services.analysis.prompts.analyze import get_analyze_prompt
from app.services.analysis.prompts.polish import get_polish_prompt, get_simple_polish_prompt
from app.services.analysis.prompts.summarize import get_summarize_prompt
from app.services.analysis.prompts.mindmap import get_mindmap_prompt

__all__ = [
    "get_analyze_prompt",
    "get_polish_prompt",
    "get_simple_polish_prompt",
    "get_summarize_prompt",
    "get_mindmap_prompt",
]
