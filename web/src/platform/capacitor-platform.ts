import type { PluginListenerHandle } from "@capacitor/core"
import { registerPlugin, SystemBars, SystemBarsStyle } from "@capacitor/core"
import { App } from "@capacitor/app"
import { Browser } from "@capacitor/browser"
import { Keyboard } from "@capacitor/keyboard"
import { Network } from "@capacitor/network"
import { Preferences } from "@capacitor/preferences"

import { api, configureApiClient } from "@/lib/api"
import { navigate } from "@/lib/router"
import { applyAndroidCapabilities } from "@/platform/capabilities"
import { bundledDefaultServerUrl, normalizeServerUrl } from "@/platform/server-url"
import type { ConnectInput, OfflineArchiveRecord, OfflineSyncStatus, PlatformAdapter } from "@/platform/types"

const SERVER_URL_KEY = "mpp_server_url"

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
  getStatus(): Promise<OfflineSyncStatus>
  listArchives(): Promise<{ archives: OfflineArchiveRecord[] }>
  getArchive(options: { archiveId: string }): Promise<{ archive: OfflineArchiveRecord }>
  readText(options: { archiveId: string; relativePath: string }): Promise<{ content: string }>
  sync(options: { serverUrl: string; token: string }): Promise<OfflineSyncStatus>
  clear(): Promise<OfflineSyncStatus>
  resetIndex(): Promise<OfflineSyncStatus>
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
    if (isActive) window.dispatchEvent(new Event("mpp:app-resume"))
  })
  await Network.addListener("networkStatusChange", ({ connected }) => {
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
    window.dispatchEvent(new CustomEvent("mpp:offline-sync-change", { detail: status }))
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
      const [savedServer, savedToken] = await Promise.all([
        Preferences.get({ key: SERVER_URL_KEY }),
        SecureCredentials.getToken(),
      ])
      serverUrl = savedServer.value ?? bundledDefaultServerUrl()
      token = savedToken.token ?? ""
      applyApiConnection()
      await Promise.all([registerNativeListeners(), this.syncTheme(document.documentElement.classList.contains("dark"))])
      document.documentElement.dataset.platform = "android"
    },
    async getConnection() {
      return { serverUrl, configured: Boolean(serverUrl) }
    },
    async connect(input: ConnectInput) {
      const nextServerUrl = normalizeServerUrl(input.serverUrl)
      const nextToken = input.token.trim()
      const previous = { serverUrl, token }
      serverUrl = nextServerUrl
      token = nextToken
      applyApiConnection()
      try {
        const health = await api.health()
        const auth = await api.auth.status()
        if (auth.required && !auth.authenticated) throw new Error("API Token 验证失败")
        await api.auth.unlockForNative(nextToken)
        const capabilities = await api.capabilities()
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
        throw error
      }
    },
    async clearToken() {
      token = ""
      applyApiConnection()
      await SecureCredentials.clearToken()
    },
    async clearConnection() {
      serverUrl = ""
      token = ""
      applyApiConnection()
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
      return OfflineArchive.getStatus()
    },
    async listOfflineArchives() {
      return (await OfflineArchive.listArchives()).archives ?? []
    },
    async getOfflineArchive(archiveId) {
      try {
        return (await OfflineArchive.getArchive({ archiveId })).archive
      } catch {
        return null
      }
    },
    async readOfflineText(archiveId, relativePath) {
      return (await OfflineArchive.readText({ archiveId, relativePath })).content ?? ""
    },
    async syncOfflineArchives() {
      if (!serverUrl) return OfflineArchive.getStatus()
      return OfflineArchive.sync({ serverUrl, token })
    },
    async clearOfflineArchives() {
      return OfflineArchive.clear()
    },
    async rebuildOfflineIndex() {
      const status = await OfflineArchive.resetIndex()
      if (serverUrl) void OfflineArchive.sync({ serverUrl, token })
      return status
    },
  }
}
