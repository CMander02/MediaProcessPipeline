"""LLM service for text analysis via LiteLLM unified gateway."""

import asyncio
import json
import logging
import os
import re
import time
from typing import Any

os.environ.setdefault("LITELLM_LOG", "WARNING")

from app.core.config import get_settings
from app.core.logging_setup import log_event
from app.core.settings import get_runtime_settings
from app.services.analysis.prompts import (
    get_analyze_prompt,
    get_polish_prompt,
    get_simple_polish_prompt,
    get_summarize_prompt,
    get_detail_prompt,
    get_mindmap_prompt,
    get_mindmap_map_prompt,
    get_mindmap_reduce_prompt,
)

logger = logging.getLogger(__name__)

_TIMESTAMP_RE = re.compile(
    r"\s*\[(\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d{1,3})?)"
    r"(?:\s*-\s*(\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d{1,3})?))?\]\s*$"
)
_SPEAKER_PREFIX_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(.*)$", re.DOTALL)
_SENTENCE_SPLIT_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]*[”’）】》」』]*")
_SENTENCE_END_RE = re.compile(r"(?:[。！？!?；;]|(?<!\d)\.)[\"'”’）】》」』]*$")


def _timestamp_to_seconds(value: str | None) -> float | None:
    if not value:
        return None
    parts = value.replace(",", ".").split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (TypeError, ValueError):
        return None


def _seconds_to_srt_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _split_mindmap_line(line: str) -> tuple[int, str, float | None, float | None] | None:
    stripped = line.rstrip()
    marker = re.match(r"^(\s*)[-*]\s+(.+?)\s*$", stripped)
    if not marker:
        return None
    depth = len(marker.group(1).replace("\t", "  ")) // 2
    title = marker.group(2).strip()
    start = end = None
    ts_match = _TIMESTAMP_RE.search(title)
    if ts_match:
        start = _timestamp_to_seconds(ts_match.group(1))
        end = _timestamp_to_seconds(ts_match.group(2))
        title = title[: ts_match.start()].strip()
    return depth, title, start, end


def mindmap_markdown_without_timestamps(markdown: str) -> str:
    """Export timed mindmap markdown as a plain nested Markdown list."""
    lines: list[str] = []
    for raw in markdown.splitlines():
        parsed = _split_mindmap_line(raw)
        if not parsed:
            continue
        depth, title, _start, _end = parsed
        lines.append(f"{'  ' * depth}- {title}")
    return "\n".join(lines)


def mindmap_markdown_to_timed_tree(markdown: str) -> dict[str, Any]:
    """Parse `- node [start - end]` markdown into a frontend-friendly tree."""
    roots: list[dict[str, Any]] = []
    stack: list[tuple[int, dict[str, Any]]] = []
    for raw in markdown.splitlines():
        parsed = _split_mindmap_line(raw)
        if not parsed:
            continue
        depth, title, start, end = parsed
        node: dict[str, Any] = {"title": title, "children": []}
        if start is not None:
            node["start"] = start
        if end is not None:
            node["end"] = end
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if stack:
            stack[-1][1]["children"].append(node)
        else:
            roots.append(node)
        stack.append((depth, node))
    if not roots:
        return {"title": "Mindmap", "children": []}
    if len(roots) == 1:
        return roots[0]
    return {"title": "Mindmap", "children": roots}


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
        from transformers.utils import logging as hf_logging
    except ImportError as e:
        raise RuntimeError(
            "transformers/torch not installed. Sync the project environment first: "
            "uv sync"
        ) from e

    log_event(logger, logging.INFO, "llm.local.load_started", model_path=model_path, device=device, dtype=dtype)
    # tqdm can fail when the daemon is launched by Electron with hidden stdio on Windows.
    hf_logging.disable_progress_bar()

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
    log_event(logger, logging.INFO, "llm.local.load_completed", model_class=model.__class__.__name__, device=device)
    return {"model": model, "tokenizer": tokenizer, "is_vl": is_vl}


