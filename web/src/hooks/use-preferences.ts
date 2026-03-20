/**
 * User preferences stored in localStorage.
 */
import { useCallback, useSyncExternalStore } from "react"

interface Preferences {
  /** Which page to show on startup: "files" | "last" */
  startupPage: "files" | "last"
  /** Last opened archive path */
  lastArchivePath: string | null
}

const STORAGE_KEY = "mpp-preferences"

function load(): Preferences {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return { ...defaults(), ...JSON.parse(raw) }
  } catch {}
  return defaults()
}

function defaults(): Preferences {
  return { startupPage: "files", lastArchivePath: null }
}

let current = load()
const listeners = new Set<() => void>()

function notify() {
  listeners.forEach((l) => l())
}

export function usePreferences() {
  const prefs = useSyncExternalStore(
    (cb) => {
      listeners.add(cb)
      return () => listeners.delete(cb)
    },
    () => current,
  )

  const update = useCallback((partial: Partial<Preferences>) => {
    current = { ...current, ...partial }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(current))
    notify()
  }, [])

  return { prefs, update }
}

/** Read preferences once (non-reactive) */
export function getPreferences(): Preferences {
  return current
}
