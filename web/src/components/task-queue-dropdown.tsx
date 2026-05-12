import { useCallback, useEffect, useRef, useState } from "react"
import { api, subscribeAllEvents, type Task } from "@/lib/api"
import { navigate } from "@/lib/router"
import { STEP_NAME, STATUS_CONFIG } from "@/lib/constants"
import { cn } from "@/lib/utils"
import { HugeiconsIcon } from "@hugeicons/react"
import { Loading03Icon, Task01Icon } from "@hugeicons/core-free-icons"

function formatSource(source: string): string {
  const parts = source.replace(/\\/g, "/").split("/")
  const name = parts.at(-1) ?? source
  return name.length > 40 ? `${name.slice(0, 37)}…` : name
}

function formatElapsed(created: string): string {
  const secs = (Date.now() - new Date(created).getTime()) / 1000
  if (secs < 60) return `${Math.floor(secs)}s`
  if (secs < 3600) return `${Math.floor(secs / 60)}m`
  return `${(secs / 3600).toFixed(1)}h`
}

function TaskRow({ task, onClick }: { task: Task; onClick: () => void }) {
  const cfg = STATUS_CONFIG[task.status] ?? STATUS_CONFIG.cancelled
  const pct = Math.round(task.progress * 100)
  const stepLabel = task.current_step ? (STEP_NAME[task.current_step] ?? task.current_step) : cfg.label

  return (
    <button
      onClick={onClick}
      className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-muted/60 transition-colors rounded-md"
    >
      {/* Status indicator */}
      {task.status === "processing" ? (
        <HugeiconsIcon icon={Loading03Icon} className="h-3 w-3 animate-spin text-blue-500 shrink-0" />
      ) : (
        <span className={cn("h-2 w-2 rounded-full shrink-0", cfg.dot)} />
      )}

      {/* Source name */}
      <span className="text-xs font-medium truncate flex-1 min-w-0" title={task.source}>
        {formatSource(task.source)}
      </span>

      {/* Current step */}
      <span className={cn("text-[11px] shrink-0", cfg.color)}>
        {stepLabel}
      </span>

      {/* Progress */}
      {task.status === "processing" && (
        <div className="w-12 shrink-0 flex items-center gap-1">
          <div className="flex-1 h-1 rounded-full bg-muted overflow-hidden">
            <div
              className="h-full bg-blue-500 rounded-full transition-all"
              style={{ width: `${pct}%` }}
            />
          </div>
          <span className="text-[10px] text-muted-foreground tabular-nums w-7 text-right">{pct}%</span>
        </div>
      )}

      {/* Elapsed time */}
      <span className="text-[10px] text-muted-foreground tabular-nums shrink-0 w-6 text-right">
        {formatElapsed(task.created_at)}
      </span>
    </button>
  )
}

export function TaskQueueDropdown() {
  const [activeTasks, setActiveTasks] = useState<Task[]>([])
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [processing, queued] = await Promise.all([
        api.tasks.list("processing", 200),
        api.tasks.list("queued", 200),
      ])
      setActiveTasks([...processing, ...queued])
    } catch {}
  }, [])

  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current) return
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null
      refresh()
    }, 500)
  }, [refresh])

  // Fetch on mount + SSE + polling while tasks are active
  useEffect(() => {
    refresh()
    const unsub = subscribeAllEvents(() => scheduleRefresh())
    const interval = setInterval(refresh, 5000)
    return () => {
      unsub()
      clearInterval(interval)
      if (refreshTimer.current) {
        clearTimeout(refreshTimer.current)
        refreshTimer.current = null
      }
    }
  }, [refresh, scheduleRefresh])

  const count = activeTasks.length

  // Close on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [open])

  // Close on Escape
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false)
    }
    document.addEventListener("keydown", handler)
    return () => document.removeEventListener("keydown", handler)
  }, [open])

  // Nothing active — don't show
  if (count === 0) return null

  return (
    <div ref={ref} className="relative">
      {/* Trigger button */}
      <button
        onClick={() => setOpen(!open)}
        className={cn(
          "flex items-center gap-1.5 rounded-md px-2 py-1 text-xs transition-colors",
          open
            ? "bg-primary/10 text-primary"
            : "text-muted-foreground hover:text-foreground hover:bg-muted",
        )}
        title="处理队列"
      >
        <HugeiconsIcon icon={Task01Icon} className="h-3.5 w-3.5" />
        <span className="inline-flex items-center justify-center rounded-full bg-blue-500 text-white text-[10px] font-medium min-w-[1.1rem] h-[1.1rem] px-1 leading-none">
          {count}
        </span>
      </button>

      {/* Dropdown panel */}
      {open && (
        <div className="absolute right-0 top-full mt-1.5 z-50 w-80 rounded-lg border bg-background shadow-lg">
          <div className="px-3 py-2 border-b">
            <span className="text-xs font-medium text-muted-foreground">
              处理队列 ({activeTasks.filter((t) => t.status === "processing").length} 进行中
              {activeTasks.filter((t) => t.status === "queued").length > 0 &&
                `, ${activeTasks.filter((t) => t.status === "queued").length} 排队`})
            </span>
          </div>
          <div className="py-1 max-h-64 overflow-y-auto">
            {activeTasks.map((task) => (
              <TaskRow
                key={task.id}
                task={task}
                onClick={() => {
                  setOpen(false)
                  const outputDir = task.result?.output_dir as string | undefined
                  if (outputDir) {
                    navigate(`#/result/archive?path=${encodeURIComponent(outputDir)}&taskId=${encodeURIComponent(task.id)}`)
                  } else {
                    navigate(`#/result/task/${task.id}`)
                  }
                }}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
