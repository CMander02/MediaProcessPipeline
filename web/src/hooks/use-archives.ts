import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { createArchiveRepository } from "@/repositories/archive-repository"
import type { ArchiveItem } from "@/repositories/archive-types"
import { usePlatform } from "@/platform/use-platform"

export type { ArchiveItem } from "@/repositories/archive-types"

export function useArchives(lite = true) {
  const platform = usePlatform()
  const repository = useMemo(() => createArchiveRepository(platform), [platform])
  const [archives, setArchives] = useState<ArchiveItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const loadedRef = useRef(false)

  const refresh = useCallback(async (silent = false) => {
    const showInitialLoader = !silent && !loadedRef.current
    try {
      if (showInitialLoader) setLoading(true)
      const items = await repository.list(lite)
      setArchives(items)
      setError(null)
      if (platform.isNative && !silent) repository.triggerSync()
    } catch (e) {
      setError(String(e))
    } finally {
      loadedRef.current = true
      if (showInitialLoader) setLoading(false)
    }
  }, [lite, platform.isNative, repository])

  const removeArchive = useCallback((path: string) => {
    setArchives((current) => current.filter((archive) => archive.path !== path))
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

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
