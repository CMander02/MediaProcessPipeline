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

export function getBackendBridge(): MppBackendBridge | undefined {
  return window.mppBackend
}

export function getDialogBridge() {
  return window.mppDialog
}
