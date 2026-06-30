import { invoke, isTauri } from "@tauri-apps/api/core"
import { open } from "@tauri-apps/plugin-dialog"

export type BackendState = "stopped" | "starting" | "running" | "stopping" | "external" | "error"

export interface BackendStatus {
  state: BackendState
  command: string
  cwd: string
  pid: number | null
  url: string
  message: string
}

export interface BackendLogEntry {
  ts: string
  source: "stdout" | "stderr" | "system" | "error"
  line: string
}

export interface MppBackendBridge {
  getStatus(): Promise<BackendStatus>
  getLogs(): Promise<BackendLogEntry[]>
  start(): Promise<BackendStatus>
  stop(): Promise<BackendStatus>
  restart(): Promise<BackendStatus>
  onStatus(callback: (status: BackendStatus) => void): () => void
  onLog(callback: (entry: BackendLogEntry) => void): () => void
}

let tauriBackendBridge: MppBackendBridge | undefined

type TauriWindow = Window & {
  __TAURI_INTERNALS__?: {
    invoke?: unknown
  }
  __TAURI__?: {
    core?: {
      invoke?: typeof invoke
    }
  }
  isTauri?: boolean
}

function isTauriRuntime() {
  const tauriWindow = window as TauriWindow
  if (
    tauriWindow.isTauri ||
    typeof tauriWindow.__TAURI_INTERNALS__?.invoke === "function" ||
    typeof tauriWindow.__TAURI__?.core?.invoke === "function"
  ) {
    return true
  }
  try {
    return isTauri()
  } catch {
    return false
  }
}

function tauriInvoke<T>(command: string, args?: Record<string, unknown>) {
  const tauriWindow = window as TauriWindow
  const globalInvoke = tauriWindow.__TAURI__?.core?.invoke
  if (typeof globalInvoke === "function") {
    return globalInvoke<T>(command, args)
  }
  return invoke<T>(command, args)
}

function getTauriBackendBridge(): MppBackendBridge | undefined {
  if (!isTauriRuntime()) return undefined
  if (tauriBackendBridge) return tauriBackendBridge

  const getStatus = () => tauriInvoke<BackendStatus>("backend_get_status")
  const getLogs = () => tauriInvoke<BackendLogEntry[]>("backend_get_logs")

  tauriBackendBridge = {
    getStatus,
    getLogs,
    start: () => tauriInvoke<BackendStatus>("backend_start"),
    stop: () => tauriInvoke<BackendStatus>("backend_stop"),
    restart: () => tauriInvoke<BackendStatus>("backend_restart"),
    onStatus(callback) {
      let disposed = false
      let lastStatus = ""

      const tick = async () => {
        try {
          const status = await getStatus()
          const serialized = JSON.stringify(status)
          if (!disposed && serialized !== lastStatus) {
            lastStatus = serialized
            callback(status)
          }
        } catch {
          // The backend page keeps the static browser-mode fallback when Tauri IPC is unavailable.
        }
      }

      void tick()
      const interval = window.setInterval(tick, 1000)
      return () => {
        disposed = true
        window.clearInterval(interval)
      }
    },
    onLog(callback) {
      let disposed = false
      let seenCount: number | null = null

      const tick = async () => {
        try {
          const logs = await getLogs()
          if (disposed) return
          if (seenCount === null) {
            seenCount = logs.length
            return
          }
          for (const entry of logs.slice(seenCount)) {
            callback(entry)
          }
          seenCount = logs.length
        } catch {
          // Log polling is best-effort; direct getLogs() still populates the initial buffer.
        }
      }

      void tick()
      const interval = window.setInterval(tick, 750)
      return () => {
        disposed = true
        window.clearInterval(interval)
      }
    },
  }

  return tauriBackendBridge
}

export function getBackendBridge(): MppBackendBridge | undefined {
  return getTauriBackendBridge()
}

export interface SelectDirectoryOptions {
  title?: string
  defaultPath?: string
}

export async function selectDirectory(options: SelectDirectoryOptions = {}): Promise<string | null | undefined> {
  if (!isTauriRuntime()) return undefined

  const selected = await open({
    title: options.title,
    defaultPath: options.defaultPath,
    directory: true,
    multiple: false,
    canCreateDirectories: true,
  })
  if (Array.isArray(selected)) return selected[0] ?? null
  return selected
}

export async function openExternalUrl(url: string): Promise<void> {
  if (!/^https?:\/\//i.test(url)) return

  if (isTauriRuntime()) {
    try {
      await tauriInvoke<void>("open_external_url", { url })
      return
    } catch (error) {
      console.warn("Tauri open_external_url failed, falling back to window.open:", error)
    }
  }

  window.open(url, "_blank", "noopener,noreferrer")
}
