import { useEffect, useMemo, useRef, useState } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import {
  CancelCircleIcon,
  ComputerTerminal01Icon,
  Loading03Icon,
  PlayIcon,
  RefreshIcon,
  ServerStack01Icon,
  StopIcon,
  Tick02Icon,
} from "@hugeicons/core-free-icons"
import { Button } from "@/components/ui/button"
import { getBackendBridge, type BackendLogEntry, type BackendState, type BackendStatus } from "@/lib/tauri"
import { cn } from "@/lib/utils"

const defaultStatus: BackendStatus = {
  state: "stopped",
  command: "uv run python -m app.cli serve",
  cwd: "backend",
  pid: null,
  url: "http://127.0.0.1:18000",
  message: "仅桌面端应用内可管理后端进程。",
}

const statusLabel: Record<BackendState, string> = {
  stopped: "已停止",
  starting: "启动中",
  running: "运行中",
  stopping: "停止中",
  external: "外部运行",
  error: "异常",
}

const statusClassName: Record<BackendState, string> = {
  stopped: "bg-muted text-muted-foreground",
  starting: "bg-blue-500/10 text-blue-600",
  running: "bg-emerald-500/10 text-emerald-600",
  stopping: "bg-amber-500/10 text-amber-600",
  external: "bg-cyan-500/10 text-cyan-600",
  error: "bg-destructive/10 text-destructive",
}

const sourceClassName: Record<BackendLogEntry["source"], string> = {
  stdout: "text-emerald-600",
  stderr: "text-amber-600",
  system: "text-blue-600",
  error: "text-destructive",
}

function formatTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return "--:--:--"
  return date.toLocaleTimeString("zh-CN", { hour12: false })
}

function formatError(error: unknown) {
  if (error instanceof Error) return error.message
  if (typeof error === "string") return error
  return JSON.stringify(error)
}

function makeLogEntry(source: BackendLogEntry["source"], line: string): BackendLogEntry {
  return {
    ts: new Date().toISOString(),
    source,
    line,
  }
}

function StatusIcon({ state }: { state: BackendState }) {
  if (state === "running" || state === "external") {
    return <HugeiconsIcon icon={Tick02Icon} className="h-4 w-4" />
  }
  if (state === "starting" || state === "stopping") {
    return <HugeiconsIcon icon={Loading03Icon} className="h-4 w-4 animate-spin" />
  }
  if (state === "error") {
    return <HugeiconsIcon icon={CancelCircleIcon} className="h-4 w-4" />
  }
  return <HugeiconsIcon icon={ServerStack01Icon} className="h-4 w-4" />
}

