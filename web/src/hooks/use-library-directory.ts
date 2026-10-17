import { useCallback, useSyncExternalStore } from "react"
import { api } from "@/lib/api"
import { archiveCacheScope, archiveServerKey, rememberArchiveWorkspace } from "@/lib/archive-cache-scope"
import type { LibraryDirectory } from "@/lib/library-types"

const empty: LibraryDirectory = { folders: [], members: {}, unclassified_count: 0, library_revision: 0 }
const snapshots = new Map<string, LibraryDirectory>()
const requests = new Map<string, Promise<void>>()
const listeners = new Set<() => void>()
const events = ["mpp:library-change", "mpp:workspace-change", "mpp:connection-change", "mpp:offline-scope-change", "mpp:app-resume"]
let timer = 0
let generation = 0
const update = (event?: Event) => {
  if (event?.type === "mpp:workspace-change" || event?.type === "mpp:connection-change" || event?.type === "mpp:offline-scope-change") { generation++; requests.clear() }
  publish(); void refreshLibraryDirectory().catch(() => {})
}
const subscribe = (listener: () => void) => {
  listeners.add(listener)
  if (listeners.size === 1) {
    for (const event of events) window.addEventListener(event, update)
    timer = window.setInterval(update, 15000)
    void refreshLibraryDirectory().catch(() => {})
  }
  return () => {
    listeners.delete(listener)
    if (!listeners.size) { window.clearInterval(timer); for (const event of events) window.removeEventListener(event, update) }
  }
}
const publish = () => listeners.forEach((listener) => listener())
function key() { return `mpp-library:${archiveCacheScope()}` }
export function libraryDirectorySnapshot(): LibraryDirectory {
  const id = key()
  if (!snapshots.has(id)) {
    try { snapshots.set(id, JSON.parse(localStorage.getItem(id) ?? "null") ?? empty) }
    catch { snapshots.set(id, empty) }
  }
  return snapshots.get(id)!
}
export function refreshLibraryDirectory() {
  const id = key()
  const server = archiveServerKey()
  const requestedGeneration = generation
  const pending = requests.get(id)
  if (pending) return pending
  const request = api.library.directory().then((directory) => {
    if (requestedGeneration !== generation || server !== archiveServerKey()) return
    if (directory.workspace_id) rememberArchiveWorkspace(server, directory.workspace_id)
    const resolvedKey = key()
    const previous = snapshots.get(resolvedKey)
    snapshots.set(resolvedKey, directory)
    try { localStorage.setItem(resolvedKey, JSON.stringify(directory)) } catch { /* Keep the in-memory directory. */ }
    publish()
    if (!previous || previous.library_revision !== directory.library_revision
      || Object.entries(directory.processed_at ?? {}).some(([contentKey, time]) => previous.processed_at?.[contentKey] !== time)) {
      window.dispatchEvent(new Event("mpp:library-directory-change"))
    }
  }).finally(() => { if (requests.get(id) === request) requests.delete(id) })
  requests.set(id, request)
  return request
}
export function libraryChanged() {
  window.dispatchEvent(new Event("mpp:library-change"))
}
export function useLibraryDirectory() {
  const directory = useSyncExternalStore(subscribe, libraryDirectorySnapshot)
  const refresh = useCallback(() => refreshLibraryDirectory(), [])
  return { ...directory, refresh }
}