def offload_local_llm() -> None:
    """Release the local HF model and free VRAM. Safe to call multiple times."""
    global _local_llm, _local_llm_path
    if _local_llm is not None:
        log_event(logger, logging.INFO, "llm.local.offload_started")
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


def _get_deepseek_params(stage: str = "polish") -> dict[str, Any] | None:
    """Build OpenAI SDK kwargs for DeepSeek's OpenAI-compatible API."""
    rt = get_runtime_settings()
    if not rt.deepseek_api_key:
        return None

    stage_map = {
        "analyze": (rt.deepseek_analyze_model, rt.deepseek_analyze_thinking, rt.deepseek_analyze_effort),
        "polish": (rt.deepseek_polish_model, rt.deepseek_polish_thinking, rt.deepseek_polish_effort),
        "summary": (rt.deepseek_summary_model, rt.deepseek_summary_thinking, rt.deepseek_summary_effort),
        "mindmap": (rt.deepseek_mindmap_model, rt.deepseek_mindmap_thinking, rt.deepseek_mindmap_effort),
    }
    model_name, thinking_type, effort = stage_map.get(stage, stage_map["polish"])
    if not model_name:
        return None

    params: dict[str, Any] = {
        "model": model_name,
        "api_key": rt.deepseek_api_key,
        "api_base": rt.deepseek_api_base or "https://api.deepseek.com",
        "extra_body": {"thinking": {"type": "enabled" if thinking_type == "enabled" else "disabled"}},
    }
    if thinking_type == "enabled" and effort:
        params["reasoning_effort"] = effort
    return params


def _get_litellm_params(provider_override: str = "", stage: str = "polish") -> dict[str, Any] | None:
    """Build litellm.acompletion kwargs from runtime settings.

    Returns None if provider is not configured or is the local HF path.
    Callers should handle the local provider case before calling this function.

    Args:
        provider_override: If non-empty, use this provider instead of rt.llm_provider.
        stage: One of "analyze" | "polish" | "summary" | "mindmap". Currently
            only deepseek uses it to pick per-stage model/thinking/effort.
    """
    rt = get_runtime_settings()
    provider = provider_override or rt.llm_provider

    if provider == "local":
        return None  # caller handles local path

    params: dict[str, Any] = {"num_retries": 3}

    if provider == "anthropic":
        if not rt.anthropic_api_key:
            return None
        model_name = rt.anthropic_model or "claude-sonnet-4-6"
        params["model"] = f"anthropic/{model_name}"
        params["api_key"] = rt.anthropic_api_key
        if rt.anthropic_api_base:
            params["api_base"] = rt.anthropic_api_base

    elif provider == "openai":
        if not rt.openai_api_key:
            return None
        model_name = rt.openai_model or "gpt-4o"
        params["model"] = f"openai/{model_name}"
        params["api_key"] = rt.openai_api_key
        if rt.openai_api_base:
            params["api_base"] = rt.openai_api_base

    elif provider == "deepseek":
        deepseek_params = _get_deepseek_params(stage)
        if deepseek_params is None:
            return None
        params["model"] = f"openai/{deepseek_params['model']}"  # DeepSeek is OpenAI-compatible
        params["api_key"] = deepseek_params["api_key"]
        params["api_base"] = deepseek_params["api_base"]
        params["extra_body"] = deepseek_params["extra_body"]
        if deepseek_params.get("reasoning_effort"):
            params["reasoning_effort"] = deepseek_params["reasoning_effort"]

    elif provider == "custom":
        if not rt.custom_api_base or not rt.custom_model:
            return None
        params["model"] = f"openai/{rt.custom_model}"
        params["api_key"] = rt.custom_api_key or "not-needed"
        params["api_base"] = rt.custom_api_base
        if rt.custom_name:
            params["custom_llm_provider"] = "openai"

    else:
        return None

    return params


