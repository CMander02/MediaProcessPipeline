import { useCallback } from "react"

import {
  MEDIA_FILTER_OPTIONS,
  SOURCE_FILTER_OPTIONS,
  type ArchiveSort,
  type MediaFilter,
  type SourceFilter,
} from "@/lib/archive-filters"
import { buildHash, navigate, useRoute } from "@/lib/router"

const SORTS: ArchiveSort[] = ["created_desc", "created_asc", "published_desc", "title_asc"]

interface LibraryState {
  search: string
  mediaFilter: MediaFilter
  sourceFilter: SourceFilter
  sort: ArchiveSort
  page: number
}

export function libraryStateFromParams(params: Record<string, string>): LibraryState {
  const media = MEDIA_FILTER_OPTIONS.some((option) => option.value === params.media)
    ? params.media as MediaFilter
    : "all"
  const source = SOURCE_FILTER_OPTIONS.some((option) => option.value === params.src)
    ? params.src as SourceFilter
    : "all"
  const sort = SORTS.includes(params.sort as ArchiveSort) ? params.sort as ArchiveSort : "created_desc"
  const page = Number.parseInt(params.page ?? "", 10)
  return {
    search: params.q ?? "",
    mediaFilter: media,
    sourceFilter: source,
    sort,
    page: Number.isFinite(page) && page > 1 ? page : 1,
  }
}

export function libraryHashFromState(state: LibraryState): string {
  return buildHash("files", {
    q: state.search,
    media: state.mediaFilter === "all" ? null : state.mediaFilter,
    src: state.sourceFilter === "all" ? null : state.sourceFilter,
    sort: state.sort === "created_desc" ? null : state.sort,
    page: state.page > 1 ? state.page : null,
  })
}

/**
 * Library search, filters, sort and page live in the URL hash (#/files?q=..&page=3),
 * so returning from a result page, reloading or opening a copied link restores them.
 */
export function useLibraryControls() {
  const route = useRoute()
  const state = libraryStateFromParams(route.page === "files" ? route.params : {})

  const update = useCallback((patch: Partial<LibraryState>) => {
    const current = libraryStateFromParams(window.location.hash.includes("?")
      ? Object.fromEntries(new URLSearchParams(window.location.hash.slice(window.location.hash.indexOf("?") + 1)))
      : {})
    // Changing what is shown starts again from page 1 unless a page is given explicitly.
    const next = { ...current, page: 1, ...patch }
    navigate(libraryHashFromState(next), { replace: true })
  }, [])

  return {
    ...state,
    setSearch: useCallback((search: string) => update({ search }), [update]),
    setMediaFilter: useCallback((mediaFilter: MediaFilter) => update({ mediaFilter }), [update]),
    setSourceFilter: useCallback((sourceFilter: SourceFilter) => update({ sourceFilter }), [update]),
    setSort: useCallback((sort: ArchiveSort) => update({ sort }), [update]),
    setPage: useCallback((page: number) => update({ page }), [update]),
  }
}
