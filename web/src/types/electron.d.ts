import type { MppBackendBridge } from "@/lib/electron"

declare global {
  interface Window {
    mppBackend?: MppBackendBridge
    mppDialog?: {
      selectDirectory(options?: { title?: string; defaultPath?: string }): Promise<string | null>
    }
  }
}

export {}
