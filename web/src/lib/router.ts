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
let currentHash = window.location.hash || "#/files"
let historyIndex = Number(window.history.state?.mppLibraryIndex ?? 0)
let historyMaximum = historyIndex
const historyEntries = new Map<number, string>([[historyIndex, currentHash]])
window.history.replaceState({ ...window.history.state, mppLibraryIndex: historyIndex }, "")
let navigationGuard: ((proceed: () => void, hash: string) => boolean) | null = null
let rollingBack = false
let approvedTraversal = false
export function setNavigationGuard(guard: ((proceed: () => void, hash: string) => boolean) | null) {
  navigationGuard = guard
  return () => { if (navigationGuard === guard) navigationGuard = null }
}
export function libraryHistoryState() { return { back: historyIndex > 0, forward: historyIndex < historyMaximum } }
/** The entry Back returns to, when this session recorded it. */
export function previousHash(): string | undefined {
  return historyIndex > 0 ? historyEntries.get(historyIndex - 1) : undefined
}
export function goLibraryHistory(direction: "back" | "forward") {
  const delta = direction === "back" ? -1 : 1
  if (delta < 0 ? historyIndex <= 0 : historyIndex >= historyMaximum) return
  const proceed = () => { approvedTraversal = true; window.history.go(delta) }
  if (!navigationGuard || navigationGuard(proceed, historyEntries.get(historyIndex + delta) ?? "")) proceed()
}
let lastLibraryHash = currentRoute.page === "files" ? normalizedLibraryHash(window.location.hash) : "#/files"
const listeners = new Set<() => void>()

function normalizedLibraryHash(hash: string): string {
  return hash && hash !== "#" && hash !== "#/" ? hash : "#/files"
}

function handleHashChange() {
  if (rollingBack) return
  currentHash = window.location.hash || "#/files"
  currentRoute = parseHash(window.location.hash)
  if (currentRoute.page === "files") lastLibraryHash = normalizedLibraryHash(window.location.hash)
  listeners.forEach((l) => l())
}
window.addEventListener("hashchange", handleHashChange)
window.addEventListener("popstate", () => {
  const next = Number(window.history.state?.mppLibraryIndex ?? historyIndex)
  if (rollingBack) { rollingBack = false; return }
  if (!approvedTraversal && navigationGuard && next !== historyIndex) {
    const delta = next - historyIndex
    const proceed = () => {
      // Wait for the rollback before traversing to the chosen entry.
      const traverse = () => { approvedTraversal = true; window.history.go(delta) }
      if (rollingBack) window.addEventListener("popstate", traverse, { once: true })
      else traverse()
    }
    if (!navigationGuard(proceed, window.location.hash)) {
      rollingBack = true
      window.history.go(-delta)
      // Do not publish the temporary target during its following hashchange event.
      return
    }
  }
  approvedTraversal = false
  historyIndex = next
  historyMaximum = Math.max(historyMaximum, next)
  historyEntries.set(next, window.location.hash)
  handleHashChange()
})

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
  if (hash === currentHash) return
  const proceed = () => commitNavigation(hash, options)
  if (navigationGuard && !navigationGuard(proceed, hash)) return
  proceed()
}

/** Replace the current entry with `parent` and open `hash` above it, so Back lands on `parent`. */
export function navigateAbove(parent: string, hash: string) {
  if (parent === hash) { navigate(hash, { replace: true }); return }
  const proceed = () => {
    if (parent !== currentHash) commitNavigation(parent, { replace: true })
    commitNavigation(hash)
  }
  if (navigationGuard && !navigationGuard(proceed, hash)) return
  proceed()
}

function commitNavigation(hash: string, options?: { replace?: boolean }) {
  if (options?.replace) {
    window.history.replaceState({ ...window.history.state, mppLibraryIndex: historyIndex }, "", hash)
  } else {
    historyIndex += 1
    historyMaximum = historyIndex
    for (const index of historyEntries.keys()) if (index >= historyIndex) historyEntries.delete(index)
    window.history.pushState({ ...window.history.state, mppLibraryIndex: historyIndex }, "", hash)
  }
  historyEntries.set(historyIndex, hash)
  handleHashChange()
  window.dispatchEvent(new Event("mpp:history-change"))
}
