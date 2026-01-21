"""Content analysis prompt for extracting metadata from transcripts."""

from typing import Any


def get_analyze_prompt(
    title: str,
    text: str,
    uploader: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    chapters: list[dict[str, Any]] | None = None,
) -> str:
    """
    Generate the content analysis prompt.

    Args:
        title: Video/audio title
        text: Transcript text (first 8000 chars recommended)
        uploader: Author/uploader name
        description: Video description/summary
        tags: List of tags from the source
        chapters: List of chapter markers with title and start_time

    Returns:
        Formatted prompt string
    """
    # Build metadata section
    metadata_parts = [f"- 标题: {title}"]

    if uploader:
        metadata_parts.append(f"- 作者: {uploader}")

    if description:
        # Truncate long descriptions
        desc_truncated = description[:1000] + "..." if len(description) > 1000 else description
        metadata_parts.append(f"- 简介: {desc_truncated}")

    if tags:
        tags_str = ", ".join(tags[:20])  # Limit to 20 tags
        metadata_parts.append(f"- 标签: {tags_str}")

    if chapters:
        chapters_str = "\n".join(
            f"  - [{_format_time(ch.get('start_time', 0))}] {ch.get('title', '')}"
            for ch in chapters[:30]  # Limit to 30 chapters
        )
        metadata_parts.append(f"- 章节:\n{chapters_str}")

    metadata_section = "\n".join(metadata_parts)

    return f"""请分析以下转录文本，提取关键信息。

## 视频/音频元信息
{metadata_section}

## 转录内容
{text}

请根据上述元信息和转录内容，返回 JSON 格式:
{{
    "language": "检测到的主要语言代码，如 zh-CN, en-US, ja-JP",
    "content_type": "内容类型，如 技术讲座/访谈/播客/会议/教程/演讲/评测/新闻",
    "main_topics": ["主要话题1", "主要话题2", "主要话题3"],
    "keywords": ["关键词1", "关键词2", "关键词3", "关键词4", "关键词5"],
    "proper_nouns": ["专有名词/人名/产品名/术语1", "专有名词2", "专有名词3"],
    "speakers_detected": 1,
    "tone": "语气风格，如 正式/非正式/教学/对话/幽默"
}}

注意:
1. 充分利用标签和简介中的信息来辅助识别专有名词和话题
2. 如果有章节标记，可参考章节标题来确定主要话题
3. 只返回 JSON，不要其他内容"""


def _format_time(seconds: float) -> str:
    """Format seconds to HH:MM:SS or MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
