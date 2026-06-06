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
    """Short-content prompt for the concise, display-oriented mindmap."""
    return f"""You are a content-structuring expert. Read the following transcript
(it may be a meeting, interview, lecture, or podcast) and distill it into a
concise display-oriented mind-map.

{_language_clause(user_language)}

## Core requirements
1. Distill and rephrase — do NOT copy sentences verbatim. Turn scattered
   conversational speech into information-dense bullet points.
2. The root topic is implicit; level-1 nodes are 5-8 main branches; level-2
   nodes are the key points under each branch.
3. Keep the default hierarchy shallow: normally 2 levels below the root;
   never exceed 3 levels below the root unless one exceptional branch truly
   needs one more layer. Absolute maximum depth: 5 including the root.
4. Keep the whole map presentation-friendly: ideally 20-40 nodes total.
5. Each node should be a short phrase, not a paragraph or transcript quote.
6. Filter out filler words, speaker small talk, and low-value anecdotes unless
   they are crucial to the video's argument.
7. If the transcript includes timestamps, append a timestamp range to every node
   in square brackets, using the node's earliest relevant start and latest
   relevant end, e.g. `- 背景 [00:00:03 - 00:01:12]`.

## Output format
- Use `- ` markers with 2-space indentation to express hierarchy.
- Plain text only. NO markdown formatting — no bold, italic, links, code
  blocks, or heading symbols.
- Emit the list directly, with no preamble or closing remarks.

## Transcript
{text}

Output the concise plain-text bullet list now:"""


def get_detail_prompt(text: str, user_language: str | None = None) -> str:
    """Former deep mindmap prompt, now used for optional detail.md."""
    return f"""You are a content-structuring expert. Read the following transcript
(it may be a meeting, interview, lecture, or podcast) and distill it into a
detailed structured outline for archival video details.

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

Output the detailed plain-text bullet list now:"""


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
Distill this chapter into a concise display-oriented mind-map outline. Requirements:
1. Distill and rephrase — do NOT copy sentences verbatim. Turn scattered
   conversational speech into information-dense points.
2. Level-1 nodes are the chapter's main sub-topics (2-4 of them); add only the
   essential level-2 points under each sub-topic.
3. Keep this chapter shallow and presentation-friendly: normally 2 levels below
   the chapter root; only exceptional content may add one more layer.
4. Each node is a short phrase, not a paragraph.
5. Filter out filler words and repetition; keep key names, terms, numbers, and
   conclusions.
6. If timestamps are available, append `[start - end]` to every node.
7. Use `- ` markers with 2-space indentation.
8. Plain text only — no markdown formatting (no bold, italic, links, code).
9. Emit the plain-text list directly, with no code fences or prose wrapper."""


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
Merge these chapter summaries into one concise display-oriented mind-map outline.
Requirements:
1. Level-1 nodes are 5-8 major topic sections for this part/video.
2. Keep the hierarchy shallow: normally 2 levels below the root; allow one
   exceptional extra layer only when it materially improves navigation.
3. Keep the total node count presentation-friendly, ideally 20-40 nodes.
4. Merge overlapping content and remove redundancy.
5. Each node is a short, informative phrase.
6. Preserve or infer timestamp ranges in `[start - end]` when source summaries
   contain timestamps.
7. Plain text only — no markdown formatting (no bold, italic, links, code).
8. Emit the list directly with `- ` markers and 2-space indentation. No code
   fences."""
