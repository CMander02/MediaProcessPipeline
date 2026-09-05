import { getApiClientConfig } from "@/lib/api"

const workspaces = new Map<string, string>()

export function archiveServerKey() {
  return getApiClientConfig().baseUrl || globalThis.location?.origin || "local"
}

export function archiveCacheScope(server = archiveServerKey()) {
  return `${server}:${workspaces.get(server) ?? "unresolved"}`
}

export function rememberArchiveWorkspace(server: string, workspace: string) {
  workspaces.set(server, workspace)
  return archiveCacheScope(server)
}
