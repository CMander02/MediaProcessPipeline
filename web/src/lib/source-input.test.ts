import { describe, expect, it } from "vitest"

import { isMediaFileName, parseSources, platformOf, removeSourceText, sourceKey } from "./source-input"

describe("parseSources", () => {
  it("reads one source per link and keeps only the link from share texts", () => {
    const { entries, unrecognized } = parseSources([
      "【零基础平面设计】 https://b23.tv/AbCdEf 复制打开",
      "https://www.youtube.com/watch?v=abcdefghijk, https://youtu.be/zzzzzzzzzzz",
      "看看这个 BV1DK4y1b7bY",
      "",
      "随便写的一行",
    ].join("\n"))
    expect(entries.map((entry) => entry.source)).toEqual([
      "https://b23.tv/AbCdEf",
      "https://www.youtube.com/watch?v=abcdefghijk",
      "https://youtu.be/zzzzzzzzzzz",
      "https://www.bilibili.com/video/BV1DK4y1b7bY",
    ])
    expect(entries.map((entry) => entry.platform)).toEqual(["bilibili", "youtube", "youtube", "bilibili"])
    expect(unrecognized).toEqual(["随便写的一行"])
  })

  it("drops the same video pasted twice in different forms", () => {
    const { entries } = parseSources([
      "https://www.bilibili.com/video/BV1DK4y1b7bY/?spm_id_from=333.1007",
      "https://m.bilibili.com/video/BV1DK4y1b7bY",
      "https://www.bilibili.com/video/BV1DK4y1b7bY?p=2",
      "https://youtu.be/abcdefghijk",
      "https://www.youtube.com/watch?v=abcdefghijk&t=30",
    ].join("\n"))
    expect(entries).toHaveLength(3)
  })

  it("accepts local paths only when allowed", () => {
    const text = "C:\\Videos\\访谈.mp4\n\"D:\\录音\\a b.m4a\""
    expect(parseSources(text).entries).toHaveLength(0)
    const { entries } = parseSources(text, { allowPaths: true })
    expect(entries.map((entry) => entry.source)).toEqual(["C:\\Videos\\访谈.mp4", "D:\\录音\\a b.m4a"])
    expect(entries.every((entry) => entry.kind === "path" && entry.platform === null)).toBe(true)
  })
})

describe("removeSourceText", () => {
  it("removes the link together with its share text", () => {
    const text = "【标题】 https://b23.tv/AbCdEf 复制打开\nhttps://youtu.be/abcdefghijk"
    const [first] = parseSources(text).entries
    expect(removeSourceText(text, first)).toBe("https://youtu.be/abcdefghijk")
  })
})

describe("helpers", () => {
  it("names platforms and keys like the archive index", () => {
    expect(platformOf("https://www.xiaohongshu.com/explore/1")).toBe("xiaohongshu")
    expect(platformOf("https://x.com/a/status/1")).toBe("x")
    expect(platformOf("https://example.com/post")).toBe("webpage")
    expect(sourceKey("https://youtu.be/abc123")).toBe("youtube:abc123")
    expect(sourceKey("https://www.bilibili.com/video/BV1DK4y1b7bY/?p=3")).toBe("bilibili:BV1DK4y1b7bY:p3")
    expect(isMediaFileName("talk.MKV")).toBe(true)
    expect(isMediaFileName("notes.txt")).toBe(false)
  })
})
