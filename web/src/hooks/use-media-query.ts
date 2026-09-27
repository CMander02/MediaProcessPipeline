import { useSyncExternalStore } from "react"

/** Reactive matchMedia; false where matchMedia is unavailable (tests, old WebViews). */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (listener) => {
      if (typeof window.matchMedia !== "function") return () => {}
      const list = window.matchMedia(query)
      list.addEventListener("change", listener)
      return () => list.removeEventListener("change", listener)
    },
    () => typeof window.matchMedia === "function" && window.matchMedia(query).matches,
  )
}
