import { STATUS_FILTER_OPTIONS, type StatusFilter } from "@/lib/archive-filters"
import { cn } from "@/lib/utils"
import type { ArchiveFacets } from "@/repositories/archive-types"

interface StatusChipsProps {
  status: StatusFilter
  onChange: (status: StatusFilter) => void
  counts?: ArchiveFacets["status"] | null
  className?: string
}

/** Library status filter. Empty buckets are hidden (unless selected) so the row stays short. */
export function StatusChips({ status, onChange, counts, className }: StatusChipsProps) {
  return (
    <div className={cn("flex min-w-0 items-center gap-1.5 overflow-x-auto", className)} role="group" aria-label="按状态筛选">
      {STATUS_FILTER_OPTIONS.map((option) => {
        const count = counts?.[option.value]
        if (option.value !== "all" && option.value !== status && counts && !count) return null
        const active = status === option.value
        const attention = option.value === "failed" && !active && Boolean(count)
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            title={option.hint}
            onClick={() => onChange(option.value)}
            className={cn(
              "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full border px-3 text-[13px] transition-colors",
              active
                ? "border-primary bg-primary/10 font-medium text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
              attention && "border-destructive/40 text-destructive hover:text-destructive",
            )}
          >
            {attention && <span className="size-1.5 rounded-full bg-destructive" aria-hidden="true" />}
            {option.label}
            {typeof count === "number" && <span className="text-xs tabular-nums opacity-75">{count}</span>}
          </button>
        )
      })}
    </div>
  )
}
