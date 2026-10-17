import { Fragment, useMemo, useState } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { AiMagicIcon, ArrowLeft01Icon, ArrowRight01Icon, Loading03Icon } from "@hugeicons/core-free-icons"
import type { Subtitle, SpeakerHints, SpeakerQuality } from "@/lib/srt"
import { getSpeakerColor, extractSpeakers, findSubtitleAtTime, formatSpeakerLabel, subtitleCoverageMs } from "@/lib/srt"
import { formatDuration } from "@/lib/format"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
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
  quality?: SpeakerQuality | null
  hints?: SpeakerHints
  /** Decide pending lines and name anonymous voices in the background */
  onAutoFill?: () => void
  autoFill?: { busy: boolean; error?: string | null }
  /** Summary and mind map were written before the latest name changes */
  stale?: boolean
  onRegenerate?: () => void
  regenerating?: boolean
}

interface SpeakerInfo {
  name: string
  color: string
  totalMs: number
  percentage: number
  segments: { startTime: number; endTime: number }[]
}

const PENDING_PATTERN = "repeating-linear-gradient(135deg, currentColor 0 2px, transparent 2px 5px)"

function coverageShare(value: number): string {
  return value > 0 && value < 1 ? "<1%" : `${value.toFixed(0)}%`
}

function isAnonymous(name: string): boolean {
  return /^unknown[-_]/i.test(name) || /^SPEAKER_\d+$/i.test(name)
}

