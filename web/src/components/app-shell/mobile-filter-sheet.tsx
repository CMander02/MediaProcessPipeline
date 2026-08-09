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
import {
  MEDIA_FILTER_OPTIONS,
  SOURCE_FILTER_OPTIONS,
  type ArchiveSort,
  type MediaFilter,
  type SourceFilter,
} from "@/lib/archive-filters"
import { cn } from "@/lib/utils"

const SORT_OPTIONS: Array<{ value: ArchiveSort; label: string }> = [
  { value: "created_desc", label: "最新创建" },
  { value: "created_asc", label: "最早创建" },
  { value: "published_desc", label: "最新发布" },
  { value: "title_asc", label: "标题排序" },
]

interface MobileFilterSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  mediaFilter: MediaFilter
  sourceFilter: SourceFilter
  sort: ArchiveSort
  onMediaFilterChange: (value: MediaFilter) => void
  onSourceFilterChange: (value: SourceFilter) => void
  onSortChange: (value: ArchiveSort) => void
}

function FilterOption({
  active,
  label,
  onClick,
}: {
  active: boolean
  label: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "flex min-h-11 min-w-0 items-center justify-between gap-2 rounded-lg border px-3 text-left text-sm transition-colors",
        active
          ? "border-primary bg-primary/10 font-medium text-primary"
          : "border-border bg-background text-foreground active:bg-muted",
      )}
    >
      <span className="truncate">{label}</span>
      {active ? <HugeiconsIcon icon={Tick02Icon} className="size-4 shrink-0" /> : null}
    </button>
  )
}

export function MobileFilterSheet({
  open,
  onOpenChange,
  mediaFilter,
  sourceFilter,
  sort,
  onMediaFilterChange,
  onSourceFilterChange,
  onSortChange,
}: MobileFilterSheetProps) {
  const reset = () => {
    onMediaFilterChange("all")
    onSourceFilterChange("all")
    onSortChange("created_desc")
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="bottom-0 left-0 top-auto max-h-[82dvh] w-full max-w-none translate-x-0 translate-y-0 grid-rows-[auto_minmax(0,1fr)_auto] gap-0 overflow-hidden rounded-b-none rounded-t-2xl p-0 sm:max-w-none"
        showCloseButton={false}
      >
        <DialogHeader className="border-b px-4 py-4 text-left">
          <DialogTitle>筛选文件</DialogTitle>
          <DialogDescription>筛选条件会立即应用，文件列表保持当前结果。</DialogDescription>
        </DialogHeader>

        <div className="space-y-5 overflow-y-auto px-4 py-4">
          <section className="space-y-2" aria-labelledby="media-filter-title">
            <h3 id="media-filter-title" className="text-xs font-semibold text-muted-foreground">内容类型</h3>
            <div className="grid grid-cols-2 gap-2">
              {MEDIA_FILTER_OPTIONS.map((option) => (
                <FilterOption
                  key={option.value}
                  active={mediaFilter === option.value}
                  label={option.label}
                  onClick={() => onMediaFilterChange(option.value)}
                />
              ))}
            </div>
          </section>

          <section className="space-y-2" aria-labelledby="source-filter-title">
            <h3 id="source-filter-title" className="text-xs font-semibold text-muted-foreground">内容来源</h3>
            <div className="grid grid-cols-2 gap-2">
              {SOURCE_FILTER_OPTIONS.map((option) => (
                <FilterOption
                  key={option.value}
                  active={sourceFilter === option.value}
                  label={option.label}
                  onClick={() => onSourceFilterChange(option.value)}
                />
              ))}
            </div>
          </section>

          <section className="space-y-2" aria-labelledby="sort-filter-title">
            <h3 id="sort-filter-title" className="text-xs font-semibold text-muted-foreground">排列方式</h3>
            <div className="grid grid-cols-2 gap-2">
              {SORT_OPTIONS.map((option) => (
                <FilterOption
                  key={option.value}
                  active={sort === option.value}
                  label={option.label}
                  onClick={() => onSortChange(option.value)}
                />
              ))}
            </div>
          </section>
        </div>

        <DialogFooter className="grid grid-cols-2 gap-2 border-t px-4 pb-[max(1rem,var(--mpp-safe-bottom))] pt-3">
          <Button variant="outline" className="h-11" onClick={reset}>恢复默认</Button>
          <Button className="h-11" onClick={() => onOpenChange(false)}>完成</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
