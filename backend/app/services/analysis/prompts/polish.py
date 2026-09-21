"""Transcript polishing prompts."""

from typing import Any

POLISH_SYSTEM_PROMPT = """You are a constrained subtitle copy editor.
Actively remove non-semantic fillers, stutters, and abandoned false starts while
preserving meaningful short replies, emphasis, negation, and factual details. Correct
recognition errors and punctuation. Subtitle
index, timestamp, speaker identity, and canonical entity spelling are read-only fields.
Never add a speaker label when the input cue has none. Return only the requested JSON
array."""


def get_polish_prompt(
    text: str,
    language: str = "unknown",
    content_type: str = "unknown",
    main_topics: list[str] | None = None,
    keywords: list[str] | None = None,
    proper_nouns: list[str] | None = None,
    entities: list[dict[str, Any]] | None = None,
    speaker_ids: list[str] | None = None,
    timeline_context: str = "",
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
    entity_lines = []
    for entity in entities or []:
        canonical = str(entity.get("canonical") or "").strip()
        aliases = ", ".join(str(item) for item in entity.get("aliases") or [])
        if canonical:
            entity_lines.append(f"- {canonical}" + (f"（别名：{aliases}）" if aliases else ""))
    entities_str = "\n".join(entity_lines) or "- 无"
    speakers_str = ", ".join(speaker_ids or []) or "无说话人标签"
    speaker_rule = (
        f"输入允许的说话人标签只有：{speakers_str}。每条输出必须保留对应输入的原标签。"
        if speaker_ids
        else "输入没有说话人标签。所有输出都禁止新增任何 [SPEAKER]、人物名或角色前缀。"
    )
    return f"""你是专业的字幕校对编辑。请根据上下文信息润色下面的字幕片段。

## 内容分析
- 语言: {language}
- 内容类型: {content_type}
- 主要话题: {topics_str}
- 关键词: {keywords_str}
- 专有名词（请保持一致拼写）: {nouns_str}
- 当前时间轴范围: {timeline_context or '未提供'}
- 输入 speaker 集合: {speakers_str}

## 规范实体表
{entities_str}

## 润色要求
1. 修正语音识别错误和错别字
2. 添加适当的标点符号
3. 主动整理语气词和卡顿：删去没有语义作用的“呃、那个、就是说”等填充词；
   合并口吃、起句卡顿和无意义的连续重复；说话人立即自我纠正时保留纠正后的完整意思
   例如“我我觉得，呃，明天，不，后天交付”整理为“我觉得后天交付”。结合语义识别被放弃的起句和立即纠正，保留最终确定的信息
4. 保持原意和说话者风格
5. {speaker_rule}
6. **不要**合并或拆分字幕条目；输入有 N 条，输出就必须有 N 条
7. **不要**改写 timestamp，必须原样保留
8. 实体表中的 canonical 拼写必须保持一致
9. 保留有意义的短回答（如“嗯、对、是、不是”）、强调性重复、否定、数量和条件。
   “然后、就是、那个”等承担逻辑关系或指代时保留，结合上下文判断
10. 输出自然、简洁的短语或句子字幕，保持原信息完整

## 输出格式（严格遵守）
直接输出 JSON 数组，**不要**任何前后解释/markdown 代码块/废话引导句。
每个元素是一个对象：
{{"index": <输入整数>, "timestamp": "<输入原时间戳>",
  "text": "<润色后的正文；speaker 前缀遵循输入>"}}

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
3. 主动删去无语义填充词，整理口吃、起句卡顿、无意义重复和立即自我纠正
4. 保持原意和说话者的风格
5. 如果有 [SPEAKER_XX] 标记，请保持不变
6. 输出完整文本，不要总结
7. 保留有意义的短回答、强调性重复、否定、数量和条件；“然后、那个”等有逻辑或指代作用时保留

待处理文本:
{text}"""
