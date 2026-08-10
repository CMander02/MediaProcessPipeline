import { describe, expect, it } from "vitest"
import { formatSourceDescriptionMarkdown } from "@/lib/source-description"

describe("formatSourceDescriptionMarkdown", () => {
  it("adds structure to legacy plain podcast descriptions", () => {
    const source = [
      "开场介绍。",
      "",
      "【人类博物馆】",
      "导游：曲凯",
      "【时光机】",
      "Part 1 后训练与模型竞争",
      "01:15 在 Kimi 和出来创业的感受有什么不同？",
    ].join("\n")

    expect(formatSourceDescriptionMarkdown(source)).toContain("## 人类博物馆")
    expect(formatSourceDescriptionMarkdown(source)).toContain("**导游：** 曲凯")
    expect(formatSourceDescriptionMarkdown(source)).toContain("### Part 1 后训练与模型竞争")
    expect(formatSourceDescriptionMarkdown(source)).toContain("- **01:15** 在 Kimi")
  })

  it("preserves rich Markdown fetched from the source", () => {
    const source = "## 时光机\n\n- [RSIBench-Data](https://example.com)"

    expect(formatSourceDescriptionMarkdown(source)).toBe(source)
  })

  it("restores X thread author and paragraph structure and removes login chrome", () => {
    const source = [
      "# stdrc on X",
      "",
      "Source: https://x.com/istdrc/status/1",
      "",
      "Hanchi Sun",
      "@sun_hanchi",
      "15h",
      "第一条正文",
      "1",
      "8",
      "4.8K",
      "stdrc",
      "@istdrc",
      "第二条正文第一段",
      "第二条正文第二段",
      "118.7K",
      "Views",
      "Log in or sign up for X",
      "Continue with Google",
    ].join("\n")

    const formatted = formatSourceDescriptionMarkdown(source)

    expect(formatted).toContain("**Hanchi Sun**  \n@sun_hanchi · 15h\n\n第一条正文")
    expect(formatted).toContain("**stdrc**  \n@istdrc\n\n第二条正文第一段\n\n第二条正文第二段")
    expect(formatted).not.toContain("4.8K")
    expect(formatted).not.toContain("Log in or sign up for X")
  })
})
