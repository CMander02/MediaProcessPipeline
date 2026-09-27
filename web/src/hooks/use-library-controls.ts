import { useCallback } from "react"

import {
  DEFAULT_LIBRARY_FILTERS,
  MEDIA_TYPE_OPTIONS,
  SORT_OPTIONS,
  SOURCE_KEY_OPTIONS,
  STATUS_OPTIONS,
  type ArchiveSort,
  type ArchiveStatus,
  type LibraryFilters,
  type MediaType,
  type SourceKey,
} from "@/lib/archive-filters"
import { buildHash, navigate, useRoute } from "@/lib/router"

export interface LibraryState extends LibraryFilters {
  page: number
}

/** Known values from a comma-separated URL param, in the order the options are listed. */
function listParam<T extends string>(raw: string | undefined, options: ReadonlyArray<{ value: T }>): T[] {
  const picked = new Set((raw ?? "").split(",").map((item) => item.trim()))
  return options.map((option) => option.value).filter((value) => picked.has(value))
}

export function libraryStateFromParams(params: Record<string, string>): LibraryState {
  const sort = SORT_OPTIONS.some((option) => option.value === params.sort)
    ? params.sort as ArchiveSort
    : DEFAULT_LIBRARY_FILTERS.sort
  const page = Number.parseInt(params.page ?? "", 10)
  return {
    search: params.q ?? "",
    media: listParam<MediaType>(params.media, MEDIA_TYPE_OPTIONS),
    sources: listParam<SourceKey>(params.src, SOURCE_KEY_OPTIONS),
    statuses: listParam<ArchiveStatus>(params.status, STATUS_OPTIONS),
    // "status=duplicates" is what links from before the duplicates switch used.
    duplicates: params.dup === "1" || (params.status ?? "").split(",").includes("duplicates"),
    sort,
    page: Number.isFinite(page) && page > 1 ? page : 1,
  }
}

export function libraryHashFromState(state: LibraryState): string {
  return buildHash("files", {
    q: state.search,
    media: state.media.join(",") || null,
    src: state.sources.join(",") || null,
    status: state.statuses.join(",") || null,
    dup: state.duplicates ? 1 : null,
    sort: state.sort === DEFAULT_LIBRARY_FILTERS.sort ? null : state.sort,
    page: state.page > 1 ? state.page : null,
  })
}

/** The filters as the archive API expects them: comma lists, "all" when nothing is picked. */
export function archiveQueryFilters(filters: LibraryFilters) {
  return {
    search: filters.search,
    media: filters.media.join(",") || "all",
    source: filters.sources.join(",") || "all",
    status: filters.statuses.join(",") || "all",
    duplicates: filters.duplicates,
    sort: filters.sort,
  }
}

function currentParams(): Record<string, string> {
  const hash = window.location.hash
  return hash.includes("?") ? Object.fromEntries(new URLSearchParams(hash.slice(hash.indexOf("?") + 1))) : {}
}

/**
 * Library search, filters, sort and page live in the URL hash (#/files?q=..&status=failed,paused&page=3),
 * so returning from a result page, reloading or opening a copied link restores them.
 */
export function useLibraryControls() {
  const route = useRoute()
  const state = libraryStateFromParams(route.page === "files" ? route.params : {})

  const update = useCallback((patch: Partial<LibraryState>) => {
    const current = libraryStateFromParams(currentParams())
    // Changing what is shown starts again from page 1 unless a page is given explicitly.
    const next = { ...current, page: 1, ...patch }
    navigate(libraryHashFromState(next), { replace: true })
  }, [])

  return {
    ...state,
    setSearch: useCallback((search: string) => update({ search }), [update]),
    setMedia: useCallback((media: MediaType[]) => update({ media }), [update]),
    setSources: useCallback((sources: SourceKey[]) => update({ sources }), [update]),
    setStatuses: useCallback((statuses: ArchiveStatus[]) => update({ statuses }), [update]),
    setDuplicates: useCallback((duplicates: boolean) => update({ duplicates }), [update]),
    setSort: useCallback((sort: ArchiveSort) => update({ sort }), [update]),
    setPage: useCallback((page: number) => update({ page }), [update]),
    /** Clear search and every filter; the sort order stays. */
    resetFilters: useCallback(() => update({ search: "", media: [], sources: [], statuses: [], duplicates: false }), [update]),
  }
}

export type LibraryControls = ReturnType<typeof useLibraryControls>
