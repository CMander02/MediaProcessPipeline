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

# ---------------------------------------------------------------------------
# Local HuggingFace model singleton — transformers + safetensors backend.
# Loaded on first use, offloaded after task ends.
# ---------------------------------------------------------------------------
_local_llm: Any = None           # dict: {"model": ..., "tokenizer": ...}
_local_llm_path: str = ""        # path that was used to load the model
_local_llm_lock: asyncio.Lock | None = None
_local_llm_infer_lock: asyncio.Lock | None = None


def _get_local_llm_lock() -> asyncio.Lock:
    global _local_llm_lock
    if _local_llm_lock is None:
        _local_llm_lock = asyncio.Lock()
    return _local_llm_lock


def _get_local_llm_infer_lock() -> asyncio.Lock:
    global _local_llm_infer_lock
    if _local_llm_infer_lock is None:
        _local_llm_infer_lock = asyncio.Lock()
    return _local_llm_infer_lock


def _resolve_dtype(name: str):
    """Map a string dtype to a torch dtype. Unknown/empty → bfloat16."""
    import torch
    mapping = {
        "bfloat16": torch.bfloat16,
        "bf16": torch.bfloat16,
        "float16": torch.float16,
        "fp16": torch.float16,
        "half": torch.float16,
        "float32": torch.float32,
        "fp32": torch.float32,
        "auto": "auto",
    }
    return mapping.get(name.lower() if name else "", torch.bfloat16)


def _load_local_llm(model_path: str, device: str = "cuda", dtype: str = "bfloat16") -> Any:
    """Load HF model from a local directory via transformers (blocking).

    Supports both text-only and VL/multimodal checkpoints — we auto-pick the
    right AutoModel class from the config's architectures field.
    """
    try:
        import torch
        from transformers import AutoConfig, AutoTokenizer
    except ImportError as e:
        raise RuntimeError(
            "transformers/torch not installed. Sync the project environment first: "
            "uv sync"
        ) from e

    logger.info(f"Loading local HF model: {model_path} (device={device}, dtype={dtype})")

    config = AutoConfig.from_pretrained(model_path, trust_remote_code=True)
    torch_dtype = _resolve_dtype(dtype)

    # Decide model class. VL / image-text-to-text architectures expose
    # `*ForConditionalGeneration`; plain text uses `*ForCausalLM`.
    archs = getattr(config, "architectures", []) or []
    is_vl = any("ConditionalGeneration" in a or "ImageTextToText" in a for a in archs)

    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)

    if is_vl:
        try:
            from transformers import AutoModelForImageTextToText
            ModelCls = AutoModelForImageTextToText
        except ImportError:
            from transformers import AutoModelForCausalLM
            ModelCls = AutoModelForCausalLM
    else:
        from transformers import AutoModelForCausalLM
        ModelCls = AutoModelForCausalLM

    device_map = device if device else "auto"
    model = ModelCls.from_pretrained(
        model_path,
        torch_dtype=torch_dtype,
        device_map=device_map,
        trust_remote_code=True,
    )
    model.eval()
    logger.info(f"Local HF model loaded: {model.__class__.__name__} on {device}")
    return {"model": model, "tokenizer": tokenizer, "is_vl": is_vl}


def offload_local_llm() -> None:
    """Release the local HF model and free VRAM. Safe to call multiple times."""
    global _local_llm, _local_llm_path
    if _local_llm is not None:
        logger.info("Offloading local HF model from VRAM")
        _local_llm = None
        _local_llm_path = ""
        try:
            import gc
            gc.collect()
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


