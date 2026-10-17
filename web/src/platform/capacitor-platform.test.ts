/** @vitest-environment jsdom */
import { beforeEach, expect, it, vi } from "vitest"

type Scope = { serverUrl: string; workspaceId: string }
const m = vi.hoisted(() => {
  const preferences = new Map<string, string>()
  const libraries = new Map<string, { cursor: number; archiveCount: number }>()
  const key = (scope: Scope) => JSON.stringify(scope)
  const status = (scope: Scope) => ({ ...scope, ...(libraries.get(key(scope)) ?? { cursor: 0, archiveCount: 0 }) })
  return {
    preferences, libraries, key, status,
    baseUrl: "", workspace: "/workspace/A", listener: null as null | ((value: unknown) => void),
    credentials: { getToken: vi.fn(async () => ({ token: "token" })), setToken: vi.fn(async () => {}), clearToken: vi.fn(async () => {}) },
    offline: {
      selectWorkspace: vi.fn(async (scope: Scope) => status(scope)),
      getStatus: vi.fn(async (scope: Scope) => status(scope)),
      listArchives: vi.fn(async () => ({ archives: [] })),
      getArchive: vi.fn(), readText: vi.fn(),
      sync: vi.fn(async (scope: Scope) => { libraries.set(key({ serverUrl: scope.serverUrl, workspaceId: scope.workspaceId }), { cursor: 1, archiveCount: 1 }); return status(scope) }),
      clear: vi.fn(), resetIndex: vi.fn(),
      addListener: vi.fn(async (_: string, listener: (value: unknown) => void) => { m.listener = listener; return { remove() {} } }),
    },
    api: {
      health: vi.fn(async () => ({ version: "test" })),
      auth: { status: vi.fn(async () => ({ required: true, authenticated: true })), unlockForNative: vi.fn(async () => ({})) },
      capabilities: vi.fn(async () => ({})),
      sync: { status: vi.fn(async () => ({ workspace_id: m.workspace })) },
    },
    configure: vi.fn((config: { baseUrl: string }) => { m.baseUrl = config.baseUrl }),
  }
})
vi.mock("@capacitor/core", () => ({
  registerPlugin: (name: string) => name === "SecureCredentials" ? m.credentials : name === "OfflineArchive" ? m.offline : name === "ShareTarget" ? { addListener: vi.fn(async () => ({})), getPendingShare: vi.fn(async () => ({ text: "" })) } : {},
  SystemBars: { setStyle: vi.fn(async () => {}), show: vi.fn(async () => {}) }, SystemBarsStyle: { Dark: "DARK", Light: "LIGHT" },
}))
vi.mock("@capacitor/app", () => ({ App: { addListener: vi.fn(async () => ({})) } }))
vi.mock("@capacitor/browser", () => ({ Browser: {} }))
vi.mock("@capacitor/keyboard", () => ({ Keyboard: { addListener: vi.fn(async () => ({})) } }))
vi.mock("@capacitor/network", () => ({ Network: { addListener: vi.fn(async () => ({})) } }))
vi.mock("@capacitor/preferences", () => ({ Preferences: {
  get: vi.fn(async ({ key }: { key: string }) => ({ value: m.preferences.get(key) ?? null })),
  set: vi.fn(async ({ key, value }: { key: string; value: string }) => { m.preferences.set(key, value) }),
  remove: vi.fn(async ({ key }: { key: string }) => { m.preferences.delete(key) }),
} }))
vi.mock("@/lib/api", () => ({ api: m.api, configureApiClient: m.configure, getApiClientConfig: () => ({ baseUrl: m.baseUrl }) }))
vi.mock("@/lib/router", () => ({ navigate: vi.fn() }))

const serverA = "https://server-a.example"
const serverB = "https://server-b.example"
const scopeA = { serverUrl: serverA, workspaceId: "/workspace/A" }
beforeEach(() => {
  vi.resetModules(); vi.clearAllMocks()
  m.workspace = "/workspace/A"
  m.preferences.clear(); m.libraries.clear()
  m.preferences.set("mpp_server_url", serverA)
  m.libraries.set(m.key(scopeA), { cursor: 900, archiveCount: 2 })
  m.api.sync.status.mockImplementation(async () => ({ workspace_id: m.workspace }))
  m.api.health.mockResolvedValue({ version: "test" })
})

