import { describe, expect, it } from "vitest"

import type { Subtitle } from "@/lib/srt"
import { alignSecondary, carrySpeakers, languageName, parseSubtitleFile, trackKind } from "./subtitle-tracks"

const cue = (index: number, startTime: number, endTime: number, text: string, speaker?: string): Subtitle =>
  ({ index, startTime, endTime, text, speaker })

const polished = [
  cue(1, 0, 4000, "大家好，今天聊 AI。", "Dario"),
  cue(2, 4000, 9000, "我先说说为什么。", "Jared"),
  cue(3, 9000, 12000, "好的。", "Dario"),
]

describe("carrySpeakers", () => {
  it("takes the speaker whose segment overlaps each line the most", () => {
    const english = [cue(1, 0, 3500, "Hi everyone"), cue(2, 3500, 8000, "Let me start"), cue(3, 8800, 11000, "Sure")]
    expect(carrySpeakers(english, polished).map((line) => line.speaker)).toEqual(["Dario", "Jared", "Dario"])
  })

  it("leaves lines alone when the reference has no speakers", () => {
    const plain = polished.map((line) => ({ ...line, speaker: undefined }))
    expect(carrySpeakers([cue(1, 0, 1000, "x")], plain)[0].speaker).toBeUndefined()
  })
})

describe("alignSecondary", () => {
  it("puts each cue of the second language under the line it is spoken in", () => {
    const english = [cue(1, 100, 2000, "Hi"), cue(2, 2000, 3900, "everyone"), cue(3, 4100, 8800, "Let me start"), cue(4, 9100, 11800, "Sure")]
    expect(alignSecondary(polished, english)).toEqual(["Hi everyone", "Let me start", "Sure"])
  })

  it("joins Chinese cues without spaces, after full stops too", () => {
    const chinese = [cue(1, 0, 1500, "首位？"), cue(2, 1500, 2500, "我选贾里德。"), cue(3, 2500, 3500, "任意地。")]
    expect(alignSecondary(polished, chinese)[0]).toBe("首位？我选贾里德。任意地。")
  })

  it("does not repeat rolling captions", () => {
    const rolling = [cue(1, 0, 2000, "Hi everyone"), cue(2, 1000, 3000, "Hi everyone")]
    expect(alignSecondary(polished, rolling)[0]).toBe("Hi everyone")
  })
})

describe("parseSubtitleFile", () => {
  it("reads YouTube json3 saved under an .srt name", () => {
    const json3 = JSON.stringify({
      wireMagic: "pb3",
      events: [
        { tStartMs: 0, dDurationMs: 3109922, id: 1 },
        { tStartMs: 229, dDurationMs: 2907, segs: [{ utf8: "- Why are we working on\nAI in the first place?" }] },
        { tStartMs: 2239, dDurationMs: 1000, aAppend: 1, segs: [{ utf8: "\n" }] },
        { tStartMs: 3136, dDurationMs: 2493, segs: [{ utf8: "我们" }, { utf8: "为什么", tOffsetMs: 278 }] },
      ],
    })
    expect(parseSubtitleFile(json3)).toEqual([
      { index: 1, startTime: 229, endTime: 3136, text: "- Why are we working on AI in the first place?" },
      { index: 2, startTime: 3136, endTime: 5629, text: "我们为什么" },
    ])
  })

  it("reads WebVTT, keeps brackets as text and drops rolling repeats", () => {
    const vtt = [
      "WEBVTT", "Kind: captions", "",
      "00:00.500 --> 00:02.000 align:start", "[Music]", "",
      "00:00:02.000 --> 00:00:04.000", "hello<00:00:02.500><c> there</c>", "",
      "00:00:04.000 --> 00:00:06.000", "hello there", "general", "",
    ].join("\n")
    expect(parseSubtitleFile(vtt).map(({ startTime, text, speaker }) => ({ startTime, text, speaker }))).toEqual([
      { startTime: 500, text: "[Music]", speaker: undefined },
      { startTime: 2000, text: "hello there", speaker: undefined },
      { startTime: 4000, text: "general", speaker: undefined },
    ])
  })

  it("reads plain SRT and returns nothing for other JSON", () => {
    expect(parseSubtitleFile("1\r\n00:00:01,000 --> 00:00:02,500\r\n第一行\r\n第二行\r\n")[0])
      .toEqual({ index: 1, startTime: 1000, endTime: 2500, text: "第一行第二行" })
    expect(parseSubtitleFile('{"body": []}')).toEqual([])
  })
})

describe("labels", () => {
  it("names languages and track kinds", () => {
    expect(languageName("zh-Hans")).toBe("简体中文")
    expect(languageName("pt-BR")).toBe("pt-BR")
    expect(trackKind({ lang: "zh-Hans", type: "ai", polished: true })).toBe("润色 · 带说话人")
    expect(trackKind({ lang: "en-orig", type: "ai", polished: false })).toBe("平台自动识别")
    expect(trackKind({ lang: "en", type: "cc", polished: false })).toBe("平台字幕")
  })
})
