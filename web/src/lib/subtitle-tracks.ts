import type { Subtitle } from "@/lib/srt"

/** One subtitle file of an archive, as listed in metadata.extra.subtitle_tracks. */
export interface SubtitleTrackInfo {
  lang: string
  /** cc: uploaded by the platform; ai: generated or machine-translated; asr: our recognition */
  type: string
  filename: string
  /** The track that went through speaker attribution and polishing */
  polished: boolean
}

/** Menu value for the archive's default transcript when no listed track is marked polished. */
export const DEFAULT_TRACK = "__default__"

const LANGUAGE_NAMES: Record<string, string> = {
  zh: "中文",
  "zh-CN": "简体中文",
  "zh-Hans": "简体中文",
  "zh-TW": "繁體中文",
  "zh-Hant": "繁體中文",
  "ai-zh": "中文",
  en: "English",
  "en-US": "English",
  "en-GB": "English",
  "en-orig": "English",
  ja: "日本語",
  ko: "한국어",
  fr: "Français",
  de: "Deutsch",
  es: "Español",
  ru: "Русский",
}

export function languageName(lang: string): string {
  return LANGUAGE_NAMES[lang] ?? LANGUAGE_NAMES[lang.split("-")[0]] ?? lang
}

/** How a track was made, in a few words. */
export function trackKind(track: Pick<SubtitleTrackInfo, "type" | "polished" | "lang">): string {
  if (track.polished) return "润色 · 带说话人"
  if (track.lang.endsWith("-orig")) return "平台自动识别"
  if (track.type === "cc") return "平台字幕"
  if (track.type === "ai") return "平台自动生成"
  if (track.type === "asr") return "语音识别"
  return "字幕"
}

const CJK = /[　-ヿ㐀-鿿가-힯＀-￯]/
const TIMESTAMP = /(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{3})/g

/** Caption lines as one sentence, without a space between CJK characters. */
function joinLines(parts: string[]): string {
  return parts.reduce((text, part) => {
    if (!text) return part
    return text + (CJK.test(text.at(-1)!) && CJK.test(part[0]) ? "" : " ") + part
  }, "")
}

function cleanLines(text: string): string[] {
  return text.split("\n").map((line) => line.replace(/<[^>]+>/g, "").trim()).filter(Boolean)
}

interface Json3Event {
  tStartMs?: number
  dDurationMs?: number
  segs?: Array<{ utf8?: string }>
}

function parseJson3(events: Json3Event[]): Subtitle[] {
  const cues = events.flatMap((event) => {
    const text = joinLines(cleanLines((event.segs ?? []).map((seg) => seg.utf8 ?? "").join("")))
    const startTime = event.tStartMs ?? 0
    return text ? [{ startTime, endTime: startTime + (event.dDurationMs ?? 0), text }] : []
  })
  // Rolling captions overlap; each one ends where the next begins.
  return cues.map((cue, index) => {
    const next = cues[index + 1]
    const endTime = next && next.startTime > cue.startTime ? Math.min(cue.endTime, next.startTime) : cue.endTime
    return { index: index + 1, startTime: cue.startTime, endTime, text: cue.text }
  })
}

function timestampMs(match: RegExpMatchArray): number {
  const [, hours, minutes, seconds, millis] = match
  return ((Number(hours ?? 0) * 60 + Number(minutes)) * 60 + Number(seconds)) * 1000 + Number(millis)
}

function parseCues(content: string): Subtitle[] {
  const lines: Subtitle[] = []
  let previous: string[] = []
  for (const block of content.replace(/\r\n?/g, "\n").split(/\n\s*\n/)) {
    const rows = block.split("\n")
    const timing = rows.findIndex((row) => row.includes("-->"))
    if (timing < 0) continue
    const times = [...rows[timing].matchAll(TIMESTAMP)]
    if (times.length < 2) continue
    const raw = cleanLines(rows.slice(timing + 1).join("\n"))
    // Rolling captions repeat the lines already on screen; keep what is new.
    let first = 0
    while (first < raw.length && previous.includes(raw[first])) first += 1
    previous = raw
    if (first === raw.length) continue
    lines.push({
      index: lines.length + 1,
      startTime: timestampMs(times[0]),
      endTime: timestampMs(times[1]),
      text: joinLines(raw.slice(first)),
    })
  }
  return lines
}

/**
 * A platform track as saved: SRT, WebVTT or YouTube's json3, whatever the file is called.
 * Brackets are caption text here ("[Music]"), not speaker tags.
 */
export function parseSubtitleFile(content: string): Subtitle[] {
  const text = content.trimStart() // also drops a byte-order mark
  if (text.startsWith("{")) {
    try {
      const events = (JSON.parse(text) as { events?: unknown }).events
      return Array.isArray(events) ? parseJson3(events as Json3Event[]) : []
    } catch {
      return []
    }
  }
  return parseCues(text)
}

function overlap(a: Subtitle, b: Subtitle): number {
  return Math.min(a.endTime, b.endTime) - Math.max(a.startTime, b.startTime)
}

/** First index whose end is after `time` (subtitles sorted by start). */
function firstEndingAfter(subtitles: Subtitle[], time: number): number {
  let low = 0
  let high = subtitles.length
  while (low < high) {
    const mid = (low + high) >> 1
    if (subtitles[mid].endTime <= time) low = mid + 1
    else high = mid
  }
  return low
}

/**
 * Other-language tracks come without speakers; borrow them from the polished track by
 * picking, for each line, the speaker whose segment overlaps it the most.
 */
export function carrySpeakers(lines: Subtitle[], reference: Subtitle[]): Subtitle[] {
  if (!reference.some((item) => item.speaker)) return lines
  return lines.map((line) => {
    if (line.speaker) return line
    let best: Subtitle | undefined
    let bestOverlap = 0
    for (let index = firstEndingAfter(reference, line.startTime); index < reference.length; index += 1) {
      const candidate = reference[index]
      if (candidate.startTime >= line.endTime) break
      const amount = overlap(line, candidate)
      if (amount > bestOverlap) {
        best = candidate
        bestOverlap = amount
      }
    }
    return best?.speaker ? { ...line, speaker: best.speaker } : line
  })
}

/**
 * Text of a second language for each line of the shown track: every cue whose middle falls
 * inside the line, so each cue appears once even when timings differ between tracks.
 */
export function alignSecondary(lines: Subtitle[], secondary: Subtitle[]): string[] {
  const texts: string[][] = lines.map(() => [])
  let lineIndex = 0
  for (const cue of secondary) {
    const middle = (cue.startTime + cue.endTime) / 2
    while (lineIndex < lines.length - 1 && lines[lineIndex].endTime <= middle && lines[lineIndex + 1].startTime <= middle) {
      lineIndex += 1
    }
    const text = cue.text.replace(/\s*\n\s*/g, " ").trim()
    const bucket = texts[lineIndex]
    if (text && bucket && bucket.at(-1) !== text) bucket.push(text)
  }
  return texts.map(joinLines)
}
