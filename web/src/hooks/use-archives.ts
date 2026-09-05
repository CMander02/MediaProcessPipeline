import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { createArchiveRepository } from "@/repositories/archive-repository"
import type { ArchiveItem } from "@/repositories/archive-types"
import { usePlatform } from "@/platform/use-platform"
import { api } from "@/lib/api"
import { archiveCacheScope, archiveServerKey, rememberArchiveWorkspace } from "@/lib/archive-cache-scope"

export type { ArchiveItem } from "@/repositories/archive-types"

const archiveCache = new Map<string, ArchiveItem[]>()
const archiveRequests = new Map<string, Promise<ArchiveItem[]>>()
const preloadedThumbnails = new Set<string>()
const THUMBNAIL_PRELOAD_LIMIT = 28

export function preloadThumbnails(archives: ArchiveItem[]) {
  if (typeof window === "undefined") return
  const urls = archives
    .slice(0, THUMBNAIL_PRELOAD_LIMIT)
    .filter((archive) => archive.has_thumbnail !== false)
    .map((archive) => archive.thumbnail_url ?? api.archives.thumbnailUrl(archive.path))
    .filter((url) => url && !preloadedThumbnails.has(url))
  if (!urls.length) return

  urls.forEach((url) => preloadedThumbnails.add(url))
  const preload = () => {
    urls.forEach((url) => {
      const image = new Image()
      image.decoding = "async"
      image.src = url
    })
  }
  const requestIdle = (window as unknown as {
    requestIdleCallback?: (callback: IdleRequestCallback, options?: IdleRequestOptions) => number
  }).requestIdleCallback
  if (requestIdle) {
    requestIdle(preload, { timeout: 800 })
  } else {
    globalThis.setTimeout(preload, 0)
  }
}

export function useArchives(lite = true) {
  const platform = usePlatform()
  const [, updateIdentity] = useState(0)
  const repository = useMemo(() => createArchiveRepository(platform), [platform])
  const cacheKey = `${archiveCacheScope()}:${platform.kind}:${lite ? "lite" : "full"}`
  const activeKey = useRef(cacheKey)
  activeKey.current = cacheKey
  const initialCache = archiveCache.get(cacheKey)
  const [archives, setArchives] = useState<ArchiveItem[]>(initialCache ?? [])
  const [loading, setLoading] = useState(initialCache === undefined)
  const [error, setError] = useState<string | null>(null)
  const loadedRef = useRef(initialCache !== undefined)

  const refresh = useCallback(async (silent = false) => {
    const showInitialLoader = !silent && !loadedRef.current
    try {
      if (showInitialLoader) setLoading(true)
      let request = archiveRequests.get(cacheKey)
      if (!request) {
        request = repository.list(lite)
        archiveRequests.set(cacheKey, request)
        const clearRequest = () => {
          if (archiveRequests.get(cacheKey) === request) archiveRequests.delete(cacheKey)
        }
        void request.then(clearRequest, clearRequest)
      }
      const items = await request
      archiveCache.set(cacheKey, items)
      if (activeKey.current !== cacheKey) return
      setArchives(items)
      preloadThumbnails(items)
      setError(null)
      if (platform.isNative && !silent) repository.triggerSync()
    } catch (e) {
      setError(String(e))
    } finally {
      if (activeKey.current === cacheKey) {
        loadedRef.current = true
        if (showInitialLoader) setLoading(false)
      }
    }
  }, [cacheKey, lite, platform.isNative, repository])

  const removeArchive = useCallback((path: string) => {
    setArchives((current) => {
      const next = current.filter((archive) => archive.path !== path)
      archiveCache.set(cacheKey, next)
      return next
    })
  }, [cacheKey])

  useEffect(() => {
    refresh()
  }, [refresh])

  useEffect(() => {
    const changed = (event: Event) => {
      const workspace = (event as CustomEvent<{ workspace_id?: string }>).detail?.workspace_id
      if (workspace) rememberArchiveWorkspace(archiveServerKey(), workspace)
      loadedRef.current = false
      setArchives([])
      setLoading(true)
      updateIdentity((value) => value + 1)
    }
    window.addEventListener("mpp:workspace-change", changed)
    window.addEventListener("mpp:connection-change", changed)
    return () => {
      window.removeEventListener("mpp:workspace-change", changed)
      window.removeEventListener("mpp:connection-change", changed)
    }
  }, [])

  useEffect(() => {
    if (!platform.isNative) return
    const reload = () => void refresh(true)
    const resume = () => {
      repository.triggerSync()
      void refresh(true)
    }
    window.addEventListener("mpp:offline-library-change", reload)
    window.addEventListener("mpp:app-resume", resume)
    return () => {
      window.removeEventListener("mpp:offline-library-change", reload)
      window.removeEventListener("mpp:app-resume", resume)
    }
  }, [platform.isNative, refresh, repository])

  return { archives, loading, error, refresh, removeArchive }
}