class LLMService:
    def __init__(self):
        self._static_settings = get_settings()

    def _effective_provider(self, provider_override: str = "") -> str:
        rt = get_runtime_settings()
        return provider_override or rt.llm_provider

    async def _call_local(self, prompt: str) -> str:
        """Call local HF model (transformers). Loads on first call; serialised via lock."""
        global _local_llm, _local_llm_path
        rt = get_runtime_settings()
        model_path = rt.local_llm_model_path

        if not model_path:
            log_event(logger, logging.WARNING, "llm.local.not_configured")
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

    async def _call(
        self,
        prompt: str,
        *,
        max_retries: int = 3,
        provider_override: str = "",
        stage: str = "polish",
    ) -> str:
        rt = get_runtime_settings()
        provider = provider_override or rt.llm_provider

        # Local HF path — unchanged, no LiteLLM involved
        if provider == "local":
            if rt.local_llm_model_path:
                log_event(logger, logging.INFO, "llm.local.call_started")
                return await self._call_local(prompt)
            log_event(logger, logging.WARNING, "llm.local.fallback", reason="model_path_empty")
            provider = rt.llm_provider
            provider_override = ""

        if provider == "deepseek":
            return await self._call_deepseek(prompt, stage=stage, max_retries=max_retries)

        import litellm
        params = _get_litellm_params(provider_override=provider_override, stage=stage)
        if params is None:
            log_event(logger, logging.WARNING, "llm.not_configured", provider=provider)
            return "[LLM not configured]"

        params["messages"] = [{"role": "user", "content": prompt}]

        model = params.get("model")
        t0 = time.perf_counter()
        log_event(logger, logging.INFO, "llm.call.started", provider=provider, model=model, stage=stage)
        try:
            response = await litellm.acompletion(**params)
            content = response.choices[0].message.content or ""
            log_event(
                logger,
                logging.INFO,
                "llm.call.completed",
                provider=provider,
                model=model,
                stage=stage,
                duration_ms=round((time.perf_counter() - t0) * 1000),
                chars=len(content),
            )
            return content
        except Exception as e:
            log_event(
                logger,
                logging.ERROR,
                "llm.call.failed",
                provider=provider,
                model=model,
                stage=stage,
                duration_ms=round((time.perf_counter() - t0) * 1000),
                error=e,
            )
            raise

    async def _call_deepseek(
        self,
        prompt: str,
        *,
        stage: str = "polish",
        max_retries: int = 3,
    ) -> str:
        """Call DeepSeek through the OpenAI SDK so native v4 options pass through."""
        params = _get_deepseek_params(stage)
        if params is None:
            log_event(logger, logging.WARNING, "llm.not_configured", provider="deepseek")
            return "[LLM not configured]"

        import httpx
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            base_url=params["api_base"],
            api_key=params["api_key"],
            max_retries=max_retries,
            timeout=httpx.Timeout(300.0, connect=30.0, read=300.0, write=30.0, pool=30.0),
        )
        request: dict[str, Any] = {
            "model": params["model"],
            "messages": [{"role": "user", "content": prompt}],
            "extra_body": params["extra_body"],
        }
        if params.get("reasoning_effort"):
            request["reasoning_effort"] = params["reasoning_effort"]

        t0 = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            "llm.call.started",
            provider="deepseek",
            model=params["model"],
            stage=stage,
        )
        try:
            response = await client.chat.completions.create(**request)
            content = response.choices[0].message.content or ""
            log_event(
                logger,
                logging.INFO,
                "llm.call.completed",
                provider="deepseek",
                model=params["model"],
                stage=stage,
                duration_ms=round((time.perf_counter() - t0) * 1000),
                chars=len(content),
            )
            return content
        except Exception as e:
            log_event(
                logger,
                logging.ERROR,
                "llm.call.failed",
                provider="deepseek",
                model=params["model"],
                stage=stage,
                duration_ms=round((time.perf_counter() - t0) * 1000),
                error=e,
            )
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
        resp = await self._call(prompt, stage="analyze")

        try:
            # Extract JSON from response
            start = resp.find("{")
            end = resp.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(resp[start:end])
        except json.JSONDecodeError as e:
            log_event(logger, logging.WARNING, "llm.analysis.parse_failed", error=e)

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

    @staticmethod
    def _split_speaker_prefix(text: str) -> tuple[str | None, str]:
        """Return (speaker, body) for text starting with a [speaker] tag."""
        match = _SPEAKER_PREFIX_RE.match(text.strip())
        if not match:
            return None, text.strip()
        return match.group(1).strip(), match.group(2).strip()

    @staticmethod
    def _timestamp_bounds(timestamp: str) -> tuple[str, str]:
        if "-->" not in timestamp:
            ts = timestamp.strip()
            return ts, ts
        start, end = timestamp.split("-->", 1)
        return start.strip(), end.strip()

    @staticmethod
    def _join_turn_text(existing: str, new_text: str) -> str:
        """Join cue text fragments without inserting noisy spaces in Chinese."""
        existing = existing.strip()
        new_text = new_text.strip()
        if not existing:
            return new_text
        if not new_text:
            return existing
        prev_core = existing.rstrip("\"'”’）】》」』")
        prev = prev_core[-1] if prev_core else existing[-1]
        nxt = new_text[0]
        if (
            prev.isascii()
            and nxt.isascii()
            and (prev.isalnum() or prev in ".,!?;:)]}\"'")
            and (nxt.isalnum() or nxt in "([{\"'")
        ):
            return f"{existing} {new_text}"
        return f"{existing}{new_text}"

    @staticmethod
    def _split_sentence_like(text: str) -> list[str]:
        """Split Chinese and English transcript text at likely sentence endings."""
        closers = "\"'”’）】》」』"
        pieces: list[str] = []
        start = 0
        i = 0
        while i < len(text):
            ch = text[i]
            boundary = ch in "。！？!?；;"
            if ch == ".":
                prev = text[i - 1] if i > 0 else ""
                nxt = text[i + 1] if i + 1 < len(text) else ""
                if not (prev.isdigit() and nxt.isdigit()):
                    j = i + 1
                    while j < len(text) and text[j] in closers:
                        j += 1
                    boundary = j >= len(text) or text[j].isspace()
            if boundary:
                end = i + 1
                while end < len(text) and text[end] in closers:
                    end += 1
                piece = text[start:end].strip()
                if piece:
                    pieces.append(piece)
                start = end
                i = end
                continue
            i += 1

        tail = text[start:].strip()
        if tail:
            pieces.append(tail)
        return pieces

    @staticmethod
    def _split_text_for_readable_turns(text: str, max_chars: int) -> list[str]:
        """Split text into sentence-ish pieces, with a comma/length fallback."""
        normalized = re.sub(r"\s+", " ", text.strip())
        normalized = re.sub(
            r"((?<!\d)[.!?][\"'”’）】》」』]*)(?=[A-Z])",
            r"\1 ",
            normalized,
        )
        if not normalized:
            return []

        pieces = LLMService._split_sentence_like(normalized) or [normalized]

        result: list[str] = []
        hard_limit = max(max_chars, 80)
        soft_limit = max(80, min(hard_limit, max_chars))
        for piece in pieces:
            if len(piece) <= hard_limit:
                result.append(piece)
                continue
            comma_parts = [
                part.strip()
                for part in re.split(r"(?<=[，,、：:])", piece)
                if part.strip()
            ]
            current = ""
            for part in comma_parts or [piece]:
                if current and len(current) + len(part) > soft_limit:
                    result.append(current)
                    current = part
                else:
                    current = current + part
            if current:
                while len(current) > hard_limit:
                    result.append(current[:hard_limit])
                    current = current[hard_limit:]
                if current:
                    result.append(current)
        return result

    @staticmethod
    def _sentence_count(text: str) -> int:
        count = len(re.findall(r"[。！？!?；;]|(?<!\d)\.(?=\s|$|[\"'”’）】》」』])", text))
        return max(1, count) if text.strip() else 0

    @staticmethod
    def _ends_sentence(text: str) -> bool:
        return bool(_SENTENCE_END_RE.search(text.strip()))

    def _segment_to_readable_events(
        self,
        seg: dict[str, Any],
        *,
        max_chars: int,
    ) -> list[dict[str, Any]]:
        speaker, body = self._split_speaker_prefix(str(seg.get("text", "")))
        pieces = self._split_text_for_readable_turns(body, max_chars=max_chars)
        if not pieces:
            return []

        start_ts, end_ts = self._timestamp_bounds(str(seg.get("timestamp", "")))
        start_s = _timestamp_to_seconds(start_ts)
        end_s = _timestamp_to_seconds(end_ts)
        if start_s is None or end_s is None or end_s <= start_s or len(pieces) == 1:
            return [
                {
                    "speaker": speaker,
                    "start": start_ts,
                    "end": end_ts,
                    "start_s": start_s,
                    "end_s": end_s,
                    "text": pieces[0] if len(pieces) == 1 else "".join(pieces),
                }
            ]

        total_chars = max(1, sum(len(piece) for piece in pieces))
        cursor_chars = 0
        events: list[dict[str, Any]] = []
        for piece in pieces:
            piece_start_s = start_s + (end_s - start_s) * (cursor_chars / total_chars)
            cursor_chars += len(piece)
            piece_end_s = start_s + (end_s - start_s) * (cursor_chars / total_chars)
            events.append(
                {
                    "speaker": speaker,
                    "start": _seconds_to_srt_timestamp(piece_start_s),
                    "end": _seconds_to_srt_timestamp(piece_end_s),
                    "start_s": piece_start_s,
                    "end_s": piece_end_s,
                    "text": piece,
                }
            )
        return events

    def merge_consecutive_speaker_segments(
        self,
        srt_content: str,
        *,
        max_chars: int = 180,
        max_duration: float = 30.0,
        max_sentences: int = 3,
        max_gap: float = 2.0,
    ) -> str:
        """Merge adjacent SRT cues by speaker, then split into readable turns.

        The LLM polishing step intentionally preserves cue count while it fixes
        words and punctuation. This deterministic pass converts those polished
        cues into dialogue turns: adjacent same-speaker cues are combined, but
        the result is cut at sentence boundaries when a turn gets too long.
        Empty speaker-only cues are ignored so they do not split a turn.
        """
        segments = self._parse_srt(srt_content)
        if not segments:
            return srt_content

        events: list[dict[str, Any]] = []
        for seg in segments:
            events.extend(self._segment_to_readable_events(seg, max_chars=max_chars))
        if not events:
            return srt_content

        readable: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None

        def flush_current() -> None:
            nonlocal current
            if current is not None and str(current.get("text", "")).strip():
                readable.append(current)
            current = None

        def duration_with(event: dict[str, Any]) -> float:
            if current is None:
                return 0.0
            start_s = current.get("start_s")
            end_s = event.get("end_s")
            if start_s is None or end_s is None:
                return 0.0
            return float(end_s) - float(start_s)

        for event in events:
            speaker = event.get("speaker")
            if (
                current is not None
                and (
                    speaker != current.get("speaker")
                    or (
                        event.get("start_s") is not None
                        and current.get("end_s") is not None
                        and float(event["start_s"]) - float(current["end_s"]) > max_gap
                    )
                )
            ):
                flush_current()

            if current is not None:
                projected = self._join_turn_text(str(current["text"]), str(event["text"]))
                current_can_end = self._ends_sentence(str(current["text"]))
                projected_duration = duration_with(event)
                hard_split = len(projected) > max_chars or projected_duration > max_duration
                sentence_split = current_can_end and (
                    int(current.get("sentences", 0)) >= max_sentences
                    or hard_split
                )
                should_split = hard_split or sentence_split
                if should_split:
                    flush_current()

            if current is None:
                current = {
                    "speaker": speaker,
                    "start": event["start"],
                    "end": event["end"],
                    "start_s": event.get("start_s"),
                    "end_s": event.get("end_s"),
                    "text": str(event["text"]).strip(),
                    "sentences": self._sentence_count(str(event["text"])),
                }
            else:
                current["end"] = event["end"]
                current["end_s"] = event.get("end_s")
                current["text"] = self._join_turn_text(
                    str(current["text"]),
                    str(event["text"]),
                )
                current["sentences"] = int(current.get("sentences", 0)) + self._sentence_count(
                    str(event["text"])
                )

            current_duration = 0.0
            if current.get("start_s") is not None and current.get("end_s") is not None:
                current_duration = float(current["end_s"]) - float(current["start_s"])
            if self._ends_sentence(str(current["text"])) and (
                int(current.get("sentences", 0)) >= max_sentences
                or len(str(current["text"])) >= max_chars
                or current_duration >= max_duration
            ):
                flush_current()

        flush_current()

        if not readable:
            return srt_content

        output_segments: list[dict[str, Any]] = []
        for index, item in enumerate(readable, 1):
            speaker = item.get("speaker")
            text = str(item["text"]).strip()
            if speaker:
                text = f"[{speaker}] {text}"
            output_segments.append(
                {
                    "index": index,
                    "timestamp": f"{item['start']} --> {item['end']}",
                    "text": text,
                }
            )
        return self._segments_to_srt(output_segments)

    def _parse_polish_response(
        self, response: str, fallback_segments: list[dict]
    ) -> list[dict]:
        """Parse an LLM polish response into segments.

        Tries, in order:
        1. JSON array of {index, timestamp, text} (the prompt-requested format),
           tolerating markdown fences and leading/trailing prose.
        2. Raw SRT blocks (legacy / when the model insists on SRT).
        3. Empty list — caller will align/fallback to input segments.
        """
        # Strip markdown code fences first
        text = response.strip()
        fence_match = re.match(
            r"^```(?:json|JSON)?\s*\n(.*?)\n```\s*$",
            text,
            flags=re.DOTALL,
        )
        if fence_match:
            text = fence_match.group(1).strip()

        # Try JSON: find the first '[' and the matching last ']' so we
        # tolerate leading/trailing junk like "好的，这是结果："
        try:
            start = text.find("[")
            end = text.rfind("]")
            if start >= 0 and end > start:
                arr = json.loads(text[start : end + 1])
                if isinstance(arr, list):
                    segs: list[dict] = []
                    for item in arr:
                        if not isinstance(item, dict):
                            continue
                        idx = item.get("index")
                        ts = item.get("timestamp")
                        txt = item.get("text")
                        if idx is None or not ts or txt is None:
                            continue
                        segs.append(
                            {
                                "index": int(idx),
                                "timestamp": str(ts).strip(),
                                "text": str(txt).strip(),
                            }
                        )
                    if segs:
                        return segs
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            log_event(logger, logging.DEBUG, "llm.polish.parse_json_failed", error=e)

        # Fall back to SRT block parse
        srt_segs = self._parse_srt(text)
        if srt_segs:
            return srt_segs

        # Nothing usable
        return []

    def _align_polished_to_input(
        self, polished: list[dict], original: list[dict]
    ) -> list[dict]:
        """Align polished segments back to the original cue list.

        Guarantees one output per input cue, preserving every original
        index+timestamp. For each input cue, picks the polished segment with
        the matching timestamp if present, else matching index, else falls
        back to the original text. This way we never lose cues even if the
        LLM dropped or duplicated some.
        """
        by_ts = {seg.get("timestamp"): seg for seg in polished if seg.get("timestamp")}
        by_idx = {seg.get("index"): seg for seg in polished if seg.get("index") is not None}
        aligned: list[dict] = []
        for orig in original:
            match = by_ts.get(orig["timestamp"]) or by_idx.get(orig["index"])
            if match and match.get("text"):
                aligned.append(
                    {
                        "index": orig["index"],
                        "timestamp": orig["timestamp"],
                        "text": match["text"],
                    }
                )
            else:
                aligned.append(dict(orig))
        return aligned

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
        # Local GGUF is single-threaded; serialise chunks.
        effective_provider = self._effective_provider(provider_override)
        if effective_provider == "local":
            max_concurrency = 1
        else:
            rt = get_runtime_settings()
            try:
                configured = int(
                    getattr(rt, "llm_polish_concurrency", max_concurrency) or max_concurrency
                )
            except (TypeError, ValueError):
                configured = max_concurrency
            max_concurrency = max(1, min(max_concurrency, configured))

        segments = self._parse_srt(srt_content)
        if not segments:
            # Fallback to simple polish if not valid SRT
            prompt = get_simple_polish_prompt(srt_content)
            return await self._call(prompt, provider_override=provider_override, stage="polish")

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

        log_event(
            logger,
            logging.INFO,
            "llm.polish.started",
            segments=len(segments),
            chunks=len(chunks),
            chunk_size=chunk_size,
            overlap=overlap,
            max_concurrency=max_concurrency,
        )

        # Create semaphore to limit concurrency
        semaphore = asyncio.Semaphore(max_concurrency)

        async def process_chunk(
            idx: int, start: int, end: int, chunk_segments: list[dict]
        ) -> tuple[int, list[dict]]:
            """Process a single chunk with semaphore control."""
            async with semaphore:
                chunk_t0 = time.perf_counter()
                log_event(
                    logger,
                    logging.INFO,
                    "llm.polish.chunk_started",
                    chunk=idx,
                    start_segment=start + 1,
                    end_segment=end,
                    total_segments=len(segments),
                    segments=len(chunk_segments),
                )

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
                polished_chunk = await self._call(prompt, provider_override=provider_override, stage="polish")

                # Try JSON first (preferred output format), then fall back to SRT
                polished_segs = self._parse_polish_response(polished_chunk, chunk_segments)

                if len(polished_segs) != len(chunk_segments):
                    log_event(
                        logger,
                        logging.WARNING,
                        "llm.polish.chunk_mismatch",
                        chunk=idx,
                        input_segments=len(chunk_segments),
                        output_segments=len(polished_segs),
                    )
                    polished_segs = self._align_polished_to_input(polished_segs, chunk_segments)
                else:
                    log_event(
                        logger,
                        logging.INFO,
                        "llm.polish.chunk_completed",
                        chunk=idx,
                        input_segments=len(chunk_segments),
                        output_segments=len(polished_segs),
                        duration_ms=round((time.perf_counter() - chunk_t0) * 1000),
                    )

                return (idx, polished_segs)

        # Process all chunks in parallel (with semaphore limiting concurrency)
        tasks = [
            asyncio.create_task(process_chunk(idx, start, end, segs))
            for idx, start, end, segs in chunks
        ]
        try:
            results = await asyncio.gather(*tasks)
        except Exception:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

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

        log_event(
            logger,
            logging.INFO,
            "llm.polish.completed",
            input_segments=len(segments),
            output_segments=len(polished_segments),
        )
        merged_srt = self.merge_consecutive_speaker_segments(
            self._segments_to_srt(polished_segments)
        )
        merged_count = len(self._parse_srt(merged_srt))
        log_event(
            logger,
            logging.INFO,
            "llm.polish.turn_merge_completed",
            input_segments=len(polished_segments),
            output_segments=merged_count,
        )
        return merged_srt

    async def polish(self, text: str, context: dict[str, Any] | None = None) -> str:
        """Polish text, optionally with context from analysis phase.

        Routing: if the input parses as multi-cue SRT, always use the chunked
        path. The chunked path enforces per-cue structure and falls back to
        original cues on parse failure — much safer than the simple flat
        prompt, which is prone to returning prose and destroying timestamps.
        The simple prompt is only safe for short, single-block text.
        """
        rt = get_runtime_settings()
        provider_override = rt.polish_provider
        if not context:
            context = {}
        if len(self._parse_srt(text)) >= 2:
            return await self.polish_with_context_parallel(
                text, context, provider_override=provider_override
            )
        prompt = get_simple_polish_prompt(text)
        return await self._call(prompt, provider_override=provider_override, stage="polish")

    def srt_to_markdown(self, srt_content: str, title: str = "") -> str:
        """
        Convert polished SRT to a clean Markdown document.
        Preserves SRT cue boundaries as readable paragraphs.
        """
        segments = self._parse_srt(srt_content)
        if not segments:
            return srt_content

        paragraphs: list[dict[str, str | None]] = []
        for seg in segments:
            text = seg['text'].strip()
            speaker, text = self._split_speaker_prefix(text)
            if text:
                paragraphs.append({"speaker": speaker, "text": text})

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
        resp = await self._call(prompt, stage="summary")
        try:
            start, end = resp.find("{"), resp.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(resp[start:end])
        except json.JSONDecodeError:
            pass
        return {"tldr": resp, "key_facts": [], "action_items": [], "topics": []}

    async def detail(self, text: str, user_language: str | None = None) -> str:
        """Generate optional detailed video outline (`detail.md`)."""
        prompt = get_detail_prompt(text, user_language=user_language)
        resp = await self._call(prompt, stage="summary")
        return self._filter_mindmap_lines(resp)

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
        resp = await self._call(prompt, stage="mindmap")
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
        log_event(logger, logging.INFO, "llm.mindmap.map_reduce_started", chapters=len(chapter_texts))
        map_concurrency = 1 if self._effective_provider() == "local" else 8
        semaphore = asyncio.Semaphore(map_concurrency)

        async def map_one(title: str, content: str) -> tuple[str, str]:
            async with semaphore:
                prompt = get_mindmap_map_prompt(
                    title, content, global_context, user_language=user_language,
                )
                resp = await self._call(prompt, stage="mindmap")
                return title, resp

        map_results = await asyncio.gather(*[
            map_one(title, content)
            for title, content in chapter_texts.items()
            if content.strip()
        ])
        chapter_summaries = dict(map_results)
        log_event(
            logger,
            logging.INFO,
            "llm.mindmap.map_completed",
            chapters=len(chapter_summaries),
            chars=sum(len(v) for v in chapter_summaries.values()),
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

        log_event(logger, logging.INFO, "llm.mindmap.auto_map_reduce_started", chunks=len(chapter_texts))
        map_concurrency = 1 if self._effective_provider() == "local" else 8
        semaphore = asyncio.Semaphore(map_concurrency)

        async def map_one(title: str, content: str) -> tuple[str, str]:
            async with semaphore:
                prompt = get_mindmap_map_prompt(
                    title, content, global_context, user_language=user_language,
                )
                resp = await self._call(prompt, stage="mindmap")
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

        log_event(logger, logging.INFO, "llm.mindmap.reduce_started", groups=len(groups), chapters=len(names))

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
                resp = await self._call(prompt, stage="mindmap")
                return self._filter_mindmap_lines(resp)

        results = await asyncio.gather(*[
            reduce_one(label, batch_names)
            for label, batch_names in groups
        ])

        final = "\n".join(results)
        log_event(logger, logging.INFO, "llm.mindmap.reduce_completed", chars=len(final))
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


def merge_consecutive_speaker_segments(srt_content: str, **kwargs: Any) -> str:
    """Merge adjacent polished SRT cues from the same speaker into dialogue turns."""
    return get_llm_service().merge_consecutive_speaker_segments(srt_content, **kwargs)


def srt_to_markdown(srt_content: str, title: str = "") -> str:
    """Convert SRT to clean Markdown document."""
    return get_llm_service().srt_to_markdown(srt_content, title)


async def summarize_text(text: str, user_language: str | None = None) -> dict[str, Any]:
    return await get_llm_service().summarize(text, user_language=user_language)


async def generate_detail(text: str, user_language: str | None = None) -> str:
    return await get_llm_service().detail(text, user_language=user_language)


async def generate_mindmap(
    text: str,
    metadata: dict[str, Any] | None = None,
    user_language: str | None = None,
) -> str:
    return await get_llm_service().mindmap(
        text, metadata=metadata, user_language=user_language,
    )
