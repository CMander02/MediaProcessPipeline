"""Summarization prompt."""


def get_summarize_prompt(text: str) -> str:
    """
    Generate the summarization prompt.

    Args:
        text: Transcript text to summarize

    Returns:
        Formatted prompt string
    """
    return f"""分析以下转录文本，生成结构化摘要。

转录内容:
{text}

请返回 JSON 格式:
{{
    "tldr": "一句话总结（不超过100字）",
    "key_facts": [
        "关键要点1",
        "关键要点2",
        "关键要点3",
        "..."
    ],
    "action_items": [
        "如有待办事项或建议行动，列在此处",
        "..."
    ],
    "topics": [
        "主题1",
        "主题2",
        "..."
    ]
}}

注意:
1. key_facts 应包含 3-10 个最重要的信息点
2. action_items 只包含明确提到的行动建议，如果没有则返回空数组
3. topics 列出讨论的主要主题
4. 只返回 JSON，不要其他内容"""
