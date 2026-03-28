import { Fragment, useMemo, useState, useRef, useEffect, type KeyboardEvent } from "react"
import type { Subtitle } from "@/lib/srt"
import { getSpeakerColor, extractSpeakers, formatSpeakerLabel } from "@/lib/srt"
import { formatDuration } from "@/lib/format"

interface SpeakerPanelProps {
  subtitles: Subtitle[]
  duration: number // seconds
  currentTime: number // seconds
  onSeek: (timeMs: number) => void
  onRenameSpeaker?: (oldName: string, newName: string) => void
}

interface SpeakerInfo {
  name: string
  color: string
  totalMs: number
  percentage: number
  segments: { startTime: number; endTime: number }[]
}

export function SpeakerPanel({ subtitles, duration, currentTime, onSeek, onRenameSpeaker }: SpeakerPanelProps) {
  const durationMs = duration * 1000
  const [editingSpeaker, setEditingSpeaker] = useState<string | null>(null)
  const [editValue, setEditValue] = useState("")
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editingSpeaker) {
      setEditValue(editingSpeaker)
      setTimeout(() => {
        inputRef.current?.focus()
        inputRef.current?.select()
      }, 30)
    }
  }, [editingSpeaker])

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

  const handleSaveRename = () => {
    const newName = editValue.trim()
    if (newName && editingSpeaker && newName !== editingSpeaker) {
      onRenameSpeaker?.(editingSpeaker, newName)
    }
    setEditingSpeaker(null)
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault()
      handleSaveRename()
    } else if (e.key === "Escape") {
      setEditingSpeaker(null)
    }
  }

  const playheadPct = durationMs > 0 ? ((currentTime * 1000) / durationMs) * 100 : 0

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
        说话人
      </h3>
      <div
        className="grid gap-y-3 gap-x-2.5 items-center"
        style={{ gridTemplateColumns: "auto 1fr auto" }}
      >
        {speakers.map((s) => (
          <Fragment key={s.name}>
            {editingSpeaker === s.name ? (
              <input
                ref={inputRef}
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                onKeyDown={handleKeyDown}
                onBlur={handleSaveRename}
                className="text-xs font-medium rounded border bg-background px-1 py-0.5 outline-none focus:ring-1 focus:ring-primary"
                style={{ color: s.color }}
              />
            ) : (
              <button
                className="text-xs font-medium hover:underline cursor-pointer text-left truncate max-w-[10rem]"
                style={{ color: s.color }}
                onClick={() => setEditingSpeaker(s.name)}
                title="点击编辑说话人名称"
              >
                {formatSpeakerLabel(s.name)}
              </button>
            )}
            <div
              className="relative h-4 bg-muted rounded-sm cursor-pointer overflow-hidden"
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
              <div
                className="absolute top-0 h-full w-px bg-foreground/50 z-10 pointer-events-none"
                style={{ left: `${playheadPct}%` }}
              />
            </div>
            <span className="text-xs text-muted-foreground tabular-nums text-right whitespace-nowrap">
              {formatDuration(s.totalMs / 1000)} ({s.percentage.toFixed(0)}%)
            </span>
          </Fragment>
        ))}
      </div>
    </div>
  )
}
