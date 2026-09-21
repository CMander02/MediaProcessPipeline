"""Native text attachments for the built-in OpenAI and Anthropic providers."""

from __future__ import annotations

import base64
from typing import Any

_ATTACHMENT_PROMPT = (
    "完整读取附件 request.txt，执行其中的请求并处理全部内容。"
    "直接返回请求规定格式的最终文本，保持 JSON 或 Markdown 等输出格式。"
)


def text_attachment_provider(provider: str, api_base: str) -> str:
    # Custom gateways may only implement Chat Completions. Use attachments only
    # for the built-in providers at their documented official endpoints.
    official_bases = {
        "openai": {"", "https://api.openai.com", "https://api.openai.com/v1"},
        "anthropic": {"", "https://api.anthropic.com", "https://api.anthropic.com/v1"},
    }
    return provider if api_base.rstrip("/") in official_bases.get(provider, set()) else ""


def anthropic_text_attachment(prompt: str) -> list[dict[str, Any]]:
    # Inline documents are native attachments without a persistent Files upload.
    return [
        {
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": prompt},
            "title": "request.txt",
        },
        {"type": "text", "text": _ATTACHMENT_PROMPT},
    ]


async def call_openai_text_attachment(
    params: dict[str, Any],
    prompt: str,
    *,
    system_prompt: str = "",
    max_retries: int = 3,
) -> str:
    import httpx

    from app.services.analysis._openai_client import make_async_openai_client

    api_base = str(params.get("api_base") or "https://api.openai.com/v1").rstrip("/")
    if api_base == "https://api.openai.com":
        api_base += "/v1"
    client = make_async_openai_client(
        api_base,
        params["api_key"],
        max_retries=max_retries,
        timeout=httpx.Timeout(300.0, connect=30.0, read=300.0, write=30.0, pool=30.0),
    )
    request: dict[str, Any] = {
        "model": str(params["model"]).removeprefix("openai/"),
        "store": False,
        "input": [{
            "role": "user",
            "content": [
                {
                    "type": "input_file",
                    "filename": "request.txt",
                    "file_data": "data:text/plain;base64,"
                    + base64.b64encode(prompt.encode("utf-8")).decode("ascii"),
                },
                {"type": "input_text", "text": _ATTACHMENT_PROMPT},
            ],
        }],
    }
    if system_prompt:
        request["instructions"] = system_prompt
    async with client:
        response = await client.responses.create(**request)
    if response.status != "completed":
        reason = getattr(response.incomplete_details, "reason", None) or response.status
        raise RuntimeError(f"OpenAI 文本附件推理未完成：{reason}")
    content = response.output_text.strip()
    if not content:
        raise RuntimeError("OpenAI 文本附件推理返回空正文。")
    return content
