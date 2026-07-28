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

export function isTauriRuntime() {
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
      let timer: number | null = null

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
        } finally {
          if (!disposed) {
            timer = window.setTimeout(() => void tick(), 1000)
          }
        }
      }

      void tick()
      return () => {
        disposed = true
        if (timer !== null) {
          window.clearTimeout(timer)
        }
      }
    },
    onLog(callback) {
      let disposed = false
      let lastSeenEntryKey: string | null = null

      const entryKey = (entry: BackendLogEntry) =>
        `${entry.ts}\u0000${entry.source}\u0000${entry.line}`

      const tick = async () => {
        try {
          const logs = await getLogs()
          if (disposed) return
          if (logs.length === 0) {
            return
          }

          if (lastSeenEntryKey === null) {
            lastSeenEntryKey = entryKey(logs[logs.length - 1])
            return
          }

          let lastSeenIndex = -1
          for (let index = logs.length - 1; index >= 0; index -= 1) {
            if (entryKey(logs[index]) === lastSeenEntryKey) {
              lastSeenIndex = index
              break
            }
          }

          // The native backend keeps a rolling fixed-size buffer. Once it is
          // full, its length no longer changes, so a count-based cursor misses
          // every subsequent line. An entry cursor follows the rolling window;
          // if polling falls behind the whole window, replay the current buffer
          // so the UI converges to the backend's latest 1200 lines.
          const newEntries = lastSeenIndex >= 0 ? logs.slice(lastSeenIndex + 1) : logs
          for (const entry of newEntries) {
            callback(entry)
          }
          lastSeenEntryKey = entryKey(logs[logs.length - 1])
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
    await tauriInvoke<void>("open_external_url", { url })
    return
  }

  window.open(url, "_blank", "noopener,noreferrer")
}
