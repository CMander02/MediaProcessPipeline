import type { ReactNode } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { PlusSignCircleIcon } from "@hugeicons/core-free-icons"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

export interface FacetOption<T extends string> {
  value: T
  label: string
  icon?: ReactNode
  hint?: string
}

interface FacetFilterProps<T extends string> {
  title: string
  options: ReadonlyArray<FacetOption<T>>
  selected: T[]
  onChange: (next: T[]) => void
  /** Number of cards each option would show, when known */
  counts?: Partial<Record<string, number>> | null
  /** Extra toggles below the options (e.g. the duplicates switch) */
  extra?: ReactNode
  /** Also counts as a selection on the button, e.g. an active extra toggle */
  extraLabel?: string | null
  className?: string
}

/**
 * shadcn-style faceted filter: a dashed button that lists what is picked, opening a checklist
 * with counts. Values within one facet are OR-ed; facets combine with AND.
 */
export function FacetFilter<T extends string>({
  title, options, selected, onChange, counts, extra, extraLabel, className,
}: FacetFilterProps<T>) {
  const picked = options.filter((option) => selected.includes(option.value))
  const labels = [...picked.map((option) => option.label), ...(extraLabel ? [extraLabel] : [])]
  const active = labels.length > 0

  const toggle = (value: T) => {
    const next = selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value]
    // Keep the order the options are listed in, so URLs and labels stay stable.
    onChange(options.map((option) => option.value).filter((item) => next.includes(item)))
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className={cn("h-8 shrink-0 gap-1.5 px-2.5 text-[13px] font-normal", !active && "border-dashed", className)}
        >
          <HugeiconsIcon icon={PlusSignCircleIcon} className="size-3.5" />
          {title}
          {active && (
            <>
              <span className="mx-0.5 h-4 w-px bg-border" aria-hidden="true" />
              {labels.length > 2 ? (
                <span className="rounded bg-secondary px-1.5 py-px text-xs">{labels.length} 项</span>
              ) : labels.map((label) => (
                <span key={label} className="rounded bg-secondary px-1.5 py-px text-xs">{label}</span>
              ))}
            </>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-52">
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">{title}（可多选）</DropdownMenuLabel>
        {options.map((option) => (
          <DropdownMenuCheckboxItem
            key={option.value}
            checked={selected.includes(option.value)}
            onCheckedChange={() => toggle(option.value)}
            // Keep the menu open so several values can be picked in a row.
            onSelect={(event) => event.preventDefault()}
            title={option.hint}
          >
            {option.icon}
            <span className="flex-1">{option.label}</span>
            {counts && <span className="pl-4 text-xs tabular-nums text-muted-foreground">{counts[option.value] ?? 0}</span>}
          </DropdownMenuCheckboxItem>
        ))}
        {extra && (
          <>
            <DropdownMenuSeparator />
            {extra}
          </>
        )}
        {active && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem className="justify-center text-muted-foreground" onClick={() => onChange([])}>
              清除{title}
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
