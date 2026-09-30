import { useState } from "react"
import {
  Cancel01Icon, FilterHorizontalIcon, FolderOpenIcon, Link01Icon, PlaySquareIcon, Progress03Icon, Search01Icon, SortingDownIcon,
} from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"

import { MobileFilterSheet } from "@/components/app-shell/mobile-filter-sheet"
import { titleBarButtonClass } from "@/components/app-shell/navigation"
import { FacetFilter, type FacetOption } from "@/components/library/facet-filter"
import { PlatformIcon } from "@/components/platform-icon"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import type { LibraryControls } from "@/hooks/use-library-controls"
import { useLibraryFacets } from "@/lib/library-facets"
import {
  MEDIA_TYPE_OPTIONS,
  SORT_OPTIONS,
  SOURCE_KEY_OPTIONS,
  STATUS_OPTIONS,
  hasActiveFilters,
  type ArchiveSort,
  type SourceKey,
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

export function SourceIcon({ source, className }: { source: SourceKey; className?: string }) {
  if (SOURCE_ICON_PLATFORMS.has(source)) {
    return <PlatformIcon platform={source} className={className ?? "size-4 shrink-0"} iconOnly />
  }
  return <HugeiconsIcon icon={FolderOpenIcon} className={className ?? "size-4 shrink-0 text-muted-foreground"} />
}

const SOURCE_FACETS: Array<FacetOption<SourceKey>> = SOURCE_KEY_OPTIONS.map((option) => ({
  value: option.value,
  label: option.label,
  icon: <SourceIcon source={option.value} className="size-3.5 shrink-0" />,
}))

interface PageToolbarProps {
  library: LibraryControls
}

/** Library search and filters: inline in the title bar on desktop, a search row plus sheet on phones. */
export function PageToolbar({ library }: PageToolbarProps) {
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false)
  const facets = useLibraryFacets()
  const active = hasActiveFilters(library)
  const sortLabel = SORT_OPTIONS.find((option) => option.value === library.sort)?.label ?? "最新创建"
  const mobileCount = library.statuses.length + library.media.length + library.sources.length
    + Number(library.duplicates) + Number(library.sort !== "created_desc")

  const search = (className: string, inputClassName: string) => (
    <div className={className}>
      <HugeiconsIcon
        icon={Search01Icon}
        className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
      />
      <Input
        value={library.search}
        onChange={(event) => library.setSearch(event.target.value)}
        placeholder="搜索标题…"
        className={inputClassName}
        autoComplete="off"
        aria-label="搜索文件"
      />
    </div>
  )

  return (
    <div className="min-w-0" role="search" aria-label="文件搜索与筛选">
      <div className="flex min-w-0 items-center gap-2 md:hidden">
        {search("relative min-w-0 flex-1", "h-11 pl-9 text-sm")}
        <Button
          type="button"
          variant="outline"
          className="relative size-11 px-0"
          onClick={() => setMobileFiltersOpen(true)}
          aria-label={mobileCount > 0 ? `筛选，已启用 ${mobileCount} 项` : "筛选"}
        >
          <HugeiconsIcon icon={FilterHorizontalIcon} className="size-4" />
          {mobileCount > 0 ? (
            <span className="absolute -right-1 -top-1 flex size-5 items-center justify-center rounded-full bg-primary text-[0.625rem] text-primary-foreground">
              {mobileCount}
            </span>
          ) : null}
        </Button>
      </div>

      {/* Filters sit next to the page links and look like them; search follows. */}
      <div className="hidden min-w-0 items-center gap-1.5 overflow-x-auto md:flex">
        <div className="flex shrink-0 items-center gap-0.5">
          <FacetFilter
            title="状态"
            icon={Progress03Icon}
            options={STATUS_OPTIONS}
            selected={library.statuses}
            onChange={library.setStatuses}
            counts={facets?.status}
            extraLabel={library.duplicates ? "重复" : null}
            extra={(
              <DropdownMenuCheckboxItem
                checked={library.duplicates}
                onCheckedChange={(checked) => library.setDuplicates(checked === true)}
                onSelect={(event) => event.preventDefault()}
                title="同一个来源处理过多次时，把每一次都列出来，方便比较和清理"
              >
                <span className="flex-1">只看处理过多次的来源</span>
                {facets && <span className="pl-4 text-xs tabular-nums text-muted-foreground">{facets.status.duplicates ?? 0}</span>}
              </DropdownMenuCheckboxItem>
            )}
          />
          <FacetFilter
            title="类型"
            icon={PlaySquareIcon}
            options={MEDIA_TYPE_OPTIONS}
            selected={library.media}
            onChange={library.setMedia}
            counts={facets?.media}
          />
          <FacetFilter
            title="来源"
            icon={Link01Icon}
            options={SOURCE_FACETS}
            selected={library.sources}
            onChange={library.setSources}
            counts={facets?.source}
          />
        </div>
        {/* The search box gives way first when the title bar gets tight. */}
        {search("relative w-44 min-w-32 lg:w-64", "h-8 pl-8 text-[13px]")}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button type="button" className={cn(titleBarButtonClass(false), "data-[state=open]:bg-muted data-[state=open]:text-foreground")}>
              <HugeiconsIcon icon={SortingDownIcon} className="size-4" />
              {sortLabel}
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuRadioGroup value={library.sort} onValueChange={(value) => library.setSort(value as ArchiveSort)}>
              {SORT_OPTIONS.map((option) => (
                <DropdownMenuRadioItem key={option.value} value={option.value}>{option.label}</DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
        {active && (
          <button type="button" className={titleBarButtonClass(false)} onClick={library.resetFilters}>
            <HugeiconsIcon icon={Cancel01Icon} className="size-4" />
            重置
          </button>
        )}
      </div>

      <MobileFilterSheet
        open={mobileFiltersOpen}
        onOpenChange={setMobileFiltersOpen}
        library={library}
        facets={facets}
      />
    </div>
  )
}
