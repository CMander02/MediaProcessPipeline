"""LLM service for text analysis using LiteLLM with two-phase polishing."""

import json
import logging
import re
from typing import Any

from app.core.config import get_settings
from app.api.routes.settings import get_runtime_settings

logger = logging.getLogger(__name__)

# Phase 1: Content analysis prompt
ANALYZE_PROMPT = """请分析以下转录文本，提取关键信息。文本标题: {title}

转录内容:
{text}

请返回 JSON 格式:
{{
    "language": "检测到的主要语言代码，如 zh-CN, en-US, ja-JP",
    "content_type": "内容类型，如 技术讲座/访谈/播客/会议/教程/演讲",
    "main_topics": ["主要话题1", "主要话题2"],
    "keywords": ["关键词1", "关键词2", "关键词3"],
    "proper_nouns": ["专有名词1", "专有名词2"],
    "speakers_detected": 1,
    "tone": "语气风格，如 正式/非正式/教学/对话"
}}

只返回 JSON，不要其他内容。"""

# Phase 2: Polish prompt with context
POLISH_PROMPT = """你是专业的字幕校对编辑。请根据以下上下文信息润色字幕片段。

## 内容分析
- 语言: {language}
- 内容类型: {content_type}
- 主要话题: {main_topics}
- 关键词: {keywords}
- 专有名词（请保持一致拼写）: {proper_nouns}

## 润色要求
1. 修正语音识别错误和错别字
2. 添加适当的标点符号
3. 移除口语填充词（如"呃"、"那个"、"就是说"等）
4. 保持原意和说话者风格
5. **重要**: 保持 [SPEAKER_XX] 标记不变
6. 保持 SRT 时间戳格式不变

## 待润色的字幕片段
{text}

请输出润色后的完整 SRT 片段，保持格式不变:"""

# Simple polish prompt (fallback when no context)
SIMPLE_POLISH_PROMPT = """请整理以下转录文本：修正错别字，添加适当的标点符号，移除口语化的填充词（如"呃"、"那个"等），
但保持原意和说话者的风格。如果有 [SPEAKER_XX] 标记，请保持不变。输出完整文本，不要总结。

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

    async def analyze_content(self, text: str, title: str) -> dict[str, Any]:
        """
        Phase 1: Analyze content to extract metadata.
        This provides context for the polishing phase.
        """
        # Truncate text if too long (take first 8000 chars for analysis)
        truncated = text[:8000] if len(text) > 8000 else text

        prompt = ANALYZE_PROMPT.format(title=title, text=truncated)
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

    async def polish_with_context(self, srt_content: str, context: dict[str, Any]) -> str:
        """
        Phase 2: Polish transcript using sliding window with context.
        Preserves [SPEAKER_XX] markers and SRT format.
        """
        segments = self._parse_srt(srt_content)
        if not segments:
            # Fallback to simple polish if not valid SRT
            return await self._call(SIMPLE_POLISH_PROMPT.format(text=srt_content))

        # Sliding window parameters
        # Modern LLMs have large context windows (64K+), so we can process more at once
        window_size = 200  # segments per window
        overlap = 20  # overlap between windows

        logger.info(f"Polishing {len(segments)} segments with window_size={window_size}, overlap={overlap}")

        polished_segments = []
        i = 0
        window_num = 0

        while i < len(segments):
            window_num += 1
            # Get window of segments
            window_end = min(i + window_size, len(segments))
            window = segments[i:window_end]

            logger.info(f"Processing window {window_num}: segments {i+1}-{window_end} of {len(segments)}")

            # Convert window to SRT text
            window_srt = self._segments_to_srt(window)

            # Build context string
            prompt = POLISH_PROMPT.format(
                language=context.get("language", "unknown"),
                content_type=context.get("content_type", "unknown"),
                main_topics=", ".join(context.get("main_topics", [])),
                keywords=", ".join(context.get("keywords", [])),
                proper_nouns=", ".join(context.get("proper_nouns", [])),
                text=window_srt
            )

            # Call LLM
            polished_window = await self._call(prompt)

            # Parse polished result
            polished_segs = self._parse_srt(polished_window)

            if polished_segs:
                logger.info(f"Window {window_num}: input {len(window)} segments, output {len(polished_segs)} segments")
                # If this is first window, take all
                if i == 0:
                    polished_segments.extend(polished_segs)
                else:
                    # Skip overlapping segments from previous window
                    polished_segments.extend(polished_segs[overlap:] if len(polished_segs) > overlap else polished_segs)
            else:
                logger.warning(f"Window {window_num}: LLM returned invalid SRT, keeping original segments")
                # Keep original segments if LLM fails to return valid SRT
                if i == 0:
                    polished_segments.extend(window)
                else:
                    polished_segments.extend(window[overlap:] if len(window) > overlap else window)

            # Move window, accounting for overlap
            i += window_size - overlap

        # Re-index segments
        for idx, seg in enumerate(polished_segments, 1):
            seg['index'] = idx

        logger.info(f"Polish complete: {len(segments)} -> {len(polished_segments)} segments")
        return self._segments_to_srt(polished_segments)

    async def polish(self, text: str, context: dict[str, Any] | None = None) -> str:
        """Polish text, optionally with context from analysis phase."""
        if context:
            return await self.polish_with_context(text, context)
        return await self._call(SIMPLE_POLISH_PROMPT.format(text=text))

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


async def analyze_content(text: str, title: str) -> dict[str, Any]:
    """Analyze content to extract metadata (Phase 1)."""
    return await get_llm_service().analyze_content(text, title)


async def polish_text(text: str, context: dict[str, Any] | None = None) -> str:
    """Polish text with optional context (Phase 2)."""
    return await get_llm_service().polish(text, context)


def srt_to_markdown(srt_content: str, title: str = "") -> str:
    """Convert SRT to clean Markdown document."""
    return get_llm_service().srt_to_markdown(srt_content, title)


async def summarize_text(text: str) -> dict[str, Any]:
    return await get_llm_service().summarize(text)


async def generate_mindmap(text: str) -> str:
    return await get_llm_service().mindmap(text)
