import type { MppBackendBridge } from "@/lib/electron"

declare global {
  interface Window {
    mppBackend?: MppBackendBridge
  }
}

export {}
