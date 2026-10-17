"""Output language policy for generated content outlines."""

OUTLINE_LANGUAGE_POLICY = """Write every generated topic, title and explanatory node in Simplified Chinese,
regardless of the transcript's language. Use Simplified Chinese characters throughout.
Apply this to every chapter and window,
including the final sections; never switch to English when the source is English.
Summarize foreign-language sentences in Chinese instead of copying them as nodes.
Preserve proper names, product/model names, acronyms and code identifiers in their
original spelling when needed; this exception does not cover whole sentences.
Evidence quotes must remain verbatim in the source language for verification;
the title summarizing that evidence must still be in Chinese."""
