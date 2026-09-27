/** Whether the global search (Ctrl+K) is open; the header button and the shortcut share it. */
import { useSyncExternalStore } from "react"

let open = false
const listeners = new Set<() => void>()

function set(next: boolean) {
  if (open === next) return
  open = next
  listeners.forEach((listener) => listener())
}

export function openSearch() {
  set(true)
}

export function closeSearch() {
  set(false)
}

export function toggleSearch() {
  set(!open)
}

export function useSearchOpen(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => open,
  )
}
