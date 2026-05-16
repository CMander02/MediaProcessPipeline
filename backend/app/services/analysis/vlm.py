"""VLM service for image understanding via OpenAI-Compatible API."""

from __future__ import annotations

import base64
import logging
import re
from pathlib import Path
from typing import Any

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


def _encode_image(image_path: Path) -> tuple[str, str]:
    """Return (base64_data, media_type) for an image file."""
    suffix = image_path.suffix.lower().lstrip(".")
    media_type_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                      "webp": "image/webp", "gif": "image/gif"}
    media_type = media_type_map.get(suffix, "image/jpeg")
    data = base64.b64encode(image_path.read_bytes()).decode("ascii")
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

    def _get_client(self) -> tuple[Any, str]:
        from app.core.settings import get_runtime_settings
        rt = get_runtime_settings()
        if not rt.vlm_api_base:
            raise RuntimeError("vlm_api_base is not configured — set it in Settings > 视觉模型")
        if self._client is None or self._model != rt.vlm_model:
            from app.services.analysis._openai_client import make_openai_client
            self._client = make_openai_client(rt.vlm_api_base, rt.vlm_api_key)
            self._model = rt.vlm_model
        return self._client, self._model

    def describe_image(self, image_path: Path) -> dict[str, str]:
        """Classify and describe/OCR a single image. Returns {kind, text}."""
        from app.core.settings import get_runtime_settings
        rt = get_runtime_settings()
        client, model = self._get_client()

        b64, media_type = _encode_image(image_path)
        response = client.chat.completions.create(
            model=model,
            max_tokens=rt.vlm_max_tokens,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
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
        logger.debug(f"VLM {image_path.name}: kind={result['kind']}, chars={len(result['text'])}")
        return result


_vlm_service: VLMService | None = None


def get_vlm_service() -> VLMService:
    global _vlm_service
    if _vlm_service is None:
        _vlm_service = VLMService()
    return _vlm_service
