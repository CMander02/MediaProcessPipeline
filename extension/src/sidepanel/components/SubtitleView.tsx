import { useState } from "react"
import type { SubtitleEntry } from "@/content/types"

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
}

export function SubtitleView({ subtitles }: { subtitles: SubtitleEntry[] }) {
  const [open, setOpen] = useState(false)
  if (subtitles.length === 0) return null
  return (
    <div className="border-b">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium hover:bg-gray-50"
      >
        <span className="text-xs text-gray-400">{open ? "▾" : "▸"}</span>
        Subtitles
        <span className="text-xs font-normal text-gray-400">({subtitles.length})</span>
      </button>
      {open && (
        <div className="max-h-[50vh] overflow-y-auto px-3 pb-3">
          {subtitles.map((sub, i) => (
            <div key={i} className="flex gap-2 py-0.5">
              <span className="shrink-0 text-[10px] tabular-nums text-gray-300">
                {formatTime(sub.start)}
              </span>
              <span className="text-xs leading-relaxed text-gray-600">{sub.text}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