export function SpeakerPanel({
  subtitles, duration, currentTime, onSeek, onRenameSpeaker,
  renaming = false, editingDisabled = false, compact = false, collapsed, onCollapsedChange, quality,
  hints, onAutoFill, autoFill, stale = false, onRegenerate, regenerating = false,
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

    const coverage = new Map(names.map((name) => [name, subtitleCoverageMs(subtitles.filter((s) => s.speaker === name))]))
    const totalDuration = [...coverage.values()].reduce((sum, value) => sum + value, 0)

    return names.map((name): SpeakerInfo => {
      const segs = subtitles.filter((s) => s.speaker === name)
      const totalMs = coverage.get(name) ?? 0
      return {
        name,
        color: getSpeakerColor(name, names),
        totalMs,
        percentage: totalDuration > 0 ? (totalMs / totalDuration) * 100 : 0,
        segments: segs.map((s) => ({ startTime: s.startTime, endTime: s.endTime })),
      }
    }).sort((a, b) => a.segments[0].startTime - b.segments[0].startTime)
  }, [subtitles])

  // Lines nobody is assigned to, read from the transcript itself so edits count at once.
  const pending = useMemo(
    () => subtitles.filter((s) => s.speaker?.toLowerCase() === "unknown").map((s) => ({ startTime: s.startTime, endTime: s.endTime })),
    [subtitles],
  )
  const pendingMs = useMemo(() => subtitleCoverageMs(pending.map((range, index) => ({ index, text: "", ...range }))), [pending])
  const nowMs = currentTime * 1000
  const nextPending = pending.findIndex((range) => range.startTime > nowMs + 500)
  const atPending = pending.findIndex((range) => range.startTime <= nowMs + 500 && range.endTime > nowMs)
  const pendingPosition = atPending >= 0 ? atPending : -1

  const names = new Set(speakers.map((speaker) => speaker.name))
  const anonymousCount = speakers.filter((speaker) => isAnonymous(speaker.name)).length
  const suggestionFor = (name: string) => hints?.suggestions.find((row) => row.source_label === name && names.has(row.current_name))
  // Names offered while renaming: people the source mentions, then the other voices (renaming to one merges).
  const nameOptions = (current: string) => [
    ...(hints?.participants ?? []).filter((name) => !names.has(name)),
    ...speakers.map((speaker) => speaker.name).filter((name) => name !== current && !isAnonymous(name)),
  ]

  const busy = Boolean(autoFill?.busy)
  const canAutoFill = Boolean(onAutoFill) && !editingDisabled && (pending.length > 0 || anonymousCount > 0)
  const statusLine = (
    <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground" role="status">
      <span>{quality?.target_count ? `目标 ${quality.target_count} 人 / 已分配 ${speakers.length} 人` : `${speakers.length} 位说话人`}</span>
      {pending.length > 0 && (
        <span className="inline-flex items-center gap-1">
          <span>· {pending.length} 段待定</span>
          <span className="inline-flex items-center" role="group" aria-label="逐段查看待定字幕">
            <button type="button" aria-label="上一段待定字幕" className="rounded p-0.5 hover:bg-muted hover:text-foreground"
              onClick={() => {
                const before = pending.map((range, index) => ({ range, index })).filter(({ range }) => range.startTime < nowMs - 500).at(-1)
                onSeek((before ?? { range: pending.at(-1)! }).range.startTime)
              }}>
              <HugeiconsIcon icon={ArrowLeft01Icon} className="size-3.5" />
            </button>
            {pendingPosition >= 0 && <span className="tabular-nums">{pendingPosition + 1}/{pending.length}</span>}
            <button type="button" aria-label="下一段待定字幕" className="rounded p-0.5 hover:bg-muted hover:text-foreground"
              onClick={() => onSeek(pending[nextPending >= 0 ? nextPending : 0].startTime)}>
              <HugeiconsIcon icon={ArrowRight01Icon} className="size-3.5" />
            </button>
          </span>
        </span>
      )}
      {quality?.reason && quality.status === "constraint_conflict" && <span>· {quality.reason}</span>}
      {(canAutoFill || busy) && (
        <Button type="button" size="sm" variant="outline" className="ml-auto h-6 gap-1 px-2 text-xs"
          disabled={busy || !canAutoFill} onClick={onAutoFill}
          title="按前后文判断待定字幕，用标题、上传者等来源信息给匿名声音起名。你改过的名字不会变。">
          <HugeiconsIcon icon={busy ? Loading03Icon : AiMagicIcon} className={cn("size-3.5", busy && "animate-spin")} />
          {busy ? "正在补全人物…" : "自动补全"}
        </Button>
      )}
      {autoFill?.error && !busy && <span className="basis-full text-destructive">上次补全没有完成：{autoFill.error}</span>}
    </div>
  )
  const staleNotice = stale && onRegenerate ? (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md bg-amber-500/10 px-2.5 py-1.5 text-xs text-amber-900 dark:text-amber-200">
      <span className="min-w-0 flex-1">人物改过了，摘要和导图还是按原来的名字写的。改完再更新也可以。</span>
      <Button type="button" size="sm" variant="outline" className="h-6 shrink-0 gap-1 bg-background px-2 text-xs" disabled={regenerating} onClick={onRegenerate}>
        {regenerating && <HugeiconsIcon icon={Loading03Icon} className="size-3.5 animate-spin" />}
        更新摘要和导图
      </Button>
    </div>
  ) : null
  if (speakers.length === 0 || durationMs === 0) return <div className="space-y-2">{statusLine}{staleNotice}</div>

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

  const playheadPct = durationMs > 0 ? (nowMs / durationMs) * 100 : 0

  if (collapsible && !expanded) {
    const speaking = findSubtitleAtTime(subtitles, nowMs)?.speaker
    // Tapping a chip jumps to that person's next turn, so a conversation can be skimmed by speaker.
    const nextTurn = (speaker: SpeakerInfo) =>
      (speaker.segments.find((segment) => segment.startTime > nowMs + 500) ?? speaker.segments[0]).startTime
    return (
      <div className="space-y-1">
      {statusLine}
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
              <span className="tabular-nums text-muted-foreground">{coverageShare(speaker.percentage)}</span>
            </button>
          ))}
        </div>
        <button type="button" onClick={() => setExpanded(true)} className="h-7 shrink-0 px-1.5 text-xs text-primary">
          时间轴
        </button>
      </div>
      {staleNotice}
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-base font-semibold text-foreground" title="时长按每位说话人的字幕时间并集统计">说话人 · 字幕覆盖时长</h3>
        {collapsible && (
          <button type="button" onClick={() => setExpanded(false)} className="h-7 px-1.5 text-xs text-primary">
            收起
          </button>
        )}
      </div>
      {statusLine}
      <div
        className="grid gap-y-3 gap-x-2.5 items-center"
        style={{ gridTemplateColumns: "auto 1fr auto" }}
      >
        {speakers.map((s) => {
          const suggestion = suggestionFor(s.name)
          return (
          <Fragment key={s.name}>
            {editingSpeaker === s.name ? (
              <SpeakerNameEditor
                initialValue={isAnonymous(s.name) ? "" : s.name}
                label={`${formatSpeakerLabel(s.name)} 的名字`}
                options={nameOptions(s.name)}
                color={s.color}
                onSave={handleSaveRename}
                onCancel={() => setEditingSpeaker(null)}
              />
            ) : (
              <div className="flex min-w-0 max-w-[12rem] flex-col items-start gap-0.5">
                <span className="flex min-w-0 max-w-full items-center gap-1">
                  <button
                    type="button"
                    disabled={renaming || editingDisabled}
                    className="min-w-0 truncate text-left text-xs font-medium hover:underline cursor-pointer"
                    style={{ color: s.color }}
                    onClick={() => setEditingSpeaker(s.name)}
                    title={isAnonymous(s.name) ? "给这个声音起名" : "点击编辑说话人名称"}
                  >
                    {formatSpeakerLabel(s.name)}
                  </button>
                </span>
                {suggestion && (
                  <button type="button" disabled={renaming || editingDisabled}
                    className="max-w-full truncate text-left text-[11px] text-primary hover:underline disabled:opacity-50"
                    title={`声音分离可能把同一个人分成了两个声音${suggestion.reason ? `（${suggestion.reason}）` : ""}。合并后这些字幕都归到「${formatSpeakerLabel(suggestion.current_name)}」。`}
                    onClick={() => onRenameSpeaker?.(s.name, suggestion.current_name)}>
                    声音很像 {formatSpeakerLabel(suggestion.current_name)}，合并？
                  </button>
                )}
              </div>
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
              {formatDuration(s.totalMs / 1000)} ({coverageShare(s.percentage)})
            </span>
          </Fragment>
          )
        })}
        {pending.length > 0 && (
          <>
            <span className="text-xs italic text-muted-foreground" title="还没有确定是谁说的字幕；点时间轴跳过去，在字幕里点人物标签修改">待定</span>
            <div className="relative h-4 cursor-pointer overflow-hidden rounded-sm bg-muted" onClick={handleBarClick} aria-label="待定字幕位置">
              {pending.map((range, index) => (
                <div
                  key={index}
                  className="absolute top-0 h-full text-amber-600/80 dark:text-amber-400/80"
                  style={{
                    left: `${(range.startTime / durationMs) * 100}%`,
                    width: `${Math.max(((range.endTime - range.startTime) / durationMs) * 100, 0.4)}%`,
                    backgroundImage: PENDING_PATTERN,
                  }}
                />
              ))}
              <div className="pointer-events-none absolute top-0 z-10 h-full w-px bg-foreground/50" style={{ left: `${playheadPct}%` }} />
            </div>
            <span className="text-xs text-muted-foreground tabular-nums text-right whitespace-nowrap">{formatDuration(pendingMs / 1000)}</span>
          </>
        )}
      </div>
      {staleNotice}
    </div>
  )
}
