import type { PlatformAdapter } from "@/platform/types"

export function createWebPlatform(): PlatformAdapter {
  return {
    kind: "web",
    isNative: false,
    async initialize() {},
    async getConnection() {
      return { serverUrl: window.location.origin, configured: true }
    },
    async connect() {
      throw new Error("网页端使用当前站点连接")
    },
    async clearToken() {},
    async clearConnection() {},
    applyCapabilities: (capabilities) => capabilities,
    async getNetworkStatus() {
      return navigator.onLine
    },
    async openExternal(url) {
      window.open(url, "_blank", "noopener,noreferrer")
    },
    async download(url) {
      const anchor = document.createElement("a")
      anchor.href = url
      anchor.target = "_blank"
      anchor.rel = "noopener noreferrer"
      anchor.click()
    },
    async saveTextFile(filename, content) {
      const url = URL.createObjectURL(new Blob([content], { type: "text/plain;charset=utf-8" }))
      const anchor = document.createElement("a")
      anchor.href = url
      anchor.download = filename
      anchor.click()
      URL.revokeObjectURL(url)
    },
    consumeSharedText: () => null,
    async syncTheme() {},
  }
}
