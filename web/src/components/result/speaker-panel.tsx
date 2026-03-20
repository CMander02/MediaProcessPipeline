import { useMemo } from "react"
import type { Subtitle } from "@/lib/srt"
import { getSpeakerColor, extractSpeakers } from "@/lib/srt"
import { formatDuration } from "@/lib/format"
import { User } from "lucide-react"

interface SpeakerPanelProps {
  subtitles: Subtitle[]
}

interface SpeakerInfo {
  name: string
  color: string
  totalMs: number
  percentage: number
  segmentCount: number
}

export function SpeakerPanel({ subtitles }: SpeakerPanelProps) {
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
        segmentCount: segs.length,
      }
    }).sort((a, b) => b.totalMs - a.totalMs)
  }, [subtitles])

  if (speakers.length === 0) return null

  return (
    <div className="space-y-3">
      <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
        说话人
      </h3>
      <div className="space-y-2">
        {speakers.map((s) => (
          <div key={s.name} className="flex items-center gap-2.5">
            <div
              className="w-7 h-7 rounded-full flex items-center justify-center shrink-0"
              style={{ backgroundColor: `${s.color}20` }}
            >
              <User className="w-3.5 h-3.5" style={{ color: s.color }} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between text-sm">
                <span className="font-medium truncate" style={{ color: s.color }}>
                  {s.name}
                </span>
                <span className="text-xs text-muted-foreground ml-2 shrink-0">
                  {formatDuration(s.totalMs / 1000)} ({s.percentage.toFixed(0)}%)
                </span>
              </div>
              <div className="h-1.5 bg-muted rounded-full mt-1 overflow-hidden">
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${s.percentage}%`,
                    backgroundColor: s.color,
                  }}
                />
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