export function BackendPage() {
  const bridge = getBackendBridge()
  const [status, setStatus] = useState<BackendStatus>(defaultStatus)
  const [logs, setLogs] = useState<BackendLogEntry[]>([])
  const [busy, setBusy] = useState(false)
  const logEndRef = useRef<HTMLDivElement>(null)
  const hasDesktopBridge = Boolean(bridge)
  const commandLine = useMemo(() => `cd ${status.cwd} && ${status.command}`, [status.cwd, status.command])
  const canStart = hasDesktopBridge && !busy && (status.state === "stopped" || status.state === "error")
  const canStop = hasDesktopBridge && !busy && (status.state === "running" || status.state === "starting")
  const canRestart = hasDesktopBridge && !busy && (status.state === "running" || status.state === "external" || status.state === "error")

  useEffect(() => {
    if (!bridge) return

    let active = true
    bridge.getStatus().then((nextStatus) => {
      if (active) setStatus(nextStatus)
    }).catch((error: unknown) => {
      if (!active) return
      const message = `桌面桥接状态读取失败：${formatError(error)}`
      setStatus((current) => ({ ...current, state: "error", message }))
      setLogs((current) => [...current.slice(-1199), makeLogEntry("error", message)])
    })
    bridge.getLogs().then((nextLogs) => {
      if (active) setLogs(nextLogs)
    }).catch((error: unknown) => {
      if (!active) return
      const message = `桌面桥接日志读取失败：${formatError(error)}`
      setLogs((current) => [...current.slice(-1199), makeLogEntry("error", message)])
    })

    const unsubscribeStatus = bridge.onStatus(setStatus)
    const unsubscribeLog = bridge.onLog((entry) => {
      setLogs((current) => [...current.slice(-1199), entry])
    })

    return () => {
      active = false
      unsubscribeStatus()
      unsubscribeLog()
    }
  }, [bridge])

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ block: "end" })
  }, [logs.length])

  async function runAction(action: "start" | "stop" | "restart") {
    if (!bridge) return
    setBusy(true)
    try {
      const nextStatus = await bridge[action]()
      setStatus(nextStatus)
    } catch (error) {
      const actionLabel = action === "start" ? "启动" : action === "stop" ? "停止" : "重启"
      const message = `后端${actionLabel}失败：${formatError(error)}`
      setStatus((current) => ({ ...current, state: "error", message }))
      setLogs((current) => [...current.slice(-1199), makeLogEntry("error", message)])
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="flex h-full min-h-0 flex-col bg-background">
      <div className="shrink-0 border-b px-6 py-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-sm font-medium">
              <HugeiconsIcon icon={ComputerTerminal01Icon} className="h-4.5 w-4.5 text-primary" />
              后端守护进程
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span className={cn("inline-flex items-center gap-1.5 rounded-md px-2 py-1 font-medium", statusClassName[status.state])}>
                <StatusIcon state={status.state} />
                {statusLabel[status.state]}
              </span>
              <span>PID: {status.pid ?? "-"}</span>
              <span>{status.url}</span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline" disabled={!canStart} onClick={() => runAction("start")}>
              <HugeiconsIcon icon={PlayIcon} className="h-3.5 w-3.5" />
              启动
            </Button>
            <Button size="sm" variant="outline" disabled={!canRestart} onClick={() => runAction("restart")}>
              <HugeiconsIcon icon={RefreshIcon} className={cn("h-3.5 w-3.5", busy && "animate-spin")} />
              重启
            </Button>
            <Button size="sm" variant="destructive" disabled={!canStop} onClick={() => runAction("stop")}>
              <HugeiconsIcon icon={StopIcon} className="h-3.5 w-3.5" />
              停止
            </Button>
          </div>
        </div>

        <div className="mt-4 grid gap-3 lg:grid-cols-[1fr_1fr]">
          <div className="min-w-0 rounded-md border bg-muted/30 px-3 py-2">
            <div className="text-[11px] font-medium uppercase text-muted-foreground">命令</div>
            <div className="mt-1 truncate font-mono text-xs" title={commandLine}>{commandLine}</div>
          </div>
          <div className="min-w-0 rounded-md border bg-muted/30 px-3 py-2">
            <div className="text-[11px] font-medium uppercase text-muted-foreground">状态</div>
            <div className="mt-1 truncate text-xs" title={status.message}>{status.message}</div>
          </div>
        </div>

        {!hasDesktopBridge && (
          <div className="mt-3 rounded-md border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-xs text-amber-700">
            当前是浏览器模式，只能查看 Web 前端；启动、停止和日志观察需要通过桌面端入口打开。
          </div>
        )}
      </div>

      <div className="flex min-h-0 flex-1 flex-col px-6 py-4">
        <div className="mb-2 flex items-center justify-between">
          <div className="text-sm font-medium">后端输出</div>
          <div className="text-xs text-muted-foreground">{logs.length} lines</div>
        </div>
        <div className="min-h-0 flex-1 overflow-auto rounded-md border bg-zinc-950 p-3 font-mono text-[12px] leading-5 text-zinc-100">
          {logs.length === 0 ? (
            <div className="text-zinc-500">暂无日志。</div>
          ) : (
            logs.map((entry, index) => (
              <div key={`${entry.ts}-${index}`} className="flex gap-3">
                <span className="w-20 shrink-0 text-zinc-500">{formatTime(entry.ts)}</span>
                <span className={cn("w-14 shrink-0", sourceClassName[entry.source])}>{entry.source}</span>
                <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">{entry.line}</span>
              </div>
            ))
          )}
          <div ref={logEndRef} />
        </div>
      </div>
    </section>
  )
}
