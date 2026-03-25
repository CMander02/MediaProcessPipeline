"""Mindmap generation prompts — single-pass and map-reduce.

All prompts produce plain text only: `- ` list markers with 2-space indentation.
No markdown formatting (no bold, italic, links, code, headings).
"""


def get_mindmap_prompt(text: str) -> str:
    """Short-content prompt (< ~30 min transcript). Used as fallback."""
    return f"""你是一个内容整理专家。请阅读以下文本（可能是会议录音、访谈、讲座的转录），提炼其中的核心内容，生成一份结构化的思维导图大纲。

## 关键要求
1. **归纳提炼**，不是逐句搬运。将零散的口语对话提炼为有信息量的要点
2. 一级节点为讨论的主要话题板块（3-8 个），二级为该话题下的要点，三级为具体细节
3. 每个节点应是一个完整的、有信息量的短句，而不是口语碎片
4. 过滤掉语气词、重复、无意义的对话（"嗯"、"对"、"那个"等）
5. 保留关键人名、术语、数字、决策、结论
6. 合并表达同一意思的多句话为一个节点

## 格式要求
- 使用 `- ` 标记，2 空格缩进表示层级（2-4 层深度）
- 纯文本，禁止使用任何 markdown 格式（不要加粗、斜体、链接、代码块、标题符号）
- 直接输出列表，不要任何前言或总结

## 待提炼内容
{text}

请直接输出纯文本列表:"""


def get_mindmap_map_prompt(
    chapter_title: str,
    chapter_text: str,
    global_context: str,
) -> str:
    """Map phase: summarize one chapter into a structured list."""
    return f"""你是一个内容整理专家。以下是一段视频中「{chapter_title}」章节的字幕内容。

## 视频元信息
{global_context}

## 本章节字幕
{chapter_text}

## 任务
将本章节的内容**归纳提炼**为结构化的思维导图大纲。要求：
1. 归纳提炼，不是逐句搬运。将零散口语提炼为有信息量的要点
2. 一级节点为该章节的主要话题（2-5 个），二三级逐层细化
3. 每个节点是完整的短句，不是口语碎片
4. 过滤语气词和无意义重复，保留关键人名、术语、数字
5. 使用 `- ` 标记，2 空格缩进，2-4 层深度
6. 纯文本，禁止使用任何 markdown 格式（不要加粗、斜体、链接、代码块）
7. 直接输出纯文本列表，不要代码块"""


def get_mindmap_reduce_prompt(
    group_label: str,
    group_summaries: str,
) -> str:
    """Reduce phase: merge several chapter summaries into one cohesive section."""
    return f"""你是一个内容整理专家。以下是视频「{group_label}」部分的各章节提炼摘要。

## 章节摘要
{group_summaries}

## 任务
将这些章节合并为一个结构化的思维导图大纲。要求：
1. 一级节点为该部分的主要话题板块
2. 二级、三级、四级逐层细化，保留关键人名、术语、数字
3. 合并重复内容，消除冗余
4. 每个节点是完整的、有信息量的短句
5. 纯文本，禁止使用任何 markdown 格式（不要加粗、斜体、链接、代码块）
6. 直接输出列表，`- ` 标记，2 空格缩进，无代码块"""
