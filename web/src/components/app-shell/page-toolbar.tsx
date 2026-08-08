import { useState } from "react"
import { FilterHorizontalIcon, Search01Icon, FolderOpenIcon } from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"

import { MobileFilterSheet } from "@/components/app-shell/mobile-filter-sheet"
import { PlatformIcon } from "@/components/platform-icon"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger } from "@/components/ui/select"
import {
  MEDIA_FILTER_OPTIONS,
  SOURCE_FILTER_OPTIONS,
  type ArchiveSort,
  type MediaFilter,
  type SourceFilter,
  type SourceFilterOption,
} from "@/lib/archive-filters"

const SOURCE_ICON_PLATFORMS = new Set([
  "apple_podcast",
  "bilibili",
  "webpage",
  "x",
  "xiaohongshu",
  "youtube",
  "zhihu",
])

const SORT_LABELS: Record<ArchiveSort, string> = {
  created_desc: "最新创建",
  created_asc: "最早创建",
  published_desc: "最新发布",
  title_asc: "标题排序",
}

interface PageToolbarProps {
  search: string
  mediaFilter: MediaFilter
  sourceFilter: SourceFilter
  sort: ArchiveSort
  onSearchChange: (value: string) => void
  onMediaFilterChange: (value: MediaFilter) => void
  onSourceFilterChange: (value: SourceFilter) => void
  onSortChange: (value: ArchiveSort) => void
}

function SourceFilterIcon({ option, className }: { option: SourceFilterOption; className?: string }) {
  if (option.platform && SOURCE_ICON_PLATFORMS.has(option.platform)) {
    return <PlatformIcon platform={option.platform} className={className ?? "size-4 shrink-0"} iconOnly />
  }
  return <HugeiconsIcon icon={FolderOpenIcon} className={className ?? "size-4 shrink-0 text-muted-foreground"} />
}

export function PageToolbar({
  search,
  mediaFilter,
  sourceFilter,
  sort,
  onSearchChange,
  onMediaFilterChange,
  onSourceFilterChange,
  onSortChange,
}: PageToolbarProps) {
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false)
  const selectedMediaFilter = MEDIA_FILTER_OPTIONS.find((option) => option.value === mediaFilter) ?? MEDIA_FILTER_OPTIONS[0]
  const selectedSourceFilter = SOURCE_FILTER_OPTIONS.find((option) => option.value === sourceFilter) ?? SOURCE_FILTER_OPTIONS[0]
  const activeFilterCount = Number(mediaFilter !== "all") + Number(sourceFilter !== "all") + Number(sort !== "created_desc")

  return (
    <div className="min-w-0" role="search" aria-label="文件搜索与筛选">
      <div className="flex min-w-0 items-center gap-2 md:hidden">
        <div className="relative min-w-0 flex-1">
          <HugeiconsIcon
            icon={Search01Icon}
            className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            value={search}
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder="搜索标题、话题、关键词..."
            className="h-11 pl-9 text-sm"
            autoComplete="off"
            aria-label="搜索文件"
          />
        </div>
        <Button
          type="button"
          variant="outline"
          className="relative size-11 px-0"
          onClick={() => setMobileFiltersOpen(true)}
          aria-label={activeFilterCount > 0 ? `筛选，已启用 ${activeFilterCount} 项` : "筛选"}
        >
          <HugeiconsIcon icon={FilterHorizontalIcon} className="size-4" />
          {activeFilterCount > 0 ? (
            <span className="absolute -right-1 -top-1 flex size-5 items-center justify-center rounded-full bg-primary text-[0.625rem] text-primary-foreground">
              {activeFilterCount}
            </span>
          ) : null}
        </Button>
      </div>

      <div className="hidden min-w-0 items-center gap-2 md:flex">
      <div className="relative min-w-0 flex-1 max-w-xs">
        <HugeiconsIcon
          icon={Search01Icon}
          className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="搜索标题、话题、关键词..."
          className="h-9 pl-9 text-sm"
          autoComplete="off"
          aria-label="搜索文件"
        />
      </div>

      <div className="flex min-w-0 shrink-0 gap-2">
        <Select value={mediaFilter} onValueChange={(value) => onMediaFilterChange(value as MediaFilter)}>
          <SelectTrigger size="sm" className="min-w-0 w-full data-[size=sm]:h-11 md:w-24 md:data-[size=sm]:h-9">
            <span className="truncate">{selectedMediaFilter.label}</span>
          </SelectTrigger>
          <SelectContent position="popper" align="end">
            <SelectGroup>
              {MEDIA_FILTER_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>

        <Select value={sourceFilter} onValueChange={(value) => onSourceFilterChange(value as SourceFilter)}>
          <SelectTrigger size="sm" className="min-w-0 w-full data-[size=sm]:h-11 md:w-36 md:data-[size=sm]:h-9">
            <span className="flex min-w-0 items-center gap-1.5">
              <SourceFilterIcon option={selectedSourceFilter} />
              <span className="truncate">{selectedSourceFilter.label}</span>
            </span>
          </SelectTrigger>
          <SelectContent position="popper" align="end">
            <SelectGroup>
              {SOURCE_FILTER_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  <span className="flex items-center gap-2">
                    <SourceFilterIcon option={option} />
                    <span>{option.label}</span>
                  </span>
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>

        <Select value={sort} onValueChange={(value) => onSortChange(value as ArchiveSort)}>
          <SelectTrigger size="sm" className="min-w-0 w-full data-[size=sm]:h-11 md:w-32 md:data-[size=sm]:h-9">
            <span className="truncate">{SORT_LABELS[sort]}</span>
          </SelectTrigger>
          <SelectContent position="popper" align="end">
            <SelectGroup>
              {(Object.entries(SORT_LABELS) as Array<[ArchiveSort, string]>).map(([value, label]) => (
                <SelectItem key={value} value={value}>{label}</SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
      </div>
      </div>

      <MobileFilterSheet
        open={mobileFiltersOpen}
        onOpenChange={setMobileFiltersOpen}
        mediaFilter={mediaFilter}
        sourceFilter={sourceFilter}
        sort={sort}
        onMediaFilterChange={onMediaFilterChange}
        onSourceFilterChange={onSourceFilterChange}
        onSortChange={onSortChange}
      />
    </div>
  )
}
