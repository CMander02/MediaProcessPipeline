import { invoke, isTauri } from "@tauri-apps/api/core"
import { open } from "@tauri-apps/plugin-dialog"

export type BackendState = "stopped" | "starting" | "running" | "stopping" | "external" | "error"

export const BOOTSTRAP_PHASES = [
  "SCANNING",
  "READY_TO_START",
  "STARTING_BACKEND",
  "WAITING_HEALTH",
  "APP_READY",
  "FAILED_RETRYABLE",
  "FAILED_MANUAL",
] as const

export type BootstrapPhase = (typeof BOOTSTRAP_PHASES)[number]

export interface BackendStatus {
  state: BackendState
  command: string
  cwd: string
  pid: number | null
  url: string
  message: string
  phase?: BootstrapPhase
  error_code?: string | null
  component_id?: string | null
  remediation?: string | null
  local_path?: string | null
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

export interface BootstrapStatus extends BackendStatus {
  phase: BootstrapPhase
  error_code: string | null
  component_id: string | null
  remediation: string | null
  local_path: string | null
}

export interface BootstrapDiagnostics {
  status: BootstrapStatus
  logs: BackendLogEntry[]
}

export type BootstrapPreflightOverallStatus =
  | "scanning"
  | "ready"
  | "needs_configuration"
  | "needs_repair"
  | "blocked"

export type BootstrapPreflightComponentStatus =
  | "scanning"
  | "ready"
  | "warning"
  | "missing"
  | "invalid"
  | "blocked"

const BOOTSTRAP_PREFLIGHT_OVERALL_STATUSES = [
  "scanning",
  "ready",
  "needs_configuration",
  "needs_repair",
  "blocked",
] as const satisfies readonly BootstrapPreflightOverallStatus[]

const BOOTSTRAP_PREFLIGHT_COMPONENT_STATUSES = [
  "scanning",
  "ready",
  "warning",
  "missing",
  "invalid",
  "blocked",
] as const satisfies readonly BootstrapPreflightComponentStatus[]

const BOOTSTRAP_PREFLIGHT_COMPONENT_IDS = [
  "desktop-runtime",
  "data-root",
  "bundled-uv",
  "python-environment",
  "ffmpeg",
  "ffprobe",
  "desktop-proxy-port",
  "backend-private-port",
  "runtime-settings",
  "webview2",
] as const

export interface BootstrapPreflightComponent {
  component_id: string
  label: string
  status: BootstrapPreflightComponentStatus
  required: boolean
  version: string | null
  path: string | null
  error_code: string | null
  remediation: string | null
  detail: string | null
}

export interface BootstrapPreflight {
  schema_version: 1
  overall_status: BootstrapPreflightOverallStatus
  components: BootstrapPreflightComponent[]
}

const PREFLIGHT_REPORT_FIELDS = [
  "schema_version",
  "overall_status",
  "components",
] as const

const PREFLIGHT_COMPONENT_FIELDS = [
  "component_id",
  "label",
  "status",
  "required",
  "version",
  "path",
  "error_code",
  "remediation",
  "detail",
] as const

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function hasExactFields(
  value: Record<string, unknown>,
  expectedFields: readonly string[],
) {
  const actualFields = Object.keys(value).sort()
  const sortedExpectedFields = [...expectedFields].sort()
  return (
    actualFields.length === sortedExpectedFields.length
    && actualFields.every((field, index) => field === sortedExpectedFields[index])
  )
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string"
}

function isBootstrapPreflightOverallStatus(
  value: unknown,
): value is BootstrapPreflightOverallStatus {
  return (
    typeof value === "string"
    && BOOTSTRAP_PREFLIGHT_OVERALL_STATUSES.some((status) => status === value)
  )
}

function isBootstrapPreflightComponentStatus(
  value: unknown,
): value is BootstrapPreflightComponentStatus {
  return (
    typeof value === "string"
    && BOOTSTRAP_PREFLIGHT_COMPONENT_STATUSES.some((status) => status === value)
  )
}

function invalidPreflightSchema(): never {
  throw new Error("Desktop preflight response has an incompatible schema.")
}

export function normalizeBootstrapPreflight(value: unknown): BootstrapPreflight {
  if (
    !isRecord(value)
    || !hasExactFields(value, PREFLIGHT_REPORT_FIELDS)
    || value.schema_version !== 1
    || !isBootstrapPreflightOverallStatus(value.overall_status)
    || !Array.isArray(value.components)
    || value.components.length !== BOOTSTRAP_PREFLIGHT_COMPONENT_IDS.length
  ) {
    return invalidPreflightSchema()
  }

  const components = value.components.map((component, index) => {
    if (
      !isRecord(component)
      || !hasExactFields(component, PREFLIGHT_COMPONENT_FIELDS)
      || typeof component.component_id !== "string"
      || component.component_id !== BOOTSTRAP_PREFLIGHT_COMPONENT_IDS[index]
      || typeof component.label !== "string"
      || component.label.length === 0
      || !isBootstrapPreflightComponentStatus(component.status)
      || typeof component.required !== "boolean"
      || !isNullableString(component.version)
      || !isNullableString(component.path)
      || !isNullableString(component.error_code)
      || !isNullableString(component.remediation)
      || !isNullableString(component.detail)
    ) {
      return invalidPreflightSchema()
    }

    return {
      component_id: component.component_id,
      label: component.label,
      status: component.status,
      required: component.required,
      version: component.version,
      path: component.path,
      error_code: component.error_code,
      remediation: component.remediation,
      detail: component.detail,
    }
  })

  return {
    schema_version: 1,
    overall_status: value.overall_status,
    components,
  }
}

export interface BootstrapBridge {
  getStatus(): Promise<BootstrapStatus>
  getPreflight(): Promise<BootstrapPreflight>
  retry(): Promise<BootstrapStatus>
  openLogs(): Promise<void>
  getDiagnostics(): Promise<BootstrapDiagnostics>
  onStatus(callback: (status: BootstrapStatus) => void): () => void
}

let tauriBackendBridge: MppBackendBridge | undefined
let tauriBootstrapBridge: BootstrapBridge | undefined

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

function isBootstrapPhase(value: unknown): value is BootstrapPhase {
  return typeof value === "string" && BOOTSTRAP_PHASES.some((phase) => phase === value)
}

function fallbackBootstrapPhase(status: BackendStatus): BootstrapPhase {
  if (status.state === "running" || status.state === "external") return "APP_READY"
  if (status.state === "starting") {
    return /\b(health|healthy|ready|wait)\b/i.test(status.message)
      ? "WAITING_HEALTH"
      : "STARTING_BACKEND"
  }
  if (status.state === "error") return "FAILED_RETRYABLE"
  if (status.state === "stopping") return "SCANNING"
  return "READY_TO_START"
}

export function normalizeBootstrapStatus(status: BackendStatus): BootstrapStatus {
  return {
    ...status,
    phase: isBootstrapPhase(status.phase) ? status.phase : fallbackBootstrapPhase(status),
    error_code: status.error_code ?? null,
    component_id: status.component_id ?? null,
    remediation: status.remediation ?? null,
    local_path: status.local_path ?? null,
  }
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

export function getBootstrapBridge(): BootstrapBridge | undefined {
  if (!isTauriRuntime()) return undefined
  if (tauriBootstrapBridge) return tauriBootstrapBridge

  const getRawStatus = () => tauriInvoke<BackendStatus>("backend_get_status")
  const getStatus = async () => normalizeBootstrapStatus(await getRawStatus())
  const getLogs = () => tauriInvoke<BackendLogEntry[]>("backend_get_logs")

  tauriBootstrapBridge = {
    getStatus,
    getPreflight: async () => normalizeBootstrapPreflight(
      await tauriInvoke<unknown>("bootstrap_get_preflight"),
    ),
    retry: async () => normalizeBootstrapStatus(await tauriInvoke<BackendStatus>("backend_retry")),
    openLogs: () => tauriInvoke<void>("open_logs"),
    async getDiagnostics() {
      const [status, logs] = await Promise.all([getStatus(), getLogs()])
      return { status, logs }
    },
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
          // The page keeps its last stable bootstrap state while one IPC poll is unavailable.
        } finally {
          if (!disposed) {
            timer = window.setTimeout(() => void tick(), 750)
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
  }

  return tauriBootstrapBridge
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
