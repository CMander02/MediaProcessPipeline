/**
 * Minimal hash-based router.
 * Routes: #/files, #/submit, #/result/archive?path=..., #/result/task/<id>
 */
import { useSyncExternalStore } from "react"

export interface Route {
  page: "files" | "submit" | "result"
  /** For result page: "archive" or "task" */
  resultType?: "archive" | "task"
  /** archive path or task id */
  resultId?: string
}

function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "")

  if (raw.startsWith("submit")) return { page: "submit" }

  if (raw.startsWith("result/archive")) {
    const params = new URLSearchParams(raw.split("?")[1] ?? "")
    return { page: "result", resultType: "archive", resultId: params.get("path") ?? undefined }
  }

  if (raw.startsWith("result/task/")) {
    const id = raw.replace("result/task/", "").split("?")[0]
    return { page: "result", resultType: "task", resultId: id || undefined }
  }

  return { page: "files" }
}

let currentRoute: Route = parseHash(window.location.hash)
const listeners = new Set<() => void>()

function handleHashChange() {
  currentRoute = parseHash(window.location.hash)
  listeners.forEach((l) => l())
}
window.addEventListener("hashchange", handleHashChange)

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function getSnapshot() {
  return currentRoute
}

export function useRoute(): Route {
  return useSyncExternalStore(subscribe, getSnapshot)
}

export function navigate(hash: string, options?: { replace?: boolean }) {
  if (options?.replace) {
    window.history.replaceState(null, "", hash)
  } else {
    window.location.hash = hash
  }
  // Trigger update manually for replaceState (doesn't fire hashchange)
  if (options?.replace) {
    handleHashChange()
  }
}
