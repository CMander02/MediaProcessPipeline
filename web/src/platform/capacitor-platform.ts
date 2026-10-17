import type { PluginListenerHandle } from "@capacitor/core"
import { registerPlugin, SystemBars, SystemBarsStyle } from "@capacitor/core"
import { App } from "@capacitor/app"
import { Browser } from "@capacitor/browser"
import { Keyboard } from "@capacitor/keyboard"
import { Network } from "@capacitor/network"
import { Preferences } from "@capacitor/preferences"

import { api, configureApiClient } from "@/lib/api"
import { rememberArchiveWorkspace } from "@/lib/archive-cache-scope"
import { navigate } from "@/lib/router"
import { applyAndroidCapabilities } from "@/platform/capabilities"
import { bundledDefaultServerUrl, normalizeServerUrl } from "@/platform/server-url"
import type { ConnectInput, OfflineArchiveRecord, OfflineSyncStatus, PlatformAdapter } from "@/platform/types"

const SERVER_URL_KEY = "mpp_server_url"
const WORKSPACES_KEY = "mpp_offline_workspaces"

interface OfflineScope { serverUrl: string; workspaceId: string }

interface SecureCredentialsPlugin {
  getToken(): Promise<{ token: string }>
  setToken(options: { token: string }): Promise<void>
  clearToken(): Promise<void>
}

interface ShareTargetPlugin {
  getPendingShare(): Promise<{ text?: string }>
  addListener(
    eventName: "shareReceived",
    listener: (event: { text?: string }) => void,
  ): Promise<PluginListenerHandle>
}

interface FileDownloadPlugin {
  saveText(options: { filename: string; content: string }): Promise<{ uri: string }>
}

interface OfflineArchivePlugin {
  selectWorkspace(options: OfflineScope): Promise<OfflineSyncStatus>
  getStatus(options: OfflineScope): Promise<OfflineSyncStatus>
  listArchives(options: OfflineScope): Promise<{ archives: OfflineArchiveRecord[] }>
  getArchive(options: OfflineScope & { archiveId: string }): Promise<{ archive: OfflineArchiveRecord }>
  readText(options: OfflineScope & { archiveId: string; relativePath: string }): Promise<{ content: string }>
  sync(options: OfflineScope & { token: string }): Promise<OfflineSyncStatus>
  clear(options: OfflineScope): Promise<OfflineSyncStatus>
  resetIndex(options: OfflineScope): Promise<OfflineSyncStatus>
  addListener(
    eventName: "syncProgress",
    listener: (event: OfflineSyncStatus) => void,
  ): Promise<PluginListenerHandle>
}

const SecureCredentials = registerPlugin<SecureCredentialsPlugin>("SecureCredentials")
const ShareTarget = registerPlugin<ShareTargetPlugin>("ShareTarget")
const FileDownload = registerPlugin<FileDownloadPlugin>("FileDownload")
const OfflineArchive = registerPlugin<OfflineArchivePlugin>("OfflineArchive")

let serverUrl = ""
let token = ""
let pendingSharedText: string | null = null
let initialized = false
let lastOfflineArchiveCount: number | null = null
let workspaceId = ""
let connectionRevision = 0
let savedWorkspaces: Record<string, string> = {}
let scopeTransition: Promise<void> = Promise.resolve()

async function selectOfflineWorkspace(nextWorkspace: string) {
  workspaceId = nextWorkspace
  const scope = { serverUrl, workspaceId }
  rememberArchiveWorkspace(scope.serverUrl, scope.workspaceId || "unresolved")
  lastOfflineArchiveCount = null
  scopeTransition = OfflineArchive.selectWorkspace(scope).then(() => undefined)
  window.dispatchEvent(new Event("mpp:offline-scope-change"))
  await scopeTransition
  if (scope.serverUrl !== serverUrl || scope.workspaceId !== workspaceId) return
  window.dispatchEvent(new Event("mpp:offline-library-change"))
}

async function refreshOfflineWorkspace() {
  if (!serverUrl) return
  const revision = connectionRevision
  const status = await api.sync.status()
  if (revision !== connectionRevision) throw new Error("服务器连接已切换")
  if (!status.workspace_id) throw new Error("服务器未返回资料库标识，请升级服务器")
  if (status.workspace_id !== workspaceId) await selectOfflineWorkspace(status.workspace_id)
  if (revision !== connectionRevision) throw new Error("服务器连接已切换")
  savedWorkspaces[serverUrl] = workspaceId
  await Preferences.set({ key: WORKSPACES_KEY, value: JSON.stringify(savedWorkspaces) })
}

