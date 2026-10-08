import { Fragment, useMemo, useState } from "react"
import type { Subtitle } from "@/lib/srt"
import { getSpeakerColor, extractSpeakers, findSubtitleAtTime, formatSpeakerLabel } from "@/lib/srt"
import { formatDuration } from "@/lib/format"
import { cn } from "@/lib/utils"
import { SpeakerNameEditor } from "./speaker-name-editor"

interface SpeakerPanelProps {
  subtitles: Subtitle[]
  duration: number // seconds
  currentTime: number // seconds
  onSeek: (timeMs: number) => void
  onRenameSpeaker?: (oldName: string, newName: string) => void
  renaming?: boolean
  editingDisabled?: boolean
  /** Phones: one row of speaker chips; the full timeline opens on demand. */
  compact?: boolean
  /** Desktop: collapsed to the chips row; the choice is kept by the caller */
  collapsed?: boolean
  onCollapsedChange?: (collapsed: boolean) => void
}

interface SpeakerInfo {
  name: string
  color: string
  totalMs: number
  percentage: number
  segments: { startTime: number; endTime: number }[]
}

export function SpeakerPanel({
  subtitles, duration, currentTime, onSeek, onRenameSpeaker,
  renaming = false, editingDisabled = false, compact = false, collapsed, onCollapsedChange,
}: SpeakerPanelProps) {
  const [expandedLocal, setExpandedLocal] = useState(false)
  const controlled = onCollapsedChange !== undefined
  const collapsible = controlled || compact
  const expanded = controlled ? !collapsed : expandedLocal
  const setExpanded = (value: boolean) => (controlled ? onCollapsedChange(!value) : setExpandedLocal(value))
  const durationMs = duration * 1000 || subtitles.at(-1)?.endTime || 0
  const [editingSpeaker, setEditingSpeaker] = useState<string | null>(null)
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
        color: getSpeakerColor(name, names),
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

  const handleSaveRename = (newName: string) => {
    if (newName && editingSpeaker && newName !== editingSpeaker) {
      onRenameSpeaker?.(editingSpeaker, newName)
    }
    setEditingSpeaker(null)
  }

  const playheadPct = durationMs > 0 ? ((currentTime * 1000) / durationMs) * 100 : 0

  if (collapsible && !expanded) {
    const nowMs = currentTime * 1000
    const speaking = findSubtitleAtTime(subtitles, nowMs)?.speaker
    // Tapping a chip jumps to that person's next turn, so a conversation can be skimmed by speaker.
    const nextTurn = (speaker: SpeakerInfo) =>
      (speaker.segments.find((segment) => segment.startTime > nowMs + 500) ?? speaker.segments[0]).startTime
    return (
      <div className="flex min-w-0 items-center gap-1.5">
        <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto" role="group" aria-label="说话人">
          {speakers.map((speaker) => (
            <button
              key={speaker.name}
              type="button"
              onClick={() => onSeek(nextTurn(speaker))}
              title={`跳到 ${formatSpeakerLabel(speaker.name)} 的下一次发言`}
              className={cn(
                "inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full border px-2.5 text-xs transition-colors",
                speaking === speaker.name ? "border-foreground/40 bg-muted" : "hover:bg-muted/60",
              )}
            >
              <span className="size-2 shrink-0 rounded-full" style={{ backgroundColor: speaker.color }} />
              <span className="max-w-24 truncate">{formatSpeakerLabel(speaker.name)}</span>
              <span className="tabular-nums text-muted-foreground">{speaker.percentage.toFixed(0)}%</span>
            </button>
          ))}
        </div>
        <button type="button" onClick={() => setExpanded(true)} className="h-7 shrink-0 px-1.5 text-xs text-primary">
          时间轴
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-base font-semibold text-foreground">说话人</h3>
        {collapsible && (
          <button type="button" onClick={() => setExpanded(false)} className="h-7 px-1.5 text-xs text-primary">
            收起
          </button>
        )}
      </div>
      <div
        className="grid gap-y-3 gap-x-2.5 items-center"
        style={{ gridTemplateColumns: "auto 1fr auto" }}
      >
        {speakers.map((s) => (
          <Fragment key={s.name}>
            {editingSpeaker === s.name ? (
              <SpeakerNameEditor
                initialValue={s.name}
                label={`${formatSpeakerLabel(s.name)} 的名字`}
                options={speakers.filter((speaker) => speaker.name !== s.name).map((speaker) => speaker.name)}
                color={s.color}
                onSave={handleSaveRename}
                onCancel={() => setEditingSpeaker(null)}
              />
            ) : (
              <button
                type="button"
                disabled={renaming || editingDisabled}
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
