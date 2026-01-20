import type { Subtitle } from "@/types"

/**
 * Parse SRT content into Subtitle array
 */
export function parseSRT(content: string): Subtitle[] {
  const subtitles: Subtitle[] = []
  const blocks = content.trim().split(/\n\n+/)

  for (const block of blocks) {
    const lines = block.trim().split('\n')
    if (lines.length < 3) continue

    try {
      const index = parseInt(lines[0], 10)
      const timestampMatch = lines[1].match(
        /(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})/
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

      let text = lines.slice(2).join('\n')
      let speaker: string | undefined

      // Extract speaker tag [SPEAKER_XX]
      const speakerMatch = text.match(/^\[([^\]]+)\]\s*/)
      if (speakerMatch) {
        speaker = speakerMatch[1]
        text = text.substring(speakerMatch[0].length)
      }

      subtitles.push({
        index,
        startTime,
        endTime,
        text: text.trim(),
        speaker,
      })
    } catch {
      continue
    }
  }

  return subtitles
}

/**
 * Format milliseconds to SRT timestamp format
 */
export function formatSRTTime(ms: number): string {
  const h = Math.floor(ms / 3600000)
  const m = Math.floor((ms % 3600000) / 60000)
  const s = Math.floor((ms % 60000) / 1000)
  const msRem = ms % 1000
  return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')},${msRem.toString().padStart(3, '0')}`
}

/**
 * Convert Subtitle array back to SRT string
 */
export function subtitlesToSRT(subtitles: Subtitle[]): string {
  return subtitles.map((sub, i) => {
    const text = sub.speaker ? `[${sub.speaker}] ${sub.text}` : sub.text
    return `${i + 1}\n${formatSRTTime(sub.startTime)} --> ${formatSRTTime(sub.endTime)}\n${text}`
  }).join('\n\n')
}

/**
 * Find subtitle at given time (in milliseconds)
 */
export function findSubtitleAtTime(subtitles: Subtitle[], timeMs: number): Subtitle | undefined {
  return subtitles.find(sub => timeMs >= sub.startTime && timeMs <= sub.endTime)
}
