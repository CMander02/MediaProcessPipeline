import { describe, expect, it } from "vitest"
import { subtitlesToMarkdown, type Subtitle } from "./srt"

describe("subtitlesToMarkdown", () => {
  it("exports detailed YAML frontmatter and speaker paragraphs", () => {
    const subtitles: Subtitle[] = [
      { index: 1, startTime: 0, endTime: 1000, speaker: "Alice", text: "第一段" },
      { index: 2, startTime: 1000, endTime: 2000, speaker: "Bob", text: "第二段\n继续" },
    ]

    expect(subtitlesToMarkdown(subtitles, {
      title: "标题: 示例",
      source_url: "https://example.com/watch?v=1",
      platform: "bilibili",
      duration_seconds: 2,
      polished: true,
    })).toBe(`---
title: "标题: 示例"
document_type: "transcript"
source_url: "https://example.com/watch?v=1"
platform: "bilibili"
duration_seconds: 2
polished: true
speakers: ["Alice","Bob"]
---

Alice: 第一段

Bob: 第二段 继续
`)
  })

  it("uses a stable fallback label when diarization is unavailable", () => {
    const subtitles: Subtitle[] = [
      { index: 1, startTime: 0, endTime: 1000, text: "内容" },
    ]

    expect(subtitlesToMarkdown(subtitles, { title: "无说话人" }))
      .toContain("\nSpeaker: 内容\n")
  })
})
