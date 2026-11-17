import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { createArchiveRepository } from "@/repositories/archive-repository"
import type { ArchivePage, ArchiveQuery } from "@/repositories/archive-types"
import { usePlatform } from "@/platform/use-platform"
import { archiveCacheScope, archiveServerKey, rememberArchiveWorkspace } from "@/lib/archive-cache-scope"
import { preloadThumbnails } from "@/hooks/use-archives"

interface CachedPage {
  value: ArchivePage
  pageCount: number
}

const pages = new Map<string, CachedPage>()
const requests = new Map<string, Promise<ArchivePage>>()
const MAX_CACHED_PAGES = 80
const EMPTY_ARCHIVES: ArchivePage["archives"] = []
const EMPTY_FOLDERS: NonNullable<ArchivePage["folders"]> = []

function mergePages(results: ArchivePage[]): ArchivePage {
  const first = results[0]
  if (results.length === 1) return first
  const archives = new Map<string, ArchivePage["archives"][number]>()
  const folders = new Map<string, NonNullable<ArchivePage["folders"]>[number]>()
  const entries = new Map<string, NonNullable<ArchivePage["entries"]>[number]>()
  for (const result of results) {
    for (const archive of result.archives) archives.set(archive.archive_id ?? archive.path, archive)
    for (const folder of result.folders ?? []) folders.set(folder.id, folder)
    for (const entry of result.entries ?? result.archives.map((archive) => ({ kind: "archive" as const, id: archive.archive_id ?? archive.path }))) {
      entries.set(`${entry.kind}:${entry.id}`, entry)
    }
  }
  return { ...first, archives: [...archives.values()], folders: [...folders.values()], entries: [...entries.values()] }
}

