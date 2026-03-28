import { memo, useState, useRef, useEffect, type MouseEvent } from "react"
import type { Subtitle } from "@/lib/srt"
import { getSpeakerColor, formatSpeakerLabel, formatSRTTime } from "@/lib/srt"
import { formatTimeShort } from "@/lib/format"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { HugeiconsIcon } from "@hugeicons/react"
import { PencilEdit01Icon, Delete01Icon, ArrowUp01Icon, ArrowDown01Icon, Copy01Icon, Tick02Icon, Cancel01Icon } from "@hugeicons/core-free-icons"

interface TranscriptSegmentProps {
  subtitle: Subtitle
  isActive: boolean
  searchQuery: string
  editing: boolean
  speakers: string[]
  onClick: () => void
  onEdit: (updated: Partial<Subtitle>) => void
  onDelete: () => void
  onInsert: (position: "above" | "below") => void
  onEditStart: () => void
  onEditCancel: () => void
}

export const TranscriptSegment = memo(function TranscriptSegment({
  subtitle,
  isActive,
  searchQuery,
  editing,
  speakers,
  onClick,
  onEdit,
  onDelete,
  onInsert,
  onEditStart,
  onEditCancel,
}: TranscriptSegmentProps) {
  const speakerColor = subtitle.speaker ? getSpeakerColor(subtitle.speaker) : undefined

  const [speakerDropdownOpen, setSpeakerDropdownOpen] = useState(false)

  const [editText, setEditText] = useState(subtitle.text)
  const [editStart, setEditStart] = useState(formatTimeInput(subtitle.startTime))
  const [editEnd, setEditEnd] = useState(formatTimeInput(subtitle.endTime))
  const textRef = useRef<HTMLTextAreaElement>(null)

  // Reset edit state when entering edit mode
  useEffect(() => {
    if (editing) {
      setEditText(subtitle.text)
      setEditStart(formatTimeInput(subtitle.startTime))
      setEditEnd(formatTimeInput(subtitle.endTime))
      setTimeout(() => textRef.current?.focus(), 50)
    }
  }, [editing, subtitle])

  const handleCopy = () => {
    navigator.clipboard.writeText(subtitle.text)
  }

  const handleSave = () => {
    onEdit({
      text: editText.trim(),
      startTime: parseTimeInput(editStart),
      endTime: parseTimeInput(editEnd),
    })
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSave()
    } else if (e.key === "Escape") {
      onEditCancel()
    }
  }

  if (editing) {
    return (
      <div className="px-3 py-2 rounded-md border border-primary/30 bg-primary/5 space-y-2">
        {/* Time inputs */}
        <div className="flex items-center gap-2 text-xs">
          <input
            value={editStart}
            onChange={(e) => setEditStart(e.target.value)}
            onKeyDown={handleKeyDown}
            className="w-20 rounded border bg-background px-1.5 py-0.5 font-mono text-xs"
            placeholder="0:00.0"
          />
          <span className="text-muted-foreground">→</span>
          <input
            value={editEnd}
            onChange={(e) => setEditEnd(e.target.value)}
            onKeyDown={handleKeyDown}
            className="w-20 rounded border bg-background px-1.5 py-0.5 font-mono text-xs"
            placeholder="0:00.0"
          />
          {subtitle.speaker && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  className="text-xs font-medium px-1.5 py-0.5 rounded ml-1 cursor-pointer hover:ring-1 hover:ring-current transition-all"
                  style={{
                    color: speakerColor,
                    backgroundColor: speakerColor ? `${speakerColor}15` : undefined,
                  }}
                >
                  {formatSpeakerLabel(subtitle.speaker)}
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                {speakers.map((spk) => (
                  <DropdownMenuItem
                    key={spk}
                    onClick={() => onEdit({ speaker: spk })}
                    className="flex items-center gap-2"
                  >
                    <span
                      className="inline-block w-2 h-2 rounded-full shrink-0"
                      style={{ backgroundColor: getSpeakerColor(spk) }}
                    />
                    <span style={{ color: getSpeakerColor(spk) }} className="font-medium">
                      {formatSpeakerLabel(spk)}
                    </span>
                    {spk === subtitle.speaker && <HugeiconsIcon icon={Tick02Icon} className="h-3 w-3 ml-auto" />}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          <div className="flex-1" />
          <button onClick={handleSave} className="p-1 rounded hover:bg-emerald-100 text-emerald-600" title="保存">
            <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5" />
          </button>
          <button onClick={onEditCancel} className="p-1 rounded hover:bg-muted text-muted-foreground" title="取消">
            <HugeiconsIcon icon={Cancel01Icon} className="h-3.5 w-3.5" />
          </button>
        </div>
        {/* Text edit */}
        <textarea
          ref={textRef}
          value={editText}
          onChange={(e) => setEditText(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={Math.max(1, Math.ceil(editText.length / 50))}
          className="w-full rounded border bg-background px-2 py-1 text-sm leading-relaxed resize-none outline-none focus:ring-1 focus:ring-primary"
        />
      </div>
    )
  }

  return (
    <ContextMenu>
      <ContextMenuTrigger>
        <div
          className={`group flex gap-3 px-3 py-2.5 rounded-md cursor-pointer transition-colors ${
            isActive
              ? "bg-primary/15 border-l-3 border-primary shadow-sm"
              : "hover:bg-muted/50 border-l-3 border-transparent"
          }`}
          onClick={onClick}
        >
          {/* Timestamp */}
          <button
            className={`text-xs font-mono shrink-0 pt-0.5 transition-colors ${
              isActive ? "text-primary font-semibold" : "text-muted-foreground hover:text-primary"
            }`}
            onClick={(e) => {
              e.stopPropagation()
              onClick()
            }}
          >
            {formatTimeShort(subtitle.startTime)}
          </button>

          <div className="flex-1 min-w-0">
            {/* Speaker tag — click to open dropdown */}
            {subtitle.speaker && (
              <DropdownMenu open={speakerDropdownOpen} onOpenChange={setSpeakerDropdownOpen}>
                <DropdownMenuTrigger asChild>
                  <button
                    className="text-xs font-medium mr-2 px-1.5 py-0.5 rounded cursor-pointer hover:ring-1 hover:ring-current transition-all"
                    style={{
                      color: speakerColor,
                      backgroundColor: speakerColor ? `${speakerColor}15` : undefined,
                    }}
                    onClick={(e) => e.stopPropagation()}
                    title="点击切换说话人"
                  >
                    {formatSpeakerLabel(subtitle.speaker)}
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" onClick={(e) => e.stopPropagation()}>
                  {speakers.map((spk) => (
                    <DropdownMenuItem
                      key={spk}
                      onClick={() => { onEdit({ speaker: spk }); setSpeakerDropdownOpen(false) }}
                      className="flex items-center gap-2"
                    >
                      <span
                        className="inline-block w-2 h-2 rounded-full shrink-0"
                        style={{ backgroundColor: getSpeakerColor(spk) }}
                      />
                      <span style={{ color: getSpeakerColor(spk) }} className="font-medium">
                        {formatSpeakerLabel(spk)}
                      </span>
                      {spk === subtitle.speaker && <HugeiconsIcon icon={Tick02Icon} className="h-3 w-3 ml-auto" />}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            )}

            {/* Text */}
            <span className={`text-sm leading-relaxed ${isActive ? "text-foreground font-medium" : ""}`}>
              {searchQuery ? highlightText(subtitle.text, searchQuery) : subtitle.text}
            </span>
          </div>
        </div>
      </ContextMenuTrigger>

      <ContextMenuContent>
        <ContextMenuItem onClick={onEditStart}>
          <HugeiconsIcon icon={PencilEdit01Icon} className="h-4 w-4" />
          编辑
        </ContextMenuItem>
        <ContextMenuItem onClick={handleCopy}>
          <HugeiconsIcon icon={Copy01Icon} className="h-4 w-4" />
          复制
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem onClick={() => onInsert("above")}>
          <HugeiconsIcon icon={ArrowUp01Icon} className="h-4 w-4" />
          在上方插入
        </ContextMenuItem>
        <ContextMenuItem onClick={() => onInsert("below")}>
          <HugeiconsIcon icon={ArrowDown01Icon} className="h-4 w-4" />
          在下方插入
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem variant="destructive" onClick={onDelete}>
          <HugeiconsIcon icon={Delete01Icon} className="h-4 w-4" />
          删除
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  )
})

/** Format ms to editable time string: M:SS.m */
function formatTimeInput(ms: number): string {
  const totalSec = ms / 1000
  const m = Math.floor(totalSec / 60)
  const s = totalSec % 60
  return `${m}:${s.toFixed(1).padStart(4, "0")}`
}

/** Parse editable time string back to ms. Supports M:SS.m, MM:SS, H:MM:SS */
function parseTimeInput(str: string): number {
  const parts = str.trim().split(":")
  let totalSec = 0
  if (parts.length === 3) {
    totalSec = parseFloat(parts[0]) * 3600 + parseFloat(parts[1]) * 60 + parseFloat(parts[2])
  } else if (parts.length === 2) {
    totalSec = parseFloat(parts[0]) * 60 + parseFloat(parts[1])
  } else {
    totalSec = parseFloat(parts[0]) || 0
  }
  return Math.round(totalSec * 1000)
}

function highlightText(text: string, query: string) {
  if (!query) return text
  const parts = text.split(new RegExp(`(${escapeRegex(query)})`, "gi"))
  return parts.map((part, i) =>
    part.toLowerCase() === query.toLowerCase() ? (
      <mark key={i} className="bg-yellow-200 dark:bg-yellow-800 rounded px-0.5">
        {part}
      </mark>
    ) : (
      part
    ),
  )
}

function escapeRegex(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}
