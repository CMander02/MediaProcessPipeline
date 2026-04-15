"""Mindmap generation prompts — single-pass and map-reduce.

All prompts produce plain text only: `- ` list markers with 2-space indentation.
No markdown formatting (no bold, italic, links, code, headings).

Prompts are written in English to avoid biasing the model toward outputting
a single language. The user's primary language is injected explicitly so the
model can mirror the source's linguistic structure — including code-switched
segments (e.g. Chinese with English technical terms).
"""


# ---------------------------------------------------------------------------
# Shared clauses
# ---------------------------------------------------------------------------

def _language_clause(user_language: str | None) -> str:
    """Build the 'primary language + preserve code-switching' instruction block.

    The cardinal rule: do NOT force the output into a single language when the
    source mixes languages. Proper nouns, quoted phrases, product/model names,
    code identifiers, and in-line foreign terms must be kept verbatim in their
    original script. Only the surrounding connective prose adopts the primary
    language.
    """
    lang = (user_language or "").strip() or "auto-detect from transcript"
    return f"""## Language policy (critical)
- The transcript's primary language: {lang}.
- Write the connective / narrative words of each node in that primary language.
- PRESERVE code-switched content verbatim. If the source mixes languages
  (for example Chinese narration with English technical terms, Japanese quotes,
  proper nouns, brand names, code identifiers, song titles), keep those tokens
  in their original script and spelling. Do NOT translate them.
- Do NOT collapse a multilingual transcript into a monolingual output.
- If a speaker quotes another language, keep the quote in its original form
  and (only when clarity demands it) add a short parenthetical gloss."""


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def get_mindmap_prompt(text: str, user_language: str | None = None) -> str:
    """Short-content prompt (< ~30 min transcript). Used as fallback."""
    return f"""You are a content-structuring expert. Read the following transcript
(it may be a meeting, interview, lecture, or podcast) and distill it into a
structured mind-map outline.

{_language_clause(user_language)}

## Core requirements
1. Distill and rephrase — do NOT copy sentences verbatim. Turn scattered
   conversational speech into information-dense bullet points.
2. Level-1 nodes are the main topic sections of the discussion (3-8 of them);
   level-2 are sub-points under each topic; level-3 are supporting details.
3. Each node should be a complete, informative short phrase — not a fragment
   of spoken language.
4. Filter out filler words, hesitations, and meaningless repetition (e.g.
   "uh", "you know", "嗯", "那个", "えーと", etc.) in whichever language(s)
   they appear.
5. Keep key names, technical terms, numbers, decisions, and conclusions.
6. Merge multiple sentences expressing the same idea into one node.

## Output format
- Use `- ` markers with 2-space indentation to express hierarchy (2-4 levels deep).
- Plain text only. NO markdown formatting — no bold, italic, links, code
  blocks, or heading symbols.
- Emit the list directly, with no preamble or closing remarks.

## Transcript
{text}

Output the plain-text bullet list now:"""


def get_mindmap_map_prompt(
    chapter_title: str,
    chapter_text: str,
    global_context: str,
    user_language: str | None = None,
) -> str:
    """Map phase: summarize one chapter into a structured list."""
    return f"""You are a content-structuring expert. The following is the
transcript of the chapter titled "{chapter_title}" from a longer video.

{_language_clause(user_language)}

## Video-level context
{global_context}

## Chapter transcript
{chapter_text}

## Task
Distill this chapter into a structured mind-map outline. Requirements:
1. Distill and rephrase — do NOT copy sentences verbatim. Turn scattered
   conversational speech into information-dense points.
2. Level-1 nodes are the chapter's main sub-topics (2-5 of them); levels 2
   and 3 progressively add detail.
3. Each node is a complete short phrase, not a fragment of spoken language.
4. Filter out filler words and meaningless repetition; keep key names,
   technical terms, numbers.
5. Use `- ` markers with 2-space indentation, 2-4 levels deep.
6. Plain text only — no markdown formatting (no bold, italic, links, code).
7. Emit the plain-text list directly, with no code fences or prose wrapper."""


def get_mindmap_reduce_prompt(
    group_label: str,
    group_summaries: str,
    user_language: str | None = None,
) -> str:
    """Reduce phase: merge several chapter summaries into one cohesive section."""
    return f"""You are a content-structuring expert. Below are the distilled
summaries of several chapters from the "{group_label}" part of a video.

{_language_clause(user_language)}

## Chapter summaries
{group_summaries}

## Task
Merge these chapter summaries into a single structured mind-map outline.
Requirements:
1. Level-1 nodes are the major topic sections of this part.
2. Levels 2, 3, and 4 progressively add detail; preserve key names, technical
   terms, and numbers.
3. Merge overlapping content and remove redundancy.
4. Each node is a complete, informative short phrase.
5. Plain text only — no markdown formatting (no bold, italic, links, code).
6. Emit the list directly with `- ` markers and 2-space indentation. No code
   fences."""
