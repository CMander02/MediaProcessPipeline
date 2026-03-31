import { useState } from "react"

interface SummaryData {
  tldr: string
  key_facts: string[]
}

export function SummaryView({ summary }: { summary: SummaryData | null }) {
  const [open, setOpen] = useState(true)
  if (!summary) return null
  return (
    <div className="border-b">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium hover:bg-gray-50"
      >
        <span className="text-xs text-gray-400">{open ? "▾" : "▸"}</span>
        Summary
      </button>
      {open && (
        <div className="space-y-2 px-3 pb-3">
          <p className="text-sm leading-relaxed text-gray-700">{summary.tldr}</p>
          {summary.key_facts.length > 0 && (
            <ul className="space-y-1">
              {summary.key_facts.map((fact, i) => (
                <li key={i} className="flex gap-2 text-xs leading-relaxed text-gray-600">
                  <span className="mt-0.5 shrink-0 text-gray-300">•</span>
                  {fact}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
