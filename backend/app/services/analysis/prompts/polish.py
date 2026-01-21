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

    return f"""你是专业的字幕校对编辑。请根据以下上下文信息润色字幕片段。

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
5. **重要**: 保持 [SPEAKER_XX] 标记不变
6. 保持 SRT 时间戳格式不变
7. 不要合并或拆分字幕条目，保持原有的分段

## 待润色的字幕片段
{text}

请输出润色后的完整 SRT 片段，保持格式不变:"""


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
