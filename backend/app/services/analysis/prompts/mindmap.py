"""Mindmap generation prompts — single-pass and map-reduce."""


def get_mindmap_prompt(text: str) -> str:
    """Short-content prompt (< ~30 min transcript). Used as fallback."""
    return f"""将以下文本转换为结构化的 markdown 无序列表思维导图。

## 格式要求
1. 一级节点用粗体，作为主要话题板块
2. 二级、三级、四级逐层细化，保留关键人名、术语、数字
3. 使用 `- ` 标记，2 空格缩进
4. 按内容先后顺序排列，不要重新归类
5. 不要遗漏重要信息

## 待转换内容
{text}

请直接输出 markdown 无序列表，不要包含代码块标记:"""


def get_mindmap_map_prompt(
    chapter_title: str,
    chapter_text: str,
    global_context: str,
) -> str:
    """Map phase: summarize one chapter into a structured list."""
    return f"""你是一个视频内容整理专家。以下是一段访谈/讲座视频中「{chapter_title}」章节的字幕内容。

## 视频元信息
{global_context}

## 本章节字幕
{chapter_text}

## 任务
按照内容的先后顺序，将本章节的内容整理为结构化的 markdown 无序列表。要求：
1. 按话题先后顺序排列，不要重新归类
2. 使用 2-4 层缩进（一级为章节内的主要话题段落，二级为具体内容，三级四级为补充细节）
3. 每个节点简洁但有信息量，保留关键人名、术语、数字
4. 不要遗漏重要信息，宁可多写不要少写
5. 使用 `- ` 标记，2 空格缩进
6. 直接输出 markdown 列表，不要代码块"""


def get_mindmap_reduce_prompt(
    group_label: str,
    group_summaries: str,
) -> str:
    """Reduce phase: merge several chapter summaries into one cohesive section."""
    return f"""你是一个视频内容整理专家。以下是访谈视频「{group_label}」部分的各章节摘要。

## 章节摘要
{group_summaries}

## 任务
将这些章节合并为一个结构化 markdown 无序列表。要求：
1. 严格按时间先后顺序
2. 一级节点用粗体，作为该部分的主要话题板块
3. 二级、三级、四级逐层细化，保留关键人名、术语、数字、名言
4. 不遗漏重要信息
5. 直接输出列表，`- ` 标记，2 空格缩进，无代码块"""
