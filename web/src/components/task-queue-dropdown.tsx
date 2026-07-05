import { useCallback, useEffect, useRef, useState } from "react"
import { api, subscribeAllEvents, type Task } from "@/lib/api"
import { navigate } from "@/lib/router"
import { STEP_NAME, STATUS_CONFIG } from "@/lib/constants"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { HugeiconsIcon } from "@hugeicons/react"
import { Delete01Icon, Loading03Icon, PauseIcon, PlayIcon, Task01Icon } from "@hugeicons/core-free-icons"

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

function TaskRow({
  task,
  onClick,
  onPause,
  onResume,
  onDelete,
  busy,
}: {
  task: Task
  onClick: () => void
  onPause: () => void
  onResume: () => void
  onDelete: () => void
  busy: boolean
}) {
  const cfg = STATUS_CONFIG[task.status] ?? STATUS_CONFIG.cancelled
  const pct = Math.round(task.progress * 100)
  const stepLabel = task.current_step ? (STEP_NAME[task.current_step] ?? task.current_step) : cfg.label
  const canPause = task.status === "queued" || task.status === "processing"
  const canResume = task.status === "paused"

  return (
    <div className="flex items-center gap-1 px-2 py-1.5 hover:bg-muted/60 transition-colors rounded-md">
      <button
        onClick={onClick}
        className="min-w-0 flex flex-1 items-center gap-2 rounded px-1 py-0.5 text-left"
      >
        {task.status === "processing" ? (
          <HugeiconsIcon icon={Loading03Icon} className="h-3 w-3 animate-spin text-blue-500 shrink-0" />
        ) : (
          <span className={cn("h-2 w-2 rounded-full shrink-0", cfg.dot)} />
        )}

        <span className="text-xs font-medium truncate flex-1 min-w-0" title={task.source}>
          {formatSource(task.source)}
        </span>

        <span className={cn("text-[11px] shrink-0", cfg.color)}>
          {stepLabel}
        </span>

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

        <span className="text-[10px] text-muted-foreground tabular-nums shrink-0 w-6 text-right">
          {formatElapsed(task.created_at)}
        </span>
      </button>

      <div className="flex shrink-0 items-center gap-0.5">
        {canPause && (
          <Button size="icon-xs" variant="ghost" title="暂停" disabled={busy} onClick={onPause}>
            <HugeiconsIcon icon={busy ? Loading03Icon : PauseIcon} className={cn("h-3 w-3", busy && "animate-spin")} />
          </Button>
        )}
        {canResume && (
          <Button size="icon-xs" variant="ghost" title="恢复" disabled={busy} onClick={onResume}>
            <HugeiconsIcon icon={busy ? Loading03Icon : PlayIcon} className={cn("h-3 w-3", busy && "animate-spin")} />
          </Button>
        )}
        <Button
          size="icon-xs"
          variant="ghost"
          title="删除"
          disabled={busy}
          onClick={onDelete}
          className="text-destructive hover:text-destructive"
        >
          <HugeiconsIcon icon={Delete01Icon} className="h-3 w-3" />
        </Button>
      </div>
    </div>
  )
}

export function TaskQueueDropdown() {
  const [activeTasks, setActiveTasks] = useState<Task[]>([])
  const [open, setOpen] = useState(false)
  const [busyTaskId, setBusyTaskId] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Task | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [processing, queued, paused] = await Promise.all([
        api.tasks.list("processing", 200),
        api.tasks.list("queued", 200),
        api.tasks.list("paused", 200),
      ])
      setActiveTasks([...processing, ...queued, ...paused])
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
  const processingCount = activeTasks.filter((t) => t.status === "processing").length
  const queuedCount = activeTasks.filter((t) => t.status === "queued").length
  const pausedCount = activeTasks.filter((t) => t.status === "paused").length

  const runTaskAction = useCallback(async (task: Task, action: "pause" | "resume" | "delete") => {
    setBusyTaskId(task.id)
    try {
      if (action === "pause") await api.tasks.pause(task.id)
      if (action === "resume") await api.tasks.resume(task.id)
      if (action === "delete") await api.tasks.delete(task.id)
      await refresh()
    } catch (error) {
      console.warn(`Task ${action} failed:`, error)
    } finally {
      setBusyTaskId(null)
    }
  }, [refresh])

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
  if (count === 0 && !deleteTarget) return null

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
              处理队列 ({processingCount} 进行中
              {queuedCount > 0 && `, ${queuedCount} 排队`}
              {pausedCount > 0 && `, ${pausedCount} 暂停`})
            </span>
          </div>
          <div className="py-1 max-h-64 overflow-y-auto">
            {activeTasks.map((task) => (
              <TaskRow
                key={task.id}
                task={task}
                busy={busyTaskId === task.id}
                onClick={() => {
                  setOpen(false)
                  const outputDir = task.result?.output_dir as string | undefined
                  if (outputDir) {
                    navigate(`#/result/archive?path=${encodeURIComponent(outputDir)}&taskId=${encodeURIComponent(task.id)}`)
                  } else {
                    navigate(`#/result/task/${task.id}`)
                  }
                }}
                onPause={() => runTaskAction(task, "pause")}
                onResume={() => runTaskAction(task, "resume")}
                onDelete={() => setDeleteTarget(task)}
              />
            ))}
          </div>
        </div>
      )}

      <AlertDialog open={!!deleteTarget} onOpenChange={(nextOpen) => { if (!nextOpen) setDeleteTarget(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除任务</AlertDialogTitle>
            <AlertDialogDescription>
              将停止任务并删除已生成文件，此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="border-t-0">
            <AlertDialogCancel disabled={busyTaskId === deleteTarget?.id}>取消</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={busyTaskId === deleteTarget?.id}
              onClick={(event) => {
                event.preventDefault()
                if (!deleteTarget) return
                runTaskAction(deleteTarget, "delete").then(() => setDeleteTarget(null))
              }}
            >
              {busyTaskId === deleteTarget?.id && (
                <HugeiconsIcon icon={Loading03Icon} className="h-4 w-4 animate-spin mr-1" />
              )}
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
