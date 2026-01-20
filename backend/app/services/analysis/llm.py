"""LLM service for text analysis using LiteLLM."""

import json
import logging
from typing import Any

from app.core.config import get_settings
from app.api.routes.settings import get_runtime_settings

logger = logging.getLogger(__name__)

POLISH_PROMPT = """请整理以下转录文本：修正错别字，添加适当的标点符号，移除口语化的填充词（如"呃"、"那个"等），
但保持原意和说话者的风格。输出完整文本，不要总结。

{text}"""

SUMMARIZE_PROMPT = """分析以下转录文本，返回 JSON 格式：
{{"tldr": "一句话总结", "key_facts": ["关键要点1", "关键要点2", ...], "action_items": ["待办事项..."], "topics": ["主题1", "主题2", ...]}}

{text}"""

MINDMAP_PROMPT = """将以下文本转换为 markmap 格式的思维导图（使用 2 空格缩进）：
- 主题
  - 子主题
    - 细节

{text}"""


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

        config["max_tokens"] = self._static_settings.max_tokens
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
                max_tokens=config.get("max_tokens", 4096),
                temperature=config.get("temperature", 0.7),
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"LLM error: {e}")
            raise

    async def polish(self, text: str) -> str:
        return await self._call(POLISH_PROMPT.format(text=text))

    async def summarize(self, text: str) -> dict[str, Any]:
        resp = await self._call(SUMMARIZE_PROMPT.format(text=text))
        try:
            start, end = resp.find("{"), resp.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(resp[start:end])
        except json.JSONDecodeError:
            pass
        return {"tldr": resp, "key_facts": [], "action_items": [], "topics": []}

    async def mindmap(self, text: str) -> str:
        resp = await self._call(MINDMAP_PROMPT.format(text=text))
        lines = [l for l in resp.strip().split("\n") if l.strip().startswith("-")]
        return "\n".join(lines) if lines else resp


_service: LLMService | None = None


def get_llm_service() -> LLMService:
    global _service
    if _service is None:
        _service = LLMService()
    return _service


async def polish_text(text: str) -> str:
    return await get_llm_service().polish(text)


async def summarize_text(text: str) -> dict[str, Any]:
    return await get_llm_service().summarize(text)


async def generate_mindmap(text: str) -> str:
    return await get_llm_service().mindmap(text)
