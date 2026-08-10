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

  it("renders legacy list levels as recursive Markdown headings", () => {
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
      "##### RSI",
    ].join("\n"))
  })

  it("keeps heading-based mindmaps unchanged", () => {
    const markdown = "## 开场\n### 嘉宾背景"

    expect(mindmapMarkdownForReading(markdown)).toBe(markdown)
  })
})
