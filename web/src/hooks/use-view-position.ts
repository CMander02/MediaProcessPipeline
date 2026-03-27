/**
 * Persist and restore per-archive viewing position:
 * - media playback time (seconds)
 * - active tab
 */
import { useCallback, useEffect, useRef } from "react"

interface ViewPosition {
  mediaTime: number // seconds
  activeTab: string
}

const STORAGE_KEY = "mpp-view-positions"
const MAX_ENTRIES = 200 // cap to avoid unbounded growth
const SAVE_INTERVAL = 3000 // throttle writes to every 3s

function loadAll(): Record<string, ViewPosition> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return JSON.parse(raw)
  } catch {}
  return {}
}

function saveAll(data: Record<string, ViewPosition>) {
  // Evict oldest entries if over limit
  const keys = Object.keys(data)
  if (keys.length > MAX_ENTRIES) {
    const toRemove = keys.slice(0, keys.length - MAX_ENTRIES)
    for (const k of toRemove) delete data[k]
  }
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
}

export function useViewPosition(archivePath: string) {
  const posRef = useRef<ViewPosition>({ mediaTime: 0, activeTab: "summary" })
  const lastSaveRef = useRef(0)
  const dirtyRef = useRef(false)

  // Load saved position on mount
  useEffect(() => {
    const all = loadAll()
    const saved = all[archivePath]
    if (saved) {
      posRef.current = { ...posRef.current, ...saved }
    }
  }, [archivePath])

  // Flush on unmount / page hide
  useEffect(() => {
    const flush = () => {
      if (!dirtyRef.current) return
      const all = loadAll()
      all[archivePath] = posRef.current
      saveAll(all)
      dirtyRef.current = false
    }

    // Save on page hide (tab switch, close, etc.)
    const onVisChange = () => {
      if (document.visibilityState === "hidden") flush()
    }
    document.addEventListener("visibilitychange", onVisChange)

    return () => {
      document.removeEventListener("visibilitychange", onVisChange)
      flush() // save on component unmount
    }
  }, [archivePath])

  /** Call on every timeupdate — throttled internally */
  const updateMediaTime = useCallback((time: number) => {
    posRef.current.mediaTime = time
    dirtyRef.current = true

    const now = Date.now()
    if (now - lastSaveRef.current > SAVE_INTERVAL) {
      lastSaveRef.current = now
      const all = loadAll()
      all[archivePath] = posRef.current
      saveAll(all)
      dirtyRef.current = false
    }
  }, [archivePath])

  const updateActiveTab = useCallback((tab: string) => {
    posRef.current.activeTab = tab
    dirtyRef.current = true
  }, [])

  /** Get the initial saved position (call once after mount) */
  const getSavedPosition = useCallback((): ViewPosition => {
    return { ...posRef.current }
  }, [])

  return { updateMediaTime, updateActiveTab, getSavedPosition }
}
