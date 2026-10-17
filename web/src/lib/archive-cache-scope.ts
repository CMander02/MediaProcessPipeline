import { getApiClientConfig } from "@/lib/api"

const workspaces = new Map<string, string>()

export function archiveServerKey() {
  return getApiClientConfig().baseUrl || globalThis.location?.origin || "local"
}

export function archiveCacheScope(server = archiveServerKey()) {
  if (!workspaces.has(server)) {
    try { const saved = localStorage.getItem(`mpp-workspace:${server}`); if (saved) workspaces.set(server, saved) } catch { /* Use this session's workspace. */ }
  }
  return `${server}:${workspaces.get(server) ?? "unresolved"}`
}

export function rememberArchiveWorkspace(server: string, workspace: string) {
  workspaces.set(server, workspace)
  try { localStorage.setItem(`mpp-workspace:${server}`, workspace) } catch { /* Browser storage may be unavailable. */ }
  return archiveCacheScope(server)
}
