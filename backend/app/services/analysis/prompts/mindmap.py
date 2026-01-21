"""Mindmap generation prompt."""


def get_mindmap_prompt(text: str) -> str:
    """
    Generate the mindmap prompt.

    The mindmap is limited to 3 levels of depth (excluding root).
    Root node is a one-sentence summary of the entire content.

    Args:
        text: Transcript text to convert to mindmap

    Returns:
        Formatted prompt string
    """
    return f"""将以下文本转换为 Markmap 格式的思维导图。

## 格式要求
1. 根节点：全文的一句话概括（不超过30字）
2. 深度限制：最多3层（根 → 一级主题 → 二级要点 → 三级细节）
3. 使用 2 空格缩进
4. 每个节点文字简洁，不超过20字
5. 一级主题控制在 3-6 个
6. 每个一级主题下的二级要点控制在 2-5 个

## 格式示例
```
- 根节点：全文概括
  - 一级主题1
    - 二级要点1.1
      - 三级细节1.1.1
      - 三级细节1.1.2
    - 二级要点1.2
  - 一级主题2
    - 二级要点2.1
    - 二级要点2.2
      - 三级细节2.2.1
  - 一级主题3
    - 二级要点3.1
```

## 待转换内容
{text}

请直接输出思维导图，以 "- " 开头，不要包含 markdown 代码块标记:"""
