/**
 * SRT parser with speaker extraction and time utilities.
 * Ported from frontend/src/utils/srt.ts.
 */

export interface Subtitle {
  index: number
  startTime: number // milliseconds
  endTime: number
  text: string
  speaker?: string
}

/**
 * Parse SRT content into Subtitle array
 */
export function parseSRT(content: string): Subtitle[] {
  const subtitles: Subtitle[] = []
  const blocks = content.trim().split(/\n\n+/)

  for (const block of blocks) {
    const lines = block.trim().split("\n")
    if (lines.length < 3) continue

    try {
      const index = parseInt(lines[0], 10)
      const timestampMatch = lines[1].match(
        /(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})/,
      )

      if (!timestampMatch) continue

      const startTime =
        parseInt(timestampMatch[1]) * 3600000 +
        parseInt(timestampMatch[2]) * 60000 +
        parseInt(timestampMatch[3]) * 1000 +
        parseInt(timestampMatch[4])

      const endTime =
        parseInt(timestampMatch[5]) * 3600000 +
        parseInt(timestampMatch[6]) * 60000 +
        parseInt(timestampMatch[7]) * 1000 +
        parseInt(timestampMatch[8])

      let text = lines.slice(2).join("\n")
      let speaker: string | undefined

      // Extract speaker tag [SPEAKER_XX]
      const speakerMatch = text.match(/^\[([^\]]+)\]\s*/)
      if (speakerMatch) {
        speaker = speakerMatch[1]
        text = text.substring(speakerMatch[0].length)
      }

      subtitles.push({ index, startTime, endTime, text: text.trim(), speaker })
    } catch {
      continue
    }
  }

  return subtitles
}

/**
 * Convert SRT content to WebVTT format for use with <track> elements.
 * Strips speaker tags [Name] since VTT renders them as visible text.
 */
export function srtToVTT(srt: string): string {
  // Replace SRT comma timestamps with VTT period timestamps
  const body = srt
    .replace(/(\d{2}:\d{2}:\d{2}),(\d{3})/g, "$1.$2")
    // Remove speaker tags like [张小琦] from subtitle text
    .replace(/^\[([^\]]+)\]\s*/gm, "")
  return "WEBVTT\n\n" + body
}

/**
 * Format milliseconds to SRT timestamp: HH:MM:SS,mmm
 */
export function formatSRTTime(ms: number): string {
  const h = Math.floor(ms / 3600000)
  const m = Math.floor((ms % 3600000) / 60000)
  const s = Math.floor((ms % 60000) / 1000)
  const msRem = ms % 1000
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")},${String(msRem).padStart(3, "0")}`
}

/**
 * Find subtitle at given time using binary search (ms)
 */
export function findSubtitleAtTime(
  subtitles: Subtitle[],
  timeMs: number,
): Subtitle | undefined {
  let lo = 0
  let hi = subtitles.length - 1
  while (lo <= hi) {
    const mid = (lo + hi) >>> 1
    const sub = subtitles[mid]
    if (timeMs < sub.startTime) {
      hi = mid - 1
    } else if (timeMs > sub.endTime) {
      lo = mid + 1
    } else {
      return sub
    }
  }
  return undefined
}

/**
 * Find the index of the subtitle at or just before given time
 */
export function findSubtitleIndexAtTime(
  subtitles: Subtitle[],
  timeMs: number,
): number {
  let lo = 0
  let hi = subtitles.length - 1
  let best = -1
  while (lo <= hi) {
    const mid = (lo + hi) >>> 1
    if (subtitles[mid].startTime <= timeMs) {
      best = mid
      lo = mid + 1
    } else {
      hi = mid - 1
    }
  }
  return best
}

/**
 * Extract unique speakers from subtitles
 */
export function extractSpeakers(subtitles: Subtitle[]): string[] {
  const set = new Set<string>()
  for (const sub of subtitles) {
    if (sub.speaker) set.add(sub.speaker)
  }
  return Array.from(set)
}

/**
 * Fixed 8-color palette for speakers
 */
const SPEAKER_COLORS = [
  "#3b82f6", // blue
  "#ef4444", // red
  "#22c55e", // green
  "#f59e0b", // amber
  "#8b5cf6", // violet
  "#ec4899", // pink
  "#06b6d4", // cyan
  "#f97316", // orange
]

// Global speaker → index mapping (assigned in order of first appearance)
const _speakerIndex = new Map<string, number>()
let _nextIndex = 0

/**
 * Get a deterministic color for a speaker name (assigned by order of appearance)
 */
export function getSpeakerColor(speaker: string): string {
  if (!_speakerIndex.has(speaker)) {
    _speakerIndex.set(speaker, _nextIndex++)
  }
  return SPEAKER_COLORS[_speakerIndex.get(speaker)! % SPEAKER_COLORS.length]
}

/**
 * Format speaker label for display: SPEAKER_00 → S0, SPEAKER_02 → S2
 */
export function formatSpeakerLabel(speaker: string): string {
  const m = speaker.match(/^SPEAKER_0*(\d+)$/)
  if (m) return `S${m[1]}`
  return speaker
}
