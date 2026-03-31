export function getAnalyzePrompt(
  title: string,
  text: string,
  uploader?: string,
  description?: string,
): string {
  const metaParts = [`- 标题: ${title}`]
  if (uploader) metaParts.push(`- 作者: ${uploader}`)
  if (description) {
    const desc = description.length > 1000 ? description.slice(0, 1000) + "..." : description
    metaParts.push(`- 简介: ${desc}`)
  }
  const metaSection = metaParts.join("\n")

  return `请分析以下转录文本，提取关键信息。

## 视频/音频元信息
${metaSection}

## 转录内容
${text}

请根据上述元信息和转录内容，返回 JSON 格式:
{
    "language": "检测到的主要语言代码，如 zh-CN, en-US, ja-JP",
    "content_type": "内容类型，如 技术讲座/访谈/播客/会议/教程/演讲/评测/新闻",
    "keywords": ["关键词1", "关键词2", "关键词3", "关键词4", "关键词5"],
    "proper_nouns": ["专有名词/人名/产品名/术语1", "专有名词2", "专有名词3"]
}

注意:
1. 充分利用简介中的信息来辅助识别专有名词和话题
2. 只返回 JSON，不要其他内容`
}

export function getSummarizePrompt(text: string): string {
  return `分析以下转录文本，生成结构化摘要。

转录内容:
${text}

请返回 JSON 格式:
{
    "tldr": "一句话总结（不超过100字）",
    "key_facts": [
        "关键要点1",
        "关键要点2",
        "关键要点3"
    ]
}

注意:
1. key_facts 应包含 3-10 个最重要的信息点
2. 只返回 JSON，不要其他内容`
}

export function getOutlinePrompt(text: string): string {
  return `你是一个内容整理专家。请阅读以下文本（可能是会议录音、访谈、讲座的转录），提炼其中的核心内容，生成一份结构化的思维导图大纲。

## 关键要求
1. **归纳提炼**，不是逐句搬运。将零散的口语对话提炼为有信息量的要点
2. 一级节点为讨论的主要话题板块（3-8 个），二级为该话题下的要点，三级为具体细节
3. 每个节点应是一个完整的、有信息量的短句，而不是口语碎片
4. 过滤掉语气词、重复、无意义的对话
5. 保留关键人名、术语、数字、决策、结论
6. 合并表达同一意思的多句话为一个节点

## 格式要求
- 使用 \`- \` 标记，2 空格缩进表示层级（最多 3 层深度）
- 纯文本，禁止使用任何 markdown 格式（不要加粗、斜体、链接、代码块、标题符号）
- 直接输出列表，不要任何前言或总结

## 待提炼内容
${text}

请直接输出纯文本列表:`
}
