import { useMemo } from "react"
import type { Subtitle } from "@/lib/srt"
import { getSpeakerColor, extractSpeakers, formatSpeakerLabel } from "@/lib/srt"

interface SpeakerTimelineProps {
  subtitles: Subtitle[]
  duration: number // seconds
  currentTime: number // seconds
  onSeek: (timeMs: number) => void
}

export function SpeakerTimeline({
  subtitles,
  duration,
  currentTime,
  onSeek,
}: SpeakerTimelineProps) {
  const speakers = useMemo(() => extractSpeakers(subtitles), [subtitles])
  const durationMs = duration * 1000

  if (speakers.length === 0 || durationMs === 0) return null

  const handleClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const fraction = (e.clientX - rect.left) / rect.width
    onSeek(fraction * durationMs)
  }

  const playheadPosition = durationMs > 0 ? (currentTime * 1000) / durationMs : 0

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
        时间线
      </h3>
      <div
        className="relative h-6 bg-muted rounded cursor-pointer overflow-hidden"
        onClick={handleClick}
      >
        {/* Speaker segments */}
        {subtitles.map((sub) => {
          if (!sub.speaker) return null
          const left = (sub.startTime / durationMs) * 100
          const width = ((sub.endTime - sub.startTime) / durationMs) * 100
          return (
            <div
              key={sub.index}
              className="absolute top-0 h-full opacity-70 hover:opacity-100 transition-opacity"
              style={{
                left: `${left}%`,
                width: `${Math.max(width, 0.3)}%`,
                backgroundColor: getSpeakerColor(sub.speaker),
              }}
            />
          )
        })}

        {/* Playhead */}
        <div
          className="absolute top-0 h-full w-0.5 bg-foreground z-10 pointer-events-none"
          style={{ left: `${playheadPosition * 100}%` }}
        />
      </div>

      {/* Speaker legend */}
      <div className="flex flex-wrap gap-3">
        {speakers.map((name) => (
          <div key={name} className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <div
              className="w-2.5 h-2.5 rounded-sm"
              style={{ backgroundColor: getSpeakerColor(name) }}
            />
            <span>{formatSpeakerLabel(name)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