export function useArchivePage(query: ArchiveQuery, infiniteView?: string) {
  const platform = usePlatform()
  const repository = useMemo(() => createArchiveRepository(platform), [platform])
  const [generation, setGeneration] = useState(0)
  const server = archiveServerKey()
  const queryKey = JSON.stringify(query)
  const viewKey = infiniteView ?? "page"
  const requestKey = `${server}:${platform.kind}:${viewKey}:${generation}:${queryKey}`
  const activeKey = useRef(requestKey)
  useLayoutEffect(() => { activeKey.current = requestKey }, [requestKey])
  const cacheKey = `${archiveCacheScope(server)}:${platform.kind}:${viewKey}:${queryKey}`
  const [snapshot, setSnapshot] = useState<{ key: string; cached: CachedPage } | null>(null)
  const [pendingKey, setPendingKey] = useState<string | null>(null)
  const pending = useRef<string | null>(null)
  const queuedRefresh = useRef<string | null>(null)
  const [error, setError] = useState<{ key: string; message: string } | null>(null)
  const cached = snapshot?.key === requestKey ? snapshot.cached : pages.get(cacheKey)
  const data = cached?.value
  const cachedRef = useRef(cached)
  useLayoutEffect(() => { cachedRef.current = cached }, [cached])
  useLayoutEffect(() => () => { activeKey.current = "" }, [requestKey])

  const load = useCallback(async function loadPages(append: boolean, silent = false) {
    if (pending.current === requestKey) {
      if (!append) queuedRefresh.current = requestKey
      return
    }
    const previous = cachedRef.current
    if (append && (!previous || previous.pageCount * query.page_size >= previous.value.total)) return
    pending.current = requestKey
    setPendingKey(requestKey)
    const queryValue: ArchiveQuery = JSON.parse(queryKey)
    const count = infiniteView ? (previous?.pageCount ?? 1) : 1
    const pageNumbers = append ? [count + 1] : Array.from({ length: count }, (_, index) => index + 1)
    try {
      const results = await Promise.all(pageNumbers.map((page) => {
        const pageQuery = infiniteView ? { ...queryValue, page, offset: (page - 1) * queryValue.page_size } : queryValue
        const key = `${archiveCacheScope(server)}:${platform.kind}:${JSON.stringify(pageQuery)}`
        let request = requests.get(key)
        if (!request) {
          request = repository.listPage(pageQuery)
          requests.set(key, request)
          const clear = () => { if (requests.get(key) === request) requests.delete(key) }
          void request.then(clear, clear)
        }
        return request
      }))
      if (activeKey.current !== requestKey) return
      const first = results[0]
      const scope = platform.isNative && first.workspace_id === "offline" ? archiveCacheScope(server) : rememberArchiveWorkspace(server, first.workspace_id)
      const resolvedKey = `${scope}:${platform.kind}:${viewKey}:${queryKey}`
      const value = append && previous
        ? mergePages([{ ...previous.value, total: first.total, archive_total: first.archive_total }, ...results])
        : mergePages(results)
      const next = { value, pageCount: append ? count + 1 : count }
      pages.delete(resolvedKey)
      pages.set(resolvedKey, next)
      if (pages.size > MAX_CACHED_PAGES) {
        const oldest = pages.keys().next().value
        if (oldest !== undefined) pages.delete(oldest)
      }
      cachedRef.current = next
      setSnapshot({ key: requestKey, cached: next })
      setError(null)
      if (!silent) preloadThumbnails(first.archives)
    } catch (reason) {
      if (activeKey.current === requestKey) setError({ key: requestKey, message: String(reason) })
    } finally {
      if (pending.current === requestKey) pending.current = null
      if (activeKey.current === requestKey) setPendingKey(null)
      if (activeKey.current === requestKey && queuedRefresh.current === requestKey) {
        queuedRefresh.current = null
        void loadPages(false, true)
      }
    }
  }, [infiniteView, platform.kind, platform.isNative, query.page_size, queryKey, repository, requestKey, server, viewKey])
  const refresh = useCallback((silent = false) => load(false, silent), [load])
  const loadMore = useCallback(() => load(true), [load])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => { if (platform.isNative) repository.triggerSync() }, [platform.isNative, repository])

  useEffect(() => {
    const changed = (event: Event) => {
      const workspace = (event as CustomEvent<{ workspace_id?: string }>).detail?.workspace_id
      if (workspace) rememberArchiveWorkspace(archiveServerKey(), workspace)
      setGeneration((current) => current + 1)
    }
    const reload = () => { void refresh(true) }
    const resume = () => { repository.triggerSync(); void refresh(true) }
    window.addEventListener("mpp:workspace-change", changed)
    window.addEventListener("mpp:connection-change", changed)
    window.addEventListener("mpp:offline-scope-change", changed)
    window.addEventListener("mpp:offline-library-change", reload)
    window.addEventListener("mpp:library-change", reload)
    window.addEventListener("mpp:library-refresh", reload)
    window.addEventListener("mpp:library-directory-change", reload)
    window.addEventListener("mpp:app-resume", resume)
    return () => {
      window.removeEventListener("mpp:workspace-change", changed)
      window.removeEventListener("mpp:connection-change", changed)
      window.removeEventListener("mpp:offline-scope-change", changed)
      window.removeEventListener("mpp:offline-library-change", reload)
      window.removeEventListener("mpp:library-change", reload)
      window.removeEventListener("mpp:library-refresh", reload)
      window.removeEventListener("mpp:library-directory-change", reload)
      window.removeEventListener("mpp:app-resume", resume)
    }
  }, [refresh, repository])

  const removeArchive = useCallback(() => {
    const prefix = `${archiveCacheScope(server)}:${platform.kind}:`
    for (const key of pages.keys()) if (key.startsWith(prefix)) pages.delete(key)
    void refresh(true)
  }, [platform.kind, refresh, server])

  return { archives: data?.archives ?? EMPTY_ARCHIVES, folders: data?.folders ?? EMPTY_FOLDERS, entries: data?.entries,
    archiveTotal: data?.archive_total ?? data?.total ?? 0,
    total: data?.total ?? 0, page: data?.page ?? query.page,
    loading: !data && error?.key !== requestKey, error: error?.key === requestKey ? error.message : null,
    indexing: data?.indexing ?? false, lastReconciledAt: data?.last_reconciled_at, facets: data?.facets,
    refresh, removeArchive, loadMore, loadingMore: pendingKey === requestKey,
    loadedPages: cached?.pageCount ?? 0, hasMore: !!infiniteView && !!cached && cached.pageCount * query.page_size < (data?.total ?? 0),
    sessionKey: cacheKey }
}