class LLMService:
    def __init__(self):
        self._static_settings = get_settings()

    def _effective_provider(self, provider_override: str = "") -> str:
        rt = get_runtime_settings()
        return provider_override or rt.llm_provider

    def _get_client_and_model(self, provider_override: str = ""):
        """Build an AsyncOpenAI client from runtime settings.

        Returns (client, model_name, temperature) or None if not configured.
        All three providers (anthropic, openai, custom) are called through the
        OpenAI-compatible chat completions endpoint.
        Returns "local" string when local provider is selected.

        Args:
            provider_override: If non-empty, use this provider instead of rt.llm_provider.
        """
        from openai import AsyncOpenAI
        rt = get_runtime_settings()
        provider = provider_override or rt.llm_provider

        if provider == "local":
            return "local"

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

    async def _call_local(self, prompt: str) -> str:
        """Call local HF model (transformers). Loads on first call; serialised via lock."""
        global _local_llm, _local_llm_path
        rt = get_runtime_settings()
        model_path = rt.local_llm_model_path

        if not model_path:
            logger.warning("Local LLM: model path not configured")
            return "[Local LLM not configured]"

        loop = asyncio.get_running_loop()
        load_lock = _get_local_llm_lock()
        infer_lock = _get_local_llm_infer_lock()

        async with load_lock:
            if _local_llm is None or _local_llm_path != model_path:
                if _local_llm is not None:
                    offload_local_llm()
                device = getattr(rt, "local_llm_device", "cuda") or "cuda"
                dtype = getattr(rt, "local_llm_dtype", "bfloat16") or "bfloat16"
                _local_llm = await loop.run_in_executor(
                    None,
                    _load_local_llm,
                    model_path,
                    device,
                    dtype,
                )
                _local_llm_path = model_path

            state = _local_llm
            temperature = self._static_settings.temperature
            max_new_tokens = int(getattr(rt, "local_llm_max_new_tokens", 4096) or 4096)

        def _infer() -> str:
            import torch
            model = state["model"]
            tokenizer = state["tokenizer"]

            messages = [{"role": "user", "content": prompt}]
            text = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True,
            )
            inputs = tokenizer(text, return_tensors="pt").to(model.device)
            input_len = inputs["input_ids"].shape[1]

            do_sample = temperature > 0
            gen_kwargs = {
                "max_new_tokens": max_new_tokens,
                "do_sample": do_sample,
                "pad_token_id": tokenizer.eos_token_id,
            }
            if do_sample:
                gen_kwargs["temperature"] = temperature

            with torch.inference_mode():
                out = model.generate(**inputs, **gen_kwargs)
            new_tokens = out[0][input_len:]
            return tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

        async with infer_lock:
            return await loop.run_in_executor(None, _infer)

    async def _call(self, prompt: str, *, max_retries: int = 3, provider_override: str = "") -> str:
        if provider_override == "local":
            rt = get_runtime_settings()
            if rt.local_llm_model_path:
                logger.info("Calling local HF model (polish_provider override)")
                return await self._call_local(prompt)
            logger.warning("polish_provider=local but local_llm_model_path is empty, falling back to llm_provider")
            provider_override = ""

        result = self._get_client_and_model(provider_override)
        if not result:
            logger.warning("LLM not configured - check API key and settings")
            return "[LLM not configured]"

        # Local HF path — no retry loop needed (errors surface directly)
        if result == "local":
            logger.info("Calling local HF model")
            return await self._call_local(prompt)

        client, model, temperature = result
        import openai

        for attempt in range(max_retries):
            try:
                logger.info(f"Calling LLM: {model}")
                response = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                )
                return response.choices[0].message.content or ""
            except (openai.APITimeoutError, openai.APIConnectionError) as e:
                if attempt < max_retries - 1:
                    delay = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning(f"LLM request failed ({e}), retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"LLM error after {max_retries} attempts: {e}")
                    raise
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
        provider_override: str = "",
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
            provider_override: If non-empty, use this provider instead of global llm_provider
        """
        # Local GGUF is single-threaded; serialise chunks
        if self._effective_provider(provider_override) == "local":
            max_concurrency = 1

        segments = self._parse_srt(srt_content)
        if not segments:
            # Fallback to simple polish if not valid SRT
            prompt = get_simple_polish_prompt(srt_content)
            return await self._call(prompt, provider_override=provider_override)

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
                polished_chunk = await self._call(prompt, provider_override=provider_override)

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
        rt = get_runtime_settings()
        # polish_provider="" means follow global llm_provider (no override)
        provider_override = rt.polish_provider
        if context:
            return await self.polish_with_context_parallel(text, context, provider_override=provider_override)
        prompt = get_simple_polish_prompt(text)
        return await self._call(prompt, provider_override=provider_override)

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

    async def summarize(self, text: str, user_language: str | None = None) -> dict[str, Any]:
        prompt = get_summarize_prompt(text, user_language=user_language)
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
        user_language: str | None = None,
    ) -> str:
        """Generate mindmap, auto-selecting single-pass or map-reduce based on length."""
        # Rough threshold: ~15k chars ≈ 30min of Chinese transcript
        if len(text) > 15000 and metadata:
            chapters = metadata.get("chapters")
            if chapters:
                return await self._mindmap_map_reduce(text, metadata, chapters, user_language=user_language)
            # No chapters but long text — auto-split by segment count
            return await self._mindmap_map_reduce_auto(text, metadata, user_language=user_language)

        # Short content: single-pass
        prompt = get_mindmap_prompt(text, user_language=user_language)
        resp = await self._call(prompt)
        return self._filter_mindmap_lines(resp)

    async def _mindmap_map_reduce(
        self,
        text: str,
        metadata: dict[str, Any],
        chapters: list[dict],
        user_language: str | None = None,
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
        map_concurrency = 1 if self._effective_provider() == "local" else 8
        semaphore = asyncio.Semaphore(map_concurrency)

        async def map_one(title: str, content: str) -> tuple[str, str]:
            async with semaphore:
                prompt = get_mindmap_map_prompt(
                    title, content, global_context, user_language=user_language,
                )
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
        return await self._mindmap_reduce(chapter_summaries, user_language=user_language)

    async def _mindmap_map_reduce_auto(
        self,
        text: str,
        metadata: dict[str, Any],
        user_language: str | None = None,
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
        map_concurrency = 1 if self._effective_provider() == "local" else 8
        semaphore = asyncio.Semaphore(map_concurrency)

        async def map_one(title: str, content: str) -> tuple[str, str]:
            async with semaphore:
                prompt = get_mindmap_map_prompt(
                    title, content, global_context, user_language=user_language,
                )
                resp = await self._call(prompt)
                return title, resp

        map_results = await asyncio.gather(*[
            map_one(t, c) for t, c in chapter_texts.items() if c.strip()
        ])
        chapter_summaries = dict(map_results)

        return await self._mindmap_reduce(chapter_summaries, user_language=user_language)

    async def _mindmap_reduce(
        self,
        chapter_summaries: dict[str, str],
        user_language: str | None = None,
    ) -> str:
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
        reduce_concurrency = 1 if self._effective_provider() == "local" else 4
        semaphore = asyncio.Semaphore(reduce_concurrency)

        async def reduce_one(label: str, batch_names: list[str]) -> str:
            async with semaphore:
                summaries = ""
                for name in batch_names:
                    summaries += f"\n### {name}\n{chapter_summaries[name]}\n"
                prompt = get_mindmap_reduce_prompt(
                    label, summaries, user_language=user_language,
                )
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


async def summarize_text(text: str, user_language: str | None = None) -> dict[str, Any]:
    return await get_llm_service().summarize(text, user_language=user_language)


async def generate_mindmap(
    text: str,
    metadata: dict[str, Any] | None = None,
    user_language: str | None = None,
) -> str:
    return await get_llm_service().mindmap(
        text, metadata=metadata, user_language=user_language,
    )