async function withOfflineScope<T>(read: (scope: OfflineScope) => Promise<T>): Promise<T> {
  const revision = connectionRevision
  const scope = { serverUrl, workspaceId }
  await scopeTransition
  if (revision !== connectionRevision || scope.workspaceId !== workspaceId) throw new Error("离线资料库已切换")
  const result = await read(scope)
  if (revision !== connectionRevision || scope.workspaceId !== workspaceId) throw new Error("离线资料库已切换")
  return result
}

function applyApiConnection() {
  configureApiClient({
    baseUrl: serverUrl,
    credentials: "omit",
    credentialProvider: (): HeadersInit => token ? { Authorization: `Bearer ${token}` } : {},
    requestedWith: "mpp-android",
  })
}

function applySharedText(text: string | undefined) {
  const value = text?.trim()
  if (!value) return
  pendingSharedText = value
  window.dispatchEvent(new CustomEvent("mpp:share-received", { detail: { text: value } }))
  navigate("#/submit")
}

function closeOpenOverlay(): boolean {
  const overlay = document.querySelector('[role="dialog"][data-state="open"], [role="alertdialog"][data-state="open"], [data-radix-menu-content][data-state="open"]')
  if (!overlay) return false
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true }))
  return true
}

async function handleBackButton() {
  const nativeEvent = new CustomEvent("mpp:native-back", { cancelable: true })
  if (!window.dispatchEvent(nativeEvent) || closeOpenOverlay()) return
  const hash = window.location.hash || "#/files"
  if (!hash.startsWith("#/files")) {
    if (window.history.length > 1) window.history.back()
    else navigate("#/files", { replace: true })
    return
  }
  await App.exitApp()
}

async function registerNativeListeners() {
  await App.addListener("backButton", () => void handleBackButton())
  await App.addListener("appStateChange", ({ isActive }) => {
    if (isActive) {
      void refreshOfflineWorkspace().catch(() => {})
      window.dispatchEvent(new Event("mpp:app-resume"))
    }
  })
  await Network.addListener("networkStatusChange", ({ connected }) => {
    if (connected) void refreshOfflineWorkspace().catch(() => {})
    window.dispatchEvent(new Event(connected ? "online" : "offline"))
    window.dispatchEvent(new CustomEvent("mpp:network-change", { detail: { connected } }))
  })
  await Keyboard.addListener("keyboardWillShow", ({ keyboardHeight }) => {
    document.documentElement.classList.add("keyboard-open")
    document.documentElement.style.setProperty("--keyboard-height", `${keyboardHeight}px`)
  })
  await Keyboard.addListener("keyboardDidHide", () => {
    document.documentElement.classList.remove("keyboard-open")
    document.documentElement.style.setProperty("--keyboard-height", "0px")
  })
  await ShareTarget.addListener("shareReceived", ({ text }) => {
    applySharedText(text)
    void ShareTarget.getPendingShare()
  })
  await OfflineArchive.addListener("syncProgress", (status) => {
    if (status.serverUrl !== serverUrl || status.workspaceId !== workspaceId) return
    window.dispatchEvent(new CustomEvent("mpp:offline-sync-change", { detail: status }))
    if (lastOfflineArchiveCount !== status.archiveCount) {
      lastOfflineArchiveCount = status.archiveCount
      window.dispatchEvent(new Event("mpp:offline-library-change"))
    }
  })
  window.addEventListener("mpp:workspace-change", () => {
    void selectOfflineWorkspace("").then(refreshOfflineWorkspace).catch(() => {})
  })
  applySharedText((await ShareTarget.getPendingShare()).text)
}

