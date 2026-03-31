import { useState } from "react"

export function OutlineView({ outline }: { outline: string | null }) {
  const [open, setOpen] = useState(true)
  if (!outline) return null
  const lines = outline.split("\n").filter((l) => l.trim())
  return (
    <div className="border-b">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium hover:bg-gray-50"
      >
        <span className="text-xs text-gray-400">{open ? "▾" : "▸"}</span>
        Outline
      </button>
      {open && (
        <div className="space-y-0.5 px-3 pb-3">
          {lines.map((line, i) => {
            const stripped = line.replace(/^[\s]*[-*]\s*/, "")
            const indent = (line.length - line.trimStart().length) / 2
            return (
              <p
                key={i}
                className="text-xs leading-relaxed text-gray-600"
                style={{ paddingLeft: `${indent * 16}px` }}
              >
                {stripped}
              </p>
            )
          })}
        </div>
      )}
    </div>
  )
}
