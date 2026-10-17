"""LLM service for text analysis via LiteLLM unified gateway."""

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Callable

os.environ.setdefault("LITELLM_LOG", "WARNING")

from app.core.config import get_settings
from app.core.logging_setup import log_event
from app.core.settings import get_runtime_settings
from app.services.analysis import mindmap_outputs as _mindmap_outputs
from app.services.analysis import response_parsing as _response_parsing
from app.services.analysis import transcript_outputs as _transcript_outputs
from app.services.analysis.mindmap_outputs import _TIMESTAMP_RE as _TIMESTAMP_RE
from app.services.analysis.mindmap_outputs import _split_mindmap_line as _split_mindmap_line
from app.services.analysis.mindmap_outputs import (
    mindmap_markdown_to_timed_tree as mindmap_markdown_to_timed_tree,
)
from app.services.analysis.mindmap_outputs import (
    mindmap_markdown_without_timestamps as mindmap_markdown_without_timestamps,
)
from app.services.analysis.prompts import (
    ANALYZE_SYSTEM_PROMPT,
    MINDMAP_SYSTEM_PROMPT,
    POLISH_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    get_analyze_prompt,
    get_detail_prompt,
    get_mindmap_prompt,
    get_mindmap_reduce_prompt,
    get_polish_prompt,
    get_simple_polish_prompt,
    get_summarize_prompt,
)
from app.services.analysis.text_attachments import (
    anthropic_text_attachment,
    call_openai_text_attachment,
    text_attachment_provider,
)
from app.services.analysis.text_locale import normalize_chinese_script
from app.services.analysis.transcript_outputs import _SENTENCE_END_RE as _SENTENCE_END_RE
from app.services.analysis.transcript_outputs import _SENTENCE_SPLIT_RE as _SENTENCE_SPLIT_RE
from app.services.analysis.transcript_outputs import _SPEAKER_PREFIX_RE as _SPEAKER_PREFIX_RE
from app.services.analysis.transcript_outputs import (
    _seconds_to_srt_timestamp as _seconds_to_srt_timestamp,
)
from app.services.analysis.transcript_outputs import _timestamp_to_seconds as _timestamp_to_seconds

logger = logging.getLogger(__name__)


