"""Prompt templates for LLM analysis."""

from app.services.analysis.prompts.analyze import ANALYZE_SYSTEM_PROMPT, get_analyze_prompt
from app.services.analysis.prompts.mindmap import (
    MINDMAP_SYSTEM_PROMPT,
    get_detail_prompt,
    get_mindmap_map_prompt,
    get_mindmap_prompt,
    get_mindmap_reduce_prompt,
)
from app.services.analysis.prompts.polish import (
    POLISH_SYSTEM_PROMPT,
    get_polish_prompt,
    get_simple_polish_prompt,
)
from app.services.analysis.prompts.summarize import SUMMARY_SYSTEM_PROMPT, get_summarize_prompt

__all__ = [
    "get_analyze_prompt",
    "ANALYZE_SYSTEM_PROMPT",
    "get_polish_prompt",
    "get_simple_polish_prompt",
    "POLISH_SYSTEM_PROMPT",
    "get_summarize_prompt",
    "SUMMARY_SYSTEM_PROMPT",
    "get_detail_prompt",
    "get_mindmap_prompt",
    "get_mindmap_map_prompt",
    "get_mindmap_reduce_prompt",
    "MINDMAP_SYSTEM_PROMPT",
]
