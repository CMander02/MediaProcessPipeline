import { describe, expect, it } from "vitest"
import { parseSummaryMarkdown } from "./markdown"

describe("parseSummaryMarkdown", () => {
  it("extracts H3 key facts and removes timeline from the display body", () => {
    const parsed = parseSummaryMarkdown(`---
title: "示例"
---

# 示例

## Summary
这是一段摘要。

### Key Facts
- 事实一
- 事实二

### Timeline
- [00:00:00] 开场
- [00:01:15] 第一章
`)

    expect(parsed.keyFacts).toEqual(["事实一", "事实二"])
    expect(parsed.displayBody).toContain("这是一段摘要。")
    expect(parsed.displayBody).not.toContain("Key Facts")
    expect(parsed.displayBody).not.toContain("Timeline")
    expect(parsed.displayBody).not.toContain("00:01:15")
  })

  it("keeps ordinary nested sections", () => {
    const parsed = parseSummaryMarkdown(`## Summary\n摘要\n\n### 主要观点\n观点内容`)

    expect(parsed.displayBody).toContain("### 主要观点")
    expect(parsed.sections.map((section) => section.title)).toEqual(["Summary", "主要观点"])
  })

  it("removes standalone separators from the displayed summary", () => {
    const parsed = parseSummaryMarkdown(`## Summary\n摘要\n\n---\n\n### Key Facts\n- 事实一`)

    expect(parsed.displayBody).toBe("## Summary\n摘要")
  })
})
