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
import { buildHash, goLibraryHistory, libraryHash, navigate, navigateAbove, parseHash, previousHash, useRoute } from "@/lib/router"

export type LibraryScope = "folders" | "all"

export interface LibraryState extends LibraryFilters {
  page: number
  view?: "grid" | "split"
  /** "all" lists every item by itself, ignoring folders. */
  scope?: LibraryScope
  folder?: string
  item?: string
  attempt?: string
  viewerTask?: string
  generation?: string
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
    view: params.view === "split" || params.view === "browse" ? "split" : "grid",
    scope: params.scope === "all" ? "all" : "folders",
    folder: params.scope === "all" ? undefined : params.folder || undefined,
    item: params.item || undefined,
    attempt: params.attempt || undefined,
    viewerTask: params.viewerTask || undefined,
    generation: params.generation || undefined,
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
    view: state.view === "split" ? "split" : null,
    scope: state.scope === "all" ? "all" : null,
    folder: state.scope === "all" ? undefined : state.folder,
    item: state.item,
    attempt: state.attempt,
    viewerTask: state.viewerTask,
    generation: state.generation,
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
  const route = parseHash(window.location.hash)
  return route.page === "files" ? route.params : parseHash(libraryHash()).params
}

/** The library state Back would return to, when this session recorded it. */
function previousLibraryState(): LibraryState | null {
  const hash = previousHash()
  if (!hash) return null
  const route = parseHash(hash)
  return route.page === "files" ? libraryStateFromParams(route.params) : null
}

const FILTER_KEYS = ["search", "media", "sources", "statuses", "duplicates", "sort"] as const

/** The same place in the library: scope, view, folder, filters, and in the split view the open item. */
export function sameLibraryLocation(a: LibraryState, b: LibraryState): boolean {
  if (a.scope !== b.scope || a.view !== b.view || (a.folder ?? "") !== (b.folder ?? "")) return false
  if (a.view === "split" && (a.item ?? "") !== (b.item ?? "")) return false
  return FILTER_KEYS.every((key) => JSON.stringify(a[key]) === JSON.stringify(b[key]))
}

/** Going to the place Back would return to is Back, so the history does not grow in circles. */
function returnsBack(next: LibraryState): boolean {
  const previous = previousLibraryState()
  if (!previous || !sameLibraryLocation(previous, next)) return false
  goLibraryHistory("back")
  return true
}

/**
 * Library search, filters, sort and page live in the URL hash (#/files?q=..&status=failed,paused&page=3),
 * so returning from a result page, reloading or opening a copied link restores them.
 */
export function useLibraryControls() {
  const route = useRoute()
  const state = libraryStateFromParams(route.page === "files" ? route.params : parseHash(libraryHash()).params)

  const update = useCallback((patch: Partial<LibraryState>, push = false) => {
    const current = libraryStateFromParams(currentParams())
    // Changing what is shown starts again from page 1 unless a page is given explicitly.
    const next = { ...current, page: 1, ...(Object.hasOwn(patch, "item") ? { attempt: undefined, viewerTask: undefined, generation: undefined } : {}), ...patch }
    navigate(libraryHashFromState(next), { replace: !push })
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
    setLocation: useCallback((patch: Pick<Partial<LibraryState>, "folder" | "view" | "item">, push = true) => {
      const current = libraryStateFromParams(currentParams())
      const next: LibraryState = {
        ...current,
        page: Object.hasOwn(patch, "folder") && patch.folder !== current.folder ? 1 : current.page,
        ...(Object.hasOwn(patch, "item") ? { attempt: undefined, viewerTask: undefined, generation: undefined } : {}),
        ...patch,
      }
      if (push && returnsBack(next)) return
      navigate(libraryHashFromState(next), { replace: !push })
    }, []),
    /** Grid or split is how the place is shown, not a place: switching never adds a Back step. */
    setView: useCallback((view: "grid" | "split") => {
      const current = libraryStateFromParams(currentParams())
      const inLibrary = parseHash(window.location.hash).page === "files"
      if (inLibrary && current.view === view && (view !== "grid" || !(current.item || current.attempt || current.viewerTask || current.generation))) return
      const next: LibraryState = {
        ...current,
        view,
        ...(view === "grid" ? { item: undefined, attempt: undefined, viewerTask: undefined, generation: undefined } : {}),
      }
      // Closing the split view returns to the grid it was opened from.
      if (returnsBack(next)) return
      const root: LibraryState = { ...next, folder: undefined, item: undefined, page: 1 }
      const previous = previousLibraryState()
      if (view === "grid" && next.folder && !(previous && sameLibraryLocation(previous, root))) {
        // Back from a folder always leads to the library root, never to the other view.
        navigateAbove(libraryHashFromState(root), libraryHashFromState(next))
        return
      }
      navigate(libraryHashFromState(next), { replace: true })
    }, []),
    /** Folders, or everything in one list regardless of folders. */
    setScope: useCallback((scope: LibraryScope) => {
      const current = libraryStateFromParams(currentParams())
      if (current.scope === scope) {
        if (parseHash(window.location.hash).page !== "files" && !returnsBack(current)) {
          navigate(libraryHashFromState(current), { replace: true })
        }
        return
      }
      navigate(libraryHashFromState({
        ...current, scope, folder: undefined, page: 1,
        ...(current.view === "split" ? {} : { item: undefined, attempt: undefined, viewerTask: undefined, generation: undefined }),
      }), { replace: true })
    }, []),
    /** Clear search and every filter; the sort order stays. */
    resetFilters: useCallback(() => update({ search: "", media: [], sources: [], statuses: [], duplicates: false }), [update]),
  }
}

export type LibraryControls = ReturnType<typeof useLibraryControls>
