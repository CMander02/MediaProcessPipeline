import { memo } from "react"
import type { Subtitle } from "@/lib/srt"
import { getSpeakerColor } from "@/lib/srt"
import { formatTimeShort } from "@/lib/format"

interface TranscriptSegmentProps {
  subtitle: Subtitle
  isActive: boolean
  searchQuery: string
  onClick: () => void
}

export const TranscriptSegment = memo(function TranscriptSegment({
  subtitle,
  isActive,
  searchQuery,
  onClick,
}: TranscriptSegmentProps) {
  const speakerColor = subtitle.speaker ? getSpeakerColor(subtitle.speaker) : undefined

  return (
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
        {/* Speaker tag */}
        {subtitle.speaker && (
          <span
            className="text-xs font-medium mr-2 px-1.5 py-0.5 rounded"
            style={{
              color: speakerColor,
              backgroundColor: speakerColor ? `${speakerColor}15` : undefined,
            }}
          >
            {subtitle.speaker}
          </span>
        )}

        {/* Text */}
        <span className={`text-sm leading-relaxed ${isActive ? "text-foreground font-medium" : ""}`}>
          {searchQuery ? highlightText(subtitle.text, searchQuery) : subtitle.text}
        </span>
      </div>
    </div>
  )
})

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
