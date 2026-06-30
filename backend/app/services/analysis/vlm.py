"""VLM service for image understanding via OpenAI-Compatible API."""

from __future__ import annotations

import base64
import logging
import re
from pathlib import Path
from typing import Any

from app.core.model_router import EndpointBinding, resolve_vlm_binding
from app.core.logging_setup import log_event

logger = logging.getLogger(__name__)

# Built-in prompt: binary-classify (text-heavy vs content) then extract/describe.
_SYSTEM_PROMPT = (
    "You are an image understanding assistant. Analyze the image and respond in exactly two parts:\n"
    "1. First line: 'KIND: text' if the image is primarily text (screenshot, slide, post, article, "
    "infographic with text, diagram with labels) or 'KIND: content' if it is primarily visual "
    "(photo, illustration, product shot, scenery, face).\n"
    "2. Remaining lines: If KIND is 'text', extract all readable text from the image faithfully "
    "(OCR). If KIND is 'content', write 1-3 sentences in Chinese describing the main subject and "
    "key visual details.\n"
    "Respond in the same language as the text in the image (default Chinese)."
)

_OCR_RETRY_PROMPT = (
    "这是一张以文字信息为主的图片。请直接提取所有可读文字，保持原文语言、段落顺序和编号。"
    "如果有标题、列表、括号、英文术语或代码，请完整保留。只输出提取结果。"
)

_USER_IMAGE_PROMPT = (
    "请分析这张图片。若图片主要包含文字，请完整 OCR；若主要是照片或插画，请用中文描述主要内容。"
)


def _detect_media_type(data: bytes, suffix: str) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    media_type_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                      "webp": "image/webp", "gif": "image/gif"}
    return media_type_map.get(suffix, "image/jpeg")


def _encode_image(image_path: Path) -> tuple[str, str]:
    """Return (base64_data, media_type) for an image file."""
    suffix = image_path.suffix.lower().lstrip(".")
    raw = image_path.read_bytes()
    media_type = _detect_media_type(raw, suffix)
    data = base64.b64encode(raw).decode("ascii")
    return data, media_type


def _parse_response(text: str) -> dict[str, str]:
    """Parse 'KIND: ...\n<body>' into {kind, text}."""
    lines = text.strip().splitlines()
    kind = "content"
    body_lines: list[str] = []
    for i, line in enumerate(lines):
        m = re.match(r"KIND:\s*(text|content)", line, re.IGNORECASE)
        if m:
            kind = m.group(1).lower()
            body_lines = lines[i + 1:]
            break
    else:
        body_lines = lines
    return {"kind": kind, "text": "\n".join(body_lines).strip()}


class VLMService:
    """Singleton VLM service. Describes or OCRs images via an OpenAI-Compatible API."""

    def __init__(self) -> None:
        self._client: Any = None
        self._model: str = ""
        self._api_base: str = ""
        self._api_key: str = ""

    def _get_client(self, binding: EndpointBinding | None = None) -> tuple[Any, str]:
        from app.core.settings import get_runtime_settings
        binding = binding or resolve_vlm_binding(get_runtime_settings())
        if not binding.api_base:
            raise RuntimeError("vlm_api_base is not configured — set it in Settings > 视觉模型")
        if (
            self._client is None
            or self._api_base != binding.api_base
            or self._api_key != binding.api_key
            or self._model != binding.model
        ):
            from app.services.analysis._openai_client import make_openai_client
            self._client = make_openai_client(binding.api_base, binding.api_key)
            self._model = binding.model
            self._api_base = binding.api_base
            self._api_key = binding.api_key
        return self._client, self._model

    def describe_image(
        self,
        image_path: Path,
        binding: EndpointBinding | None = None,
    ) -> dict[str, str]:
        """Classify and describe/OCR a single image. Returns {kind, text}."""
        from app.core.settings import get_runtime_settings
        rt = get_runtime_settings()
        binding = binding or resolve_vlm_binding(rt)
        client, model = self._get_client(binding)

        b64, media_type = _encode_image(image_path)
        timeout_sec = int(binding.request_kwargs.get("timeout_sec") or 90)
        response = client.chat.completions.create(
            model=model,
            max_tokens=int(binding.request_kwargs.get("max_tokens") or rt.vlm_max_tokens),
            timeout=timeout_sec,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _USER_IMAGE_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{b64}"},
                        }
                    ],
                },
            ],
        )
        raw = response.choices[0].message.content or ""
        result = _parse_response(raw)
        if not result["text"].strip():
            retry = client.chat.completions.create(
                model=model,
                max_tokens=max(2048, int(binding.request_kwargs.get("max_tokens") or rt.vlm_max_tokens)),
                timeout=timeout_sec,
                messages=[
                    {"role": "system", "content": _OCR_RETRY_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "请完整识别这张图片中的所有中文和英文文字。不要分类，不要描述画面，只输出 OCR 文本。",
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{media_type};base64,{b64}"},
                            }
                        ],
                    },
                ],
            )
            retry_text = (retry.choices[0].message.content or "").strip()
            if retry_text:
                result = {"kind": "text", "text": retry_text}
        log_event(
            logger,
            logging.INFO,
            "vlm.image.completed",
            file=image_path.name,
            kind=result["kind"],
            chars=len(result["text"]),
        )
        return result


_vlm_service: VLMService | None = None


def get_vlm_service() -> VLMService:
    global _vlm_service
    if _vlm_service is None:
        _vlm_service = VLMService()
    return _vlm_service