export function createCapacitorPlatform(): PlatformAdapter {
  return {
    kind: "android",
    isNative: true,
    async initialize() {
      if (initialized) return
      initialized = true
      const [savedServer, savedToken, workspaces] = await Promise.all([
        Preferences.get({ key: SERVER_URL_KEY }),
        SecureCredentials.getToken(),
        Preferences.get({ key: WORKSPACES_KEY }),
      ])
      serverUrl = savedServer.value ?? bundledDefaultServerUrl()
      token = savedToken.token ?? ""
      try { savedWorkspaces = JSON.parse(workspaces.value || "{}") } catch { savedWorkspaces = {} }
      applyApiConnection()
      await selectOfflineWorkspace(savedWorkspaces[serverUrl] ?? "")
      // Offline startup keeps the last confirmed workspace for this server.
      try { await refreshOfflineWorkspace() } catch { /* Cached files stay available offline. */ }
      await Promise.all([registerNativeListeners(), this.syncTheme(document.documentElement.classList.contains("dark"))])
      document.documentElement.dataset.platform = "android"
    },
    async getConnection() {
      return { serverUrl, configured: Boolean(serverUrl) }
    },
    async connect(input: ConnectInput) {
      const nextServerUrl = normalizeServerUrl(input.serverUrl)
      const nextToken = input.token.trim()
      const previous = { serverUrl, token, workspaceId }
      connectionRevision += 1
      serverUrl = nextServerUrl
      token = nextToken
      applyApiConnection()
      try {
        // Hide the previous library and stop its native sync before connecting.
        await selectOfflineWorkspace("")
        const health = await api.health()
        const auth = await api.auth.status()
        if (auth.required && !auth.authenticated) throw new Error("API Token 验证失败")
        await api.auth.unlockForNative(nextToken)
        const capabilities = await api.capabilities()
        await refreshOfflineWorkspace()
        await SecureCredentials.setToken({ token })
        await Preferences.set({ key: SERVER_URL_KEY, value: serverUrl })
        window.dispatchEvent(new Event("mpp:connection-change"))
        return {
          serverUrl,
          serverVersion: health.version,
          capabilities: this.applyCapabilities(capabilities),
        }
      } catch (error) {
        serverUrl = previous.serverUrl
        token = previous.token
        applyApiConnection()
        connectionRevision += 1
        await selectOfflineWorkspace(previous.workspaceId)
        throw error
      }
    },
    async clearToken() {
      token = ""
      applyApiConnection()
      await SecureCredentials.clearToken()
    },
    async clearConnection() {
      connectionRevision += 1
      serverUrl = ""
      token = ""
      applyApiConnection()
      await selectOfflineWorkspace("")
      await Promise.all([
        SecureCredentials.clearToken(),
        Preferences.remove({ key: SERVER_URL_KEY }),
      ])
    },
    applyCapabilities: applyAndroidCapabilities,
    async getNetworkStatus() {
      return (await Network.getStatus()).connected
    },
    async openExternal(url) {
      await Browser.open({ url })
    },
    async download(url) {
      await Browser.open({ url })
    },
    async saveTextFile(filename, content) {
      await FileDownload.saveText({ filename, content })
    },
    consumeSharedText() {
      const text = pendingSharedText
      pendingSharedText = null
      return text
    },
    async syncTheme(dark) {
      await SystemBars.setStyle({ style: dark ? SystemBarsStyle.Dark : SystemBarsStyle.Light })
      await SystemBars.show()
    },
    async getOfflineSyncStatus() {
      const revision = connectionRevision
      const workspace = workspaceId
      try {
        return await withOfflineScope((scope) => OfflineArchive.getStatus(scope))
      } catch (error) {
        // Status observers remain mounted during connection changes. Re-read the
        // new status instead of publishing an old count or an unhandled rejection.
        if (revision !== connectionRevision || workspace !== workspaceId) return this.getOfflineSyncStatus()
        throw error
      }
    },
    async listOfflineArchives() {
      return (await withOfflineScope((scope) => OfflineArchive.listArchives(scope))).archives ?? []
    },
    async getOfflineArchive(archiveId) {
      try {
        return (await withOfflineScope((scope) => OfflineArchive.getArchive({ ...scope, archiveId }))).archive
      } catch {
        return null
      }
    },
    async readOfflineText(archiveId, relativePath) {
      return (await withOfflineScope((scope) => OfflineArchive.readText({ ...scope, archiveId, relativePath }))).content ?? ""
    },
    async syncOfflineArchives() {
      if (!serverUrl) return this.getOfflineSyncStatus()
      await refreshOfflineWorkspace()
      return withOfflineScope((scope) => OfflineArchive.sync({ ...scope, token }))
    },
    async clearOfflineArchives() {
      return withOfflineScope((scope) => OfflineArchive.clear(scope))
    },
    async rebuildOfflineIndex() {
      const status = await withOfflineScope((scope) => OfflineArchive.resetIndex(scope))
      if (serverUrl) void this.syncOfflineArchives().catch(() => {})
      return status
    },
  }
}
