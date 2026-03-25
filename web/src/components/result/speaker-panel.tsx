import { useMemo } from "react"
import type { Subtitle } from "@/lib/srt"
import { getSpeakerColor, extractSpeakers, formatSpeakerLabel } from "@/lib/srt"
import { formatDuration } from "@/lib/format"

interface SpeakerPanelProps {
  subtitles: Subtitle[]
  duration: number // seconds
  currentTime: number // seconds
  onSeek: (timeMs: number) => void
}

interface SpeakerInfo {
  name: string
  color: string
  totalMs: number
  percentage: number
  segments: { startTime: number; endTime: number }[]
}

export function SpeakerPanel({ subtitles, duration, currentTime, onSeek }: SpeakerPanelProps) {
  const durationMs = duration * 1000

  const speakers = useMemo(() => {
    const names = extractSpeakers(subtitles)
    if (names.length === 0) return []

    const totalDuration = subtitles.reduce(
      (acc, sub) => acc + (sub.endTime - sub.startTime),
      0,
    )

    return names.map((name): SpeakerInfo => {
      const segs = subtitles.filter((s) => s.speaker === name)
      const totalMs = segs.reduce((acc, s) => acc + (s.endTime - s.startTime), 0)
      return {
        name,
        color: getSpeakerColor(name),
        totalMs,
        percentage: totalDuration > 0 ? (totalMs / totalDuration) * 100 : 0,
        segments: segs.map((s) => ({ startTime: s.startTime, endTime: s.endTime })),
      }
    }).sort((a, b) => a.segments[0].startTime - b.segments[0].startTime)
  }, [subtitles])

  if (speakers.length === 0 || durationMs === 0) return null

  const handleBarClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const fraction = (e.clientX - rect.left) / rect.width
    onSeek(fraction * durationMs)
  }

  const playheadPct = durationMs > 0 ? ((currentTime * 1000) / durationMs) * 100 : 0

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
        说话人
      </h3>
      <div className="space-y-3">
        {speakers.map((s) => (
          <div key={s.name} className="flex items-center gap-2.5">
            <span
              className="text-xs font-medium shrink-0"
              style={{ color: s.color, minWidth: "2em" }}
            >
              {formatSpeakerLabel(s.name)}
            </span>
            <div
              className="relative flex-1 h-4 bg-muted rounded-sm cursor-pointer overflow-hidden"
              onClick={handleBarClick}
            >
              {s.segments.map((seg, i) => {
                const left = (seg.startTime / durationMs) * 100
                const width = ((seg.endTime - seg.startTime) / durationMs) * 100
                return (
                  <div
                    key={i}
                    className="absolute top-0 h-full opacity-80 hover:opacity-100 transition-opacity"
                    style={{
                      left: `${left}%`,
                      width: `${Math.max(width, 0.3)}%`,
                      backgroundColor: s.color,
                    }}
                  />
                )
              })}
              {/* Playhead */}
              <div
                className="absolute top-0 h-full w-px bg-foreground/50 z-10 pointer-events-none"
                style={{ left: `${playheadPct}%` }}
              />
            </div>
            {/* Fixed width for "H:MM:SS (NN%)" — ~11ch at tabular-nums */}
            <span className="text-xs text-muted-foreground shrink-0 tabular-nums text-right" style={{ width: "11ch" }}>
              {formatDuration(s.totalMs / 1000)} ({s.percentage.toFixed(0)}%)
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
