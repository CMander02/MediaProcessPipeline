"""Summarization prompt.

Written in English with explicit language-preservation policy so multilingual
transcripts don't get collapsed into a single output language.
"""


def get_summarize_prompt(text: str, user_language: str | None = None) -> str:
    """
    Generate the summarization prompt.

    Args:
        text: Transcript text to summarize
        user_language: Primary language of the transcript (e.g. "Chinese",
            "English", "Japanese"). When unknown the model will auto-detect.

    Returns:
        Formatted prompt string
    """
    lang = (user_language or "").strip() or "auto-detect from transcript"

    return f"""Analyse the following transcript and produce a structured summary.

## Language policy (critical)
- The transcript's primary language: {lang}.
- Write the narrative fields (tldr, key_facts, action_items) in that primary
  language.
- PRESERVE code-switched content verbatim. When the transcript mixes
  languages (e.g. Chinese narration with English technical terms, Japanese
  quotes, proper nouns, brand names, code identifiers, song titles), keep
  those tokens in their original script and spelling — do NOT translate them.
- Do NOT collapse a multilingual transcript into a monolingual output.

## Transcript
{text}

## Output
Return JSON in exactly this shape:
{{
    "tldr": "one-sentence summary (<= 100 chars in the primary language)",
    "key_facts": [
        "key point 1",
        "key point 2",
        "key point 3",
        "..."
    ],
    "action_items": [
        "only include explicitly stated action items / recommendations;",
        "empty array if there are none"
    ],
    "topics": [
        "topic 1",
        "topic 2",
        "..."
    ]
}}

Rules:
1. key_facts: 3-10 of the most important information points.
2. action_items: only things explicitly proposed as actions; otherwise empty.
3. topics: the main discussion topics.
4. Return JSON only, no prose, no code fences."""
