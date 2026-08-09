import { useCallback, useEffect, useState } from "react"

import type { OfflineSyncStatus } from "@/platform"
import { usePlatform } from "@/platform/use-platform"

const EMPTY_STATUS: OfflineSyncStatus = {
  syncing: false,
  cursor: 0,
  archiveCount: 0,
  completedFiles: 0,
  totalFiles: 0,
  completedBytes: 0,
  totalBytes: 0,
  lastSync: 0,
  lastError: "",
}

export function useOfflineSync() {
  const platform = usePlatform()
  const [status, setStatus] = useState<OfflineSyncStatus>(EMPTY_STATUS)

  const refresh = useCallback(async () => {
    if (!platform.isNative) return EMPTY_STATUS
    const next = await platform.getOfflineSyncStatus()
    setStatus(next)
    return next
  }, [platform])

  useEffect(() => {
    if (!platform.isNative) return
    const initialRefresh = window.setTimeout(() => void refresh(), 0)
    const update = (event: Event) => {
      const detail = (event as CustomEvent<OfflineSyncStatus>).detail
      if (detail) setStatus(detail)
      else void refresh()
    }
    window.addEventListener("mpp:offline-sync-change", update)
    window.addEventListener("mpp:offline-library-change", update)
    return () => {
      window.clearTimeout(initialRefresh)
      window.removeEventListener("mpp:offline-sync-change", update)
      window.removeEventListener("mpp:offline-library-change", update)
    }
  }, [platform.isNative, refresh])

  const syncNow = useCallback(async () => {
    const next = await platform.syncOfflineArchives()
    setStatus(next)
    window.dispatchEvent(new Event("mpp:offline-library-change"))
    return next
  }, [platform])

  const clear = useCallback(async () => {
    const next = await platform.clearOfflineArchives()
    setStatus(next)
    window.dispatchEvent(new Event("mpp:offline-library-change"))
    return next
  }, [platform])

  const rebuild = useCallback(async () => {
    const next = await platform.rebuildOfflineIndex()
    setStatus(next)
    window.dispatchEvent(new Event("mpp:offline-library-change"))
    return next
  }, [platform])

  return { status, refresh, syncNow, clear, rebuild }
}