async function platform() {
  const { createCapacitorPlatform } = await import("./capacitor-platform")
  const instance = createCapacitorPlatform()
  await instance.initialize()
  return instance
}

it("selects separate native databases and cursors on server changes and restores the prior scope", async () => {
  const client = await platform()
  expect(await client.getOfflineSyncStatus()).toMatchObject({ cursor: 900 })
  await client.connect({ serverUrl: serverB, token: "b" })
  expect(await client.getOfflineSyncStatus()).toMatchObject({ cursor: 0, serverUrl: serverB })
  await client.syncOfflineArchives()
  expect(m.offline.sync).toHaveBeenCalledWith({ serverUrl: serverB, workspaceId: "/workspace/A", token: "b" })
  await client.connect({ serverUrl: serverA, token: "a" })
  expect(await client.getOfflineSyncStatus()).toMatchObject({ cursor: 900, archiveCount: 2 })
  expect(m.offline.clear).not.toHaveBeenCalled()
  expect(m.offline.resetIndex).not.toHaveBeenCalled()
})

it("detects a different workspace on the same server before syncing", async () => {
  const client = await platform()
  m.workspace = "/workspace/B"
  await client.syncOfflineArchives()
  expect(m.offline.selectWorkspace).toHaveBeenLastCalledWith({ serverUrl: serverA, workspaceId: "/workspace/B" })
  expect(m.offline.sync).toHaveBeenLastCalledWith({ serverUrl: serverA, workspaceId: "/workspace/B", token: "token" })
  expect(m.libraries.get(m.key(scopeA))?.cursor).toBe(900)
})

it("rejects a late native read and drops old progress after the server changes", async () => {
  const client = await platform()
  let resolveOld!: (value: { archives: [] }) => void
  m.offline.listArchives.mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve }))
  const pending = client.listOfflineArchives()
  const rejected = expect(pending).rejects.toThrow("离线资料库已切换")
  await Promise.resolve()
  await client.connect({ serverUrl: serverB, token: "b" })
  resolveOld({ archives: [] })
  await rejected
  const progress = vi.fn()
  window.addEventListener("mpp:offline-sync-change", progress)
  m.listener?.({ ...scopeA, archiveCount: 99 })
  expect(progress).not.toHaveBeenCalled()
  window.removeEventListener("mpp:offline-sync-change", progress)
})

it("reopens only the saved workspace when starting offline", async () => {
  m.preferences.set("mpp_offline_workspaces", JSON.stringify({ [serverA]: "/workspace/A" }))
  m.api.sync.status.mockRejectedValue(new Error("offline"))
  const client = await platform()
  expect(m.offline.selectWorkspace).toHaveBeenCalledWith(scopeA)
  expect(await client.getOfflineSyncStatus()).toMatchObject({ cursor: 900 })
})

it("refreshes a pending status observer after switching instead of returning the old library count", async () => {
  const client = await platform()
  let resolveOld!: (value: ReturnType<typeof m.status>) => void
  m.offline.getStatus.mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve }))
  const pending = client.getOfflineSyncStatus()
  await Promise.resolve()
  await client.connect({ serverUrl: serverB, token: "b" })
  resolveOld(m.status(scopeA))
  expect(await pending).toMatchObject({ serverUrl: serverB, cursor: 0, archiveCount: 0 })
})

it("restores the previous scope after a failed connection and hides it on disconnect", async () => {
  const client = await platform()
  m.api.health.mockRejectedValueOnce(new Error("unavailable"))
  await expect(client.connect({ serverUrl: serverB, token: "b" })).rejects.toThrow("unavailable")
  expect(await client.getOfflineSyncStatus()).toMatchObject({ cursor: 900, ...scopeA })
  await client.clearConnection()
  expect(await client.getOfflineSyncStatus()).toMatchObject({ cursor: 0, serverUrl: "", workspaceId: "" })
  expect(m.libraries.get(m.key(scopeA))?.cursor).toBe(900)
})
