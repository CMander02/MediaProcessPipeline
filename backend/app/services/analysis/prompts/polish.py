"""Transcript polishing prompts."""

from typing import Any


def get_polish_prompt(
    text: str,
    language: str = "unknown",
    content_type: str = "unknown",
    main_topics: list[str] | None = None,
    keywords: list[str] | None = None,
    proper_nouns: list[str] | None = None,
) -> str:
    """
    Generate the polish prompt with context from analysis phase.

    Args:
        text: SRT content to polish
        language: Detected language
        content_type: Content type (e.g., 技术讲座, 访谈)
        main_topics: List of main topics
        keywords: List of keywords
        proper_nouns: List of proper nouns to keep consistent

    Returns:
        Formatted prompt string
    """
    topics_str = ", ".join(main_topics) if main_topics else "未知"
    keywords_str = ", ".join(keywords) if keywords else "未知"
    nouns_str = ", ".join(proper_nouns) if proper_nouns else "未知"

    return f"""你是专业的字幕校对编辑。请根据上下文信息润色下面的字幕片段。

## 内容分析
- 语言: {language}
- 内容类型: {content_type}
- 主要话题: {topics_str}
- 关键词: {keywords_str}
- 专有名词（请保持一致拼写）: {nouns_str}

## 润色要求
1. 修正语音识别错误和错别字
2. 添加适当的标点符号
3. 移除口语填充词（如"呃"、"那个"、"就是说"、"然后"等）
4. 保持原意和说话者风格
5. **必须**保留每条字幕的 index、timestamp、[SPEAKER_XX] 标记
6. **不要**合并或拆分字幕条目；输入有 N 条，输出就必须有 N 条
7. **不要**改写 timestamp，必须原样保留

## 输出格式（严格遵守）
直接输出 JSON 数组，**不要**任何前后解释/markdown 代码块/废话引导句。
每个元素是一个对象：{{"index": <整数>, "timestamp": "<原时间戳>", "text": "<润色后的文本，包含 [SPEAKER_XX] 前缀>"}}

示例输出（仅示例，请勿照抄）：
[{{"index": 1, "timestamp": "00:00:00,800 --> 00:00:06,719", "text": "[SPEAKER_04] 大家好。"}},
 {{"index": 2, "timestamp": "00:00:07,440 --> 00:00:17,519", "text": "[SPEAKER_04] 今天我们聊一聊。"}}]

## 待润色的字幕片段
{text}

请直接输出 JSON 数组（以 [ 开始，以 ] 结束），不要任何其它内容："""


def get_simple_polish_prompt(text: str) -> str:
    """
    Generate a simple polish prompt without context.

    Used as fallback when no analysis context is available.

    Args:
        text: Text to polish

    Returns:
        Formatted prompt string
    """
    return f"""请整理以下转录文本：

要求:
1. 修正错别字和语音识别错误
2. 添加适当的标点符号
3. 移除口语化的填充词（如"呃"、"那个"、"就是说"等）
4. 保持原意和说话者的风格
5. 如果有 [SPEAKER_XX] 标记，请保持不变
6. 输出完整文本，不要总结

待处理文本:
{text}"""
