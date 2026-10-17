"""VLM service for image understanding via OpenAI-Compatible API."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.core.logging_setup import log_event
from app.core.model_router import EndpointBinding, resolve_vlm_binding

logger = logging.getLogger(__name__)

# Built-in prompt: binary-classify (text-heavy vs content) then extract/describe.
_SYSTEM_PROMPT = (
    "You are an image understanding assistant. Analyze the image and respond in exactly two parts:\n"
    "1. First line: 'KIND: text' if the image is primarily text (screenshot, slide, post, article, "
    "infographic with text, diagram with labels) or 'KIND: content' if it is primarily visual "
    "(photo, illustration, product shot, scenery, face).\n"
    "2. Remaining lines: If KIND is 'text', extract all readable text faithfully (OCR), preserving "
    "headings, paragraphs, labels, numbers, punctuation, mixed Chinese/English text, UI text, "
    "watermarks, screen text, handwritten text, and background signs. If KIND is 'content', write "
    "a detailed Chinese description that covers people, objects, scene, composition, colors, "
    "relationships, visible actions, and every readable word in the image, including small text "
    "on screens, whiteboards, posters, clothing, labels, logos, watermarks, and captions. Do not "
    "identify unknown people by face; transcribe only names or identities that are visibly written "
    "in the image. Mark uncertain text with '疑似'.\n"
    "Respond in the same language as the text in the image where practical, defaulting to Chinese."
)

_OCR_RETRY_PROMPT = (
    "这是一张以文字信息为主的图片。请直接提取所有可读文字，保持原文语言、段落顺序和编号。"
    "如果有标题、列表、括号、英文术语、代码、UI 文案、水印、背景屏幕、白板、海报、标识或人名，"
    "请完整保留。看不清但能推测的文字用“疑似”标注。只输出提取结果。"
)

_USER_IMAGE_PROMPT = (
    "请细致分析这张图片。先判断图片是文字为主还是视觉内容为主。文字为主时完整 OCR 所有可读文字；"
    "视觉内容为主时用中文详细描述画面，并额外列出所有可读文字，包括背景屏幕、白板、海报、门牌、"
    "衣物、标签、logo、水印、字幕、界面元素中的文字。人物身份只依据画面里可见文字描述，"
    "不要根据人脸猜测。"
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


def _prepare_image_bytes(image_path: Path, *, max_edge: int = 1800) -> tuple[bytes, str, dict[str, Any]]:
    raw = image_path.read_bytes()
    original_media_type = _detect_media_type(raw, image_path.suffix.lower().lstrip("."))
    meta: dict[str, Any] = {
        "source_bytes": len(raw),
        "payload_bytes": len(raw),
        "media_type": original_media_type,
    }
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as image:
            meta["source_size"] = list(image.size)
            image = image.convert("RGB")
            if max(image.size) > max_edge:
                image.thumbnail((max_edge, max_edge))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=88, optimize=True)
            prepared = output.getvalue()
            if prepared and (len(prepared) < len(raw) or max(meta["source_size"]) > max_edge):
                meta["payload_bytes"] = len(prepared)
                meta["payload_size"] = list(image.size)
                meta["media_type"] = "image/jpeg"
                return prepared, "image/jpeg", meta
    except Exception as exc:
        meta["prepare_error"] = str(exc)
    return raw, original_media_type, meta


def _encode_image(image_path: Path) -> tuple[str, str, dict[str, Any]]:
    """Return (base64_data, media_type, payload_meta) for an image file."""
    suffix = image_path.suffix.lower().lstrip(".")
    raw, media_type, meta = _prepare_image_bytes(image_path)
    media_type = media_type or _detect_media_type(raw, suffix)
    data = base64.b64encode(raw).decode("ascii")
    return data, media_type, meta


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
            self._client = make_openai_client(binding.api_base, binding.api_key, max_retries=0)
            self._model = binding.model
            self._api_base = binding.api_base
            self._api_key = binding.api_key
        return self._client, self._model

    def describe_participants(
        self,
        frames: list[dict[str, Any]],
        context: dict[str, Any],
        binding: EndpointBinding | None = None,
    ) -> dict[str, Any]:
        """Read all selected frames together. Return evidence, never voice identity."""
        from app.core.settings import get_runtime_settings
        from app.services.analysis.participant_evidence import (
            PARTICIPANT_PROMPT, validate_participant_report,
        )

        if not 1 <= len(frames) <= 6:
            raise ValueError("参与者分析需要 1–6 张图片")
        identifiers = [frame["id"] for frame in frames]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("图片编号必须唯一")
        payload = json.dumps(context, ensure_ascii=False)
        # Reserve space for every image and the structured response. Callers
        # must compact cited evidence; truncating it here would break references.
        if len(payload) > 12000:
            raise ValueError("参与者证据超出多图预算，请压缩带引用的证据后重试")
        binding = binding or resolve_vlm_binding(get_runtime_settings())
        import math
        context_tokens = int(binding.request_kwargs.get("ctx") or 16384)
        # UTF-8 bytes are a conservative text-token bound. Reserve visual tokens
        # for every frame, and reduce all images together if necessary.
        text_reserve = len((PARTICIPANT_PROMPT + payload).encode("utf-8")) + 512
        image_edge = next((edge for edge in (896, 672, 448)
                           if text_reserve + len(frames) * math.ceil(edge / 28) ** 2 + 2048 <= context_tokens), None)
        if image_edge is None:
            raise ValueError("全部图片、带引用的全文证据及输出超出上下文预算；未丢弃任何图片或字幕证据")
        if binding.request_kwargs.get("local_engine") == "llama_cpp":
            from app.services.analysis.local_llm_runtime import get_local_llm_runtime
            base_url = get_local_llm_runtime().ensure(binding.request_kwargs)
            binding = replace(binding, api_base=f"{base_url}/v1", api_key="local")
        content: list[dict[str, Any]] = [{"type": "text", "text": payload}]
        image_meta = []
        for frame in frames:
            raw, media_type, meta = _prepare_image_bytes(Path(frame["path"]), max_edge=image_edge)
            image_meta.append({"id": frame["id"], "time": frame["time"], **meta})
            content.extend([
                {"type": "text", "text": f"图片 {frame['id']}，时间 {frame['time']} 秒"},
                {"type": "image_url", "image_url": {
                    "url": f"data:{media_type};base64,{base64.b64encode(raw).decode('ascii')}"
                }},
            ])
        timeout = int(binding.request_kwargs.get("timeout_sec") or 180)
        started = time.monotonic()
        if binding.request_kwargs.get("transport") == "oauth_cli":
            from app.services.analysis.coding_plan_cli import call_coding_plan_cli
            raw_text = asyncio.run(call_coding_plan_cli(
                str(binding.request_kwargs.get("provider_type") or ""),
                model=binding.model,
                prompt=PARTICIPANT_PROMPT + "\n" + payload + "\n" + json.dumps([
                    {"id": f["id"], "time": f["time"]} for f in frames
                ]),
                cli_path=str(binding.request_kwargs.get("cli_path") or ""),
                timeout_sec=timeout,
                image_paths=[Path(f["path"]) for f in frames],
            ))
        else:
            client, model = self._get_client(binding)
            request_options = {}
            if binding.request_kwargs.get("local_engine") == "llama_cpp":
                request_options = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
                                   "response_format": {"type": "json_object"}, "temperature": 0.1}
            response = client.chat.completions.create(
                model=model, max_tokens=2048, timeout=timeout,
                messages=[{"role": "system", "content": PARTICIPANT_PROMPT},
                          {"role": "user", "content": content}],
                **request_options,
            )
            if response.choices[0].finish_reason == "length":
                raise ValueError("参与者联合分析输出被截断")
            raw_text = response.choices[0].message.content or ""
        report = validate_participant_report(raw_text, frames, context)
        return {**report, "model": binding.model, "raw_output": raw_text,
                "budget": {"context_tokens": context_tokens, "text_reserve": text_reserve, "image_edge": image_edge, "output_tokens": 2048},
                "images": image_meta, "duration_ms": int((time.monotonic() - started) * 1000)}

    def describe_image(
        self,
        image_path: Path,
        binding: EndpointBinding | None = None,
    ) -> dict[str, Any]:
        """Classify and describe/OCR a single image. Returns {kind, text}."""
        from app.core.settings import get_runtime_settings
        rt = get_runtime_settings()
        binding = binding or resolve_vlm_binding(rt)
        if binding.request_kwargs.get("local_engine") == "llama_cpp":
            from app.services.analysis.local_llm_runtime import get_local_llm_runtime

            base_url = get_local_llm_runtime().ensure(binding.request_kwargs)
            binding = replace(binding, api_base=f"{base_url}/v1", api_key="local")

        b64, media_type, payload_meta = _encode_image(image_path)
        timeout_sec = int(binding.request_kwargs.get("timeout_sec") or 90)
        started = time.monotonic()
        max_tokens = max(2048, int(binding.request_kwargs.get("max_tokens") or rt.vlm_max_tokens))
        if binding.request_kwargs.get("transport") == "oauth_cli":
            from app.services.analysis.coding_plan_cli import call_coding_plan_cli

            provider_type = str(binding.request_kwargs.get("provider_type") or "")
            raw = asyncio.run(
                call_coding_plan_cli(
                    provider_type,
                    model=binding.model,
                    prompt=f"{_SYSTEM_PROMPT}\n\n{_USER_IMAGE_PROMPT}",
                    cli_path=str(binding.request_kwargs.get("cli_path") or ""),
                    timeout_sec=timeout_sec,
                    reasoning_effort=str(
                        binding.request_kwargs.get("reasoning_effort") or ""
                    ),
                    image_paths=[image_path],
                )
            )
            result = _parse_response(raw)
            if not result["text"].strip():
                retry_text = asyncio.run(
                    call_coding_plan_cli(
                        provider_type,
                        model=binding.model,
                        prompt=_OCR_RETRY_PROMPT,
                        cli_path=str(binding.request_kwargs.get("cli_path") or ""),
                        timeout_sec=timeout_sec,
                        reasoning_effort=str(
                            binding.request_kwargs.get("reasoning_effort") or ""
                        ),
                        image_paths=[image_path],
                    )
                ).strip()
                if retry_text:
                    result = {"kind": "text", "text": retry_text}
            duration_ms = int((time.monotonic() - started) * 1000)
            result["payload_meta"] = payload_meta
            result["duration_ms"] = duration_ms
            log_event(
                logger,
                logging.INFO,
                "vlm.image.completed",
                file=image_path.name,
                kind=result["kind"],
                chars=len(result["text"]),
                payload_bytes=payload_meta.get("payload_bytes"),
                source_bytes=payload_meta.get("source_bytes"),
                source_size=payload_meta.get("source_size"),
                payload_size=payload_meta.get("payload_size"),
                duration_ms=duration_ms,
                transport="oauth_cli",
                provider=provider_type,
            )
            return result

        client, model = self._get_client(binding)
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
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
                max_tokens=max_tokens,
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
        duration_ms = int((time.monotonic() - started) * 1000)
        result["payload_meta"] = payload_meta
        result["duration_ms"] = duration_ms
        log_event(
            logger,
            logging.INFO,
            "vlm.image.completed",
            file=image_path.name,
            kind=result["kind"],
            chars=len(result["text"]),
            payload_bytes=payload_meta.get("payload_bytes"),
            source_bytes=payload_meta.get("source_bytes"),
            source_size=payload_meta.get("source_size"),
            payload_size=payload_meta.get("payload_size"),
            duration_ms=duration_ms,
        )
        return result


_vlm_service: VLMService | None = None


def get_vlm_service() -> VLMService:
    global _vlm_service
    if _vlm_service is None:
        _vlm_service = VLMService()
    return _vlm_service