def _is_retryable_llm_error(error: BaseException) -> bool:
    """Return whether an LLM failure is safe to retry without changing the request."""
    retryable_names = {
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
        "RateLimitError",
        "ServiceUnavailableError",
    }
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (TimeoutError, ConnectionError)):
            return True
        if type(current).__name__ in retryable_names:
            return True
        status_code = getattr(current, "status_code", None)
        if isinstance(status_code, int) and (
            status_code in {408, 409, 425, 429} or status_code >= 500
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


# ---------------------------------------------------------------------------
# Local HuggingFace model singleton — transformers + safetensors backend.
# Loaded on first use, offloaded after task ends.
# ---------------------------------------------------------------------------
_local_llm: Any = None  # dict: {"model": ..., "tokenizer": ...}
_local_llm_path: str = ""  # path that was used to load the model
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
        import torch  # noqa: F401 - verify the optional runtime can be imported
        from transformers import AutoConfig, AutoTokenizer
        from transformers.utils import logging as hf_logging
    except ImportError as e:
        raise RuntimeError(
            "transformers/torch not installed. Sync the project environment first: uv sync"
        ) from e

    log_event(
        logger,
        logging.INFO,
        "llm.local.load_started",
        model_path=model_path,
        device=device,
        dtype=dtype,
    )
    # tqdm can fail when the daemon is launched by the desktop shell with hidden stdio on Windows.
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
    log_event(
        logger,
        logging.INFO,
        "llm.local.load_completed",
        model_class=model.__class__.__name__,
        device=device,
    )
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
    from app.core.model_router import resolve_deepseek_llm_binding

    rt = get_runtime_settings()
    binding = resolve_deepseek_llm_binding(rt, stage)
    if not binding.configured:
        return None
    return dict(binding.request_kwargs)


def _get_litellm_params(
    provider_override: str = "", stage: str = "polish"
) -> dict[str, Any] | None:
    """Build litellm.acompletion kwargs from runtime settings.

    Returns None if provider is not configured or is the local HF path.
    Callers should handle the local provider case before calling this function.

    Args:
        provider_override: If non-empty, use this provider instead of rt.llm_provider.
        stage: One of "analyze" | "polish" | "summary" | "mindmap". Currently
            only deepseek uses it to pick per-stage model/thinking/effort.
    """
    from app.core.model_router import resolve_llm_binding

    rt = get_runtime_settings()
    binding = resolve_llm_binding(rt, provider_override=provider_override, stage=stage)
    if binding.provider == "local":
        return None  # caller handles local path
    if not binding.configured:
        return None

    params = dict(binding.request_kwargs)
    if (
        binding.provider == "deepseek"
        and params.get("model")
        and not str(params["model"]).startswith("openai/")
    ):
        params["model"] = f"openai/{params['model']}"
    return params


class LLMService:
    def __init__(self):
        self._static_settings = get_settings()

    def _effective_provider(self, provider_override: str = "") -> str:
        from app.core.model_router import resolve_llm_binding

        rt = get_runtime_settings()
        return resolve_llm_binding(rt, provider_override=provider_override, stage="polish").provider

    async def _call_local(self, prompt: str, system_prompt: str = "") -> str:
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

            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            text = tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
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
            if len(new_tokens) >= max_new_tokens and int(new_tokens[-1]) != tokenizer.eos_token_id:
                raise RuntimeError("本地模型输出被截断，请缩小输入窗口。")
            return tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

        async with infer_lock:
            return await loop.run_in_executor(None, _infer)

    async def _call_local_llama_cpp(
        self,
        prompt: str,
        binding: Any,
        system_prompt: str = "",
        *,
        stage: str = "",
        response_schema: dict | None = None,
    ) -> str:
        """Call a managed local llama.cpp OpenAI-compatible endpoint."""
        import httpx

        from app.services.analysis._openai_client import make_async_openai_client
        from app.services.analysis.local_llm_runtime import get_local_llm_runtime

        stage = stage or binding.stage
        base_url = await asyncio.to_thread(
            get_local_llm_runtime().ensure,
            binding.request_kwargs,
        )
        timeout_sec = float(binding.request_kwargs.get("timeout_sec") or 300)
        timeout = httpx.Timeout(timeout_sec, connect=30.0, read=timeout_sec, write=30.0, pool=30.0)
        client = make_async_openai_client(
            f"{base_url}/v1",
            "local",
            max_retries=1,
            timeout=timeout,
        )
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        request: dict[str, Any] = {
            "model": binding.model,
            "messages": messages,
            "max_tokens": int(binding.request_kwargs.get("max_new_tokens") or 4096),
        }
        from app.services.analysis.prompts.speaker_identity import (
            LECTURE_IDENTITY_SYSTEM_PROMPT, SPEAKER_IDENTITY_SYSTEM_PROMPT,
        )
        if response_schema is not None:
            request["response_format"] = {"type": "json_object", "schema": response_schema}
        elif stage == "speaker_review":
            request["response_format"] = {
                "type": "json_object", "schema": {"type": "array", "items": {"type": "object"}},
            }
        elif stage == "subtitle_speakers" or system_prompt in {
            SPEAKER_IDENTITY_SYSTEM_PROMPT, LECTURE_IDENTITY_SYSTEM_PROMPT,
        }:
            request["response_format"] = {"type": "json_object"}
        temperature = self._static_settings.temperature
        if temperature > 0:
            request["temperature"] = temperature
        if binding.stage == "polish":
            request["temperature"] = 0.1
        request["extra_body"] = {
            "chat_template_kwargs": {
                "enable_thinking": binding.stage != "polish"
                and bool(binding.request_kwargs.get("enable_thinking", False)),
            }
        }
        async with client:
            response = await client.chat.completions.create(**request)
        choice = response.choices[0]
        content = (choice.message.content or "").strip()
        log_event(
            logger,
            logging.INFO,
            "llm.local.call_completed",
            stage=stage,
            model=binding.model,
            finish_reason=choice.finish_reason,
            chars=len(content),
            prompt_tokens=getattr(response.usage, "prompt_tokens", None),
            completion_tokens=getattr(response.usage, "completion_tokens", None),
        )
        if choice.finish_reason == "length":
            raise RuntimeError(
                f"本地模型 {binding.model} 的 {binding.stage} 输出被截断，"
                "请缩短输入、关闭思考或增加上下文和输出长度。"
            )
        if not content:
            raise RuntimeError(f"本地模型 {binding.model} 的 {binding.stage} 返回空正文。")
        return content

    async def _call(
        self,
        prompt: str,
        *,
        max_retries: int = 3,
        provider_override: str = "",
        stage: str = "polish",
        system_prompt: str = "",
        response_schema: dict | None = None,
    ) -> str:
        from app.core.model_router import resolve_llm_binding

        rt = get_runtime_settings()
        binding = resolve_llm_binding(
            rt,
            provider_override=provider_override,
            stage=stage,
        )
        provider = binding.provider

        # Local HF path — unchanged, no LiteLLM involved
        if provider == "local":
            if rt.local_llm_model_path:
                log_event(
                    logger, logging.INFO, "llm.local.call_started",
                    stage=stage, model=binding.model,
                )
                if binding.transport == "llama_cpp":
                    return await self._call_local_llama_cpp(
                        prompt,
                        binding,
                        system_prompt=system_prompt,
                        stage=stage,
                        response_schema=response_schema,
                    )
                return await self._call_local(prompt, system_prompt=system_prompt)
            log_event(logger, logging.WARNING, "llm.local.fallback", reason="model_path_empty")
            provider_override = ""
            provider = resolve_llm_binding(rt, stage=stage).provider

        if provider == "deepseek":
            return await self._call_deepseek(
                prompt,
                stage=stage,
                max_retries=max_retries,
                system_prompt=system_prompt,
            )

        if binding.transport in {"codex_cli", "agy_cli", "kimi_cli", "qoder_cli"}:
            from app.services.analysis.coding_plan_cli import call_coding_plan_cli

            if not binding.configured:
                raise RuntimeError(f"OAuth CLI Provider 配置不完整：{binding.reason}")
            t0 = time.perf_counter()
            log_event(
                logger,
                logging.INFO,
                "llm.coding_plan_cli.started",
                provider=provider,
                model=binding.model,
                stage=stage,
            )
            try:
                cli_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
                content = await call_coding_plan_cli(
                    str(binding.request_kwargs.get("provider_type") or ""),
                    model=binding.model,
                    prompt=cli_prompt,
                    cli_path=str(binding.request_kwargs.get("cli_path") or ""),
                    timeout_sec=float(binding.request_kwargs.get("timeout_sec") or 600),
                    reasoning_effort=str(binding.request_kwargs.get("reasoning_effort") or ""),
                )
            except Exception as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    "llm.coding_plan_cli.failed",
                    provider=provider,
                    model=binding.model,
                    stage=stage,
                    duration_ms=round((time.perf_counter() - t0) * 1000),
                    error=exc,
                )
                raise
            log_event(
                logger,
                logging.INFO,
                "llm.coding_plan_cli.completed",
                provider=provider,
                model=binding.model,
                stage=stage,
                duration_ms=round((time.perf_counter() - t0) * 1000),
                chars=len(content),
            )
            return content

        import litellm

        params = _get_litellm_params(provider_override=provider_override, stage=stage)
        if params is None:
            log_event(logger, logging.WARNING, "llm.not_configured", provider=provider)
            return "[LLM not configured]"

        attachment_provider = text_attachment_provider(provider, str(params.get("api_base") or ""))
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({
            "role": "user",
            "content": anthropic_text_attachment(prompt) if attachment_provider == "anthropic"
            else prompt,
        })
        params["messages"] = messages

        model = params.get("model")
        t0 = time.perf_counter()
        log_event(
            logger, logging.INFO, "llm.call.started", provider=provider, model=model, stage=stage
        )
        try:
            if attachment_provider == "openai":
                content = await call_openai_text_attachment(
                    params, prompt, system_prompt=system_prompt, max_retries=max_retries,
                )
            else:
                response = await litellm.acompletion(**params)
                if getattr(response.choices[0], "finish_reason", None) == "length":
                    raise RuntimeError("模型输出被截断，请缩小输入窗口。")
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
        system_prompt: str = "",
    ) -> str:
        """Call DeepSeek through the OpenAI SDK so native v4 options pass through."""
        params = _get_deepseek_params(stage)
        if params is None:
            log_event(logger, logging.WARNING, "llm.not_configured", provider="deepseek")
            return "[LLM not configured]"

        import httpx

        from app.services.analysis._openai_client import make_async_openai_client

        timeout = httpx.Timeout(300.0, connect=30.0, read=300.0, write=30.0, pool=30.0)
        client = make_async_openai_client(
            params["api_base"],
            params["api_key"],
            max_retries=max_retries,
            timeout=timeout,
        )
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        request: dict[str, Any] = {
            "model": params["model"],
            "messages": messages,
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
        async with client:
            try:
                response = await client.chat.completions.create(**request)
                if getattr(response.choices[0], "finish_reason", None) == "length":
                    raise RuntimeError("模型输出被截断，请缩小输入窗口。")
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

    @staticmethod
    def _sample_analysis_text(text: str, limit: int = 8000) -> str:
        return _response_parsing._sample_analysis_text(text, limit)

    async def analyze_content(
        self,
        text: str,
        title: str,
        metadata: dict[str, Any] | None = None,
        provider_override: str = "",
    ) -> dict[str, Any]:
        """
        Phase 1: Analyze content to extract metadata.
        This provides context for the polishing phase.

        Args:
            text: Transcript text
            title: Video/audio title
            metadata: Optional metadata dict with uploader, description, tags, chapters
        """
        truncated = self._sample_analysis_text(text)

        # Extract metadata fields
        uploader = metadata.get("uploader") if metadata else None
        description = metadata.get("description") if metadata else None
        tags = metadata.get("tags") if metadata else None
        chapters = metadata.get("chapters") if metadata else None

        evidence = json.dumps({"title": title, "uploader": uploader, "description": description,
                               "tags": tags, "chapters": chapters, "transcript": truncated}, ensure_ascii=False)
        resp = await self._generate_long_text(
            evidence, lambda value: get_analyze_prompt(title="", text=value),
            provider_override=provider_override, stage="analyze", system_prompt=ANALYZE_SYSTEM_PROMPT,
        )

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
            "tone": "unknown",
        }

    def _parse_srt(self, srt_content: str) -> list[dict]:
        return _transcript_outputs._parse_srt(srt_content)

    def _segments_to_srt(self, segments: list[dict]) -> str:
        return _transcript_outputs._segments_to_srt(segments)

    @staticmethod
    def _split_speaker_prefix(text: str) -> tuple[str | None, str]:
        return _transcript_outputs._split_speaker_prefix(text)

    @staticmethod
    def _timestamp_bounds(timestamp: str) -> tuple[str, str]:
        return _transcript_outputs._timestamp_bounds(timestamp)

    @staticmethod
    def _join_turn_text(existing: str, new_text: str) -> str:
        return _transcript_outputs._join_turn_text(existing, new_text)

    @staticmethod
    def _split_sentence_like(text: str) -> list[str]:
        return _transcript_outputs._split_sentence_like(text)

    @staticmethod
    def _split_text_for_readable_turns(text: str, max_chars: int) -> list[str]:
        return _transcript_outputs._split_text_for_readable_turns(text, max_chars)

    @staticmethod
    def _sentence_count(text: str) -> int:
        return _transcript_outputs._sentence_count(text)

    @staticmethod
    def _ends_sentence(text: str) -> bool:
        return _transcript_outputs._ends_sentence(text)

    def _segment_to_readable_events(
        self,
        seg: dict[str, Any],
        *,
        max_chars: int,
    ) -> list[dict[str, Any]]:
        return _transcript_outputs._segment_to_readable_events(seg, max_chars=max_chars)

    def merge_consecutive_speaker_segments(
        self,
        srt_content: str,
        *,
        max_chars: int = 180,
        max_duration: float = 30.0,
        max_sentences: int = 3,
        max_gap: float = 2.0,
        lineage: list[dict] | None = None,
    ) -> str:
        return _transcript_outputs.merge_consecutive_speaker_segments(
            srt_content,
            max_chars=max_chars,
            max_duration=max_duration,
            max_sentences=max_sentences,
            max_gap=max_gap,
            lineage=lineage,
        )

    def _parse_polish_response(self, response: str, fallback_segments: list[dict]) -> list[dict]:
        return _transcript_outputs._parse_polish_response(response, fallback_segments)

    def _align_polished_to_input(self, polished: list[dict], original: list[dict]) -> list[dict]:
        return _transcript_outputs._align_polished_to_input(polished, original)

    def _enforce_polish_constraints(
        self,
        polished: list[dict],
        original: list[dict],
        context: dict[str, Any],
    ) -> list[dict]:
        return _transcript_outputs._enforce_polish_constraints(polished, original, context)

    def _polish_timeline_context(
        self,
        context: dict[str, Any],
        chunk_segments: list[dict],
    ) -> str:
        return _transcript_outputs._polish_timeline_context(context, chunk_segments)

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
        from app.services.analysis.long_text import compact_metadata, prompt_budget
        caller_context = context
        context = compact_metadata(context, max(512, prompt_budget(provider_override, "polish", POLISH_SYSTEM_PROMPT) // 3))
        rt = get_runtime_settings()
        effective_provider = self._effective_provider(provider_override)
        configured = int(getattr(rt, "llm_polish_concurrency", 4))
        max_concurrency = max(1, min(max_concurrency, configured, 8))
        if effective_provider == "local":
            if getattr(rt, "local_llm_engine", "transformers") == "llama_cpp":
                max_concurrency = min(max_concurrency, rt.local_llm_concurrency)
                # Bound JSON output so a successful chunk covers every input cue.
                chunk_size = min(chunk_size, 24)
                overlap = min(overlap, 4)
            else:
                # Transformers shares one model.generate() instance.
                max_concurrency = 1

        # Kimi Code produces subtitle JSON reliably at this bounded response
        # size. Larger chunks can exceed the OAuth CLI request timeout.
        if effective_provider in {"kimi-oauth", "kimi_oauth"}:
            chunk_size = min(chunk_size, 24)
            overlap = min(overlap, 4)

        segments = self._parse_srt(srt_content)
        if not segments:
            # Fallback to simple polish if not valid SRT
            return await self._polish_plain(srt_content, provider_override)

        # Generate all chunks with overlap
        chunks: list[tuple[int, int, list[dict]]] = []
        i = 0
        chunk_idx = 0
        while i < len(segments):
            end = min(i + chunk_size, len(segments))
            chunks.append((chunk_idx, i, end, segments[i:end]))
            chunk_idx += 1
            if end == len(segments):
                break
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

                speaker_ids = sorted(
                    {
                        speaker
                        for speaker, _body in (
                            self._split_speaker_prefix(str(segment.get("text") or ""))
                            for segment in chunk_segments
                        )
                        if speaker
                    }
                )

                async def polish_batch(
                    batch: list[dict], *, retry: bool = False, punctuation_only: bool = False,
                ) -> list[dict]:
                    def build_prompt(rows, visible):
                        return get_polish_prompt(
                            text=self._segments_to_srt(rows),
                            language=context.get("language", "unknown"),
                            content_type=context.get("content_type", "unknown"),
                            main_topics=context.get("main_topics"),
                            keywords=context.get("keywords"),
                            proper_nouns=context.get("proper_nouns"),
                            entities=context.get("entities"),
                            speaker_ids=speaker_ids,
                            timeline_context=self._polish_timeline_context(context, rows),
                        )
                    suffix = ""
                    if retry:
                        suffix += (
                            "\n\n本批是上一轮缺失或正文无效的补修字幕。"
                            "只输出本批输入的条目，每个原始 index 必须完整、唯一地出现一次。"
                            "每条必须保留非空正文，禁止空字符串或仅说话人标签；"
                            "整条短回应、语气词或起句片段保留自然表达。"
                            "字幕将拼成阅读稿，分句停顿加逗号，完整陈述和疑问句加句号或问号，"
                            "恢复跨条目的语法标点；短片段可不加句号。"
                            "保持原 timestamp 和说话人，直接输出完整 JSON 数组。"
                        )
                    if punctuation_only:
                        suffix += (
                            "\n\n本批正文已经完整，本次只补充跨字幕的逗号、句号和问号。"
                            "保留每条现有文字、原 index、timestamp 和说话人标签，"
                            "每条保持非空，直接输出完整 JSON 数组。"
                        )
                    from app.services.analysis.long_text import prompt_budget, record_windows, restore_boundary_space

                    async def call(prompt):
                        return await self._call(prompt, provider_override=provider_override,
                                                stage="polish", system_prompt=POLISH_SYSTEM_PROMPT)

                    valid_parts = {}
                    source_parts = {}
                    parsed = []
                    async for rows, visible, response in record_windows(
                        batch, lambda rows, visible: build_prompt(rows, visible) + suffix, call,
                        budget=prompt_budget(provider_override, "polish", POLISH_SYSTEM_PROMPT),
                        chunk_size=len(batch), overlap=0,
                    ):
                        result = self._parse_polish_response(response, rows)
                        parsed.extend(result)
                        valid_rows = {row["index"]: row for row in _transcript_outputs._valid_polished_segments(result, rows)}
                        for row in rows:
                            valid_parts.setdefault(row["index"], []).append(valid_rows.get(row["index"]))
                            source_parts.setdefault(row["index"], []).append(row["text"])
                    valid = []
                    for original in batch:
                        parts = valid_parts.get(original["index"], [])
                        if not parts or any(part is None for part in parts):
                            continue
                        if len(parts) == 1:
                            valid.append(parts[0])
                        else:
                            speaker, _ = self._split_speaker_prefix(original["text"])
                            body = "".join(
                                restore_boundary_space(
                                    re.sub(r"^\[[^\]\r\n]+\][ \t]?", "", source),
                                    self._split_speaker_prefix(part["text"])[1],
                                ) for source, part in zip(source_parts[original["index"]], parts, strict=True)
                            )
                            valid.append({**original, "text": f"[{speaker}] {body}" if speaker else body})
                    repair_type = (
                        "punctuation" if punctuation_only else "missing" if retry else "initial"
                    )
                    log_event(
                        logger,
                        logging.INFO,
                        "llm.polish.response_validated",
                        chunk=idx,
                        input_segments=len(batch),
                        parsed_segments=len(parsed),
                        valid_segments=len(valid),
                        repair=repair_type,
                    )
                    return valid

                # Count actual cue coverage: duplicates and speaker-only responses
                # cannot stand in for missing dialogue. Retry only affected cues.
                polished_segs = await polish_batch(chunk_segments)
                recovered = {segment["index"]: segment for segment in polished_segs}
                missing = [
                    segment for segment in chunk_segments if segment["index"] not in recovered
                ]
                if missing:
                    log_event(
                        logger,
                        logging.WARNING,
                        "llm.polish.incomplete_retry_started",
                        chunk=idx,
                        input_segments=len(chunk_segments),
                        output_segments=len(polished_segs),
                        missing_segments=",".join(str(segment["index"]) for segment in missing),
                        retry_batch_size=8,
                    )
                    for offset in range(0, len(missing), 8):
                        repaired = await polish_batch(missing[offset : offset + 8], retry=True)
                        recovered.update((segment["index"], segment) for segment in repaired)
                missing_indexes = [
                    segment["index"]
                    for segment in chunk_segments
                    if segment["index"] not in recovered
                ]
                polished_segs = [
                    recovered.get(segment["index"], dict(segment))
                    for segment in chunk_segments
                ]
                if _transcript_outputs._polish_response_needs_punctuation(
                    polished_segs, chunk_segments,
                ):
                    log_event(
                        logger,
                        logging.WARNING,
                        "llm.polish.punctuation_missing",
                        chunk=idx,
                        valid_segments=len(recovered),
                    )
                    try:
                        punctuated = await polish_batch(
                            polished_segs, punctuation_only=True,
                        )
                    except Exception as repair_error:
                        log_event(
                            logger,
                            logging.WARNING,
                            "llm.polish.punctuation_repair_failed",
                            chunk=idx,
                            error=repair_error,
                        )
                    else:
                        if len(punctuated) == len(chunk_segments) and not (
                            _transcript_outputs._polish_response_needs_punctuation(
                                punctuated, chunk_segments,
                            )
                        ):
                            polished_segs = punctuated
                            missing_indexes = []
                        else:
                            log_event(
                                logger,
                                logging.WARNING,
                                "llm.polish.punctuation_repair_incomplete",
                                chunk=idx,
                                valid_segments=len(punctuated),
                                input_segments=len(chunk_segments),
                            )
                if missing_indexes:
                    log_event(
                        logger,
                        logging.WARNING,
                        "llm.polish.original_cues_preserved",
                        chunk=idx,
                        indexes=",".join(map(str, missing_indexes)),
                    )
                log_event(
                    logger,
                    logging.INFO,
                    "llm.polish.chunk_completed",
                    chunk=idx,
                    input_segments=len(chunk_segments),
                    output_segments=len(polished_segs),
                    preserved_original=len(missing_indexes),
                    duration_ms=round((time.perf_counter() - chunk_t0) * 1000),
                )
                polished_segs = self._enforce_polish_constraints(
                    polished_segs,
                    chunk_segments,
                    context,
                )
                return (idx, polished_segs)

        # Process all chunks in parallel (with semaphore limiting concurrency)
        tasks = [
            asyncio.create_task(process_chunk(idx, start, end, segs))
            for idx, start, end, segs in chunks
        ]
        try:
            raw_results = await asyncio.gather(*tasks)
        except Exception as first_error:
            if not _is_retryable_llm_error(first_error):
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            # A transient connection failure in one chunk should not discard
            # successful work or cancel unrelated in-flight chunks.
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[tuple[int, list[dict]]] = []
        failed_chunks: list[tuple[tuple[int, int, int, list[dict]], BaseException]] = []
        for chunk, result in zip(chunks, raw_results, strict=True):
            if isinstance(result, BaseException):
                if not _is_retryable_llm_error(result):
                    raise result
                failed_chunks.append((chunk, result))
            else:
                results.append(result)

        if failed_chunks:
            log_event(
                logger,
                logging.WARNING,
                "llm.polish.chunk_retry_batch_started",
                failed_chunks=",".join(str(chunk[0]) for chunk, _error in failed_chunks),
                retry_mode="sequential",
            )
            for (idx, start, end, segs), previous_error in failed_chunks:
                log_event(
                    logger,
                    logging.WARNING,
                    "llm.polish.chunk_retry_started",
                    chunk=idx,
                    previous_error=previous_error,
                )
                try:
                    results.append(await process_chunk(idx, start, end, segs))
                except Exception as retry_error:
                    log_event(
                        logger,
                        logging.ERROR,
                        "llm.polish.chunk_retry_failed",
                        chunk=idx,
                        error=retry_error,
                    )
                    raise RuntimeError(
                        f"字幕润色第 {idx + 1} 批重试失败：{retry_error}"
                    ) from retry_error

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

        log_event(
            logger,
            logging.INFO,
            "llm.polish.completed",
            input_segments=len(segments),
            output_segments=len(polished_segments),
        )
        lineage = []
        merged_srt = self.merge_consecutive_speaker_segments(
            self._segments_to_srt(polished_segments), lineage=lineage,
        )
        caller_context["_speaker_lineage"] = {"version": 1, "input_srt": srt_content,
                                               "output_srt": merged_srt, "rows": lineage}
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
        if context is None:
            context = {}
        if self._parse_srt(text):
            return await self.polish_with_context_parallel(
                text, context, provider_override=provider_override
            )
        return await self._polish_plain(text, provider_override)

    async def _polish_plain(self, text: str, provider_override: str = "") -> str:
        from app.services.analysis.long_text import prompt_budget, record_windows, restore_boundary_space

        async def call(prompt):
            return await self._call(prompt, provider_override=provider_override,
                                    stage="polish", system_prompt=POLISH_SYSTEM_PROMPT)

        output = []
        async for rows, _, response in record_windows(
            [{"index": 0, "text": text}],
            lambda rows, visible: get_simple_polish_prompt(rows[0]["text"]), call,
            budget=prompt_budget(provider_override, "polish", POLISH_SYSTEM_PROMPT),
            chunk_size=1, overlap=0,
        ):
            output.append(restore_boundary_space(rows[0]["text"], response))
        return "".join(output)

    def srt_to_markdown(self, srt_content: str, title: str = "") -> str:
        return _transcript_outputs.srt_to_markdown(srt_content, title)

    async def _generate_long_text(
        self, text: str, build_prompt: Callable[[str], str], *,
        provider_override: str = "", stage: str, system_prompt: str,
        build_digest_prompt: Callable[[str], str] | None = None,
    ) -> str:
        from app.core.model_router import resolve_llm_binding
        from app.services.analysis.long_text import generate_bounded

        binding = resolve_llm_binding(
            get_runtime_settings(), provider_override=provider_override, stage=stage,
        )
        params = binding.request_kwargs
        context_size = max(2048, int(params.get("ctx") or 32768))
        output_tokens = int(params.get("max_new_tokens") or 4096)

        async def call(prompt: str, *, system_prompt: str) -> str:
            return await self._call(
                prompt, provider_override=provider_override, stage=stage,
                system_prompt=system_prompt,
            )

        return await generate_bounded(
            text, build_prompt, call, system_prompt=system_prompt,
            context_size=context_size, output_tokens=output_tokens,
            build_digest_prompt=build_digest_prompt,
        )

    async def summarize(
        self,
        text: str,
        user_language: str | None = None,
        provider_override: str = "",
        source_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from app.services.analysis.source_context import canonicalize_json

        if (
            source_context
            and source_context.get("timeline")
            and len(text) > 15000
            and self._parse_srt(text)
        ):
            return await self._summarize_by_timeline(
                text,
                user_language=user_language,
                provider_override=provider_override,
                source_context=source_context,
            )

        from app.services.analysis.long_text import compact_metadata, prompt_budget
        prompt_context = compact_metadata(
            {**(source_context or {}), "timeline": []},
            max(512, prompt_budget(provider_override, "summary", SUMMARY_SYSTEM_PROMPT) // 3),
        )
        # Plain-text reductions cannot align every source chapter. Repeating the
        # entire timeline in each intermediate JSON defeats bounded compression;
        # chapter anchors/summaries are restored deterministically below.
        prompt_context = {**prompt_context, "timeline": []}
        from app.services.analysis.prompts.summarize import get_summary_digest_prompt
        resp = await self._generate_long_text(
            text,
            lambda part: get_summarize_prompt(
                part, user_language=user_language, source_context=prompt_context,
            ),
            provider_override=provider_override,
            stage="summary",
            system_prompt=SUMMARY_SYSTEM_PROMPT,
            build_digest_prompt=lambda part: get_summary_digest_prompt(part, user_language),
        )
        try:
            start, end = resp.find("{"), resp.rfind("}") + 1
            if start >= 0 and end > start:
                result = canonicalize_json(json.loads(resp[start:end]), source_context)
                if source_context and source_context.get("timeline"):
                    generated = result.get("timeline") or []
                    generated_by_start = {
                        float(item.get("start") or 0): str(item.get("summary") or "")
                        for item in generated
                        if isinstance(item, dict)
                    }
                    result["timeline"] = [
                        {
                            **item,
                            "summary": generated_by_start.get(float(item.get("start") or 0), "")
                            or str(item.get("summary") or ""),
                        }
                        for item in source_context["timeline"]
                    ]
                else:
                    result["timeline"] = []
                return result
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        result = {"tldr": resp, "key_facts": [], "action_items": [], "topics": []}
        if source_context and source_context.get("timeline"):
            result["timeline"] = [dict(item, summary="") for item in source_context["timeline"]]
        return canonicalize_json(result, source_context)

    @staticmethod
    def _parse_summary_json(response: str) -> dict[str, Any] | None:
        return _response_parsing._parse_summary_json(response)

    async def _summarize_by_timeline(
        self,
        text: str,
        *,
        user_language: str | None,
        provider_override: str,
        source_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Summarize long SRT chapter by chapter before a compact final reduce."""
        from app.services.analysis.source_context import canonicalize_json
        from app.services.analysis.mindmap_outputs import _chapter_key

        timeline = [
            item
            for item in source_context.get("timeline") or []
            if isinstance(item, dict) and item.get("title")
        ]
        chapters = [
            {"title": item["title"], "start_time": item.get("start", 0)} for item in timeline
        ]
        blocks = self._split_segments_by_chapters(self._parse_srt(text), chapters)
        semaphore = asyncio.Semaphore(6)

        from app.services.analysis.long_text import compact_metadata, prompt_budget
        prompt_context = compact_metadata(source_context, max(512, prompt_budget(provider_override, "summary", SUMMARY_SYSTEM_PROMPT) // 3))

        async def summarize_chapter(index: int, item: dict[str, Any]) -> dict[str, Any]:
            chapter_context = {**prompt_context, "timeline": [item]}
            chapter_text = blocks.get(_chapter_key(item, index), "")
            if not chapter_text.strip():
                return {
                    "tldr": "",
                    "key_facts": [],
                    "action_items": [],
                    "topics": [],
                }
            async with semaphore:
                response = await self._generate_long_text(
                    chapter_text,
                    lambda part: get_summarize_prompt(
                        part,
                        user_language=user_language,
                        source_context=chapter_context,
                    ),
                    provider_override=provider_override,
                    stage="summary",
                    system_prompt=SUMMARY_SYSTEM_PROMPT,
                )
            parsed = self._parse_summary_json(response)
            if parsed is None:
                parsed = {
                    "tldr": response.strip(),
                    "key_facts": [],
                    "action_items": [],
                    "topics": [],
                }
            return canonicalize_json(parsed, source_context)

        chapter_results = await asyncio.gather(
            *(summarize_chapter(index, item) for index, item in enumerate(timeline))
        )
        digest_lines = []
        for item, result in zip(timeline, chapter_results, strict=True):
            digest_lines.append(
                f"[{float(item.get('start') or 0):g}s] {item['title']}: "
                f"{json.dumps(result, ensure_ascii=False)}"
            )
        reduce_response = await self._generate_long_text(
            "\n".join(digest_lines),
            lambda part: get_summarize_prompt(
                part,
                user_language=user_language,
                source_context=prompt_context,
            ),
            provider_override=provider_override,
            stage="summary",
            system_prompt=SUMMARY_SYSTEM_PROMPT,
        )
        reduced = self._parse_summary_json(reduce_response) or {
            "tldr": reduce_response.strip(),
            "key_facts": [],
            "action_items": [],
            "topics": [],
        }
        reduced["timeline"] = [
            {
                **item,
                "summary": str(result.get("tldr") or "").strip(),
            }
            for item, result in zip(timeline, chapter_results, strict=True)
        ]
        if not reduced.get("key_facts"):
            reduced["key_facts"] = [
                fact for result in chapter_results for fact in list(result.get("key_facts") or [])
            ][:10]
        if not reduced.get("action_items"):
            reduced["action_items"] = [
                action
                for result in chapter_results
                for action in list(result.get("action_items") or [])
            ]
        if not reduced.get("topics"):
            reduced["topics"] = [str(item["title"]) for item in timeline]
        return canonicalize_json(reduced, source_context)

    async def detail(
        self,
        text: str,
        user_language: str | None = None,
        provider_override: str = "",
    ) -> str:
        """Generate optional detailed video outline (`detail.md`)."""
        resp = await self._generate_long_text(
            text, lambda part: get_detail_prompt(part, user_language=user_language),
            provider_override=provider_override,
            stage="summary",
            system_prompt=SUMMARY_SYSTEM_PROMPT,
        )
        return self._filter_mindmap_lines(resp)

    async def mindmap(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
        user_language: str | None = None,
        provider_override: str = "",
    ) -> str:
        """Generate mindmap, auto-selecting single-pass or map-reduce based on length."""
        if metadata:
            chapters = metadata.get("chapters")
            if chapters and self._parse_srt(text):
                result = await self._mindmap_map_reduce(
                    text,
                    metadata,
                    chapters,
                    user_language=user_language,
                    provider_override=provider_override,
                )
                result = await self._organize_mindmap(result, user_language, provider_override)
                return normalize_chinese_script(
                    result,
                    user_language,
                    source_text=text,
                )
        # Rough threshold: ~15k chars ≈ 30min of Chinese transcript
        if len(text) > 15000:
            # No source chapters: split by segment count.
            result = await self._mindmap_map_reduce_auto(
                text,
                metadata or {},
                user_language=user_language,
                provider_override=provider_override,
            )
        else:
            # Short clips need the same source-derived timestamps as long ones.
            # A compact list of grounded points is enough until there are enough
            # distinct points for the global organizer to add topic parents.
            from app.services.analysis.grounded_outline import (
                GROUNDED_OUTLINE_SCHEMA, SYSTEM_PROMPT, grounded_chapter_outline,
            )
            from app.services.analysis.long_text import prompt_budget

            async def call(prompt, *, system_prompt):
                return await self._call(
                    prompt, system_prompt=system_prompt,
                    provider_override=provider_override, stage="mindmap",
                    response_schema=GROUNDED_OUTLINE_SCHEMA,
                )

            result = await grounded_chapter_outline(
                (metadata or {}).get("title") or "内容要点", text, call,
                budget=max(1024, prompt_budget(provider_override, "mindmap", SYSTEM_PROMPT) - 512),
            )
        result = await self._organize_mindmap(result, user_language, provider_override)
        return normalize_chinese_script(
            result,
            user_language,
            source_text=text,
        )

    async def _organize_mindmap(self, markdown: str, language: str | None, provider: str) -> str:
        from app.services.analysis.long_text import prompt_budget
        from app.services.analysis.outline_organization import GROUPING_SCHEMA, SYSTEM_PROMPT, organize_outline

        async def call(prompt: str, *, system_prompt: str) -> str:
            return await self._call(prompt, provider_override=provider, stage="mindmap", system_prompt=system_prompt, response_schema=GROUPING_SCHEMA)

        result, report = await organize_outline(
            markdown, call, language=language,
            budget=max(512, prompt_budget(provider, "mindmap", SYSTEM_PROMPT) - 512),
        )
        log_event(logger, logging.INFO, "llm.mindmap.organization", **report)
        return result

    async def _mindmap_map_reduce(
        self,
        text: str,
        metadata: dict[str, Any],
        chapters: list[dict],
        user_language: str | None = None,
        provider_override: str = "",
    ) -> str:
        """Map-reduce mindmap using chapter markers to split transcript."""
        segments = self._parse_srt(text)

        # Build chapter text blocks
        if segments:
            chapter_texts = self._split_segments_by_chapters(segments, chapters)
        else:
            # Plain text — split by rough char position proportional to chapter times
            chapter_texts = self._split_plain_by_chapters(text, chapters)


        # --- Map phase: parallel ---
        log_event(
            logger, logging.INFO, "llm.mindmap.map_reduce_started", chapters=len(chapter_texts)
        )
        map_concurrency = 1 if self._effective_provider(provider_override) == "local" else 8
        semaphore = asyncio.Semaphore(map_concurrency)

        from app.services.analysis.mindmap_outputs import _chapter_key

        async def map_one(index: int, title: str, content: str) -> tuple[tuple[int, float], str]:
            async with semaphore:
                from app.services.analysis.grounded_outline import (
                    GROUNDED_OUTLINE_SCHEMA, SYSTEM_PROMPT, grounded_chapter_outline,
                )
                from app.services.analysis.long_text import prompt_budget

                async def call(prompt, *, system_prompt):
                    return await self._call(
                        prompt, system_prompt=system_prompt,
                        provider_override=provider_override, stage="mindmap",
                        response_schema=GROUNDED_OUTLINE_SCHEMA,
                    )

                resp = await grounded_chapter_outline(
                    title, content, call,
                    budget=max(1024, prompt_budget(provider_override, "mindmap", SYSTEM_PROMPT) - 512),
                )
                return _chapter_key(chapters[index], index), resp

        map_results = await asyncio.gather(
            *[
                map_one(index, str(chapter.get("title") or ""), content)
                for index, chapter in enumerate(chapters)
                for content in [chapter_texts.get(_chapter_key(chapter, index), "")]
                if content.strip()
            ]
        )
        chapter_summaries = dict(map_results)
        log_event(
            logger,
            logging.INFO,
            "llm.mindmap.map_completed",
            chapters=len(chapter_summaries),
            chars=sum(len(v) for v in chapter_summaries.values()),
        )

        return self._compose_chapter_mindmap(chapters, chapter_summaries)

    def _compose_chapter_mindmap(
        self,
        chapters: list[dict],
        chapter_summaries: dict[tuple[int, float], str],
    ) -> str:
        return _mindmap_outputs._compose_chapter_mindmap(chapters, chapter_summaries)

    async def _mindmap_map_reduce_auto(
        self,
        text: str,
        metadata: dict[str, Any],
        user_language: str | None = None,
        provider_override: str = "",
    ) -> str:
        """Map-reduce for long text without chapter markers — auto-split."""
        segments = self._parse_srt(text)

        if segments:
            # Split into groups of ~120 segments
            chunk_size = min(120, max(80, len(segments) // 8))
            chapter_texts = {}
            stride = chunk_size - 4
            for i in range(0, len(segments), stride):
                batch = segments[i : i + chunk_size]
                label = (
                    f"Part {i // stride + 1} ({batch[0]['timestamp'].split('-->')[0].strip()})"
                )
                chapter_texts[label] = self._segments_to_srt(batch)
                if i + chunk_size >= len(segments):
                    break
        else:
            from app.services.analysis.long_text import split_overlapping
            chapter_texts = {
                f"Part {i + 1}": part
                for i, part in enumerate(split_overlapping(text, 10000, 384))
            }


        log_event(
            logger, logging.INFO, "llm.mindmap.auto_map_reduce_started", chunks=len(chapter_texts)
        )
        map_concurrency = 1 if self._effective_provider(provider_override) == "local" else 8
        semaphore = asyncio.Semaphore(map_concurrency)

        async def map_one(title: str, content: str) -> tuple[str, str]:
            async with semaphore:
                from app.services.analysis.grounded_outline import (
                    GROUNDED_OUTLINE_SCHEMA, SYSTEM_PROMPT, grounded_chapter_outline,
                )
                from app.services.analysis.long_text import prompt_budget

                async def call(prompt, *, system_prompt):
                    return await self._call(
                        prompt, system_prompt=system_prompt,
                        provider_override=provider_override, stage="mindmap",
                        response_schema=GROUNDED_OUTLINE_SCHEMA,
                    )

                resp = await grounded_chapter_outline(
                    title, content, call,
                    budget=max(1024, prompt_budget(provider_override, "mindmap", SYSTEM_PROMPT) - 512),
                )
                return title, resp

        map_results = await asyncio.gather(
            *[map_one(t, c) for t, c in chapter_texts.items() if c.strip()]
        )
        chapter_summaries = dict(map_results)

        # Keep source-grounded points intact; the global organizer adds parents.
        return "\n".join(chapter_summaries.values())

    async def _mindmap_reduce(
        self,
        chapter_summaries: dict[str, str],
        user_language: str | None = None,
        provider_override: str = "",
    ) -> str:
        """Reduce chapter summaries into final mindmap, batching to fit output limits."""
        from app.services.analysis.long_text import compact_metadata
        names = list(chapter_summaries.keys())

        # Group chapters into batches of 3-4 to keep each reduce output under 8k tokens
        batch_size = max(2, min(4, len(names) // 4 + 1))
        groups: list[tuple[str, list[str]]] = []
        for i in range(0, len(names), batch_size):
            batch_names = names[i : i + batch_size]
            label = f"{batch_names[0]} ~ {batch_names[-1]}"
            groups.append((label, batch_names))

        log_event(
            logger,
            logging.INFO,
            "llm.mindmap.reduce_started",
            groups=len(groups),
            chapters=len(names),
        )

        # Reduce each group (can be parallel for small groups)
        reduce_concurrency = 1 if self._effective_provider(provider_override) == "local" else 4
        semaphore = asyncio.Semaphore(reduce_concurrency)

        async def reduce_one(label: str, batch_names: list[str]) -> str:
            async with semaphore:
                summaries = ""
                for name in batch_names:
                    summaries += f"\n### {name}\n{chapter_summaries[name]}\n"
                resp = await self._generate_long_text(
                    summaries,
                    lambda part: get_mindmap_reduce_prompt(
                        compact_metadata(label, 512), part, user_language=user_language,
                    ),
                    provider_override=provider_override,
                    stage="mindmap",
                    system_prompt=MINDMAP_SYSTEM_PROMPT,
                )
                return self._filter_mindmap_lines(resp)

        results = await asyncio.gather(
            *[reduce_one(label, batch_names) for label, batch_names in groups]
        )

        final = "\n".join(results)
        log_event(logger, logging.INFO, "llm.mindmap.reduce_completed", chars=len(final))
        return final

    def _split_segments_by_chapters(
        self,
        segments: list[dict],
        chapters: list[dict],
    ) -> dict[str, str]:
        return _mindmap_outputs._split_segments_by_chapters(segments, chapters)

    def _split_plain_by_chapters(
        self,
        text: str,
        chapters: list[dict],
    ) -> dict[str, str]:
        return _mindmap_outputs._split_plain_by_chapters(text, chapters)

    def _build_global_context(
        self,
        metadata: dict[str, Any],
        chapters: list[dict],
    ) -> str:
        return _mindmap_outputs._build_global_context(metadata, chapters)

    @staticmethod
    def _filter_mindmap_lines(resp: str) -> str:
        return _mindmap_outputs._filter_mindmap_lines(resp)


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
    provider_override: str = "",
) -> dict[str, Any]:
    """Analyze content to extract metadata (Phase 1)."""
    return await get_llm_service().analyze_content(
        text,
        title,
        metadata,
        provider_override=provider_override,
    )


async def polish_text(text: str, context: dict[str, Any] | None = None) -> str:
    """Polish text with optional context (Phase 2)."""
    return await get_llm_service().polish(text, context)


def merge_consecutive_speaker_segments(srt_content: str, **kwargs: Any) -> str:
    """Merge adjacent polished SRT cues from the same speaker into dialogue turns."""
    return get_llm_service().merge_consecutive_speaker_segments(srt_content, **kwargs)


def srt_to_markdown(srt_content: str, title: str = "") -> str:
    """Convert SRT to clean Markdown document."""
    return get_llm_service().srt_to_markdown(srt_content, title)


async def summarize_text(
    text: str,
    user_language: str | None = None,
    provider_override: str = "",
    source_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await get_llm_service().summarize(
        text,
        user_language=user_language,
        provider_override=provider_override,
        source_context=source_context,
    )


async def generate_detail(
    text: str,
    user_language: str | None = None,
    provider_override: str = "",
) -> str:
    return await get_llm_service().detail(
        text,
        user_language=user_language,
        provider_override=provider_override,
    )


async def generate_mindmap(
    text: str,
    metadata: dict[str, Any] | None = None,
    user_language: str | None = None,
    provider_override: str = "",
) -> str:
    return await get_llm_service().mindmap(
        text,
        metadata=metadata,
        user_language=user_language,
        provider_override=provider_override,
    )
