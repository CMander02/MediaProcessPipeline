import { useEffect, useRef, useState } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import {
  ArrowDown01Icon,
  ArrowUp01Icon,
  CancelCircleIcon,
  FolderOpenIcon,
  Loading03Icon,
  RefreshIcon,
  ServerStack01Icon,
  Tick02Icon,
} from "@hugeicons/core-free-icons"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { APP_VERSION } from "@/generated/app-version"
import {
  getBootstrapBridge,
  type BootstrapBridge,
  type BootstrapDiagnostics,
  type BootstrapPhase,
  type BootstrapStatus,
} from "@/lib/tauri"
import { cn } from "@/lib/utils"

const initialStatus: BootstrapStatus = {
  state: "starting",
  command: "",
  cwd: "",
  pid: null,
  url: "http://localhost:18000",
  message: "正在检查桌面运行环境。",
  phase: "SCANNING",
  error_code: null,
  component_id: null,
  remediation: null,
  local_path: null,
}

const phaseCopy: Record<BootstrapPhase, { title: string; description: string; progress: number }> = {
  SCANNING: {
    title: "正在检查本地运行环境",
    description: "正在确认应用资源、数据目录和服务端口。",
    progress: 15,
  },
  READY_TO_START: {
    title: "运行环境已准备完成",
    description: "本地组件检查通过，可以启动媒体处理服务。",
    progress: 35,
  },
  STARTING_BACKEND: {
    title: "正在启动本地服务",
    description: "服务进程已经创建，正在载入运行配置。",
    progress: 60,
  },
  WAITING_HEALTH: {
    title: "正在确认服务状态",
    description: "启动已经完成，正在等待健康检查通过。",
    progress: 82,
  },
  APP_READY: {
    title: "应用已就绪",
    description: "本地服务运行正常，正在进入工作区。",
    progress: 100,
  },
  FAILED_RETRYABLE: {
    title: "启动遇到可恢复问题",
    description: "按建议处理后可以直接重试。",
    progress: 35,
  },
  FAILED_MANUAL: {
    title: "需要完成本地处理",
    description: "请按修复建议处理组件或安装目录，然后重新打开应用。",
    progress: 15,
  },
}

const stepLabels = ["检查环境", "启动服务", "健康检查", "进入应用"]

function failureStatus(
  phase: "FAILED_RETRYABLE" | "FAILED_MANUAL",
  errorCode: string,
  componentId: string,
  remediation: string,
): BootstrapStatus {
  return {
    ...initialStatus,
    state: "error",
    phase,
    message: phaseCopy[phase].description,
    error_code: errorCode,
    component_id: componentId,
    remediation,
  }
}

function activeStep(phase: BootstrapPhase) {
  if (phase === "APP_READY") return 3
  if (phase === "WAITING_HEALTH") return 2
  if (phase === "STARTING_BACKEND" || phase === "READY_TO_START") return 1
  return 0
}

function PhaseIcon({ phase }: { phase: BootstrapPhase }) {
  if (phase === "APP_READY") {
    return <HugeiconsIcon icon={Tick02Icon} className="size-6" />
  }
  if (phase === "FAILED_RETRYABLE" || phase === "FAILED_MANUAL") {
    return <HugeiconsIcon icon={CancelCircleIcon} className="size-6" />
  }
  return <HugeiconsIcon icon={Loading03Icon} className="size-6 animate-spin" />
}

function enterApplication() {
  const nextUrl = new URL(window.location.href)
  nextUrl.searchParams.delete("bootstrap")
  window.location.assign(nextUrl.toString())
}

