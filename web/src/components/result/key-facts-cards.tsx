import { HugeiconsIcon } from "@hugeicons/react"
import { BulbIcon } from "@hugeicons/core-free-icons"

interface KeyFactsCardsProps {
  facts: string[]
}

export function KeyFactsCards({ facts }: KeyFactsCardsProps) {
  if (facts.length === 0) return null

  return (
    <div className="space-y-2">
      <h3 className="flex items-center gap-1.5 text-base font-semibold text-foreground">
        <HugeiconsIcon icon={BulbIcon} className="w-3.5 h-3.5" />
        核心要点
      </h3>
      <div className="grid gap-2 sm:grid-cols-2">
        {facts.map((fact, i) => (
          <div
            key={i}
            className="border-t py-3 text-sm leading-relaxed first:border-t-0"
          >
            <span className="inline-flex w-5 items-center justify-center text-xs font-semibold text-muted-foreground mr-2">
              {i + 1}
            </span>
            {fact}
          </div>
        ))}
      </div>
    </div>
  )
}
