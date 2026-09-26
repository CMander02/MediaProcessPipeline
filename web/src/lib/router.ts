/**
 * Minimal hash-based router.
 * Routes: #/files?page=2&q=..., #/submit, #/backend?tab=logs, #/result/archive?path=..., #/result/task/<id>
 */
import { useSyncExternalStore } from "react"

export interface Route {
  page: "files" | "submit" | "backend" | "result" | "settings"
  /** For result page: "archive" or "task" */
  resultType?: "archive" | "task"
  /** archive path or task id */
  resultId?: string
  /** task id for SSE subscription (available on archive routes too) */
  taskId?: string
  /** Query parameters after "?" (library filters, backend tab, ...) */
  params: Record<string, string>
}

function parseParams(query: string): Record<string, string> {
  return Object.fromEntries(new URLSearchParams(query))
}

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "")
  const query = raw.includes("?") ? raw.slice(raw.indexOf("?") + 1) : ""
  const params = parseParams(query)

  if (raw.startsWith("submit")) return { page: "submit", params }
  if (raw.startsWith("backend")) return { page: "backend", params }
  if (raw.startsWith("settings")) return { page: "settings", params }

  if (raw.startsWith("result/archive")) {
    return {
      page: "result",
      resultType: "archive",
      resultId: params.path ?? undefined,
      taskId: params.taskId ?? undefined,
      params,
    }
  }

  if (raw.startsWith("result/task/")) {
    const id = raw.replace("result/task/", "").split("?")[0]
    return { page: "result", resultType: "task", resultId: id || undefined, params }
  }

  return { page: "files", params }
}

/** Build a hash for a page plus query params, dropping empty values. */
export function buildHash(page: string, params: Record<string, string | number | null | undefined> = {}): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue
    search.set(key, String(value))
  }
  const query = search.toString()
  return query ? `#/${page}?${query}` : `#/${page}`
}

let currentRoute: Route = parseHash(window.location.hash)
let lastLibraryHash = currentRoute.page === "files" ? normalizedLibraryHash(window.location.hash) : "#/files"
const listeners = new Set<() => void>()

function normalizedLibraryHash(hash: string): string {
  return hash && hash !== "#" && hash !== "#/" ? hash : "#/files"
}

function handleHashChange() {
  currentRoute = parseHash(window.location.hash)
  if (currentRoute.page === "files") lastLibraryHash = normalizedLibraryHash(window.location.hash)
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

/** Where "文件" / "返回" should go: the library with the page and filters last used. */
export function libraryHash(): string {
  return lastLibraryHash
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
