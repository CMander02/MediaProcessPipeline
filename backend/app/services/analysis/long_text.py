"""Bound long-text generation without dropping the tail of the source."""

from __future__ import annotations

import logging
import json
import re
from bisect import bisect_right
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


def model_limits(provider: str = "", stage: str = "polish") -> tuple[int, int]:
    from app.core.model_router import resolve_llm_binding
    from app.core.settings import get_runtime_settings

    params = resolve_llm_binding(
        get_runtime_settings(), provider_override=provider, stage=stage,
    ).request_kwargs
    return int(params.get("ctx") or 32768), int(params.get("max_new_tokens") or 4096)


def prompt_budget(provider: str = "", stage: str = "polish", system_prompt: str = "") -> int:
    context, output = model_limits(provider, stage)
    return context - output - byte_size(system_prompt) - 768


def is_length_error(error: Exception) -> bool:
    return is_context_overflow(error) or "输出被截断" in str(error)


def compact_metadata(value, budget: int):
    """Bound auxiliary prompt context; source transcripts are never passed here.

    Keep head and tail samples when descriptions or vocabulary lists are unusually
    large. The caller still processes every source cue through record_windows.
    """
    def size(item):
        return byte_size(json.dumps(item, ensure_ascii=False))

    def sample(item, chars, count):
        if isinstance(item, str) and len(item) > chars:
            return item[:chars // 2] + " … " + item[-chars // 2:]
        if isinstance(item, list):
            rows = item if len(item) <= count else item[:count // 2] + item[-count // 2:]
            return [sample(row, chars, count) for row in rows]
        if isinstance(item, dict):
            return {key: sample(row, chars, count) for key, row in item.items()}
        return item

    result = value
    chars, count = 2048, 64
    while size(result) > budget and (chars > 32 or count > 2):
        chars, count = max(32, chars // 2), max(2, count // 2)
        result = sample(value, chars, count)
    return result


async def record_windows(
    records, build_prompt, call, *, budget, chunk_size=24, overlap=4,
    keep_errors=False, validate=None, skip_indexes=None, window_state=None, max_calls=None,
):
    """Yield bounded replies with read-only overlap and stable source cue IDs.

    The prompt factory runs immediately before each request so a caller can update
    its shared speaker roster between windows. Oversized individual cues are split
    losslessly; consumers must reconcile results sharing a cue ID.
    """
    pending = list(records)
    skipped = set(skip_indexes or ())
    offset = 0
    limit = budget - (768 if validate else 0)
    calls = 0

    async def request(prompt):
        nonlocal calls
        if max_calls is not None and calls >= max_calls:
            raise RuntimeError(f"人物阶段已达到调用上限 {max_calls}，剩余字幕保留待修复")
        calls += 1
        return await call(prompt)

    while offset < len(pending):
        if pending[offset]["index"] in skipped:
            offset += 1
            continue
        count = min(chunk_size, len(pending) - offset)
        for position in range(1, count):
            if pending[offset + position]["index"] in skipped:
                count = position
                break
        radius = overlap
        while True:
            targets = pending[offset:offset + count]
            visible = pending[max(0, offset - radius):offset + count + radius]
            # Fragments sharing an ID are processed separately, so evidence is unambiguous.
            if len({row["index"] for row in targets}) != len(targets):
                count = 1
                radius = 0
                continue
            if len({row["index"] for row in visible}) != len(visible):
                radius = 0
                visible = targets
            prompt = build_prompt(targets, visible)
            if byte_size(prompt) <= limit:
                try:
                    response = await request(prompt)
                    if validate is not None:
                        try:
                            response = validate(response, targets, visible)
                        except ValueError as exc:
                            repair = prompt + (
                                "\n上次输出校验失败。Return complete valid JSON using only the visible cue IDs. "
                                "Omit unsupported changes. Previous validation error: " + str(exc)[:120]
                            )
                            response = validate(await request(repair), targets, visible)
                    break
                except Exception as exc:
                    if not is_length_error(exc):
                        if not keep_errors:
                            raise
                        response = exc
                        break
                    if not is_context_overflow(exc):
                        # Output exhaustion does not reduce the model's input capacity.
                        # Shrink editable work first, retaining room for fixed instructions.
                        if count > 1:
                            count = max(1, count // 2)
                            continue
                        if radius:
                            radius = 0
                            continue
                        # A single output still exhausting its budget is a failed
                        # decision, not evidence that the source needs splitting.
                        if not keep_errors:
                            raise
                        response = exc
                        break
                    else:
                        limit = min(limit - 1, max(1, byte_size(prompt) // 2))
                    logger.warning("Structured request exceeded model capacity; budget=%d", limit)
            if count > 1:
                count = max(1, count // 2)
            elif radius:
                radius = 0
            else:
                row = targets[0]
                empty = {**row, "text": ""}
                available = limit - byte_size(build_prompt([empty], [empty])) - 32
                if available < 32 or len(row.get("text", "")) < 2:
                    response = ValueError("说话人/字幕提示词的固定信息超过上下文容量，请减少元数据或增加上下文。")
                    if not keep_errors:
                        raise response
                    break
                text = row["text"]
                prefix = ""
                if "timestamp" in row:
                    match = re.match(r"^(\[[^\]\r\n]+\][ \t]?)([\s\S]*)$", text)
                    if match:
                        prefix, text = match.groups()
                available -= byte_size(prefix)
                if available < 4 or len(text) < 2:
                    response = ValueError("单条字幕的固定信息超过上下文容量")
                    if not keep_errors:
                        raise response
                    break
                parts = [prefix + part for part in split_overlapping(
                    text, min(available, max(4, byte_size(text) // 2)), 0,
                )]
                pending[offset:offset + 1] = [{**row, "text": part} for part in parts]
        if window_state is not None:
            next_index = pending[offset + count]["index"] if offset + count < len(pending) else None
            window_state.update(
                calls=calls,
                completed_indexes=[row["index"] for row in targets if row["index"] != next_index],
            )
        yield targets, visible, response
        offset += count


def restore_boundary_space(original: str, edited: str) -> str:
    """Keep word separators removed by model/parser stripping at a fragment edge."""
    leading = original[:len(original) - len(original.lstrip())]
    trailing = original[len(original.rstrip()):]
    return leading + edited.strip() + trailing


def byte_size(text: str) -> int:
    # A conservative token upper estimate for byte-based model vocabularies.
    # This also works offline and avoids assuming an English chars/token ratio.
    return len(text.encode("utf-8"))


def split_overlapping(text: str, budget: int, overlap: int) -> list[str]:
    """Prefer paragraph/cue boundaries; split oversized paragraphs losslessly."""
    offsets = [0]
    for char in text:
        offsets.append(offsets[-1] + byte_size(char))
    chunks = []
    start = 0
    while start < len(text):
        end = bisect_right(offsets, offsets[start] + budget) - 1
        if end <= start:
            raise ValueError("Text budget cannot hold one Unicode character")
        if end < len(text):
            # SRT cues/paragraphs stay whole whenever they fit in a chunk.
            for separator in ("\n\n", "\n"):
                boundary = text.rfind(separator, start + (end - start) // 2, end)
                if boundary >= 0:
                    end = boundary + len(separator)
                    break
        chunks.append(text[start:end])
        if end == len(text):
            break
        next_start = bisect_right(offsets, offsets[end] - overlap) - 1
        boundary = text.find("\n\n", max(start + 1, next_start), end)
        if boundary >= 0 and boundary + 2 < end:
            next_start = boundary + 2
        start = max(start + 1, next_start)
    return chunks


def is_context_overflow(error: Exception) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in (
        "exceed_context_size_error", "context_length_exceeded",
        "exceeds the available context size", "maximum context length",
        "prompt is too long", "input is too long", "too many tokens",
    ))


async def generate_bounded(
    text: str,
    build_prompt: Callable[[str], str],
    call: Callable[..., Awaitable[str]],
    *,
    system_prompt: str,
    context_size: int,
    output_tokens: int,
    build_digest_prompt: Callable[[str], str] | None = None,
) -> str:
    """Map overlapping excerpts and recursively reduce all intermediate outputs.

    Prompt/metadata, chat framing and output all consume the same context.
    Unknown provider tokenizers are handled by shrinking only on context errors.
    """
    guidance = (
        "\nThe input may contain ordered summaries of overlapping source excerpts. "
        "Merge repeated facts once, retaining distinct facts, names, numbers, actions "
        "and supplied timestamps. Keep the requested output schema and language."
    )
    # Reserve the longest fixed prompt overhead, plus chat-template tokens. The
    # digest builder can have a different prefix/schema than the final builder.
    prompt_builders = [build_prompt]
    if build_digest_prompt is not None:
        prompt_builders.append(build_digest_prompt)
    fixed_prompt = max(byte_size(builder("")) for builder in prompt_builders)
    overhead = fixed_prompt + byte_size(system_prompt + guidance) + 768
    budget = context_size - output_tokens - overhead
    if budget < 256:
        raise RuntimeError(
            "摘要/导图的提示词和元数据已占满上下文，请增加模型上下文或减少输出长度配置。"
        )

    learned_limit = budget

    async def run(value: str, limit: int, depth: int, partial: bool = False) -> str:
        nonlocal learned_limit
        limit = min(limit, learned_limit)
        if depth > 12 or limit < 256:
            raise RuntimeError("长文本分层压缩无法收敛到模型上下文，请增加上下文容量。")
        instruction = system_prompt + guidance
        if partial:
            instruction += (
                f"\nThis is an intermediate excerpt digest. Keep the complete response "
                f"within {max(80, min(800, limit // 8))} characters. Prioritize concrete "
                "facts and timestamps over broad descriptions."
            )
        if byte_size(value) <= limit:
            try:
                builder = build_digest_prompt if partial and build_digest_prompt else build_prompt
                return await call(builder(value), system_prompt=instruction)
            except Exception as exc:
                if not is_length_error(exc):
                    raise
                # The provider's real context/tokenizer takes precedence over our estimate.
                limit = min(limit // 2, byte_size(value) // 2)
                learned_limit = min(learned_limit, limit)
                if limit < 256:
                    raise
                logger.warning("Context rejected long-text request; shrinking excerpt budget to %d", limit)

        pieces = split_overlapping(value, limit, min(384, limit // 10))
        logger.info("Long-text map/reduce: depth=%d chunks=%d byte_budget=%d", depth, len(pieces), limit)
        digests = [await run(piece, limit, depth + 1, partial=True) for piece in pieces]
        merged = "\n\n".join(digests)
        if byte_size(merged) >= byte_size(value):
            raise RuntimeError("模型生成的片段提要未缩短，无法完成长文本合并；请检查模型输出。")
        return await run(merged, limit, depth + 1, partial=partial)

    return await run(text, budget, 0)
