"""LLM service for text analysis via OpenAI-compatible API."""

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
    get_mindmap_map_prompt,
    get_mindmap_reduce_prompt,
)

logger = logging.getLogger(__name__)


class LLMService:
    def __init__(self):
        self._static_settings = get_settings()

    def _get_client_and_model(self):
        """Build an AsyncOpenAI client from runtime settings.

        Returns (client, model_name, temperature) or None if not configured.
        All three providers (anthropic, openai, custom) are called through the
        OpenAI-compatible chat completions endpoint.
        """
        from openai import AsyncOpenAI
        rt = get_runtime_settings()
        provider = rt.llm_provider

        if provider == "anthropic":
            if not rt.anthropic_api_key:
                return None
            client = AsyncOpenAI(
                api_key=rt.anthropic_api_key,
                base_url=rt.anthropic_api_base or "https://api.anthropic.com/v1",
            )
            model = rt.anthropic_model
        elif provider == "openai":
            if not rt.openai_api_key:
                return None
            kwargs: dict[str, Any] = {"api_key": rt.openai_api_key}
            if rt.openai_api_base:
                kwargs["base_url"] = rt.openai_api_base
            client = AsyncOpenAI(**kwargs)
            model = rt.openai_model
        elif provider == "custom":
            if not rt.custom_api_base or not rt.custom_model:
                return None
            client = AsyncOpenAI(
                api_key=rt.custom_api_key or "not-needed",
                base_url=rt.custom_api_base,
            )
            model = rt.custom_model
        else:
            return None

        return client, model, self._static_settings.temperature

    async def _call(self, prompt: str) -> str:
        result = self._get_client_and_model()
        if not result:
            logger.warning("LLM not configured - check API key and settings")
            return "[LLM not configured]"

        client, model, temperature = result
        try:
            logger.info(f"Calling LLM: {model}")
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
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

    async def mindmap(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Generate mindmap, auto-selecting single-pass or map-reduce based on length."""
        # Rough threshold: ~15k chars ≈ 30min of Chinese transcript
        if len(text) > 15000 and metadata:
            chapters = metadata.get("chapters")
            if chapters:
                return await self._mindmap_map_reduce(text, metadata, chapters)
            # No chapters but long text — auto-split by segment count
            return await self._mindmap_map_reduce_auto(text, metadata)

        # Short content: single-pass
        prompt = get_mindmap_prompt(text)
        resp = await self._call(prompt)
        return self._filter_mindmap_lines(resp)

    async def _mindmap_map_reduce(
        self,
        text: str,
        metadata: dict[str, Any],
        chapters: list[dict],
    ) -> str:
        """Map-reduce mindmap using chapter markers to split transcript."""
        segments = self._parse_srt(text) if "\n-->" in text else None

        # Build chapter text blocks
        if segments:
            chapter_texts = self._split_segments_by_chapters(segments, chapters)
        else:
            # Plain text — split by rough char position proportional to chapter times
            chapter_texts = self._split_plain_by_chapters(text, chapters)

        global_context = self._build_global_context(metadata, chapters)

        # --- Map phase: parallel ---
        logger.info(f"Mindmap map-reduce: {len(chapter_texts)} chapters")
        semaphore = asyncio.Semaphore(8)

        async def map_one(title: str, content: str) -> tuple[str, str]:
            async with semaphore:
                prompt = get_mindmap_map_prompt(title, content, global_context)
                resp = await self._call(prompt)
                return title, resp

        map_results = await asyncio.gather(*[
            map_one(title, content)
            for title, content in chapter_texts.items()
            if content.strip()
        ])
        chapter_summaries = dict(map_results)
        logger.info(
            f"Mindmap map done: {len(chapter_summaries)} chapters, "
            f"total {sum(len(v) for v in chapter_summaries.values())} chars"
        )

        # --- Reduce phase: group into batches to stay within output limits ---
        return await self._mindmap_reduce(chapter_summaries)

    async def _mindmap_map_reduce_auto(
        self,
        text: str,
        metadata: dict[str, Any],
    ) -> str:
        """Map-reduce for long text without chapter markers — auto-split."""
        segments = self._parse_srt(text) if "\n-->" in text else None

        if segments:
            # Split into groups of ~120 segments
            chunk_size = min(120, max(80, len(segments) // 8))
            chapter_texts = {}
            for i in range(0, len(segments), chunk_size):
                batch = segments[i:i + chunk_size]
                label = f"Part {i // chunk_size + 1} ({batch[0]['timestamp'].split('-->')[0].strip()})"
                chapter_texts[label] = "\n".join(seg["text"] for seg in batch)
        else:
            # Plain text — split by char count
            chunk_chars = max(10000, len(text) // 10)
            chapter_texts = {}
            for i in range(0, len(text), chunk_chars):
                chapter_texts[f"Part {i // chunk_chars + 1}"] = text[i:i + chunk_chars]

        global_context = self._build_global_context(metadata, [])

        logger.info(f"Mindmap auto map-reduce: {len(chapter_texts)} chunks")
        semaphore = asyncio.Semaphore(8)

        async def map_one(title: str, content: str) -> tuple[str, str]:
            async with semaphore:
                prompt = get_mindmap_map_prompt(title, content, global_context)
                resp = await self._call(prompt)
                return title, resp

        map_results = await asyncio.gather(*[
            map_one(t, c) for t, c in chapter_texts.items() if c.strip()
        ])
        chapter_summaries = dict(map_results)

        return await self._mindmap_reduce(chapter_summaries)

    async def _mindmap_reduce(self, chapter_summaries: dict[str, str]) -> str:
        """Reduce chapter summaries into final mindmap, batching to fit output limits."""
        names = list(chapter_summaries.keys())

        # Group chapters into batches of 3-4 to keep each reduce output under 8k tokens
        batch_size = max(2, min(4, len(names) // 4 + 1))
        groups: list[tuple[str, list[str]]] = []
        for i in range(0, len(names), batch_size):
            batch_names = names[i:i + batch_size]
            label = f"{batch_names[0]} ~ {batch_names[-1]}"
            groups.append((label, batch_names))

        logger.info(f"Mindmap reduce: {len(groups)} groups from {len(names)} chapters")

        # Reduce each group (can be parallel for small groups)
        semaphore = asyncio.Semaphore(4)

        async def reduce_one(label: str, batch_names: list[str]) -> str:
            async with semaphore:
                summaries = ""
                for name in batch_names:
                    summaries += f"\n### {name}\n{chapter_summaries[name]}\n"
                prompt = get_mindmap_reduce_prompt(label, summaries)
                resp = await self._call(prompt)
                return self._filter_mindmap_lines(resp)

        results = await asyncio.gather(*[
            reduce_one(label, batch_names)
            for label, batch_names in groups
        ])

        final = "\n".join(results)
        logger.info(f"Mindmap reduce done: {len(final)} chars")
        return final

    def _split_segments_by_chapters(
        self,
        segments: list[dict],
        chapters: list[dict],
    ) -> dict[str, str]:
        """Split SRT segments into chapter-keyed text blocks."""
        def ts_to_seconds(ts_str: str) -> float:
            """Parse HH:MM:SS or MM:SS or seconds to float."""
            ts_str = str(ts_str).strip()
            parts = ts_str.replace(",", ".").split(":")
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            elif len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
            return float(ts_str)

        def seg_start_seconds(seg: dict) -> float:
            ts_line = seg["timestamp"]
            start = ts_line.split("-->")[0].strip()
            return ts_to_seconds(start)

        seg_starts = [(seg_start_seconds(seg), seg["text"]) for seg in segments]

        chapter_texts: dict[str, str] = {}
        for i, ch in enumerate(chapters):
            start_s = ts_to_seconds(ch.get("start_time", 0))
            end_s = ts_to_seconds(chapters[i + 1]["start_time"]) if i + 1 < len(chapters) else 1e9
            texts = [text for s, text in seg_starts if start_s <= s < end_s]
            chapter_texts[ch["title"]] = "\n".join(texts)

        return chapter_texts

    def _split_plain_by_chapters(
        self,
        text: str,
        chapters: list[dict],
    ) -> dict[str, str]:
        """Rough split of plain text by chapter proportion."""
        total_len = len(text)
        n = len(chapters)
        chunk = total_len // max(n, 1)
        result: dict[str, str] = {}
        for i, ch in enumerate(chapters):
            start = i * chunk
            end = (i + 1) * chunk if i + 1 < n else total_len
            result[ch["title"]] = text[start:end]
        return result

    def _build_global_context(
        self,
        metadata: dict[str, Any],
        chapters: list[dict],
    ) -> str:
        """Build a concise global context string from metadata."""
        parts = []
        if metadata.get("title"):
            parts.append(f"标题: {metadata['title']}")
        if metadata.get("uploader"):
            parts.append(f"作者: {metadata['uploader']}")
        if chapters:
            ch_list = " / ".join(ch.get("title", "") for ch in chapters)
            parts.append(f"章节: {ch_list}")
        desc = metadata.get("description", "")
        if desc:
            parts.append(f"简介: {desc[:300]}")
        return "\n".join(parts)

    @staticmethod
    def _filter_mindmap_lines(resp: str) -> str:
        """Filter response to plain text list lines, strip any markdown formatting."""
        lines = [l for l in resp.strip().split("\n") if l.strip().startswith("-") or l.strip().startswith("*")]
        # Normalize * to -
        lines = [l.replace("* ", "- ", 1) if l.lstrip().startswith("* ") else l for l in lines]
        # Strip markdown formatting: bold, italic, code, links
        cleaned = []
        for l in lines:
            l = l.replace("**", "").replace("__", "")  # bold
            l = l.replace("*", "").replace("_", "")  # italic (careful: only standalone)
            l = re.sub(r'`([^`]*)`', r'\1', l)  # inline code
            l = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', l)  # links
            l = re.sub(r'^(\s*- )#+\s*', r'\1', l)  # heading markers after bullet
            cleaned.append(l)
        return "\n".join(cleaned) if cleaned else resp


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


async def generate_mindmap(
    text: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    return await get_llm_service().mindmap(text, metadata=metadata)
