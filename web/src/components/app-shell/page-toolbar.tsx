import { useState } from "react"
import { Cancel01Icon, FilterHorizontalIcon, FolderOpenIcon, Search01Icon, SortingDownIcon } from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"

import { MobileFilterSheet } from "@/components/app-shell/mobile-filter-sheet"
import { FacetFilter, type FacetOption } from "@/components/library/facet-filter"
import { PlatformIcon } from "@/components/platform-icon"
import { Button } from "@/components/ui/button"
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

      <div className="hidden min-w-0 items-center gap-1.5 overflow-x-auto md:flex">
        {search("relative w-44 shrink-0 lg:w-64", "h-8 pl-8 text-[13px]")}
        <FacetFilter
          title="状态"
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
          options={MEDIA_TYPE_OPTIONS}
          selected={library.media}
          onChange={library.setMedia}
          counts={facets?.media}
        />
        <FacetFilter
          title="来源"
          options={SOURCE_FACETS}
          selected={library.sources}
          onChange={library.setSources}
          counts={facets?.source}
        />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button type="button" variant="ghost" size="sm" className="h-8 shrink-0 gap-1.5 px-2 text-[13px] font-normal text-muted-foreground">
              <HugeiconsIcon icon={SortingDownIcon} className="size-3.5" />
              {sortLabel}
            </Button>
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
          <Button type="button" variant="ghost" size="sm" className="h-8 shrink-0 gap-1 px-2 text-[13px] font-normal" onClick={library.resetFilters}>
            重置
            <HugeiconsIcon icon={Cancel01Icon} className="size-3.5" />
          </Button>
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
