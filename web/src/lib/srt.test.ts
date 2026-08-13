import { describe, expect, it } from "vitest"
import { parseSRT, srtToVTT, subtitlesToMarkdown, type Subtitle } from "./srt"

describe("SRT parsing", () => {
  const windowsSrt = [
    "1",
    "00:00:30,000 --> 00:00:39,333",
    "[Unknown-57b4] 第一段字幕",
    "",
    "2",
    "00:00:39,552 --> 00:00:40,058",
    "[Unknown-b735] 第二段字幕",
  ].join("\r\n")

  it("keeps Windows CRLF cues separate", () => {
    expect(parseSRT(windowsSrt)).toEqual([
      {
        index: 1,
        startTime: 30000,
        endTime: 39333,
        text: "第一段字幕",
        speaker: "Unknown-57b4",
      },
      {
        index: 2,
        startTime: 39552,
        endTime: 40058,
        text: "第二段字幕",
        speaker: "Unknown-b735",
      },
    ])
  })

  it("converts Windows CRLF cues to separate WebVTT cues", () => {
    expect(srtToVTT(windowsSrt)).toBe(
      "WEBVTT\n\n" +
      "00:00:30.000 --> 00:00:39.332\n第一段字幕\n\n" +
      "00:00:39.552 --> 00:00:40.057\n第二段字幕",
    )
  })
})

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
