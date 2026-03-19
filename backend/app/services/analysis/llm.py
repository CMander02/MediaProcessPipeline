"""LLM service for text analysis using LiteLLM with two-phase polishing."""

import asyncio
import json
import logging
import re
from typing import Any

from app.core.config import get_settings
from app.core.settings import get_runtime_settings
from app.services.analysis.prompts import (
    get_analyze_prompt,
    get_polish_prompt,
    get_simple_polish_prompt,
    get_summarize_prompt,
    get_mindmap_prompt,
)

logger = logging.getLogger(__name__)


class LLMService:
    def __init__(self):
        self._static_settings = get_settings()

    def _get_llm_config(self) -> dict | None:
        """Get LLM configuration from runtime settings."""
        rt = get_runtime_settings()
        provider = rt.llm_provider

        if provider == "anthropic":
            if not rt.anthropic_api_key:
                return None
            config = {
                "model": f"anthropic/{rt.anthropic_model}",
                "api_key": rt.anthropic_api_key,
            }
            if rt.anthropic_api_base:
                config["api_base"] = rt.anthropic_api_base
        elif provider == "openai":
            if not rt.openai_api_key:
                return None
            config = {
                "model": rt.openai_model,  # OpenAI doesn't need prefix
                "api_key": rt.openai_api_key,
            }
            if rt.openai_api_base:
                config["api_base"] = rt.openai_api_base
        elif provider == "custom":
            if not rt.custom_api_base or not rt.custom_model:
                return None
            config = {
                "model": f"openai/{rt.custom_model}",  # Use openai/ prefix for compatible APIs
                "api_key": rt.custom_api_key or "not-needed",
                "api_base": rt.custom_api_base,
            }
        else:
            return None

        config["temperature"] = self._static_settings.temperature
        return config

    async def _call(self, prompt: str) -> str:
        config = self._get_llm_config()
        if not config:
            logger.warning("LLM not configured - check API key and settings")
            return "[LLM not configured]"

        try:
            import litellm

            logger.info(f"Calling LLM: {config.get('model')}")
            response = await litellm.acompletion(
                model=config["model"],
                messages=[{"role": "user", "content": prompt}],
                api_key=config.get("api_key"),
                api_base=config.get("api_base"),
                temperature=config.get("temperature", 0.1),
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"LLM error: {e}")
            raise

    async def analyze_content(
        self,
        text: str,
        title: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Phase 1: Analyze content to extract metadata.
        This provides context for the polishing phase.

        Args:
            text: Transcript text
            title: Video/audio title
            metadata: Optional metadata dict with uploader, description, tags, chapters
        """
        # Truncate text if too long (take first 8000 chars for analysis)
        truncated = text[:8000] if len(text) > 8000 else text

        # Extract metadata fields
        uploader = metadata.get("uploader") if metadata else None
        description = metadata.get("description") if metadata else None
        tags = metadata.get("tags") if metadata else None
        chapters = metadata.get("chapters") if metadata else None

        prompt = get_analyze_prompt(
            title=title,
            text=truncated,
            uploader=uploader,
            description=description,
            tags=tags,
            chapters=chapters,
        )
        resp = await self._call(prompt)

        try:
            # Extract JSON from response
            start = resp.find("{")
            end = resp.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(resp[start:end])
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse analysis JSON: {e}")

        # Return default structure on failure
        return {
            "language": "unknown",
            "content_type": "unknown",
            "main_topics": [],
            "keywords": [],
            "proper_nouns": [],
            "speakers_detected": 1,
            "tone": "unknown"
        }

    def _parse_srt(self, srt_content: str) -> list[dict]:
        """Parse SRT content into segments."""
        segments = []
        blocks = re.split(r'\n\n+', srt_content.strip())

        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) >= 3:
                try:
                    index = int(lines[0])
                    timestamp = lines[1]
                    text = '\n'.join(lines[2:])
                    segments.append({
                        'index': index,
                        'timestamp': timestamp,
                        'text': text
                    })
                except (ValueError, IndexError):
                    continue

        return segments

    def _segments_to_srt(self, segments: list[dict]) -> str:
        """Convert segments back to SRT format."""
        result = []
        for seg in segments:
            result.append(f"{seg['index']}\n{seg['timestamp']}\n{seg['text']}")
        return '\n\n'.join(result)

    async def polish_with_context_parallel(
        self,
        srt_content: str,
        context: dict[str, Any],
        chunk_size: int = 64,
        overlap: int = 16,
        max_concurrency: int = 8,
    ) -> str:
        """
        Phase 2: Polish transcript using parallel chunks with context.
        Preserves [SPEAKER_XX] markers and SRT format.

        Args:
            srt_content: SRT content to polish
            context: Analysis context from phase 1
            chunk_size: Number of segments per chunk (default 64)
            overlap: Overlap between chunks (default 16)
            max_concurrency: Maximum parallel LLM calls (default 8)
        """
        segments = self._parse_srt(srt_content)
        if not segments:
            # Fallback to simple polish if not valid SRT
            prompt = get_simple_polish_prompt(srt_content)
            return await self._call(prompt)

        # Generate all chunks with overlap
        chunks: list[tuple[int, int, list[dict]]] = []
        i = 0
        chunk_idx = 0
        while i < len(segments):
            end = min(i + chunk_size, len(segments))
            chunks.append((chunk_idx, i, end, segments[i:end]))
            chunk_idx += 1
            # Move forward by (chunk_size - overlap) to create overlap
            i += chunk_size - overlap

        logger.info(
            f"Polishing {len(segments)} segments in {len(chunks)} chunks, "
            f"chunk_size={chunk_size}, overlap={overlap}, max_concurrency={max_concurrency}"
        )

        # Create semaphore to limit concurrency
        semaphore = asyncio.Semaphore(max_concurrency)

        async def process_chunk(
            idx: int, start: int, end: int, chunk_segments: list[dict]
        ) -> tuple[int, list[dict]]:
            """Process a single chunk with semaphore control."""
            async with semaphore:
                logger.info(f"Processing chunk {idx}: segments {start+1}-{end} of {len(segments)}")

                # Convert chunk to SRT text
                chunk_srt = self._segments_to_srt(chunk_segments)

                # Build prompt with context
                prompt = get_polish_prompt(
                    text=chunk_srt,
                    language=context.get("language", "unknown"),
                    content_type=context.get("content_type", "unknown"),
                    main_topics=context.get("main_topics"),
                    keywords=context.get("keywords"),
                    proper_nouns=context.get("proper_nouns"),
                )

                # Call LLM
                polished_chunk = await self._call(prompt)

                # Parse polished result
                polished_segs = self._parse_srt(polished_chunk)

                if not polished_segs:
                    logger.warning(f"Chunk {idx}: invalid response, keeping original")
                    polished_segs = chunk_segments
                else:
                    logger.info(
                        f"Chunk {idx}: input {len(chunk_segments)} segments, "
                        f"output {len(polished_segs)} segments"
                    )

                return (idx, polished_segs)

        # Process all chunks in parallel (with semaphore limiting concurrency)
        tasks = [
            process_chunk(idx, start, end, segs)
            for idx, start, end, segs in chunks
        ]
        results = await asyncio.gather(*tasks)

        # Sort by chunk index to maintain order
        results.sort(key=lambda x: x[0])

        # Merge results, handling overlaps
        polished_segments = []
        for i, (chunk_idx, polished_segs) in enumerate(results):
            if i == 0:
                # First chunk: take all segments
                polished_segments.extend(polished_segs)
            else:
                # Subsequent chunks: skip overlap segments
                skip = overlap if len(polished_segs) > overlap else 0
                polished_segments.extend(polished_segs[skip:])

        # Re-index segments
        for idx, seg in enumerate(polished_segments, 1):
            seg['index'] = idx

        logger.info(f"Polish complete: {len(segments)} -> {len(polished_segments)} segments")
        return self._segments_to_srt(polished_segments)

    async def polish(self, text: str, context: dict[str, Any] | None = None) -> str:
        """Polish text, optionally with context from analysis phase."""
        if context:
            return await self.polish_with_context_parallel(text, context)
        prompt = get_simple_polish_prompt(text)
        return await self._call(prompt)

    def srt_to_markdown(self, srt_content: str, title: str = "") -> str:
        """
        Convert polished SRT to a clean Markdown document.
        Groups consecutive segments by speaker and merges them into paragraphs.
        """
        segments = self._parse_srt(srt_content)
        if not segments:
            return srt_content

        # Group segments by speaker
        paragraphs = []
        current_speaker = None
        current_texts = []

        for seg in segments:
            text = seg['text'].strip()
            # Extract speaker if present
            speaker = None
            if text.startswith('[') and ']' in text:
                bracket_end = text.index(']')
                speaker = text[1:bracket_end]
                text = text[bracket_end + 1:].strip()

            # If speaker changed, save current paragraph
            if speaker != current_speaker and current_texts:
                paragraphs.append({
                    'speaker': current_speaker,
                    'text': ' '.join(current_texts)
                })
                current_texts = []

            current_speaker = speaker
            if text:
                current_texts.append(text)

        # Don't forget the last paragraph
        if current_texts:
            paragraphs.append({
                'speaker': current_speaker,
                'text': ' '.join(current_texts)
            })

        # Build markdown
        lines = []
        if title:
            lines.append(f"# {title}")
            lines.append("")

        # Check if there are multiple speakers
        speakers = set(p['speaker'] for p in paragraphs if p['speaker'])
        multi_speaker = len(speakers) > 1

        for para in paragraphs:
            if multi_speaker and para['speaker']:
                # Show speaker label for multi-speaker content
                lines.append(f"**[{para['speaker']}]** {para['text']}")
            else:
                lines.append(para['text'])
            lines.append("")

        return '\n'.join(lines)

    async def summarize(self, text: str) -> dict[str, Any]:
        prompt = get_summarize_prompt(text)
        resp = await self._call(prompt)
        try:
            start, end = resp.find("{"), resp.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(resp[start:end])
        except json.JSONDecodeError:
            pass
        return {"tldr": resp, "key_facts": [], "action_items": [], "topics": []}

    async def mindmap(self, text: str) -> str:
        prompt = get_mindmap_prompt(text)
        resp = await self._call(prompt)
        lines = [l for l in resp.strip().split("\n") if l.strip().startswith("-")]
        return "\n".join(lines) if lines else resp


_service: LLMService | None = None


def get_llm_service() -> LLMService:
    global _service
    if _service is None:
        _service = LLMService()
    return _service


async def analyze_content(
    text: str,
    title: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Analyze content to extract metadata (Phase 1)."""
    return await get_llm_service().analyze_content(text, title, metadata)


async def polish_text(text: str, context: dict[str, Any] | None = None) -> str:
    """Polish text with optional context (Phase 2)."""
    return await get_llm_service().polish(text, context)


def srt_to_markdown(srt_content: str, title: str = "") -> str:
    """Convert SRT to clean Markdown document."""
    return get_llm_service().srt_to_markdown(srt_content, title)


async def summarize_text(text: str) -> dict[str, Any]:
    return await get_llm_service().summarize(text)


async def generate_mindmap(text: str) -> str:
    return await get_llm_service().mindmap(text)
