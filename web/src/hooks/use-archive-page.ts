import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { createArchiveRepository } from "@/repositories/archive-repository"
import type { ArchivePage, ArchiveQuery } from "@/repositories/archive-types"
import { usePlatform } from "@/platform/use-platform"
import { archiveCacheScope, archiveServerKey, rememberArchiveWorkspace } from "@/lib/archive-cache-scope"
import { preloadThumbnails } from "@/hooks/use-archives"

const pages = new Map<string, ArchivePage>()
const requests = new Map<string, Promise<ArchivePage>>()
const MAX_CACHED_PAGES = 80

export function useArchivePage(query: ArchiveQuery) {
  const platform = usePlatform()
  const repository = useMemo(() => createArchiveRepository(platform), [platform])
  const [generation, setGeneration] = useState(0)
  const server = archiveServerKey()
  const queryKey = JSON.stringify(query)
  const requestKey = `${server}:${platform.kind}:${generation}:${queryKey}`
  const activeKey = useRef(requestKey)
  useLayoutEffect(() => { activeKey.current = requestKey }, [requestKey])
  const cacheKey = `${archiveCacheScope(server)}:${platform.kind}:${queryKey}`
  const [snapshot, setSnapshot] = useState<{ key: string; value: ArchivePage } | null>(null)
  const [error, setError] = useState<{ key: string; message: string } | null>(null)
  const data = snapshot?.key === requestKey ? snapshot.value : pages.get(cacheKey)

  const refresh = useCallback((silent = false) => {
    const queryValue: ArchiveQuery = JSON.parse(queryKey)
    const key = `${archiveCacheScope(server)}:${platform.kind}:${queryKey}`
    let request = requests.get(key)
    if (!request) {
      request = repository.listPage(queryValue)
      requests.set(key, request)
      const clear = () => { if (requests.get(key) === request) requests.delete(key) }
      void request.then(clear, clear)
    }
    return request.then((result) => {
      if (activeKey.current !== requestKey) return
      const scope = rememberArchiveWorkspace(server, result.workspace_id)
      const resolvedKey = `${scope}:${platform.kind}:${queryKey}`
      pages.delete(resolvedKey)
      pages.set(resolvedKey, result)
      if (pages.size > MAX_CACHED_PAGES) pages.delete(pages.keys().next().value!)
      setSnapshot({ key: requestKey, value: result })
      setError(null)
      if (!silent) preloadThumbnails(result.archives)
    }, (reason) => {
      if (activeKey.current === requestKey) setError({ key: requestKey, message: String(reason) })
    })
  }, [platform.kind, queryKey, repository, requestKey, server])

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
    window.addEventListener("mpp:offline-library-change", reload)
    window.addEventListener("mpp:app-resume", resume)
    return () => {
      window.removeEventListener("mpp:workspace-change", changed)
      window.removeEventListener("mpp:connection-change", changed)
      window.removeEventListener("mpp:offline-library-change", reload)
      window.removeEventListener("mpp:app-resume", resume)
    }
  }, [refresh, repository])

  const removeArchive = useCallback(() => {
    const prefix = `${archiveCacheScope(server)}:${platform.kind}:`
    for (const key of pages.keys()) if (key.startsWith(prefix)) pages.delete(key)
    void refresh(true)
  }, [platform.kind, refresh, server])

  return { archives: data?.archives ?? [], total: data?.total ?? 0, page: data?.page ?? query.page,
    loading: !data && error?.key !== requestKey, error: error?.key === requestKey ? error.message : null,
    indexing: data?.indexing ?? false, lastReconciledAt: data?.last_reconciled_at,
    refresh, removeArchive }
}