export function BootstrapPage() {
  const bridgeRef = useRef<BootstrapBridge | undefined>(undefined)
  const [status, setStatus] = useState<BootstrapStatus>(initialStatus)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false)
  const [diagnostics, setDiagnostics] = useState<BootstrapDiagnostics | null>(null)
  const [diagnosticsLoading, setDiagnosticsLoading] = useState(false)
  const [diagnosticsError, setDiagnosticsError] = useState<string | null>(null)

  const copy = phaseCopy[status.phase]
  const isFailure = status.phase === "FAILED_RETRYABLE" || status.phase === "FAILED_MANUAL"
  const currentStep = activeStep(status.phase)

  useEffect(() => {
    const bridge = getBootstrapBridge()
    bridgeRef.current = bridge

    if (!bridge) {
      setStatus(failureStatus(
        "FAILED_MANUAL",
        "DESKTOP_BRIDGE_UNAVAILABLE",
        "desktop-shell",
        "请通过 MediaProcessPipeline 桌面应用入口重新打开。",
      ))
      return
    }

    let active = true
    void bridge.getStatus()
      .then((nextStatus) => {
        if (active) setStatus(nextStatus)
      })
      .catch(() => {
        if (!active) return
        setStatus(failureStatus(
          "FAILED_RETRYABLE",
          "BOOTSTRAP_STATUS_UNAVAILABLE",
          "desktop-bootstrap",
          "请重试启动；问题持续时打开日志目录查看诊断文件。",
        ))
      })

    const unsubscribe = bridge.onStatus((nextStatus) => {
      if (active) setStatus(nextStatus)
    })

    return () => {
      active = false
      unsubscribe()
    }
  }, [])

  async function retry() {
    const bridge = bridgeRef.current
    if (!bridge) return

    setBusy(true)
    setActionError(null)
    setDiagnostics(null)
    setDiagnosticsError(null)
    setStatus((current) => ({
      ...current,
      state: "starting",
      phase: "SCANNING",
      message: phaseCopy.SCANNING.description,
      error_code: null,
      component_id: null,
      remediation: null,
      local_path: null,
    }))
    try {
      setStatus(await bridge.retry())
    } catch {
      setStatus(failureStatus(
        "FAILED_RETRYABLE",
        "BOOTSTRAP_RETRY_FAILED",
        "desktop-bootstrap",
        "请打开日志目录确认诊断信息，然后再次重试。",
      ))
    } finally {
      setBusy(false)
    }
  }

  async function openLogs() {
    const bridge = bridgeRef.current
    if (!bridge) {
      setActionError("桌面日志接口当前不可用，请通过桌面应用入口重新打开。")
      return
    }
    setActionError(null)
    try {
      await bridge.openLogs()
    } catch {
      setActionError("日志目录暂时无法打开，请完成修复建议后重试。")
    }
  }

  async function toggleDiagnostics() {
    const nextOpen = !diagnosticsOpen
    setDiagnosticsOpen(nextOpen)
    if (!nextOpen || diagnostics || diagnosticsLoading) return

    const bridge = bridgeRef.current
    if (!bridge) {
      setDiagnosticsError("桌面诊断接口当前不可用。")
      return
    }

    setDiagnosticsLoading(true)
    setDiagnosticsError(null)
    try {
      setDiagnostics(await bridge.getDiagnostics())
    } catch {
      setDiagnosticsError("诊断数据暂时不可用，可打开日志目录查看完整记录。")
    } finally {
      setDiagnosticsLoading(false)
    }
  }

  const errorCode = status.error_code ?? "BOOTSTRAP_FAILED"
  const componentId = status.component_id ?? "desktop-bootstrap"
  const remediation = status.remediation ?? "请打开日志目录确认诊断信息，然后重新启动应用。"

  return (
    <main className="min-h-screen supports-[min-height:100dvh]:min-h-dvh bg-slate-50 px-4 py-6 text-slate-950 dark:bg-slate-950 dark:text-slate-50 sm:px-6 sm:py-10">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-5">
        <header className="flex items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-2.5">
            <img src="/favicon.svg" className="size-7" alt="" aria-hidden="true" />
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold">MediaProcessPipeline</div>
              <div className="text-xs text-slate-500 dark:text-slate-400">桌面启动服务</div>
            </div>
          </div>
          <span className="shrink-0 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-400">
            v{APP_VERSION}
          </span>
        </header>

        <section
          className={cn(
            "overflow-hidden rounded-xl border bg-white shadow-sm dark:bg-slate-900",
            isFailure
              ? "border-red-200 dark:border-red-950"
              : "border-slate-200 dark:border-slate-800",
          )}
          aria-labelledby="bootstrap-title"
          role={isFailure ? "alert" : undefined}
          aria-live={isFailure ? "assertive" : undefined}
        >
          <div className="p-5 sm:p-7">
            <div
              className={cn(
                "flex size-12 items-center justify-center rounded-xl border",
                isFailure
                  ? "border-red-200 bg-red-50 text-red-600 dark:border-red-900 dark:bg-red-950/50 dark:text-red-400"
                  : status.phase === "APP_READY"
                    ? "border-emerald-200 bg-emerald-50 text-emerald-600 dark:border-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-400"
                    : "border-sky-200 bg-sky-50 text-sky-600 dark:border-sky-900 dark:bg-sky-950/50 dark:text-sky-400",
              )}
            >
              <PhaseIcon phase={status.phase} />
            </div>

            <div
              className="mt-5"
              role={isFailure ? undefined : "status"}
              aria-live={isFailure ? undefined : "polite"}
              aria-atomic="true"
            >
              <p className="text-xs font-medium tracking-wide text-sky-700 dark:text-sky-400">
                {status.phase}
              </p>
              <h1 id="bootstrap-title" className="mt-1 text-xl font-semibold tracking-tight sm:text-2xl">
                {copy.title}
              </h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600 dark:text-slate-300">
                {copy.description}
              </p>
            </div>

            <Progress
              className="mt-6 h-1.5 bg-slate-100 [&_[data-slot=progress-indicator]]:bg-sky-600 dark:bg-slate-800"
              value={copy.progress}
              aria-label={`启动进度 ${copy.progress}%`}
            />

            <ol className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="启动步骤">
              {stepLabels.map((label, index) => {
                const complete = index < currentStep || status.phase === "APP_READY"
                const active = index === currentStep && status.phase !== "APP_READY"
                return (
                  <li
                    key={label}
                    className={cn(
                      "flex items-center gap-2 rounded-md px-2 py-1.5 text-xs",
                      complete && "text-sky-700 dark:text-sky-400",
                      active && "bg-sky-50 font-medium text-sky-700 dark:bg-sky-950/40 dark:text-sky-400",
                      !complete && !active && "text-slate-400 dark:text-slate-600",
                    )}
                    aria-current={active ? "step" : undefined}
                  >
                    <span
                      className={cn(
                        "flex size-4 shrink-0 items-center justify-center rounded-full border text-[10px]",
                        complete || active
                          ? "border-sky-500"
                          : "border-slate-300 dark:border-slate-700",
                      )}
                      aria-hidden="true"
                    >
                      {complete ? "✓" : index + 1}
                    </span>
                    {label}
                  </li>
                )
              })}
            </ol>

            {isFailure && (
              <div className="mt-6 rounded-lg border border-red-200 bg-red-50/60 p-4 dark:border-red-950 dark:bg-red-950/20">
                <h2 className="text-sm font-semibold text-red-800 dark:text-red-300">错误详情</h2>
                <dl className="mt-3 grid gap-3 text-xs sm:grid-cols-[8rem_1fr]">
                  <dt className="font-mono text-red-700/80 dark:text-red-300/80">failure_reason</dt>
                  <dd className="min-w-0 break-words leading-5 text-red-950 dark:text-red-100">
                    {status.message || copy.description}
                  </dd>
                  <dt className="font-mono text-red-700/80 dark:text-red-300/80">error_code</dt>
                  <dd className="min-w-0 break-words font-mono text-red-950 dark:text-red-100">{errorCode}</dd>
                  <dt className="font-mono text-red-700/80 dark:text-red-300/80">component_id</dt>
                  <dd className="min-w-0 break-words font-mono text-red-950 dark:text-red-100">{componentId}</dd>
                  <dt className="font-mono text-red-700/80 dark:text-red-300/80">remediation</dt>
                  <dd className="min-w-0 break-words leading-5 text-red-950 dark:text-red-100">{remediation}</dd>
                  <dt className="font-mono text-red-700/80 dark:text-red-300/80">local_path</dt>
                  <dd className="min-w-0 break-all font-mono text-red-950 dark:text-red-100">
                    {status.local_path ?? "—"}
                  </dd>
                </dl>
              </div>
            )}

            <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
              {(status.phase === "READY_TO_START" || status.phase === "FAILED_RETRYABLE") && (
                <Button
                  className="h-9 bg-sky-600 px-4 text-white hover:bg-sky-700 focus-visible:ring-sky-500/40"
                  disabled={busy}
                  onClick={() => void retry()}
                >
                  <HugeiconsIcon
                    icon={status.phase === "READY_TO_START" ? ServerStack01Icon : RefreshIcon}
                    className={cn("size-4", busy && "animate-spin")}
                  />
                  {status.phase === "READY_TO_START" ? "启动本地服务" : "重试"}
                </Button>
              )}
              {status.phase === "APP_READY" && (
                <Button
                  className="h-9 bg-sky-600 px-4 text-white hover:bg-sky-700 focus-visible:ring-sky-500/40"
                  onClick={enterApplication}
                >
                  <HugeiconsIcon icon={Tick02Icon} className="size-4" />
                  进入应用
                </Button>
              )}
              <Button variant="outline" className="h-9" onClick={() => void openLogs()}>
                <HugeiconsIcon icon={FolderOpenIcon} className="size-4" />
                打开日志目录
              </Button>
              <Button
                variant="ghost"
                className="h-9 sm:ml-auto"
                aria-expanded={diagnosticsOpen}
                aria-controls="bootstrap-diagnostics"
                onClick={() => void toggleDiagnostics()}
              >
                {diagnosticsOpen ? "收起诊断" : "展开诊断"}
                <HugeiconsIcon
                  icon={diagnosticsOpen ? ArrowUp01Icon : ArrowDown01Icon}
                  className="size-4"
                />
              </Button>
            </div>

            {actionError && (
              <p className="mt-3 text-xs text-red-600 dark:text-red-400" role="alert">
                {actionError}
              </p>
            )}
          </div>

          {diagnosticsOpen && (
            <div
              id="bootstrap-diagnostics"
              className="border-t border-slate-200 bg-slate-50/70 p-5 dark:border-slate-800 dark:bg-slate-950/40 sm:p-7"
            >
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-sm font-semibold">启动诊断</h2>
                {diagnostics && (
                  <span className="text-xs text-slate-500 dark:text-slate-400">
                    {diagnostics.logs.length} 条日志
                  </span>
                )}
              </div>

              {diagnosticsLoading && (
                <div className="mt-4 flex items-center gap-2 text-xs text-slate-500" role="status">
                  <HugeiconsIcon icon={Loading03Icon} className="size-4 animate-spin" />
                  正在读取诊断数据
                </div>
              )}
              {diagnosticsError && (
                <p className="mt-4 text-xs text-red-600 dark:text-red-400" role="alert">
                  {diagnosticsError}
                </p>
              )}
              {diagnostics && !diagnosticsLoading && (
                <div className="mt-4 space-y-3">
                  <dl className="grid gap-2 rounded-md border border-slate-200 bg-white p-3 text-xs dark:border-slate-800 dark:bg-slate-900 sm:grid-cols-[7rem_1fr]">
                    <dt className="text-slate-500 dark:text-slate-400">当前阶段</dt>
                    <dd className="font-mono">{diagnostics.status.phase}</dd>
                    <dt className="text-slate-500 dark:text-slate-400">进程</dt>
                    <dd className="font-mono">{diagnostics.status.pid ?? "—"}</dd>
                    <dt className="text-slate-500 dark:text-slate-400">状态说明</dt>
                    <dd className="break-words">{diagnostics.status.message || "—"}</dd>
                  </dl>
                  <div
                    className="max-h-56 overflow-auto rounded-md border border-slate-800 bg-slate-950 p-3 font-mono text-[11px] leading-5 text-slate-200"
                    aria-label="最近启动日志"
                  >
                    {diagnostics.logs.length === 0 ? (
                      <p className="text-slate-500">暂无启动日志。</p>
                    ) : (
                      diagnostics.logs.slice(-80).map((entry, index) => (
                        <div key={`${entry.ts}-${entry.source}-${index}`} className="flex gap-2">
                          <span className="shrink-0 text-slate-500">{entry.source}</span>
                          <span className="min-w-0 whitespace-pre-wrap break-words">{entry.line}</span>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </section>

        <p className="text-center text-xs leading-5 text-slate-500 dark:text-slate-500">
          启动页只通过桌面进程通信读取状态；服务健康后才会加载工作区数据。
        </p>
      </div>
    </main>
  )
}
