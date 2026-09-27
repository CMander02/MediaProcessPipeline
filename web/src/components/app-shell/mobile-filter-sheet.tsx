import { Tick02Icon } from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import type { LibraryControls } from "@/hooks/use-library-controls"
import {
  MEDIA_TYPE_OPTIONS,
  SORT_OPTIONS,
  SOURCE_KEY_OPTIONS,
  STATUS_OPTIONS,
} from "@/lib/archive-filters"
import type { ArchiveFacets } from "@/repositories/archive-types"
import { cn } from "@/lib/utils"

type MobileLibrary = Pick<LibraryControls,
  "statuses" | "media" | "sources" | "duplicates" | "sort"
  | "setStatuses" | "setMedia" | "setSources" | "setDuplicates" | "setSort" | "resetFilters">

interface MobileFilterSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  library: MobileLibrary
  facets?: ArchiveFacets | null
}

function FilterOption({
  active,
  label,
  count,
  onClick,
}: {
  active: boolean
  label: string
  count?: number
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "flex min-h-11 w-full min-w-0 items-center justify-between gap-2 rounded-lg border px-3 text-left text-sm transition-colors",
        active
          ? "border-primary bg-primary/10 font-medium text-primary"
          : "border-border bg-background text-foreground active:bg-muted",
      )}
    >
      <span className="truncate">{label}</span>
      <span className="flex shrink-0 items-center gap-1.5">
        {typeof count === "number" && <span className="text-xs tabular-nums text-muted-foreground">{count}</span>}
        {active ? <HugeiconsIcon icon={Tick02Icon} className="size-4" /> : null}
      </span>
    </button>
  )
}

function toggled<T>(list: T[], value: T, order: ReadonlyArray<{ value: T }>): T[] {
  const next = list.includes(value) ? list.filter((item) => item !== value) : [...list, value]
  return order.map((option) => option.value).filter((item) => next.includes(item))
}

/** Phone version of the library filters: the same groups as the title bar, several values per group. */
export function MobileFilterSheet({ open, onOpenChange, library, facets }: MobileFilterSheetProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="bottom-0 left-0 top-auto max-h-[82dvh] w-full max-w-none translate-x-0 translate-y-0 grid-rows-[auto_minmax(0,1fr)_auto] gap-0 overflow-hidden rounded-b-none rounded-t-2xl p-0 sm:max-w-none"
        showCloseButton={false}
      >
        <DialogHeader className="border-b px-4 py-4 text-left">
          <DialogTitle>筛选文件</DialogTitle>
          <DialogDescription>每一组可以选多个，选择会立即生效。</DialogDescription>
        </DialogHeader>

        <div className="space-y-5 overflow-y-auto px-4 py-4">
          <section className="space-y-2" aria-labelledby="status-filter-title">
            <h3 id="status-filter-title" className="text-xs font-semibold text-muted-foreground">状态</h3>
            <div className="grid grid-cols-2 gap-2">
              {STATUS_OPTIONS.map((option) => (
                <FilterOption
                  key={option.value}
                  active={library.statuses.includes(option.value)}
                  label={option.label}
                  count={facets?.status[option.value]}
                  onClick={() => library.setStatuses(toggled(library.statuses, option.value, STATUS_OPTIONS))}
                />
              ))}
              <div className="col-span-2">
                <FilterOption
                  active={library.duplicates}
                  label="只看处理过多次的来源"
                  count={facets?.status.duplicates}
                  onClick={() => library.setDuplicates(!library.duplicates)}
                />
              </div>
            </div>
          </section>

          <section className="space-y-2" aria-labelledby="media-filter-title">
            <h3 id="media-filter-title" className="text-xs font-semibold text-muted-foreground">类型</h3>
            <div className="grid grid-cols-2 gap-2">
              {MEDIA_TYPE_OPTIONS.map((option) => (
                <FilterOption
                  key={option.value}
                  active={library.media.includes(option.value)}
                  label={option.label}
                  count={facets?.media[option.value]}
                  onClick={() => library.setMedia(toggled(library.media, option.value, MEDIA_TYPE_OPTIONS))}
                />
              ))}
            </div>
          </section>

          <section className="space-y-2" aria-labelledby="source-filter-title">
            <h3 id="source-filter-title" className="text-xs font-semibold text-muted-foreground">来源</h3>
            <div className="grid grid-cols-2 gap-2">
              {SOURCE_KEY_OPTIONS.map((option) => (
                <FilterOption
                  key={option.value}
                  active={library.sources.includes(option.value)}
                  label={option.label}
                  count={facets ? facets.source[option.value] ?? 0 : undefined}
                  onClick={() => library.setSources(toggled(library.sources, option.value, SOURCE_KEY_OPTIONS))}
                />
              ))}
            </div>
          </section>

          <section className="space-y-2" aria-labelledby="sort-filter-title">
            <h3 id="sort-filter-title" className="text-xs font-semibold text-muted-foreground">排序</h3>
            <div className="grid grid-cols-2 gap-2">
              {SORT_OPTIONS.map((option) => (
                <FilterOption
                  key={option.value}
                  active={library.sort === option.value}
                  label={option.label}
                  onClick={() => library.setSort(option.value)}
                />
              ))}
            </div>
          </section>
        </div>

        <DialogFooter className="grid grid-cols-2 gap-2 border-t px-4 pb-[max(1rem,var(--mpp-safe-bottom))] pt-3">
          <Button
            variant="outline"
            className="h-11"
            onClick={() => {
              library.resetFilters()
              library.setSort("created_desc")
            }}
          >
            恢复默认
          </Button>
          <Button className="h-11" onClick={() => onOpenChange(false)}>完成</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
