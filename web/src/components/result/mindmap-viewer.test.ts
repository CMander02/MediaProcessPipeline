import { describe, expect, it } from "vitest"
import { mindmapMarkdownForReading, sanitizeMindmapMarkdown } from "@/lib/mindmap"

describe("sanitizeMindmapMarkdown", () => {
  it("removes legacy timestamps from node labels", () => {
    const markdown = [
      "- 开场 [00:00:00]",
      "  - 观点 [01:15 - 02:36]",
      "  - 保留普通方括号 [Evolvent AI]",
    ].join("\n")

    expect(sanitizeMindmapMarkdown(markdown)).toBe([
      "- 开场",
      "  - 观点",
      "  - 保留普通方括号 [Evolvent AI]",
    ].join("\n"))
  })

  it("renders legacy branches as headings and leaves as body items", () => {
    const markdown = [
      "- 开场 [00:00:00]",
      "  - 嘉宾背景",
      "    - Evolvent AI",
      "      - RSI",
    ].join("\n")

    expect(mindmapMarkdownForReading(markdown)).toBe([
      "## 开场",
      "### 嘉宾背景",
      "#### Evolvent AI",
      "- RSI",
    ].join("\n"))
  })

  it("renders heading-based mindmap leaves as body items", () => {
    const markdown = "## 开场\n### 嘉宾背景\n### 课程介绍\n#### 目标"

    expect(mindmapMarkdownForReading(markdown)).toBe(
      "## 开场\n- 嘉宾背景\n### 课程介绍\n- 目标",
    )
  })
})
